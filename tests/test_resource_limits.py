"""TEST-ONLY provider requests, bounded bodies, durable admission and batching."""

import io
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import select, func
from pydantic import ValidationError

from contentflow.providers import OpenAICompatibleProvider
from contentflow.embeddings import OpenAICompatibleEmbeddingProvider
from contentflow.provider_resources import (ProviderResourceLimits, ProviderResourceLimitError,
    provider_resource_usage, resource_limit_receipt)
from contentflow.provider_invocations import ProviderInvocationLedger
from contentflow.ai_provenance import AIProvenanceRecorder
from contentflow.entities import (ProviderInvocationAttempt, KnowledgeDocument, KnowledgeChunk,
    Workspace, User, Membership, Asset, Job, JobManualReview)
from contentflow.knowledge_service import index_document
from contentflow.schemas import CampaignCreate, CampaignUpdate
from contentflow import db
from contentflow.media_providers import MediaGeneration
from contentflow.worker import Worker
import test_provider_invocations as ledger_tests
import test_worker_storage_transactions as storage_tests


class ModelReply(io.BytesIO):
    headers = {}


def model_reply():
    return ModelReply(json.dumps({"choices": [{"message": {"content": '{"ok":true}'}}]}).encode())


def test_model_wire_request_always_limits_output_tokens():
    provider = OpenAICompatibleProvider("https://test.invalid/v1", "TEST-ONLY", "test-model")
    with patch("contentflow.providers.open_model_request", return_value=model_reply()) as send:
        assert provider.complete_json("plan", {"TEST-ONLY": True}) == {"ok": True}
    body = json.loads(send.call_args.args[0].data)
    assert body.get("max_tokens") == 8192


def test_oversized_model_input_never_starts_http_request():
    provider = OpenAICompatibleProvider("https://test.invalid/v1", "TEST-ONLY", "test-model")
    with patch("contentflow.providers.open_model_request", return_value=model_reply()) as send:
        with pytest.raises(RuntimeError):
            provider.complete_json("plan", {"input": "x" * (1024 * 1024)})
        send.assert_not_called()


def test_model_response_is_read_with_a_size_bound():
    class Reply(ModelReply):
        def read(self, size=-1):
            assert 0 < size <= 4 * 1024 * 1024 + 1, "unbounded response read"
            return super().read(size)
    provider = OpenAICompatibleProvider("https://test.invalid/v1", "TEST-ONLY", "test-model")
    reply = Reply(model_reply().getvalue())
    with patch("contentflow.providers.open_model_request", return_value=reply):
        assert provider.complete_json("plan", {}) == {"ok": True}


@pytest.mark.parametrize("indices", [[0, 0], [-1, 1], [False, True], [0, 2]])
def test_embedding_indices_must_be_a_strict_permutation(indices):
    def reply(request):
        return httpx.Response(200, json={"data": [{"index": index, "embedding": [1.0, 0.0]}
            for index in indices]})
    with httpx.Client(transport=httpx.MockTransport(reply)) as client:
        provider = OpenAICompatibleEmbeddingProvider(api_base="https://test.invalid/v1",
            api_key="TEST-ONLY", model="test-embedding", dimensions=2, client=client)
        with pytest.raises(RuntimeError):
            provider.encode_many(["TEST-ONLY first", "TEST-ONLY second"])


@pytest.fixture
def ledger_fixture():
    fixture = ledger_tests.ProviderInvocationLedgerTest()
    fixture.setUp()
    try:
        yield fixture
    finally:
        fixture.tearDown()


def start(ledger, workspace_id, ordinal=1, size=100):
    return ledger.start(workspace_id=workspace_id, job_id=None, entity_type="TEST-ONLY",
        entity_id="resource-test", provider_kind="text", provider_name="TEST-ONLY",
        model_name="test-model", operation="text.plan", ordinal=ordinal,
        request_sha256=f"{ordinal:064x}", request_bytes=size, idempotency_key_sent=False)


def test_unknown_attempt_keeps_daily_budget_and_stops_before_provider_call(ledger_fixture):
    provider = ledger_tests._LedgerAwareProvider(ledger_fixture.Session)
    provider.resource_limits = ProviderResourceLimits(daily_calls=1)
    with ledger_fixture.Session() as session:
        recorder = AIProvenanceRecorder(provider, embedding_provider="not-used", embedding_model="not-used",
            ledger_session=session, workspace_id=ledger_fixture.workspace_id, entity_type="TEST-ONLY", entity_id="run")
        with pytest.raises(RuntimeError, match="provider-secret-response-body"):
            recorder.complete_json("plan", {"TEST-ONLY": True})
        provider.fail = False
        with patch.object(provider, "complete_json", wraps=provider.complete_json) as send:
            with pytest.raises(ProviderResourceLimitError) as captured:
                recorder.complete_json("plan", {"TEST-ONLY": True})
            send.assert_not_called()
        assert captured.value.code == "provider_daily_calls"
    with ledger_fixture.Session() as session:
        attempts = list(session.scalars(select(ProviderInvocationAttempt)))
        assert len(attempts) == 1 and attempts[0].status == "outcome_unknown"
        assert provider_resource_usage(session, ledger_fixture.workspace_id)["calls"] == 1


@pytest.mark.parametrize("dimension", ["calls", "bytes", "concurrent"])
def test_concurrent_ledger_admission_cannot_overdraw_last_allowance(ledger_fixture, dimension):
    limits = ProviderResourceLimits(daily_calls=1 if dimension == "calls" else 10,
        daily_input_bytes=100 if dimension == "bytes" else 1000,
        concurrent_requests=1 if dimension == "concurrent" else 4)
    barrier = Barrier(2)
    def enter(index):
        ledger = ProviderInvocationLedger(ledger_fixture.engine, limits=limits)
        barrier.wait(timeout=5)
        try:
            return start(ledger, ledger_fixture.workspace_id, index)
        except ProviderResourceLimitError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(enter, [1, 2]))
    assert len([result for result in results if not isinstance(result, str)]) == 1
    with ledger_fixture.Session() as session:
        usage = provider_resource_usage(session, ledger_fixture.workspace_id)
        assert (usage["calls"], usage["input_bytes"], usage["active_requests"]) == (1, 100, 1)


def test_finishing_request_releases_concurrency_but_not_daily_count(ledger_fixture):
    ledger = ProviderInvocationLedger(ledger_fixture.engine,
        limits=ProviderResourceLimits(daily_calls=3, concurrent_requests=1))
    first = start(ledger, ledger_fixture.workspace_id)
    with pytest.raises(ProviderResourceLimitError):
        start(ledger, ledger_fixture.workspace_id, 2)
    ledger.finish(first, status="succeeded", call_metadata={})
    second = start(ledger, ledger_fixture.workspace_id, 2)
    ledger.finish(second, status="outcome_unknown", call_metadata={})
    with ledger_fixture.Session() as session:
        usage = provider_resource_usage(session, ledger_fixture.workspace_id)
        assert (usage["calls"], usage["input_bytes"], usage["active_requests"]) == (2, 200, 0)


def test_audit_failure_rolls_back_admission_and_preserves_workspace_date(ledger_fixture):
    ledger = ProviderInvocationLedger(ledger_fixture.engine)
    with ledger_fixture.Session() as session:
        old_date = session.get(Workspace, ledger_fixture.workspace_id).updated_at
    with patch("contentflow.provider_invocations.record_audit", side_effect=RuntimeError("TEST-ONLY audit failure")):
        with pytest.raises(RuntimeError):
            start(ledger, ledger_fixture.workspace_id)
    with ledger_fixture.Session() as session:
        assert provider_resource_usage(session, ledger_fixture.workspace_id)["calls"] == 0
    start(ledger, ledger_fixture.workspace_id)
    with ledger_fixture.Session() as session:
        assert session.get(Workspace, ledger_fixture.workspace_id).updated_at == old_date


def test_daily_period_is_utc_and_cross_workspace_usage_is_isolated(ledger_fixture):
    ledger = ProviderInvocationLedger(ledger_fixture.engine)
    first = start(ledger, ledger_fixture.workspace_id)
    ledger.finish(first, status="outcome_unknown", call_metadata={})
    with ledger_fixture.Session() as session:
        other = Workspace(name="TEST-ONLY other", slug="other-resource-workspace",
            created_by=session.scalar(select(User.id)))
        session.add(other)
        old = session.get(ProviderInvocationAttempt, first.attempt_id)
        old.started_at = datetime.now(timezone.utc) - timedelta(days=2)
        session.commit()
        assert provider_resource_usage(session, ledger_fixture.workspace_id)["calls"] == 0
        assert provider_resource_usage(session, other.id)["calls"] == 0
        assert old.status == "outcome_unknown"


def test_input_preflight_does_not_reserve_ledger_allowance(ledger_fixture):
    provider = OpenAICompatibleProvider("https://test.invalid/v1", "TEST-ONLY", "model", max_request_bytes=1000)
    with ledger_fixture.Session() as session:
        recorder = AIProvenanceRecorder(provider, embedding_provider="not-used", embedding_model="not-used",
            ledger_session=session, workspace_id=ledger_fixture.workspace_id, entity_type="TEST-ONLY", entity_id="run")
        with patch("contentflow.providers.open_model_request") as send:
            with pytest.raises(ProviderResourceLimitError):
                recorder.complete_json("plan", {"TEST-ONLY": "x" * 2000})
            send.assert_not_called()
        assert provider_resource_usage(session, ledger_fixture.workspace_id)["calls"] == 0


def test_model_large_response_is_closed_and_keeps_unknown_ledger_charge(ledger_fixture):
    provider = OpenAICompatibleProvider("https://test.invalid/v1", "TEST-ONLY", "model", max_response_bytes=128)
    reply = ModelReply(b" " * 129)
    with ledger_fixture.Session() as session:
        recorder = AIProvenanceRecorder(provider, embedding_provider="not-used", embedding_model="not-used",
            ledger_session=session, workspace_id=ledger_fixture.workspace_id, entity_type="TEST-ONLY", entity_id="run")
        with patch("contentflow.providers.open_model_request", return_value=reply):
            with pytest.raises(ProviderResourceLimitError) as captured:
                recorder.complete_json("plan", {})
        assert captured.value.code == "provider_response_too_large" and reply.closed
        assert session.scalar(select(ProviderInvocationAttempt.status)) == "outcome_unknown"
        assert provider_resource_usage(session, ledger_fixture.workspace_id)["calls"] == 1


def test_embedding_oversized_input_is_rejected_without_http_call():
    requests = []
    with httpx.Client(transport=httpx.MockTransport(lambda request: requests.append(request))) as client:
        provider = OpenAICompatibleEmbeddingProvider(api_base="https://test.invalid/v1", api_key="TEST-ONLY",
            model="model", dimensions=2, client=client, max_batch_size=2, max_text_chars=10)
        for texts in (["x"] * 3, ["x" * 11], [True], [""]):
            with pytest.raises(ProviderResourceLimitError):
                provider.encode_many(texts)
    assert requests == []


@pytest.mark.parametrize("value", [True, "1", None, float("nan"), float("inf"), 10 ** 400])
def test_embedding_rejects_invalid_vector_numbers(value):
    body = json.dumps({"data": [{"index": 0, "embedding": [value, 0]}]}).encode()
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body))) as client:
        provider = OpenAICompatibleEmbeddingProvider(api_base="https://test.invalid/v1", api_key="TEST-ONLY",
            model="model", dimensions=2, client=client)
        with pytest.raises(RuntimeError):
            provider.encode("TEST-ONLY")


def test_embedding_restores_valid_out_of_order_indices():
    body = {"data": [{"index": 1, "embedding": [0, 1]}, {"index": 0, "embedding": [1, 0]}]}
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body))) as client:
        provider = OpenAICompatibleEmbeddingProvider(api_base="https://test.invalid/v1", api_key="TEST-ONLY",
            model="model", dimensions=2, client=client)
        assert provider.encode_many(["TEST-ONLY first", "TEST-ONLY second"]) == [[1.0, 0.0], [0.0, 1.0]]


def test_embedding_rejects_compressed_response_without_reading():
    class Stream(httpx.SyncByteStream):
        consumed = False
        closed = False
        def __iter__(self):
            self.consumed = True
            yield b"TEST-ONLY"
        def close(self):
            self.closed = True
    stream = Stream()
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200,
            headers={"content-encoding": "gzip"}, stream=stream))) as client:
        provider = OpenAICompatibleEmbeddingProvider(api_base="https://test.invalid/v1", api_key="TEST-ONLY",
            model="model", dimensions=2, client=client)
        with pytest.raises(RuntimeError, match="compressed response"):
            provider.encode("TEST-ONLY")
    assert not stream.consumed and stream.closed


def test_embedding_stream_stops_reading_oversized_body_and_closes():
    class Stream(httpx.SyncByteStream):
        consumed = 0
        closed = False
        def __iter__(self):
            for _ in range(100):
                self.consumed += 1
                yield b"x" * (64 * 1024)
        def close(self):
            self.closed = True
    stream = Stream()
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=stream))) as client:
        provider = OpenAICompatibleEmbeddingProvider(api_base="https://test.invalid/v1", api_key="TEST-ONLY",
            model="model", dimensions=2, client=client, max_response_bytes=1024)
        with pytest.raises(ProviderResourceLimitError):
            provider.encode("TEST-ONLY")
    assert stream.consumed == 1 and stream.closed


def test_index_batches_and_preserves_existing_vectors_after_later_batch_failure(ledger_fixture):
    class Storage:
        def read(self, uri, *, max_bytes):
            return ("TEST-ONLY " + "x" * 895 + "\n\n").encode() * 5
    class Embedder:
        dimensions = 2
        model_name = "TEST-ONLY-model"
        def __init__(self, fail=False):
            self.batches, self.fail = [], fail
        def encode_many(self, texts):
            self.batches.append(len(texts))
            if self.fail and len(self.batches) == 2:
                raise RuntimeError("TEST-ONLY second batch failed")
            return [[1.0, 0.0] for _ in texts]
    with ledger_fixture.Session() as session:
        document = KnowledgeDocument(workspace_id=ledger_fixture.workspace_id, name="TEST-ONLY.txt",
            storage_uri="test://document", checksum="0" * 64)
        session.add(document)
        session.flush()
        embedder = Embedder()
        count = index_document(session, document, embedder=embedder, storage=Storage(), embedding_batch_size=2)
        assert count > 2 and max(embedder.batches) == 2
        session.commit()
        before = list(session.scalars(select(KnowledgeChunk.id).where(KnowledgeChunk.document_id == document.id)))
        with pytest.raises(RuntimeError, match="second batch"):
            index_document(session, document, embedder=Embedder(True), storage=Storage(), embedding_batch_size=2)
        session.rollback()
        after = list(session.scalars(select(KnowledgeChunk.id).where(KnowledgeChunk.document_id == document.id)))
        assert before == after
        no_calls = Embedder()
        with pytest.raises(ProviderResourceLimitError):
            index_document(session, document, embedder=no_calls, storage=Storage(), max_chunks=1)
        assert no_calls.batches == []


@pytest.mark.parametrize("field", ["must_include", "forbidden_phrases", "product_facts"])
def test_brief_list_caps_apply_to_create_and_update(field):
    base = dict(name="TEST-ONLY", product_name="TEST-ONLY", objective="TEST-ONLY objective",
        audience="TEST-ONLY audience", platforms=["wechat"])
    for value in (["x"] * 33, ["x" * 2049]):
        with pytest.raises(ValidationError):
            CampaignCreate(**base, **{field: value})
        with pytest.raises(ValidationError):
            CampaignUpdate(**{field: value})


def test_resource_errors_never_emit_arbitrary_exception_text():
    error = ProviderResourceLimitError("provider_daily_calls")
    error.args = ("TEST-ONLY-private-data",)
    assert "TEST-ONLY-private-data" not in resource_limit_receipt(error)
    error.code = ["private"]
    assert resource_limit_receipt(error) is None


application = storage_tests.application


def test_worker_media_quota_is_shared_and_blocks_before_remote_call(application):
    first_asset, first_job = storage_tests.queued_asset(application)
    second_asset, second_job = storage_tests.queued_asset(application)
    application.settings.image_provider = "http"
    application.settings.image_model = "TEST-ONLY-model"
    application.settings.media_api_base = "https://test.invalid/v1"
    application.settings.media_api_key = "TEST-ONLY"
    application.settings.workspace_provider_daily_calls = 1
    with db.SessionLocal() as session:
        for asset_id in (first_asset, second_asset):
            session.get(Asset, asset_id).provider = "http"
        session.commit()
    class Provider:
        def __init__(self):
            self.calls = 0
        def generate(self, **kwargs):
            self.calls += 1
            return MediaGeneration(status="processing", external_task_id="TEST-ONLY task")
    provider = Provider()
    with Worker(settings=application.settings, session_factory=db.SessionLocal) as worker, \
         patch("contentflow.worker.build_media_provider", return_value=provider):
        assert worker.run_once()
        assert worker.run_once()
    assert provider.calls == 1
    with db.SessionLocal() as session:
        assert session.get(Job, first_job).status == "succeeded"
        blocked = session.get(Job, second_job)
        assert blocked.status == "failed" and "provider_daily_calls" in blocked.last_error
        assert session.scalar(select(JobManualReview.id).where(JobManualReview.job_id == second_job)) is None
        assert session.scalar(select(func.count(ProviderInvocationAttempt.id))) == 1
    response = application.client.get("/api/v1/admin/provider-resources", headers=application.headers)
    assert response.status_code == 200, response.text
    assert response.json()["remaining_calls"] == 0 and response.json()["monetary_budget"] is False
    with db.SessionLocal() as session:
        session.scalar(select(Membership).where(Membership.workspace_id == application.workspace_id)).role = "viewer"
        session.commit()
    assert application.client.get("/api/v1/admin/provider-resources", headers=application.headers).status_code == 403
