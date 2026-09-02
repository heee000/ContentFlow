from __future__ import annotations

import hashlib
import logging
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from typing import BinaryIO

from sqlalchemy import event, func, select, update
from sqlalchemy.orm import Session

from .audit import record_audit
from .entities import (
    Job,
    StorageObjectAllocation,
    Workspace,
    WorkspaceStorageUsage,
)
from .filenames import safe_filename
from .job_queue import enqueue_job
from .object_storage import (
    ObjectStorage,
    StoredObject,
    StoredObjectPage,
    build_object_storage,
    build_object_storage_for_uri,
    is_workspace_storage_uri,
)
from .settings import Settings


logger = logging.getLogger(__name__)
_ROLLBACK_OBJECTS = "contentflow_storage_rollback_objects"
_ROLLBACK_LISTENERS = "contentflow_storage_rollback_listeners"
CHARGED_STATUSES = (
    "active",
    "delete_pending",
    "missing",
    "integrity_error",
)


class StorageQuotaExceeded(RuntimeError):
    """Raised before object storage is touched when a workspace quota is full."""


class StorageLedgerUnverified(RuntimeError):
    """Raised when legacy object sizes must be reconciled before new writes."""


class StorageLedgerInvariantError(RuntimeError):
    """Raised when persisted counters and allocation state disagree."""


class StorageDeletionError(RuntimeError):
    """A retryable physical deletion failure."""


def create_workspace_storage_usage(session: Session, workspace_id: str) -> None:
    session.add(WorkspaceStorageUsage(workspace_id=workspace_id))


def _delete_rollback_objects(session: Session) -> None:
    pending = list(session.info.pop(_ROLLBACK_OBJECTS, []))
    for storage, uri in reversed(pending):
        try:
            storage.delete(uri)
        except Exception:
            logger.exception("failed to remove rolled-back storage object")


def _clear_rollback_objects(session: Session) -> None:
    session.info.pop(_ROLLBACK_OBJECTS, None)


def _register_rollback_object(
    session: Session,
    *,
    storage: ObjectStorage,
    uri: str,
) -> None:
    if not session.info.get(_ROLLBACK_LISTENERS):
        event.listen(session, "after_rollback", _delete_rollback_objects)
        event.listen(session, "after_commit", _clear_rollback_objects)
        session.info[_ROLLBACK_LISTENERS] = True
    session.info.setdefault(_ROLLBACK_OBJECTS, []).append((storage, uri))


def _usage_for_update(
    session: Session,
    *,
    workspace_id: str,
) -> WorkspaceStorageUsage:
    workspace_query = select(Workspace.id).where(Workspace.id == workspace_id)
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        workspace_query = workspace_query.with_for_update()
    if session.scalar(workspace_query) is None:
        raise StorageLedgerInvariantError("workspace does not exist")

    usage_query = select(WorkspaceStorageUsage).where(
        WorkspaceStorageUsage.workspace_id == workspace_id
    )
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        usage_query = usage_query.with_for_update()
    usage = session.scalar(
        usage_query.execution_options(populate_existing=True)
    )
    if usage is None:
        usage = WorkspaceStorageUsage(workspace_id=workspace_id)
        session.add(usage)
        session.flush()
    return usage


def reserve_storage_allocation(
    session: Session,
    *,
    settings: Settings,
    workspace_id: str,
    owner_type: str,
    owner_id: str,
    category: str,
    filename: str,
    size_bytes: int,
) -> StorageObjectAllocation:
    if not 0 <= size_bytes <= settings.max_upload_bytes:
        raise ValueError("object size is outside the configured upload boundary")
    if not owner_type or len(owner_type) > 48:
        raise ValueError("storage owner type is invalid")
    if not owner_id or len(owner_id) > 160:
        raise ValueError("storage owner id is invalid")
    if not category or len(category) > 160 or ".." in category or "\\" in category:
        raise ValueError("storage category is invalid")
    clean_name = safe_filename(filename)
    _usage_for_update(session, workspace_id=workspace_id)
    result = session.execute(
        update(WorkspaceStorageUsage)
        .where(
            WorkspaceStorageUsage.workspace_id == workspace_id,
            WorkspaceStorageUsage.unverified_objects == 0,
            WorkspaceStorageUsage.used_bytes
            + WorkspaceStorageUsage.reserved_bytes
            + size_bytes
            <= settings.workspace_storage_max_bytes,
            WorkspaceStorageUsage.used_objects
            + WorkspaceStorageUsage.reserved_objects
            + 1
            <= settings.workspace_storage_max_objects,
        )
        .values(
            reserved_bytes=WorkspaceStorageUsage.reserved_bytes + size_bytes,
            reserved_objects=WorkspaceStorageUsage.reserved_objects + 1,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        usage = session.get(WorkspaceStorageUsage, workspace_id)
        if usage is not None and usage.unverified_objects:
            raise StorageLedgerUnverified(
                "workspace contains legacy objects with unverified sizes; "
                "run storage reconciliation before uploading"
            )
        raise StorageQuotaExceeded(
            "workspace storage quota exceeded: "
            f"bytes={settings.workspace_storage_max_bytes}, "
            f"objects={settings.workspace_storage_max_objects}"
        )
    allocation = StorageObjectAllocation(
        workspace_id=workspace_id,
        owner_type=owner_type,
        owner_id=owner_id,
        category=category,
        filename=clean_name,
        status="reserved",
        size_bytes=size_bytes,
        size_verified=True,
        reserved_until=datetime.now(timezone.utc)
        + timedelta(minutes=settings.storage_reservation_ttl_minutes),
    )
    session.add(allocation)
    session.flush()
    return allocation


def _abandon_reservation(
    session: Session,
    allocation: StorageObjectAllocation,
    *,
    error: Exception,
) -> None:
    if allocation.status != "reserved":
        return
    result = session.execute(
        update(WorkspaceStorageUsage)
        .where(
            WorkspaceStorageUsage.workspace_id == allocation.workspace_id,
            WorkspaceStorageUsage.reserved_bytes >= allocation.size_bytes,
            WorkspaceStorageUsage.reserved_objects >= 1,
        )
        .values(
            reserved_bytes=(
                WorkspaceStorageUsage.reserved_bytes - allocation.size_bytes
            ),
            reserved_objects=WorkspaceStorageUsage.reserved_objects - 1,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise StorageLedgerInvariantError("cannot release storage reservation")
    allocation.status = "abandoned"
    allocation.reserved_until = None
    allocation.last_error = f"{type(error).__name__}: {error}"[:2000]


def _activate_allocation(
    session: Session,
    allocation: StorageObjectAllocation,
    stored: StoredObject,
) -> None:
    if allocation.status != "reserved" or stored.size_bytes != allocation.size_bytes:
        raise StorageLedgerInvariantError("stored object does not match its reservation")
    result = session.execute(
        update(WorkspaceStorageUsage)
        .where(
            WorkspaceStorageUsage.workspace_id == allocation.workspace_id,
            WorkspaceStorageUsage.reserved_bytes >= allocation.size_bytes,
            WorkspaceStorageUsage.reserved_objects >= 1,
        )
        .values(
            reserved_bytes=(
                WorkspaceStorageUsage.reserved_bytes - allocation.size_bytes
            ),
            reserved_objects=WorkspaceStorageUsage.reserved_objects - 1,
            used_bytes=WorkspaceStorageUsage.used_bytes + stored.size_bytes,
            used_objects=WorkspaceStorageUsage.used_objects + 1,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise StorageLedgerInvariantError("cannot activate storage allocation")
    allocation.status = "active"
    allocation.storage_uri = stored.uri
    allocation.checksum = stored.checksum
    allocation.size_bytes = stored.size_bytes
    allocation.size_verified = True
    allocation.mime_type = stored.mime_type
    allocation.reserved_until = None
    allocation.last_error = None
    session.flush()


class LedgeredObjectStorage:
    def __init__(
        self,
        *,
        session: Session,
        settings: Settings,
        owner_type: str,
        owner_id: str,
        storage: ObjectStorage | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.owner_type = owner_type
        self.owner_id = owner_id
        self.storage = storage or build_object_storage(settings)

    def put(
        self,
        *,
        workspace_id: str,
        category: str,
        filename: str,
        stream: BinaryIO,
        content_type: str | None = None,
        allocation_id: str | None = None,
    ) -> StoredObject:
        if allocation_id is not None:
            raise ValueError("nested storage allocation ids are not supported")
        with tempfile.SpooledTemporaryFile(
            max_size=min(self.settings.max_upload_bytes, 8 * 1024 * 1024)
        ) as staging:
            size_bytes = 0
            while chunk := stream.read(1024 * 1024):
                size_bytes += len(chunk)
                if size_bytes > self.settings.max_upload_bytes:
                    raise ValueError(
                        f"Upload exceeds {self.settings.max_upload_bytes} byte limit"
                    )
                staging.write(chunk)
            allocation = reserve_storage_allocation(
                self.session,
                settings=self.settings,
                workspace_id=workspace_id,
                owner_type=self.owner_type,
                owner_id=self.owner_id,
                category=category,
                filename=filename,
                size_bytes=size_bytes,
            )
            stored: StoredObject | None = None
            try:
                staging.seek(0)
                stored = self.storage.put(
                    workspace_id=workspace_id,
                    category=category,
                    filename=filename,
                    stream=staging,
                    content_type=content_type,
                    allocation_id=allocation.id,
                )
                _activate_allocation(self.session, allocation, stored)
            except Exception as error:
                if stored is not None:
                    try:
                        self.storage.delete(stored.uri)
                    except Exception:
                        logger.exception("failed to remove uncommitted storage object")
                _abandon_reservation(self.session, allocation, error=error)
                raise
        _register_rollback_object(
            self.session,
            storage=self.storage,
            uri=stored.uri,
        )
        return stored

    def read(self, uri: str, *, max_bytes: int = 100 * 1024 * 1024) -> bytes:
        return self.storage.read(uri, max_bytes=max_bytes)

    def delete(self, uri: str) -> None:
        self.storage.delete(uri)

    def list_workspace_objects(
        self,
        workspace_id: str,
        *,
        limit: int,
        cursor: str | None = None,
    ) -> StoredObjectPage:
        return self.storage.list_workspace_objects(
            workspace_id,
            limit=limit,
            cursor=cursor,
        )

    def workspace_uri_prefix(self, workspace_id: str) -> str:
        return self.storage.workspace_uri_prefix(workspace_id)

    def check(self) -> None:
        self.storage.check()


def build_ledgered_object_storage(
    session: Session,
    settings: Settings,
    *,
    owner_type: str,
    owner_id: str,
) -> LedgeredObjectStorage:
    return LedgeredObjectStorage(
        session=session,
        settings=settings,
        owner_type=owner_type,
        owner_id=owner_id,
    )


def request_storage_deletion(
    session: Session,
    *,
    settings: Settings,
    workspace_id: str,
    storage_uri: str,
    owner_type: str,
    owner_id: str,
    category: str,
    filename: str,
    size_bytes: int | None,
    checksum: str | None = None,
    mime_type: str | None = None,
) -> tuple[StorageObjectAllocation, Job | None]:
    if not is_workspace_storage_uri(settings, workspace_id, storage_uri):
        raise ValueError("object URI is not managed by this workspace")
    allocation_query = select(StorageObjectAllocation).where(
        StorageObjectAllocation.workspace_id == workspace_id,
        StorageObjectAllocation.storage_uri == storage_uri,
    )
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        allocation_query = allocation_query.with_for_update()
    allocation = session.scalar(allocation_query)
    if allocation is None:
        usage = _usage_for_update(session, workspace_id=workspace_id)
        verified_size = (
            int(size_bytes)
            if isinstance(size_bytes, int)
            and not isinstance(size_bytes, bool)
            and size_bytes > 0
            else 0
        )
        usage.used_bytes += verified_size
        usage.used_objects += 1
        if not verified_size:
            usage.unverified_objects += 1
        allocation = StorageObjectAllocation(
            workspace_id=workspace_id,
            owner_type=owner_type,
            owner_id=owner_id,
            category=category,
            filename=safe_filename(filename),
            status="delete_pending",
            storage_uri=storage_uri,
            checksum=checksum,
            size_bytes=verified_size,
            size_verified=bool(verified_size),
            mime_type=mime_type,
        )
        session.add(allocation)
        session.flush()
    elif allocation.owner_type == "shared_legacy":
        allocation.status = "integrity_error"
        allocation.last_error = (
            "multiple legacy records reference this object; "
            "automatic deletion is disabled"
        )
        return allocation, None
    elif allocation.status in {"active", "missing", "integrity_error"}:
        allocation.status = "delete_pending"
        allocation.last_error = None
    elif allocation.status == "deleted":
        pass
    elif allocation.status != "delete_pending":
        raise StorageLedgerInvariantError(
            f"allocation in status {allocation.status} cannot be deleted"
        )
    job = enqueue_job(
        session,
        job_type="storage.delete",
        payload={"allocation_id": allocation.id},
        workspace_id=workspace_id,
        idempotency_key=f"storage.delete:{allocation.id}",
        max_attempts=settings.storage_delete_max_attempts,
    )
    if allocation.status == "delete_pending" and job.status == "failed":
        job.status = "retry"
        job.attempts = 0
        job.run_at = datetime.now(timezone.utc)
        job.last_error = None
        job.locked_by = None
        job.locked_at = None
    return allocation, job


def delete_storage_allocation(
    session: Session,
    payload: dict,
    settings: Settings,
) -> dict:
    allocation_id = str(payload.get("allocation_id") or "")
    allocation = session.get(StorageObjectAllocation, allocation_id)
    if allocation is None:
        raise ValueError("storage allocation does not exist")
    if allocation.status == "deleted":
        return {"allocation_id": allocation.id, "status": "deleted"}
    if allocation.status != "delete_pending" or not allocation.storage_uri:
        raise StorageLedgerInvariantError("storage allocation is not pending deletion")
    workspace_id = allocation.workspace_id
    storage_uri = allocation.storage_uri
    session.commit()
    storage = build_object_storage_for_uri(settings, storage_uri)
    try:
        storage.delete(storage_uri)
    except Exception as error:
        allocation = session.get(StorageObjectAllocation, allocation_id)
        if allocation is not None and allocation.status == "delete_pending":
            allocation.delete_attempts += 1
            allocation.last_error = f"{type(error).__name__}: {error}"[:2000]
            session.commit()
        raise StorageDeletionError("physical object deletion failed") from error

    allocation_query = select(StorageObjectAllocation).where(
        StorageObjectAllocation.id == allocation_id
    )
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        allocation_query = allocation_query.with_for_update()
    allocation_query = allocation_query.execution_options(populate_existing=True)
    allocation = session.scalar(allocation_query)
    if allocation is None:
        raise StorageLedgerInvariantError("storage allocation disappeared after deletion")
    if allocation.status == "deleted":
        return {"allocation_id": allocation.id, "status": "deleted"}
    usage = _usage_for_update(session, workspace_id=workspace_id)
    usage.used_bytes -= allocation.size_bytes
    usage.used_objects -= 1
    if not allocation.size_verified:
        usage.unverified_objects -= 1
    if min(usage.used_bytes, usage.used_objects, usage.unverified_objects) < 0:
        raise StorageLedgerInvariantError("storage usage counters would become negative")
    allocation.status = "deleted"
    allocation.deleted_at = datetime.now(timezone.utc)
    allocation.last_error = None
    record_audit(
        session,
        action="storage.object_deleted",
        entity_type="storage_object_allocation",
        entity_id=allocation.id,
        workspace_id=workspace_id,
        actor_user_id=None,
        metadata={
            "owner_type": allocation.owner_type,
            "owner_id": allocation.owner_id,
            "category": allocation.category,
            "size_bytes": allocation.size_bytes,
        },
    )
    session.commit()
    return {"allocation_id": allocation.id, "status": "deleted"}


def reconcile_workspace_storage(
    session: Session,
    payload: dict,
    settings: Settings,
) -> dict:
    workspace_id = str(payload.get("workspace_id") or "")
    run_id = str(payload.get("run_id") or "")
    cursor_value = payload.get("storage_cursor")
    storage_cursor = str(cursor_value) if cursor_value else None
    delete_orphans = payload.get("delete_orphans") is True
    if not workspace_id or not run_id:
        raise ValueError("storage reconciliation payload is incomplete")
    now = datetime.now(timezone.utc)
    raw_scan_started_at = payload.get("scan_started_at")
    if raw_scan_started_at:
        try:
            scan_started_at = datetime.fromisoformat(str(raw_scan_started_at))
        except ValueError as error:
            raise ValueError("storage reconciliation start time is invalid") from error
        if scan_started_at.tzinfo is None:
            raise ValueError("storage reconciliation start time must include timezone")
        scan_started_at = scan_started_at.astimezone(timezone.utc)
        if scan_started_at > now + timedelta(minutes=5):
            raise ValueError("storage reconciliation start time is in the future")
    else:
        scan_started_at = now
    usage = _usage_for_update(session, workspace_id=workspace_id)
    expired_count, expired_bytes = session.execute(
        select(
            func.count(StorageObjectAllocation.id),
            func.coalesce(func.sum(StorageObjectAllocation.size_bytes), 0),
        ).where(
            StorageObjectAllocation.workspace_id == workspace_id,
            StorageObjectAllocation.status == "reserved",
            StorageObjectAllocation.reserved_until < now,
        )
    ).one()
    expired_count = int(expired_count or 0)
    expired_bytes = int(expired_bytes or 0)
    if expired_count:
        usage.reserved_bytes -= expired_bytes
        usage.reserved_objects -= expired_count
        session.execute(
            update(StorageObjectAllocation)
            .where(
                StorageObjectAllocation.workspace_id == workspace_id,
                StorageObjectAllocation.status == "reserved",
                StorageObjectAllocation.reserved_until < now,
            )
            .values(
                status="abandoned",
                reserved_until=None,
                last_error="storage reservation expired before activation",
            )
            .execution_options(synchronize_session=False)
        )
    storage = build_object_storage(settings)
    page = storage.list_workspace_objects(
        workspace_id,
        limit=settings.storage_cleanup_batch_size,
        cursor=storage_cursor,
    )
    uris = [item.uri for item in page.items]
    known = {
        item.storage_uri: item
        for item in session.scalars(
            select(StorageObjectAllocation).where(
                StorageObjectAllocation.workspace_id == workspace_id,
                StorageObjectAllocation.storage_uri.in_(uris),
                StorageObjectAllocation.status.in_(CHARGED_STATUSES),
            )
        )
        if item.storage_uri is not None
    }
    repaired = 0
    integrity_mismatches = 0
    orphan_candidates = 0
    orphan_deleted = 0
    orphan_delete_failures = 0
    orphan_keys: list[str] = []
    for item in page.items:
        allocation = known.get(item.uri)
        if allocation is not None:
            if allocation.status == "missing":
                was_unverified = not allocation.size_verified
                previous_size = allocation.size_bytes
                usage.used_bytes += item.size_bytes - allocation.size_bytes
                if was_unverified:
                    usage.unverified_objects -= 1
                allocation.size_bytes = item.size_bytes
                allocation.size_verified = True
                allocation.mime_type = allocation.mime_type or item.mime_type
                if not was_unverified and previous_size != item.size_bytes:
                    allocation.status = "integrity_error"
                    allocation.last_error = (
                        "restored object size differs from the last verified size: "
                        f"expected={previous_size}, actual={item.size_bytes}"
                    )
                    integrity_mismatches += 1
                else:
                    allocation.status = "active"
                    allocation.last_error = None
                repaired += 1
            elif not allocation.size_verified:
                usage.used_bytes += item.size_bytes - allocation.size_bytes
                usage.unverified_objects -= 1
                allocation.size_bytes = item.size_bytes
                allocation.size_verified = True
                allocation.mime_type = allocation.mime_type or item.mime_type
                allocation.last_error = None
                repaired += 1
            elif (
                allocation.status in {"active", "integrity_error"}
                and allocation.size_bytes != item.size_bytes
            ):
                previous_size = allocation.size_bytes
                usage.used_bytes += item.size_bytes - previous_size
                allocation.status = "integrity_error"
                allocation.size_bytes = item.size_bytes
                allocation.last_error = (
                    "physical object size differs from the ledger: "
                    f"expected={previous_size}, actual={item.size_bytes}"
                )
                integrity_mismatches += 1
            allocation.updated_at = datetime.now(timezone.utc)
            continue
        if now - item.modified_at < timedelta(
            seconds=settings.storage_orphan_grace_seconds
        ):
            continue
        orphan_candidates += 1
        if len(orphan_keys) < 20:
            orphan_keys.append(item.key)
        if delete_orphans:
            try:
                storage.delete(item.uri)
                orphan_deleted += 1
            except Exception:
                orphan_delete_failures += 1
                logger.exception("failed to delete orphan storage object")

    next_cursor = page.next_cursor
    if next_cursor:
        cursor_digest = hashlib.sha256(next_cursor.encode("utf-8")).hexdigest()[:20]
        enqueue_job(
            session,
            job_type="storage.reconcile",
            payload={
                "workspace_id": workspace_id,
                "run_id": run_id,
                "scan_started_at": scan_started_at.isoformat(),
                "storage_cursor": next_cursor,
                "delete_orphans": delete_orphans,
            },
            workspace_id=workspace_id,
            idempotency_key=f"storage.reconcile:{run_id}:{cursor_digest}",
            max_attempts=settings.worker_max_attempts,
        )
    else:
        managed_prefix = storage.workspace_uri_prefix(workspace_id)
        unseen_filter = (
            StorageObjectAllocation.workspace_id == workspace_id,
            StorageObjectAllocation.status.in_(("active", "integrity_error")),
            StorageObjectAllocation.updated_at <= scan_started_at,
            StorageObjectAllocation.storage_uri.startswith(managed_prefix),
        )
        unverified_missing_count, unverified_missing_bytes = session.execute(
            select(
                func.count(StorageObjectAllocation.id),
                func.coalesce(func.sum(StorageObjectAllocation.size_bytes), 0),
            ).where(
                *unseen_filter,
                StorageObjectAllocation.size_verified.is_(False),
            )
        ).one()
        unverified_missing_count = int(unverified_missing_count or 0)
        usage.used_bytes -= int(unverified_missing_bytes or 0)
        usage.unverified_objects -= unverified_missing_count
        unverified_result = session.execute(
            update(StorageObjectAllocation)
            .where(
                *unseen_filter,
                StorageObjectAllocation.size_verified.is_(False),
            )
            .values(
                status="missing",
                size_bytes=0,
                size_verified=True,
                last_error="object was not found during a complete storage scan",
            )
            .execution_options(synchronize_session=False)
        )
        verified_result = session.execute(
            update(StorageObjectAllocation)
            .where(
                *unseen_filter,
                StorageObjectAllocation.size_verified.is_(True),
            )
            .values(
                status="missing",
                last_error="object was not found during a complete storage scan",
            )
            .execution_options(synchronize_session=False)
        )
        missing_detected = int(unverified_result.rowcount or 0) + int(
            verified_result.rowcount or 0
        )
        usage.last_reconciled_at = now
    if next_cursor:
        missing_detected = 0
    if min(
        usage.used_bytes,
        usage.unverified_objects,
        usage.reserved_bytes,
        usage.reserved_objects,
    ) < 0:
        raise StorageLedgerInvariantError("reconciliation produced negative counters")
    record_audit(
        session,
        action="storage.reconcile_batch",
        entity_type="workspace",
        entity_id=workspace_id,
        workspace_id=workspace_id,
        actor_user_id=None,
        metadata={
            "run_id": run_id,
            "objects_scanned": len(page.items),
            "legacy_sizes_repaired": repaired,
            "integrity_mismatches": integrity_mismatches,
            "missing_detected": missing_detected,
            "orphan_candidates": orphan_candidates,
            "orphan_deleted": orphan_deleted,
            "orphan_delete_failures": orphan_delete_failures,
            "expired_reservations_released": expired_count,
            "has_next_page": next_cursor is not None,
        },
    )
    return {
        "run_id": run_id,
        "objects_scanned": len(page.items),
        "legacy_sizes_repaired": repaired,
        "integrity_mismatches": integrity_mismatches,
        "missing_detected": missing_detected,
        "orphan_candidates": orphan_candidates,
        "orphan_deleted": orphan_deleted,
        "orphan_delete_failures": orphan_delete_failures,
        "expired_reservations_released": expired_count,
        "orphan_keys": orphan_keys,
        "has_next_page": next_cursor is not None,
    }


def new_reconciliation_run_id() -> str:
    return str(uuid.uuid4())


def pending_storage_counts(session: Session, workspace_id: str) -> dict[str, int]:
    counts = {
        "delete_pending": 0,
        "missing": 0,
        "integrity_error": 0,
        "abandoned": 0,
    }
    for status, count in session.execute(
        select(StorageObjectAllocation.status, func.count(StorageObjectAllocation.id))
        .where(
            StorageObjectAllocation.workspace_id == workspace_id,
            StorageObjectAllocation.status.in_(counts),
        )
        .group_by(StorageObjectAllocation.status)
    ):
        counts[str(status)] = int(count)
    return counts
