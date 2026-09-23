"""Slow worker storage, real heartbeats and durable uncertain-write evidence."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from io import BytesIO
import os
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from contentflow import db
from contentflow.entities import Asset, Job, StorageObjectAllocation, WorkspaceStorageUsage
from contentflow.object_storage import build_object_storage
from contentflow.knowledge_service import local_path_from_uri
from contentflow.storage_ledger import LedgeredObjectStorage, StorageQuotaExceeded
from contentflow.worker import Worker
import test_worker_v2 as worker_tests


@pytest.fixture
def application():
    fixture = worker_tests.WorkerIntegrationTest()
    fixture.setUp()
    try:
        yield fixture
    finally:
        fixture.tearDown()


def queued_asset(application):
    fixture = application._create_publish_fixture(status="cancelled")
    with db.SessionLocal() as session:
        asset = session.scalar(select(Asset).where(Asset.content_item_id == fixture["content_id"]))
        asset.status = "queued"
        asset.provider = "mock"
        asset.storage_uri = None
        job = Job(workspace_id=application.workspace_id, job_type="asset.generate",
            payload_json={"asset_id": asset.id}, run_at=datetime.now(timezone.utc),
            idempotency_key=f"test-worker-storage:{asset.id}")
        session.add(job)
        session.commit()
        return asset.id, job.id


class StorageProbe:
    def __init__(self, delegate, after_put):
        self.delegate, self.after_put = delegate, after_put
        self.stored = None

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    def put(self, **kwargs):
        self.stored = self.delegate.put(**kwargs)
        self.after_put()
        return self.stored


def test_actual_asset_slow_upload_keeps_renewing_and_commits(application):
    asset_id, job_id = queued_asset(application)
    observed = {}

    def slow_put():
        with db.SessionLocal() as session:
            job = session.get(Job, job_id)
            observed["before"] = job.locked_at
            allocation = session.scalar(select(StorageObjectAllocation).where(
                StorageObjectAllocation.owner_id == asset_id))
            observed["during_status"] = allocation.status if allocation else None
        Event().wait(4.2)
        with db.SessionLocal() as session:
            observed["after"] = session.get(Job, job_id).locked_at

    probe = StorageProbe(build_object_storage(application.settings), slow_put)
    with patch("contentflow.storage_ledger.build_object_storage", return_value=probe):
        with Worker(settings=application.settings.model_copy(update={"worker_lease_seconds": 3}),
                session_factory=db.SessionLocal, worker_id="test-slow-upload") as worker:
            assert worker.run_once()
    with db.SessionLocal() as session:
        assert session.get(Job, job_id).status == "succeeded"
        asset = session.get(Asset, asset_id)
        allocation = session.scalar(select(StorageObjectAllocation).where(
            StorageObjectAllocation.owner_id == asset_id))
        assert asset.status == "ready" and asset.storage_uri == probe.stored.uri
        assert allocation.status == "active" and allocation.storage_uri == asset.storage_uri
        assert allocation.write_job_id == job_id
        usage = session.get(WorkspaceStorageUsage, application.workspace_id)
        assert (usage.used_objects, usage.used_bytes) == (1, asset.size_bytes)
        assert (usage.reserved_objects, usage.reserved_bytes) == (0, 0)
    assert observed["during_status"] == "staging"
    assert observed["after"] > observed["before"]


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


def test_lost_worker_retains_charged_staging_without_attaching_or_deleting(application):
    asset_id, job_id = queued_asset(application)
    control = SimpleNamespace(lost=False)
    probe = StorageProbe(build_object_storage(application.settings), lambda: setattr(control, "lost", True))
    with patch("contentflow.storage_ledger.build_object_storage", return_value=probe), \
         patch("contentflow.worker.LeaseHeartbeat", side_effect=lambda **kw: ControlledHeartbeat(control, **kw)):
        assert application.worker.run_once()
    with db.SessionLocal() as session:
        assert session.get(Job, job_id).status == "running"
        asset = session.get(Asset, asset_id)
        assert asset.status == "queued" and asset.storage_uri is None
        allocation = session.scalar(select(StorageObjectAllocation).where(
            StorageObjectAllocation.owner_id == asset_id))
        assert allocation is not None and allocation.status == "staging"
        assert allocation.storage_uri == probe.stored.uri and allocation.checksum == probe.stored.checksum
        assert allocation.write_job_id == job_id and allocation.write_lease_token
        usage = session.get(WorkspaceStorageUsage, application.workspace_id)
        assert (usage.used_objects, usage.used_bytes) == (1, probe.stored.size_bytes)
    assert probe.delegate.read(probe.stored.uri)
    response = application.client.get("/api/v1/admin/storage/usage", headers=application.headers)
    assert response.status_code == 200 and response.json()["staging_objects"] == 1
    rows = application.client.get("/api/v1/admin/storage/objects?attention_only=true", headers=application.headers).json()
    assert len(rows) == 1 and rows[0]["write_job_id"] == job_id
    assert "write_lease_token" not in rows[0]


def test_domain_failure_keeps_durable_write_record_and_original_business_state(application):
    asset_id, job_id = queued_asset(application)
    probe = StorageProbe(build_object_storage(application.settings), lambda: None)
    from contentflow.worker import record_audit

    def failing_audit(session, **kwargs):
        if kwargs.get("action") == "asset.generate":
            raise RuntimeError("TEST-ONLY domain audit failure")
        return record_audit(session, **kwargs)

    with patch("contentflow.storage_ledger.build_object_storage", return_value=probe), \
         patch("contentflow.worker.record_audit", side_effect=failing_audit):
        assert application.worker.run_once()
    with db.SessionLocal() as session:
        assert session.get(Asset, asset_id).storage_uri is None
        assert session.scalar(select(func.count(StorageObjectAllocation.id)).where(
            StorageObjectAllocation.owner_id == asset_id, StorageObjectAllocation.status == "staging")) == 1
        usage = session.get(WorkspaceStorageUsage, application.workspace_id)
        assert usage.used_bytes == probe.stored.size_bytes and usage.used_objects == 1
    assert probe.delegate.read(probe.stored.uri)


def test_staged_write_counts_against_concurrent_quota_before_upload_returns(application):
    asset_id, job_id = queued_asset(application)
    settings = application.settings.model_copy(update={"workspace_storage_max_objects": 1})

    def competing_write():
        # A separate API/process must not inherit the worker execution ContextVar.
        with db.SessionLocal() as session:
            with pytest.raises(StorageQuotaExceeded):
                LedgeredObjectStorage(session=session, settings=settings,
                    owner_type="test", owner_id="competing-upload").put(
                    workspace_id=application.workspace_id, category="test", filename="other.txt",
                    stream=BytesIO(b"other"))

    def while_uploading():
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(competing_write).result(timeout=3)

    probe = StorageProbe(build_object_storage(settings), while_uploading)
    with patch("contentflow.storage_ledger.build_object_storage", return_value=probe):
        with Worker(settings=settings, session_factory=db.SessionLocal, worker_id="test-quota") as worker:
            assert worker.run_once()
    with db.SessionLocal() as session:
        assert session.get(Job, job_id).status == "succeeded"
        assert session.get(WorkspaceStorageUsage, application.workspace_id).used_objects == 1


def test_io_after_domain_flush_fails_before_touching_storage(application):
    asset_id, job_id = queued_asset(application)
    probe = StorageProbe(build_object_storage(application.settings), lambda: None)

    def bad_handler(session, payload, settings):
        session.get(Asset, asset_id).status = "generating"
        session.flush()
        LedgeredObjectStorage(session=session, settings=settings, owner_type="asset", owner_id=asset_id).put(
            workspace_id=application.workspace_id, category="test", filename="forbidden.txt", stream=BytesIO(b"test"))
        pytest.fail("I/O under a worker write lock must not be allowed")

    with patch("contentflow.storage_ledger.build_object_storage", return_value=probe):
        with Worker(settings=application.settings, session_factory=db.SessionLocal,
                worker_id="test-unsafe-handler", handlers={"asset.generate": bad_handler}) as worker:
            assert worker.run_once()
    assert probe.stored is None
    with db.SessionLocal() as session:
        assert session.get(Job, job_id).status != "succeeded"
        assert session.scalar(select(func.count(StorageObjectAllocation.id))) == 0


def test_slow_reconciliation_expires_reservations_but_never_deletes_staging(application):
    asset_id, _ = queued_asset(application)
    control = SimpleNamespace(lost=False)
    probe = StorageProbe(build_object_storage(application.settings), lambda: setattr(control, "lost", True))
    with patch("contentflow.storage_ledger.build_object_storage", return_value=probe), \
         patch("contentflow.worker.LeaseHeartbeat", side_effect=lambda **kw: ControlledHeartbeat(control, **kw)):
        assert application.worker.run_once()
    old = (datetime.now(timezone.utc) - timedelta(days=3)).timestamp()
    os.utime(local_path_from_uri(probe.stored.uri), (old, old))
    with db.SessionLocal() as session:
        session.add(StorageObjectAllocation(workspace_id=application.workspace_id, owner_type="test",
            owner_id="expired", category="test", filename="expired.txt", size_bytes=2,
            status="reserved", reserved_until=datetime.now(timezone.utc) - timedelta(minutes=1)))
        usage = session.get(WorkspaceStorageUsage, application.workspace_id)
        usage.reserved_bytes = 2
        usage.reserved_objects = 1
        job = Job(workspace_id=application.workspace_id, job_type="storage.reconcile",
            payload_json={"workspace_id": application.workspace_id, "run_id": "test-reconcile-staging",
                "delete_orphans": True}, run_at=datetime.now(timezone.utc), idempotency_key="test-reconcile-staging")
        session.add(job)
        session.commit()
        job_id = job.id

    class SlowListing:
        def __getattr__(self, name):
            return getattr(probe.delegate, name)

        def list_workspace_objects(self, *args, **kwargs):
            Event().wait(4.2)
            return probe.delegate.list_workspace_objects(*args, **kwargs)

    with patch("contentflow.storage_ledger.build_object_storage", return_value=SlowListing()):
        with Worker(settings=application.settings.model_copy(update={"worker_lease_seconds": 3}),
                session_factory=db.SessionLocal, worker_id="test-slow-reconcile") as worker:
            assert worker.run_once()
    with db.SessionLocal() as session:
        assert session.get(Job, job_id).status == "succeeded"
        allocation = session.scalar(select(StorageObjectAllocation).where(StorageObjectAllocation.owner_id == asset_id))
        assert allocation.status == "staging"
        usage = session.get(WorkspaceStorageUsage, application.workspace_id)
        assert usage.used_bytes == probe.stored.size_bytes and usage.reserved_bytes == 0
    assert probe.delegate.read(probe.stored.uri)


def unfinished_write(application):
    asset_id, job_id = queued_asset(application)
    control = SimpleNamespace(lost=False)
    probe = StorageProbe(build_object_storage(application.settings), lambda: setattr(control, "lost", True))
    with patch("contentflow.storage_ledger.build_object_storage", return_value=probe), \
         patch("contentflow.worker.LeaseHeartbeat", side_effect=lambda **kw: ControlledHeartbeat(control, **kw)):
        assert application.worker.run_once()
    with db.SessionLocal() as session:
        allocation_id = session.scalar(select(StorageObjectAllocation.id).where(StorageObjectAllocation.owner_id == asset_id))
    return asset_id, job_id, allocation_id, probe


def test_recovery_requires_terminal_source_cooldown_confirmation_and_no_reference(application):
    asset_id, job_id, allocation_id, probe = unfinished_write(application)
    path = f"/api/v1/admin/storage/objects/{allocation_id}/discard-staged"
    payload = {"confirmed_no_inflight_write": True, "note": "TEST-ONLY confirmed old process and upload stopped"}
    for invalid in (False, 1, "true", None):
        response = application.client.post(path, headers=application.headers, json={**payload, "confirmed_no_inflight_write": invalid})
        assert response.status_code == 422
    assert application.client.post(path, headers=application.headers, json=payload).status_code == 409
    with db.SessionLocal() as session:
        session.get(Job, job_id).status = "manual_review"
        session.commit()
    assert application.client.post(path, headers=application.headers, json=payload).status_code == 409
    with db.SessionLocal() as session:
        session.get(Job, job_id).updated_at = datetime.now(timezone.utc) - timedelta(days=3)
        session.get(Asset, asset_id).storage_uri = probe.stored.uri
        session.commit()
    assert application.client.post(path, headers=application.headers, json=payload).status_code == 409
    with db.SessionLocal() as session:
        session.get(Asset, asset_id).storage_uri = None
        session.commit()
    accepted = application.client.post(path, headers=application.headers, json=payload)
    assert accepted.status_code == 202, accepted.text
    replay = application.client.post(path, headers=application.headers, json=payload)
    assert replay.status_code == 202 and replay.json()["id"] == accepted.json()["id"]
    with db.SessionLocal() as session:
        assert session.get(StorageObjectAllocation, allocation_id).status == "delete_pending"
        assert session.get(WorkspaceStorageUsage, application.workspace_id).used_objects == 1
        assert session.get(Job, job_id).status == "manual_review"
    assert application.worker.run_once()
    with db.SessionLocal() as session:
        assert session.get(StorageObjectAllocation, allocation_id).status == "deleted"
        assert session.get(WorkspaceStorageUsage, application.workspace_id).used_objects == 0
        assert session.get(Job, job_id).status == "manual_review"
        assert session.get(Asset, asset_id).storage_uri is None
    with pytest.raises(FileNotFoundError):
        probe.delegate.read(probe.stored.uri)


def test_another_workspace_cannot_see_or_discard_staging(application):
    _, _, allocation_id, _ = unfinished_write(application)
    other = application.client.post("/api/v1/auth/register", json={"email": "other-staging@example.com",
        "password": "TEST-ONLY-long-password", "display_name": "Other", "workspace_name": "Other"})
    assert other.status_code == 201, other.text
    response = application.client.post(f"/api/v1/admin/storage/objects/{allocation_id}/discard-staged",
        headers={"Authorization": f"Bearer {other.json()['access_token']}"},
        json={"confirmed_no_inflight_write": True, "note": "TEST-ONLY cannot discard another tenant"})
    assert response.status_code == 404
