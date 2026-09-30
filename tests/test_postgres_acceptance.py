"""Real PostgreSQL acceptance/queue transactions, in a disposable database."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import patch
import uuid

import pytest
from sqlalchemy import func, select, text

from contentflow.entities import AuditLog, Job, PublishJob
from contentflow.job_queue import enqueue_job
from contentflow.routers.publishing import lookup_publication, preview_publish, schedule_publish
from contentflow.schemas import PublishJobResponse, PublishPreviewRequest, PublishScheduleRequest
import test_postgres_integration as pg

postgres_harness = pg.postgres_harness
pytestmark = pytest.mark.skipif(not pg.TEST_DATABASE_URL, reason="Dedicated PostgreSQL test URL is required")


@pytest.mark.parametrize("stage", ["enqueue_job", "record_audit"])
def test_postgres_acceptance_failure_rolls_back_publication_and_queue(postgres_harness, stage):
    h = postgres_harness
    fixture = pg._create_publish_fixture(h, status="cancelled", external_id=None)
    principal = SimpleNamespace(workspace_id=fixture["workspace_id"], user_id=fixture["user_id"])
    with h.sessions() as session:
        seed = session.get(PublishJob, fixture["publish_job_id"])
        intent = dict(content_item_id=seed.content_item_id, channel_id=seed.channel_id,
            request_id=str(uuid.uuid4()), publish_now=True)
        proof = preview_publish(PublishPreviewRequest(**intent), principal, session, h.settings)
        session.commit()
    payload = PublishScheduleRequest(**intent, preview_token=proof["preview_token"])
    with h.sessions() as session:
        with patch(f"contentflow.routers.publishing.{stage}", side_effect=RuntimeError("TEST-ONLY acceptance failure")):
            with pytest.raises(RuntimeError, match="TEST-ONLY"):
                schedule_publish(payload, principal, session, h.settings)
        session.rollback()
    with h.sessions() as session:
        assert session.scalar(select(func.count(PublishJob.id)).where(PublishJob.workspace_id == principal.workspace_id)) == 1
        assert session.scalar(select(func.count(Job.id)).where(Job.workspace_id == principal.workspace_id)) == 0
        assert session.scalar(select(func.count(AuditLog.id)).where(AuditLog.workspace_id == principal.workspace_id, AuditLog.action == "publish.immediate")) == 0
        accepted = schedule_publish(payload, principal, session, h.settings)
        session.commit()
        receipt = PublishJobResponse.model_validate(accepted).model_dump(mode="json")
        assert datetime.fromisoformat(receipt["scheduled_at"]).utcoffset() == timedelta(0)
        assert receipt["request_id"] == intent["request_id"]
        assert lookup_publication(intent["request_id"], principal, session).id == accepted.id
        assert session.scalar(select(func.count(Job.id)).where(Job.idempotency_key == f"publish.dispatch:{accepted.id}")) == 1
        assert session.scalar(select(func.count(AuditLog.id)).where(AuditLog.entity_id == accepted.id)) == 1


def test_postgres_queue_unique_race_preserves_both_callers_changes(postgres_harness):
    h = postgres_harness
    suffix = str(uuid.uuid4())
    with h.sessions() as session:
        markers = [enqueue_job(session, job_type="test.marker", payload={"index": i}, workspace_id=None, idempotency_key=f"marker-{suffix}-{i}") for i in range(2)]
        session.commit()
        marker_ids = [m.id for m in markers]
    barrier = Barrier(2)

    def submit(index):
        with h.sessions() as session:
            session.execute(text("SET statement_timeout = '15s'"))
            session.get(Job, marker_ids[index]).status = "failed"
            session.flush()
            original = session.scalar
            first = True

            def simultaneous_lookup(*args, **kwargs):
                nonlocal first
                result = original(*args, **kwargs)
                if first:
                    first = False
                    assert result is None
                    barrier.wait(timeout=10)
                return result

            with patch.object(session, "scalar", side_effect=simultaneous_lookup):
                accepted = enqueue_job(session, job_type="test.shared", payload={"value": 1}, workspace_id=None, idempotency_key=f"shared-{suffix}")
            session.commit()
            return accepted.id

    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(submit, range(2)))
    assert receipts[0] == receipts[1]
    with h.sessions() as session:
        assert [session.get(Job, identifier).status for identifier in marker_ids] == ["failed", "failed"]
        assert session.scalar(select(func.count(Job.id)).where(Job.idempotency_key == f"shared-{suffix}")) == 1
