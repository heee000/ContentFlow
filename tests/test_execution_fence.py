"""Worker ownership boundaries. All providers, accounts and databases are isolated."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from functools import partial
from types import SimpleNamespace
from threading import Barrier
from unittest.mock import patch
import uuid

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from contentflow import db
from contentflow.entities import Asset, Campaign, ChannelConnection, ContentItem, Job, PublishJob, WorkflowRun
from contentflow.connectors import WechatConnector
from contentflow.object_storage import build_object_storage
from contentflow.providers import MockProvider
from contentflow.ai_provenance import AIProvenanceRecorder
from contentflow.execution_fence import ExecutionFence, JobLeaseLost, execution_scope
from contentflow.job_queue import claim_next_job, renew_job_lease
from contentflow.media_providers import MediaGeneration, download_generated_media
from contentflow.provider_invocations import (
    LedgeredEmbeddingProvider, LedgeredMediaDownloader, LedgeredMediaProvider,
    LedgeredSearchProvider, ProviderInvocationLedger, provider_job_context,
)
from contentflow.entities import ProviderInvocationAttempt
from contentflow.worker import Worker
from generation_helpers import request_run
import test_publication_confirmation as publication

application = publication.application


def new_campaign(application):
    response = application.client.post("/api/v1/campaigns", headers=application.headers, json={
        "name": "TEST-ONLY fencing", "product_name": "ContentFlow",
        "objective": "Verify stale execution stops", "audience": "Test users",
        "platforms": ["wechat"],
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


def other_thread(action):
    # Model another process: it must not inherit the worker's ContextVars.
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(action).result(timeout=10)


def revoke(job_id, mode, control):
    if mode == "heartbeat":
        control.lost = True
        return

    def change():
        with db.SessionLocal() as session:
            job = session.get(Job, job_id)
            if mode == "owner":
                job.locked_by = "replacement-worker"
            elif mode == "attempt":
                job.attempts += 1
            elif mode == "status":
                job.status = "manual_review"
            elif mode == "expired":
                job.locked_at = datetime.now(timezone.utc) - timedelta(days=1)
            elif mode == "reused_attempt":
                job.lease_token = uuid.uuid4().hex
            else:
                raise AssertionError(mode)
            session.commit()
    other_thread(change)


class ControlledHeartbeat:
    def __init__(self, control, **kwargs):
        self.control = control

    @property
    def lost(self):
        return self.control.lost

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


@pytest.mark.parametrize("mode", ["heartbeat", "owner", "attempt", "status", "expired", "reused_attempt"])
def test_actual_workflow_stops_after_plan_loses_ownership(application, mode):
    campaign_id = new_campaign(application)
    response = request_run(application.client, f"/api/v1/campaigns/{campaign_id}/runs",
        headers=application.headers, json={})
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]
    with db.SessionLocal() as session:
        job_id = session.scalar(select(Job.id).where(Job.job_type == "workflow.execute"))
    control = SimpleNamespace(lost=False)
    calls = []

    class LosingProvider(MockProvider):
        def complete_json(self, stage, payload, *, system_prompt=None):
            calls.append(stage)
            result = super().complete_json(stage, payload, system_prompt=system_prompt)
            if stage == "plan":
                revoke(job_id, mode, control)
            return result

    with patch("contentflow.worker.LeaseHeartbeat", side_effect=lambda **kw: ControlledHeartbeat(control, **kw)), \
         patch("contentflow.workflow_service.build_text_provider", return_value=LosingProvider()):
        assert application.worker.run_once()
    assert calls == ["plan"]
    with db.SessionLocal() as session:
        run = session.get(WorkflowRun, run_id)
        assert run.status == "running"
        assert run.current_stage == "planning"
        assert session.scalar(select(func.count(ContentItem.id)).where(ContentItem.run_id == run_id)) == 0
        job = session.get(Job, job_id)
        assert job.status == ("manual_review" if mode == "status" else "running")
        if mode == "owner":
            assert job.locked_by == "replacement-worker"
        if mode == "attempt":
            assert job.attempts == 2


@pytest.mark.parametrize("write", ["orm", "bulk", "progress", "flushed_then_lost"])
def test_stale_worker_cannot_commit_domain_writes(application, write):
    campaign_id = new_campaign(application)
    control = SimpleNamespace(lost=False)
    with db.SessionLocal() as session:
        job = Job(workspace_id=application.workspace_id, job_type="test.fence", payload_json={},
            idempotency_key=str(uuid.uuid4()), run_at=datetime.now(timezone.utc))
        session.add(job)
        session.commit()

    def handler(session, _payload, _settings):
        if write == "flushed_then_lost":
            session.get(Campaign, campaign_id).name = "STALE"
            session.flush()
        control.lost = True
        if write == "orm":
            session.get(Campaign, campaign_id).name = "STALE"
        elif write == "bulk":
            session.execute(update(Campaign).where(Campaign.id == campaign_id).values(name="STALE"))
        elif write == "progress":
            with Session(session.get_bind()) as progress:
                progress.execute(update(Campaign).where(Campaign.id == campaign_id).values(name="STALE"))
                progress.commit()
        session.commit()
        return {}

    with Worker(settings=application.settings, session_factory=db.SessionLocal,
            worker_id="fence-writer", handlers={"test.fence": handler}) as worker, \
         patch("contentflow.worker.LeaseHeartbeat", side_effect=lambda **kw: ControlledHeartbeat(control, **kw)):
        assert worker.run_once()
    with db.SessionLocal() as session:
        assert session.get(Campaign, campaign_id).name == "TEST-ONLY fencing"


def running_fence(application, control):
    with db.SessionLocal() as session:
        job = Job(workspace_id=application.workspace_id, job_type="test.fence", payload_json={},
            idempotency_key=str(uuid.uuid4()), status="running", locked_by="fence-worker",
            attempts=1, locked_at=datetime.now(timezone.utc))
        session.add(job)
        session.commit()
        return job, ExecutionFence(bind=session.get_bind(), job_id=job.id,
            workspace_id=job.workspace_id, worker_id=job.locked_by, attempt=1,
            lease_token=job.lease_token,
            lease_seconds=60, heartbeat_lost=lambda: control.lost)


@pytest.mark.parametrize("kind", ["text", "embedding", "media", "poll", "search", "download"])
@pytest.mark.parametrize("timing", ["before", "success_late", "error_late"])
def test_provider_boundaries_preserve_late_evidence_without_next_call(application, kind, timing):
    control = SimpleNamespace(lost=False)
    job, fence = running_fence(application, control)
    calls = []

    def invoke(value):
        calls.append(kind)
        control.lost = True
        if timing == "error_late":
            raise TimeoutError("TEST-ONLY uncertain response")
        return value

    provider = SimpleNamespace(provider_name="openai-compatible", model_name="test-only", dimensions=2,
        last_call_metadata={"provider_request_id": "test-late", "usage_source": "provider_reported",
            "input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
        complete_json=lambda *a, **kw: invoke({"ok": True}),
        encode_many=lambda *a, **kw: invoke([[0.1, 0.2]]),
        generate=lambda **kw: invoke(MediaGeneration(status="ready", content=b"test-only", mime_type="image/png", filename="test.png")),
        poll=lambda *a: invoke(MediaGeneration(status="processing", external_task_id="test-task")),
        search=lambda **kw: invoke([]))
    with db.SessionLocal() as session:
        common = dict(ledger=ProviderInvocationLedger(session.get_bind()),
            workspace_id=application.workspace_id, entity_id="test-only")
        if kind == "text":
            recorder = AIProvenanceRecorder(provider, embedding_provider="hash", embedding_model="test-only",
                ledger_session=session, workspace_id=application.workspace_id, entity_type="workflow_run", entity_id="test-only")
            operation = partial(recorder.complete_json, "plan", {})
        elif kind == "embedding":
            wrapper = LedgeredEmbeddingProvider(provider, **common, entity_type="workflow_run",
                operation="embedding.knowledge_search", provider_name="test-only")
            operation = partial(wrapper.encode_many, ["test"])
        elif kind in {"media", "poll"}:
            wrapper = LedgeredMediaProvider(provider, **common, provider_name="test-only", model_name="test-only")
            operation = (partial(wrapper.generate, kind="image", prompt="test", metadata={}, idempotency_key="test")
                if kind == "media" else partial(wrapper.poll, "test-task"))
        elif kind == "search":
            wrapper = LedgeredSearchProvider(provider, **common)
            operation = partial(wrapper.search, query="test")
        else:
            wrapper = LedgeredMediaDownloader(**common, provider_name="test-only", model_name="test-only", operation="test.download")
            operation = partial(wrapper.download, request={}, invoke=lambda: invoke(b"test"), call_metadata=provider.last_call_metadata)
        with execution_scope(fence), provider_job_context(job):
            if timing == "before":
                control.lost = True
            with pytest.raises(TimeoutError if timing == "error_late" else JobLeaseLost):
                operation()
            with pytest.raises(JobLeaseLost):
                operation()
    with db.SessionLocal() as session:
        attempts = list(session.scalars(select(ProviderInvocationAttempt)))
        if timing == "before":
            assert calls == [] and attempts == []
        else:
            assert calls == [kind] and len(attempts) == 1
            assert attempts[0].status == ("late_succeeded" if timing == "success_late" else "outcome_unknown")
            assert attempts[0].provider_request_id == "test-late"
            assert attempts[0].total_tokens == 5
            if timing == "success_late":
                assert attempts[0].response_sha256


def test_heartbeat_cannot_revive_an_expired_lease(application):
    control = SimpleNamespace(lost=False)
    job, fence = running_fence(application, control)
    revoke(job.id, "expired", control)
    with db.SessionLocal() as session:
        assert not renew_job_lease(session, job_id=job.id, worker_id=fence.worker_id,
            attempt=1, lease_seconds=60, lease_token=fence.lease_token)
        session.commit()
    with pytest.raises(JobLeaseLost):
        fence.check()


def test_download_does_not_follow_redirect_after_losing_lease(application):
    import httpx
    control = SimpleNamespace(lost=False)
    _job, fence = running_fence(application, control)
    calls = []

    def transport(request):
        calls.append(str(request.url))
        control.lost = True
        return httpx.Response(302, headers={"Location": "https://assets.example/second"})

    with httpx.Client(transport=httpx.MockTransport(transport)) as client, execution_scope(fence):
        with pytest.raises(JobLeaseLost):
            download_generated_media(MediaGeneration(status="ready", download_url="https://assets.example/first"),
                max_bytes=1024, allowed_hosts=("assets.example",), client=client)
    assert calls == ["https://assets.example/first"]


def test_sqlite_simultaneous_claim_only_one_worker_wins(application):
    with db.SessionLocal() as session:
        job = Job(workspace_id=application.workspace_id, job_type="test.fence", payload_json={},
            idempotency_key=str(uuid.uuid4()), run_at=datetime.now(timezone.utc))
        session.add(job)
        session.commit()
        job_id = job.id
    barrier = Barrier(2)

    def claim(index):
        with db.SessionLocal() as session:
            original = session.scalar

            def select_together(*args, **kwargs):
                candidate = original(*args, **kwargs)
                assert candidate is not None and candidate.id == job_id
                barrier.wait(timeout=5)
                return candidate

            with patch.object(session, "scalar", side_effect=select_together):
                result = claim_next_job(session, worker_id=f"worker-{index}", lease_seconds=60,
                    manual_review_job_types=())
            session.commit()
            return result.id if result else None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, range(2)))
    assert results.count(job_id) == 1 and results.count(None) == 1
    with db.SessionLocal() as session:
        assert session.get(Job, job_id).attempts == 1


@pytest.mark.parametrize("lose_after", [1, 2, 3, 4])
def test_actual_wechat_worker_stops_between_requests_and_rejects_late_result(application, lose_after):
    import httpx
    fixture = application.publication_fixture
    control = SimpleNamespace(lost=False)
    calls = []
    responses = [dict(access_token="TEST-token"), dict(media_id="TEST-cover"),
        dict(media_id="TEST-draft"), dict(publish_id="TEST-publish")]

    def transport(request):
        calls.append(request.url.path)
        if len(calls) == lose_after:
            control.lost = True
        return httpx.Response(200, json=responses[len(calls) - 1])

    with db.SessionLocal() as session:
        publish_job = session.get(PublishJob, fixture["publish_job_id"])
        publish_job.status = "queued"
        job = Job(workspace_id=application.workspace_id, job_type="publish.dispatch",
            payload_json={"publish_job_id": publish_job.id}, idempotency_key=str(uuid.uuid4()),
            run_at=datetime.now(timezone.utc))
        session.add(job)
        session.commit()
        job_id = job.id
        channel = session.get(ChannelConnection, fixture["channel_id"])
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        connector = WechatConnector(channel=channel, credentials={"app_id": "TEST", "app_secret": "TEST"},
            storage=build_object_storage(application.settings), client=client)
        with patch("contentflow.worker.build_connector", return_value=connector), \
             patch("contentflow.worker.LeaseHeartbeat", side_effect=lambda **kw: ControlledHeartbeat(control, **kw)):
            assert application.worker.run_once()
    assert len(calls) == lose_after
    with db.SessionLocal() as session:
        assert session.get(Job, job_id).status == "running"
        result = session.get(PublishJob, fixture["publish_job_id"])
        assert result.status == "publishing"  # Requires reconciliation; never safe automatic replay.
        assert result.external_id is None


def test_local_storage_does_not_write_or_delete_after_loss(application):
    from io import BytesIO
    control = SimpleNamespace(lost=False)
    _job, fence = running_fence(application, control)
    storage = build_object_storage(application.settings)
    with db.SessionLocal() as session:
        asset = session.scalar(select(Asset))
        uri = asset.storage_uri
    original = storage.read(uri)
    with execution_scope(fence):
        control.lost = True
        with pytest.raises(JobLeaseLost):
            storage.put(workspace_id=application.workspace_id, category="assets", filename="stale.txt", stream=BytesIO(b"stale"))
        with pytest.raises(JobLeaseLost):
            storage.delete(uri)
    assert storage.read(uri) == original
