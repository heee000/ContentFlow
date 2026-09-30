"""SQLite acceptance failures: no provider, worker or real database calls."""

from unittest.mock import patch
import uuid

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from contentflow import db
from contentflow.entities import AuditLog, Job, PublishJob
from contentflow.job_queue import enqueue_job
import test_publication_confirmation as publication

application = publication.application


@pytest.mark.parametrize("stage", ["enqueue_job", "record_audit"])
def test_failed_publication_acceptance_leaves_no_orphan(application, stage):
    intent = publication.new_intent(application)
    intent["preview_token"] = publication.preview(application, intent)["preview_token"]
    before = publication.counts()
    # The fixture already owns the lifespan. Do not reinitialize its DB engine.
    client = TestClient(application.client.app, raise_server_exceptions=False)
    try:
        with patch(f"contentflow.routers.publishing.{stage}", side_effect=RuntimeError("TEST-ONLY acceptance failure")):
            result = client.post("/api/v1/publishing/jobs", headers=application.headers, json=intent)
        assert result.status_code == 500
        assert publication.counts() == before
        retry = client.post("/api/v1/publishing/jobs", headers=application.headers, json=intent)
        assert retry.status_code == 202, retry.text
    finally:
        client.close()
    with db.SessionLocal() as session:
        identifier = retry.json()["id"]
        assert session.scalar(select(func.count(Job.id)).where(Job.idempotency_key == f"publish.dispatch:{identifier}")) == 1
        assert session.scalar(select(func.count(AuditLog.id)).where(AuditLog.entity_id == identifier)) == 1


def test_legacy_orphan_receipt_is_not_returned_as_accepted(application):
    intent = publication.new_intent(application)
    intent["preview_token"] = publication.preview(application, intent)["preview_token"]
    response = application.client.post("/api/v1/publishing/jobs", headers=application.headers, json=intent)
    assert response.status_code == 202
    identifier = response.json()["id"]
    with db.SessionLocal() as session:
        job = session.scalar(select(Job).where(Job.idempotency_key == f"publish.dispatch:{identifier}"))
        session.delete(job)
        session.commit()
    replay = application.client.post("/api/v1/publishing/jobs", headers=application.headers, json=intent)
    assert replay.status_code == 409
    assert replay.json()["error"]["code"] == "publish_receipt_incomplete"
    with db.SessionLocal() as session:
        assert session.get(PublishJob, identifier).status == "queued"
        assert session.scalar(select(func.count(Job.id)).where(Job.idempotency_key == f"publish.dispatch:{identifier}")) == 0


def seed_jobs():
    with db.SessionLocal() as session:
        existing = enqueue_job(session, job_type="test.original", payload={"value": 1}, workspace_id=None, idempotency_key="TEST-ONLY-original")
        marker = enqueue_job(session, job_type="test.marker", payload={}, workspace_id=None, idempotency_key="TEST-ONLY-marker")
        session.commit()
        return existing.id, marker.id


def miss_first_job_lookup(session):
    original = session.scalar
    calls = 0

    def lookup(*args, **kwargs):
        nonlocal calls
        calls += 1
        return None if calls == 1 else original(*args, **kwargs)

    return patch.object(session, "scalar", side_effect=lookup)


def test_queue_conflict_preserves_callers_flushed_business_change(application):
    existing_id, marker_id = seed_jobs()
    with db.SessionLocal() as session:
        session.get(Job, marker_id).status = "failed"
        session.flush()
        with miss_first_job_lookup(session):
            result = enqueue_job(session, job_type="test.original", payload={"value": 1}, workspace_id=None, idempotency_key="TEST-ONLY-original")
        assert result.id == existing_id
        session.commit()
    with db.SessionLocal() as session:
        assert session.get(Job, marker_id).status == "failed"
        assert session.scalar(select(func.count(Job.id))) == 2


@pytest.mark.parametrize("miss", [False, True])
@pytest.mark.parametrize("change", [{"payload": {"value": 2}}, {"job_type": "test.other"}, {"workspace_id": "different-workspace"}])
def test_queue_identity_mismatch_cannot_return_another_job(application, miss, change):
    seed_jobs()
    arguments = {"job_type": "test.original", "payload": {"value": 1}, "workspace_id": None, "idempotency_key": "TEST-ONLY-original", **change}
    with db.SessionLocal() as session:
        if miss:
            with miss_first_job_lookup(session), pytest.raises((RuntimeError, IntegrityError)):
                enqueue_job(session, **arguments)
        else:
            with pytest.raises(RuntimeError):
                enqueue_job(session, **arguments)
        session.rollback()


def test_queue_non_key_integrity_error_is_not_swallowed_or_rolled_back_inside(application):
    with db.SessionLocal() as session:
        with patch.object(session, "rollback", side_effect=AssertionError("Only caller owns rollback")):
            with pytest.raises(IntegrityError):
                enqueue_job(session, job_type="test.bad_fk", payload={}, workspace_id=str(uuid.uuid4()), idempotency_key="TEST-ONLY-invalid-fk")
        session.rollback()


def test_new_queue_insert_is_rolled_back_with_callers_transaction(application):
    with db.SessionLocal() as session:
        identifier = enqueue_job(session, job_type="test.rollback", payload={}, workspace_id=None, idempotency_key="TEST-ONLY-rollback").id
        session.rollback()
    with db.SessionLocal() as session:
        assert session.get(Job, identifier) is None
