from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from contentflow import db
from contentflow.entities import (
    Job,
    StorageObjectAllocation,
    User,
    Workspace,
    WorkspaceStorageUsage,
)
from contentflow.object_storage import LocalObjectStorage
from contentflow.settings import Settings
from contentflow.storage_ledger import (
    LedgeredObjectStorage,
    StorageDeletionError,
    StorageLedgerUnverified,
    StorageQuotaExceeded,
    create_workspace_storage_usage,
    delete_storage_allocation,
    reconcile_workspace_storage,
    request_storage_deletion,
    schedule_due_storage_reconciliations,
)


@dataclass(frozen=True)
class LedgerHarness:
    engine: Engine
    sessions: sessionmaker[Session]
    settings: Settings
    storage: LocalObjectStorage
    workspace_id: str


@pytest.fixture
def ledger_harness(tmp_path: Path):
    database_path = tmp_path / "storage-ledger.db"
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    db.Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    storage_root = tmp_path / "storage"
    settings = Settings(
        environment="test",
        database_url=f"sqlite:///{database_path.as_posix()}",
        secret_key="storage-ledger-test-secret",
        storage_backend="local",
        local_storage_dir=storage_root,
        max_upload_bytes=8,
        workspace_storage_max_bytes=8,
        workspace_storage_max_objects=1,
        publish_evidence_max_bytes=8,
        publish_evidence_max_total_bytes=8,
        storage_orphan_grace_seconds=3600,
    )
    storage = LocalObjectStorage(storage_root, max_upload_bytes=8)
    with sessions() as session:
        user = User(
            email="storage-ledger@example.com",
            password_hash="not-used",
            display_name="Storage Ledger Owner",
        )
        session.add(user)
        session.flush()
        workspace = Workspace(
            name="Storage Ledger",
            slug="storage-ledger",
            created_by=user.id,
        )
        session.add(workspace)
        session.flush()
        create_workspace_storage_usage(session, workspace.id)
        session.commit()
        workspace_id = workspace.id
    yield LedgerHarness(
        engine=engine,
        sessions=sessions,
        settings=settings,
        storage=storage,
        workspace_id=workspace_id,
    )
    engine.dispose()


def _ledger(harness: LedgerHarness, session: Session, owner_id: str):
    return LedgeredObjectStorage(
        session=session,
        settings=harness.settings,
        owner_type="test_object",
        owner_id=owner_id,
        storage=harness.storage,
    )


def test_successful_write_activates_exact_quota_charge(
    ledger_harness: LedgerHarness,
):
    with ledger_harness.sessions() as session:
        stored = _ledger(ledger_harness, session, "owner-1").put(
            workspace_id=ledger_harness.workspace_id,
            category="tests",
            filename="payload.bin",
            stream=BytesIO(b"12345678"),
            content_type="application/octet-stream",
        )
        session.commit()

    with ledger_harness.sessions() as session:
        usage = session.get(WorkspaceStorageUsage, ledger_harness.workspace_id)
        allocation = session.scalar(select(StorageObjectAllocation))
        assert usage is not None
        assert allocation is not None
        assert (usage.used_bytes, usage.used_objects) == (8, 1)
        assert (usage.reserved_bytes, usage.reserved_objects) == (0, 0)
        assert usage.unverified_objects == 0
        assert allocation.status == "active"
        assert allocation.storage_uri == stored.uri
        assert allocation.id in stored.uri
        assert allocation.size_bytes == 8
        assert allocation.size_verified is True
    assert ledger_harness.storage.read(stored.uri) == b"12345678"


def test_ledger_preserves_the_complete_object_storage_protocol(
    ledger_harness: LedgerHarness,
):
    with ledger_harness.sessions() as session:
        storage = _ledger(ledger_harness, session, "owner-protocol")
        assert storage.workspace_uri_prefix(ledger_harness.workspace_id) == (
            ledger_harness.storage.workspace_uri_prefix(ledger_harness.workspace_id)
        )


def test_scheduler_queues_report_only_reconciliation_once_per_interval(
    ledger_harness: LedgerHarness,
):
    now = datetime.now(timezone.utc)
    settings = ledger_harness.settings.model_copy(
        update={
            "storage_reconcile_schedule_enabled": True,
            "storage_reconcile_interval_hours": 24,
        }
    )
    with ledger_harness.sessions() as session:
        usage = session.get(WorkspaceStorageUsage, ledger_harness.workspace_id)
        assert usage is not None
        usage.last_reconciled_at = now - timedelta(hours=25)
        session.commit()

    with ledger_harness.sessions() as session:
        assert (
            schedule_due_storage_reconciliations(
                session,
                settings=settings,
                now=now,
            )
            == 1
        )
        session.commit()
        job = session.scalar(
            select(Job).where(Job.job_type == "storage.reconcile")
        )
        assert job is not None
        first_job_id = job.id
        first_run_id = job.payload_json["run_id"]
        assert job.payload_json["trigger"] == "scheduled"
        assert job.payload_json["delete_orphans"] is False

    with ledger_harness.sessions() as session:
        assert (
            schedule_due_storage_reconciliations(
                session,
                settings=settings,
                now=now + timedelta(hours=1),
            )
            == 0
        )
        job = session.get(Job, first_job_id)
        assert job is not None
        job.status = "failed"
        session.commit()

    with ledger_harness.sessions() as session:
        assert (
            schedule_due_storage_reconciliations(
                session,
                settings=settings,
                now=now + timedelta(hours=23),
            )
            == 0
        )
        assert (
            schedule_due_storage_reconciliations(
                session,
                settings=settings,
                now=now + timedelta(hours=25),
            )
            == 1
        )
        session.commit()
        jobs = list(session.scalars(select(Job).where(Job.job_type == "storage.reconcile")))
        assert len(jobs) == 1
        assert jobs[0].id == first_job_id
        assert jobs[0].status == "queued"
        assert jobs[0].attempts == 0
        assert jobs[0].payload_json["run_id"] != first_run_id


def test_scheduler_can_be_disabled_without_query_side_effects(
    ledger_harness: LedgerHarness,
):
    settings = ledger_harness.settings.model_copy(
        update={"storage_reconcile_schedule_enabled": False}
    )
    with ledger_harness.sessions() as session:
        usage = session.get(WorkspaceStorageUsage, ledger_harness.workspace_id)
        assert usage is not None
        usage.last_reconciled_at = None
        assert schedule_due_storage_reconciliations(session, settings=settings) == 0
        assert session.scalar(select(Job)) is None


def test_scheduler_rejects_an_invalid_explicit_batch(
    ledger_harness: LedgerHarness,
):
    with ledger_harness.sessions() as session:
        with pytest.raises(
            ValueError,
            match="storage reconciliation schedule batch is invalid",
        ):
            schedule_due_storage_reconciliations(
                session,
                settings=ledger_harness.settings,
                limit=0,
            )


def test_legacy_delete_registration_refreshes_same_session_usage_counters(
    ledger_harness: LedgerHarness,
):
    legacy = ledger_harness.storage.put(
        workspace_id=ledger_harness.workspace_id,
        category="legacy",
        filename="legacy.bin",
        stream=BytesIO(b"xx"),
    )
    with ledger_harness.sessions() as session:
        _ledger(ledger_harness, session, "new-owner").put(
            workspace_id=ledger_harness.workspace_id,
            category="tests",
            filename="new.bin",
            stream=BytesIO(b"12345678"),
        )
        allocation, _job = request_storage_deletion(
            session,
            settings=ledger_harness.settings,
            workspace_id=ledger_harness.workspace_id,
            storage_uri=legacy.uri,
            owner_type="legacy",
            owner_id="legacy-owner",
            category="legacy",
            filename="legacy.bin",
            size_bytes=legacy.size_bytes,
            checksum=legacy.checksum,
            mime_type=legacy.mime_type,
        )
        session.commit()
        assert allocation.status == "delete_pending"

    with ledger_harness.sessions() as session:
        usage = session.get(WorkspaceStorageUsage, ledger_harness.workspace_id)
        assert usage is not None
        assert (usage.used_bytes, usage.used_objects) == (10, 2)


def test_delete_registration_rejects_another_workspace_prefix(
    ledger_harness: LedgerHarness,
):
    foreign = ledger_harness.storage.put(
        workspace_id="another-workspace",
        category="legacy",
        filename="foreign.bin",
        stream=BytesIO(b"foreign"),
    )
    with ledger_harness.sessions() as session:
        with pytest.raises(ValueError, match="this workspace"):
            request_storage_deletion(
                session,
                settings=ledger_harness.settings,
                workspace_id=ledger_harness.workspace_id,
                storage_uri=foreign.uri,
                owner_type="legacy",
                owner_id="foreign-owner",
                category="legacy",
                filename="foreign.bin",
                size_bytes=foreign.size_bytes,
            )


def test_shared_legacy_object_is_quarantined_instead_of_deleted(
    ledger_harness: LedgerHarness,
):
    shared = ledger_harness.storage.put(
        workspace_id=ledger_harness.workspace_id,
        category="legacy",
        filename="shared.bin",
        stream=BytesIO(b"shared"),
    )
    with ledger_harness.sessions() as session:
        allocation = StorageObjectAllocation(
            workspace_id=ledger_harness.workspace_id,
            owner_type="shared_legacy",
            owner_id="multiple",
            category="legacy",
            filename="shared.bin",
            status="integrity_error",
            storage_uri=shared.uri,
            checksum=shared.checksum,
            size_bytes=shared.size_bytes,
            size_verified=True,
            mime_type=shared.mime_type,
        )
        session.add(allocation)
        usage = session.get(WorkspaceStorageUsage, ledger_harness.workspace_id)
        assert usage is not None
        usage.used_bytes = shared.size_bytes
        usage.used_objects = 1
        session.commit()

        allocation, job = request_storage_deletion(
            session,
            settings=ledger_harness.settings,
            workspace_id=ledger_harness.workspace_id,
            storage_uri=shared.uri,
            owner_type="asset",
            owner_id="one-reference",
            category="legacy",
            filename="shared.bin",
            size_bytes=shared.size_bytes,
        )
        session.commit()
        assert job is None
        assert allocation.status == "integrity_error"
        assert "automatic deletion is disabled" in allocation.last_error
    assert ledger_harness.storage.read(shared.uri) == b"shared"


def test_transaction_rollback_removes_the_uncommitted_physical_object(
    ledger_harness: LedgerHarness,
):
    with ledger_harness.sessions() as session:
        stored = _ledger(ledger_harness, session, "owner-rollback").put(
            workspace_id=ledger_harness.workspace_id,
            category="tests",
            filename="rollback.bin",
            stream=BytesIO(b"rollback"),
        )
        assert ledger_harness.storage.read(stored.uri) == b"rollback"
        session.rollback()

    with pytest.raises(FileNotFoundError):
        ledger_harness.storage.read(stored.uri)
    with ledger_harness.sessions() as session:
        usage = session.get(WorkspaceStorageUsage, ledger_harness.workspace_id)
        assert usage is not None
        assert (usage.used_bytes, usage.used_objects) == (0, 0)
        assert session.scalar(select(StorageObjectAllocation)) is None


def test_quota_and_unverified_legacy_state_fail_before_storage_write(
    ledger_harness: LedgerHarness,
):
    with ledger_harness.sessions() as session:
        stored = _ledger(ledger_harness, session, "owner-full").put(
            workspace_id=ledger_harness.workspace_id,
            category="tests",
            filename="full.bin",
            stream=BytesIO(b"12345678"),
        )
        session.commit()

    with ledger_harness.sessions() as session:
        with pytest.raises(StorageQuotaExceeded):
            _ledger(ledger_harness, session, "owner-overflow").put(
                workspace_id=ledger_harness.workspace_id,
                category="tests",
                filename="overflow.bin",
                stream=BytesIO(b"x"),
            )
        session.rollback()
    assert len(ledger_harness.storage.list_workspace_objects(
        ledger_harness.workspace_id,
        limit=10,
    ).items) == 1
    assert ledger_harness.storage.read(stored.uri) == b"12345678"

    with ledger_harness.sessions() as session:
        allocation = session.scalar(select(StorageObjectAllocation))
        usage = session.get(WorkspaceStorageUsage, ledger_harness.workspace_id)
        assert allocation is not None and usage is not None
        allocation.size_verified = False
        usage.unverified_objects = 1
        session.commit()
    with ledger_harness.sessions() as session:
        with pytest.raises(StorageLedgerUnverified):
            _ledger(ledger_harness, session, "owner-unverified").put(
                workspace_id=ledger_harness.workspace_id,
                category="tests",
                filename="blocked.bin",
                stream=BytesIO(b"x"),
            )
        session.rollback()


class _RejectingPutStorage:
    def put(self, **_kwargs):
        raise OSError("injected write outage")


def test_object_store_failure_releases_reservation_and_records_abandonment(
    ledger_harness: LedgerHarness,
):
    with ledger_harness.sessions() as session:
        storage = LedgeredObjectStorage(
            session=session,
            settings=ledger_harness.settings,
            owner_type="test_object",
            owner_id="owner-write-failure",
            storage=_RejectingPutStorage(),
        )
        with pytest.raises(OSError, match="injected write outage"):
            storage.put(
                workspace_id=ledger_harness.workspace_id,
                category="tests",
                filename="failure.bin",
                stream=BytesIO(b"failure"),
            )
        session.commit()

    with ledger_harness.sessions() as session:
        usage = session.get(WorkspaceStorageUsage, ledger_harness.workspace_id)
        allocation = session.scalar(select(StorageObjectAllocation))
        assert usage is not None and allocation is not None
        assert (usage.used_bytes, usage.used_objects) == (0, 0)
        assert (usage.reserved_bytes, usage.reserved_objects) == (0, 0)
        assert allocation.status == "abandoned"
        assert "injected write outage" in allocation.last_error


class _FailingOnceDeleteStorage:
    def __init__(self, delegate: LocalObjectStorage):
        self.delegate = delegate
        self.delete_attempts = 0

    def delete(self, uri: str) -> None:
        self.delete_attempts += 1
        if self.delete_attempts == 1:
            raise OSError("injected object-store outage")
        self.delegate.delete(uri)


def test_delete_failure_stays_charged_and_retry_releases_quota(
    ledger_harness: LedgerHarness,
):
    with ledger_harness.sessions() as session:
        stored = _ledger(ledger_harness, session, "owner-delete").put(
            workspace_id=ledger_harness.workspace_id,
            category="tests",
            filename="delete.bin",
            stream=BytesIO(b"12345678"),
        )
        session.commit()
        allocation, _job = request_storage_deletion(
            session,
            settings=ledger_harness.settings,
            workspace_id=ledger_harness.workspace_id,
            storage_uri=stored.uri,
            owner_type="test_object",
            owner_id="owner-delete",
            category="tests",
            filename="delete.bin",
            size_bytes=stored.size_bytes,
            checksum=stored.checksum,
            mime_type=stored.mime_type,
        )
        allocation_id = allocation.id
        delete_requested_at = allocation.delete_requested_at
        assert delete_requested_at is not None
        session.commit()

    failing_storage = _FailingOnceDeleteStorage(ledger_harness.storage)
    with patch(
        "contentflow.storage_ledger.build_object_storage_for_uri",
        return_value=failing_storage,
    ):
        with ledger_harness.sessions() as session:
            with pytest.raises(StorageDeletionError):
                delete_storage_allocation(
                    session,
                    {"allocation_id": allocation_id},
                    ledger_harness.settings,
                )
        with ledger_harness.sessions() as session:
            allocation = session.get(StorageObjectAllocation, allocation_id)
            usage = session.get(WorkspaceStorageUsage, ledger_harness.workspace_id)
            assert allocation is not None and usage is not None
            assert allocation.status == "delete_pending"
            assert allocation.delete_attempts == 1
            assert (
                allocation.delete_requested_at.replace(tzinfo=timezone.utc)
                == delete_requested_at
            )
            assert "injected object-store outage" in allocation.last_error
            assert (usage.used_bytes, usage.used_objects) == (8, 1)
        assert ledger_harness.storage.read(stored.uri) == b"12345678"

        with ledger_harness.sessions() as session:
            result = delete_storage_allocation(
                session,
                {"allocation_id": allocation_id},
                ledger_harness.settings,
            )
            assert result["status"] == "deleted"

    with pytest.raises(FileNotFoundError):
        ledger_harness.storage.read(stored.uri)
    with ledger_harness.sessions() as session:
        allocation = session.get(StorageObjectAllocation, allocation_id)
        usage = session.get(WorkspaceStorageUsage, ledger_harness.workspace_id)
        assert allocation is not None and usage is not None
        assert allocation.status == "deleted"
        assert allocation.delete_attempts == 1
        assert (
            allocation.delete_requested_at.replace(tzinfo=timezone.utc)
            == delete_requested_at
        )
        assert (usage.used_bytes, usage.used_objects) == (0, 0)


def test_reconciliation_repairs_legacy_sizes_marks_missing_and_deletes_orphans(
    ledger_harness: LedgerHarness,
):
    known = ledger_harness.storage.put(
        workspace_id=ledger_harness.workspace_id,
        category="legacy",
        filename="known.bin",
        stream=BytesIO(b"legacy"),
    )
    orphan = ledger_harness.storage.put(
        workspace_id=ledger_harness.workspace_id,
        category="legacy",
        filename="orphan.bin",
        stream=BytesIO(b"orphan"),
    )
    orphan_path = next(
        ledger_harness.storage.root / item.key
        for item in ledger_harness.storage.list_workspace_objects(
            ledger_harness.workspace_id,
            limit=10,
        ).items
        if item.uri == orphan.uri
    )
    old_timestamp = (datetime.now(timezone.utc) - timedelta(hours=2)).timestamp()
    os.utime(orphan_path, (old_timestamp, old_timestamp))

    missing_uri = (
        ledger_harness.settings.local_storage_dir
        / ledger_harness.workspace_id
        / "legacy"
        / "missing.bin"
    ).resolve().as_uri()
    with ledger_harness.sessions() as session:
        session.add_all(
            [
                StorageObjectAllocation(
                    workspace_id=ledger_harness.workspace_id,
                    owner_type="legacy",
                    owner_id="known",
                    category="legacy",
                    filename="known.bin",
                    status="active",
                    storage_uri=known.uri,
                    size_bytes=0,
                    size_verified=False,
                ),
                StorageObjectAllocation(
                    workspace_id=ledger_harness.workspace_id,
                    owner_type="legacy",
                    owner_id="missing",
                    category="legacy",
                    filename="missing.bin",
                    status="active",
                    storage_uri=missing_uri,
                    size_bytes=0,
                    size_verified=False,
                ),
                StorageObjectAllocation(
                    workspace_id=ledger_harness.workspace_id,
                    owner_type="legacy",
                    owner_id="expired-reservation",
                    category="legacy",
                    filename="expired.bin",
                    status="reserved",
                    size_bytes=3,
                    size_verified=True,
                    reserved_until=datetime.now(timezone.utc) - timedelta(minutes=1),
                ),
            ]
        )
        usage = session.get(WorkspaceStorageUsage, ledger_harness.workspace_id)
        assert usage is not None
        usage.used_objects = 2
        usage.unverified_objects = 2
        usage.reserved_bytes = 3
        usage.reserved_objects = 1
        session.commit()

    with patch(
        "contentflow.storage_ledger.build_object_storage",
        return_value=ledger_harness.storage,
    ):
        with ledger_harness.sessions() as session:
            result = reconcile_workspace_storage(
                session,
                {
                    "workspace_id": ledger_harness.workspace_id,
                    "run_id": "first-reconciliation",
                    "delete_orphans": False,
                },
                ledger_harness.settings,
            )
            session.commit()
        assert result["legacy_sizes_repaired"] == 1
        assert result["orphan_candidates"] == 1
        assert result["orphan_deleted"] == 0
        assert result["expired_reservations_released"] == 1
        assert ledger_harness.storage.read(orphan.uri) == b"orphan"

        with ledger_harness.sessions() as session:
            usage = session.get(WorkspaceStorageUsage, ledger_harness.workspace_id)
            missing = session.scalar(
                select(StorageObjectAllocation).where(
                    StorageObjectAllocation.owner_id == "missing"
                )
            )
            assert usage is not None and missing is not None
            assert usage.used_bytes == len(b"legacy")
            assert usage.unverified_objects == 0
            assert (usage.reserved_bytes, usage.reserved_objects) == (0, 0)
            assert missing.status == "missing"
            assert missing.size_verified is True
            assert missing.size_bytes == 0

        with ledger_harness.sessions() as session:
            result = reconcile_workspace_storage(
                session,
                {
                    "workspace_id": ledger_harness.workspace_id,
                    "run_id": "orphan-cleanup",
                    "delete_orphans": True,
                },
                ledger_harness.settings,
            )
            session.commit()
        assert result["orphan_deleted"] == 1
    with pytest.raises(FileNotFoundError):
        ledger_harness.storage.read(orphan.uri)


def test_reconciliation_detects_verified_missing_and_size_integrity_errors(
    ledger_harness: LedgerHarness,
):
    with ledger_harness.sessions() as session:
        stored = _ledger(ledger_harness, session, "verified-owner").put(
            workspace_id=ledger_harness.workspace_id,
            category="tests",
            filename="verified.bin",
            stream=BytesIO(b"12345678"),
        )
        session.commit()
        allocation = session.scalar(select(StorageObjectAllocation))
        assert allocation is not None
        allocation_id = allocation.id

    physical_path = next(
        ledger_harness.storage.root / item.key
        for item in ledger_harness.storage.list_workspace_objects(
            ledger_harness.workspace_id,
            limit=10,
        ).items
        if item.uri == stored.uri
    )
    physical_path.write_bytes(b"bad")
    with patch(
        "contentflow.storage_ledger.build_object_storage",
        return_value=ledger_harness.storage,
    ):
        with ledger_harness.sessions() as session:
            result = reconcile_workspace_storage(
                session,
                {
                    "workspace_id": ledger_harness.workspace_id,
                    "run_id": "integrity-scan",
                    "delete_orphans": False,
                },
                ledger_harness.settings,
            )
            session.commit()
        assert result["integrity_mismatches"] == 1
        assert result["missing_detected"] == 0

        with ledger_harness.sessions() as session:
            allocation = session.get(StorageObjectAllocation, allocation_id)
            usage = session.get(WorkspaceStorageUsage, ledger_harness.workspace_id)
            assert allocation is not None and usage is not None
            assert allocation.status == "integrity_error"
            assert allocation.size_bytes == 3
            assert "expected=8, actual=3" in allocation.last_error
            assert (usage.used_bytes, usage.used_objects) == (3, 1)

        physical_path.unlink()
        with ledger_harness.sessions() as session:
            result = reconcile_workspace_storage(
                session,
                {
                    "workspace_id": ledger_harness.workspace_id,
                    "run_id": "missing-scan",
                    "delete_orphans": False,
                },
                ledger_harness.settings,
            )
            session.commit()
        assert result["missing_detected"] == 1

    with ledger_harness.sessions() as session:
        allocation = session.get(StorageObjectAllocation, allocation_id)
        usage = session.get(WorkspaceStorageUsage, ledger_harness.workspace_id)
        assert allocation is not None and usage is not None
        assert allocation.status == "missing"
        assert allocation.size_bytes == 3
        assert (usage.used_bytes, usage.used_objects) == (3, 1)
