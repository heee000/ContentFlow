from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from collections.abc import Collection
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from .atomic_insert import insert_on_unique_key
from .audit import record_audit
from .entities import Job, JobManualReview
from .execution_fence import JobLeaseLost as JobLeaseLost
from .provider_invocations import mark_job_provider_attempts_outcome_unknown


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def make_idempotency_key(job_type: str, payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(f"{job_type}:{canonical}".encode("utf-8")).hexdigest()
    return f"{job_type}:{digest}"


def enqueue_job(
    session: Session,
    *,
    job_type: str,
    payload: dict[str, Any],
    workspace_id: str | None,
    idempotency_key: str | None = None,
    run_at: datetime | None = None,
    max_attempts: int = 4,
) -> Job:
    key = idempotency_key or make_idempotency_key(job_type, payload)
    existing = session.scalar(select(Job).where(Job.idempotency_key == key))
    if existing is None:
        inserted_id = insert_on_unique_key(
            session, Job.__table__, key="idempotency_key",
            values={"job_type": job_type, "payload_json": payload,
                "workspace_id": workspace_id, "idempotency_key": key,
                "run_at": run_at or utcnow(), "max_attempts": max_attempts},
        )
        if inserted_id is not None:
            return session.get(Job, inserted_id)
        existing = session.scalar(select(Job).where(Job.idempotency_key == key))
    if (existing is None or existing.workspace_id != workspace_id
        or existing.job_type != job_type
        or make_idempotency_key(existing.job_type, existing.payload_json)
        != make_idempotency_key(job_type, payload)):
        raise RuntimeError("Queue operation identity conflicts with its existing receipt")
    return existing


def claim_next_job(
    session: Session,
    *,
    worker_id: str,
    lease_seconds: int,
    manual_review_job_types: Collection[str],
) -> Job | None:
    now = utcnow()
    lease_expired = now - timedelta(seconds=lease_seconds)
    expired_running = (Job.status == "running") & (Job.locked_at < lease_expired)
    if manual_review_job_types:
        expired_running &= Job.job_type.not_in(tuple(manual_review_job_types))
    query = (
        select(Job)
        .where(
            Job.run_at <= now,
            Job.attempts < Job.max_attempts,
            or_(
                Job.status.in_(["queued", "retry"]),
                expired_running,
            ),
        )
        .order_by(Job.run_at.asc(), Job.created_at.asc())
        .limit(1)
    )
    if session.bind and session.bind.dialect.name == "postgresql":
        query = query.with_for_update(skip_locked=True)
    job = session.scalar(query)
    if job is None:
        return None
    # SQLite has no SELECT FOR UPDATE: two readers can select the same queued
    # row. Only the unchanged candidate may be claimed, with a fresh identity.
    claimed_id = session.execute(update(Job).where(
        Job.id == job.id, Job.status == job.status, Job.attempts == job.attempts,
        Job.lease_token == job.lease_token, Job.locked_at == job.locked_at,
    ).values(status="running", locked_by=worker_id, locked_at=now,
        lease_token=uuid.uuid4().hex, attempts=Job.attempts + 1)
        .returning(Job.id).execution_options(synchronize_session=False)).scalar_one_or_none()
    if claimed_id is None:
        return None
    return session.get(Job, claimed_id, populate_existing=True)


def request_job_manual_review(
    session: Session,
    job: Job,
    *,
    reason_code: str,
    error: Exception | str,
    source: str,
) -> JobManualReview:
    """Move a job to a durable, audited human-review state."""
    normalized_reason = reason_code.strip()
    if not normalized_reason:
        raise ValueError("manual review reason_code must not be empty")
    message = str(error)[:8000]
    job.status = "manual_review"
    job.last_error = message
    job.locked_by = None
    job.locked_at = None
    unknown_provider_attempts = mark_job_provider_attempts_outcome_unknown(
        session,
        job_id=job.id,
        reason_code=normalized_reason,
    )

    review = session.scalar(
        select(JobManualReview).where(
            JobManualReview.job_id == job.id,
            JobManualReview.resolved_at.is_(None),
        )
    )
    if review is None:
        review = JobManualReview(
            workspace_id=job.workspace_id,
            job_id=job.id,
            reason_code=normalized_reason[:80],
            context_json={
                "source": source,
                "job_type": job.job_type,
                "attempt": job.attempts,
                "provider_attempts_marked_outcome_unknown": unknown_provider_attempts,
                "possible_side_effect": (
                    "供应商可能已经接收请求或产生计费，但 ContentFlow 没有保存最终结果。"
                ),
                "required_checks": [
                    "打开当前配置的供应商控制台，检查本次任务时间窗口内的调用活动。",
                    "确认是否已经存在对应请求、计费或生成结果。",
                    "仅在确认供应商没有处理时重试；已有结果或无法确认时应放弃并人工对账。",
                ],
            },
            requested_at=utcnow(),
        )
        session.add(review)
        session.flush()
        record_audit(
            session,
            action="job.manual_review_requested",
            entity_type="job",
            entity_id=job.id,
            workspace_id=job.workspace_id,
            actor_user_id=None,
            metadata={
                "job_type": job.job_type,
                "reason_code": review.reason_code,
                "source": source,
                "attempt": job.attempts,
                "provider_attempts_marked_outcome_unknown": unknown_provider_attempts,
            },
        )
    elif unknown_provider_attempts:
        context = dict(review.context_json or {})
        context["provider_attempts_marked_outcome_unknown"] = (
            int(context.get("provider_attempts_marked_outcome_unknown") or 0)
            + unknown_provider_attempts
        )
        review.context_json = context
    session.flush()
    return review


def fail_expired_manual_review_leases(
    session: Session,
    *,
    lease_seconds: int,
    job_types: Collection[str],
    limit: int = 100,
) -> list[Job]:
    """Request review for expired jobs whose side effects are unsafe to replay."""
    normalized_job_types = tuple(sorted(set(job_types)))
    if not normalized_job_types:
        return []

    lease_expired = utcnow() - timedelta(seconds=lease_seconds)
    query = (
        select(Job)
        .where(
            Job.status == "running",
            Job.job_type.in_(normalized_job_types),
            Job.locked_at.is_not(None),
            Job.locked_at < lease_expired,
        )
        .order_by(Job.locked_at.asc())
        .limit(limit)
    )
    if session.bind and session.bind.dialect.name == "postgresql":
        query = query.with_for_update(skip_locked=True)
    jobs = list(session.scalars(query))
    for job in jobs:
        request_job_manual_review(
            session,
            job,
            reason_code="worker_lease_expired_provider_outcome_unknown",
            error=(
                "Worker lease expired during a provider operation whose external "
                "side effects cannot be safely replayed automatically. Automatic "
                "retry was blocked; review provider activity before deciding."
            ),
            source="expired_worker_lease",
        )
    if jobs:
        session.flush()
    return jobs


def renew_job_lease(
    session: Session,
    *,
    job_id: str,
    worker_id: str,
    attempt: int,
    lease_seconds: int,
    lease_token: str,
) -> bool:
    if not lease_token:
        return False
    statement = (
        update(Job)
        .where(
            Job.id == job_id,
            Job.status == "running",
            Job.locked_by == worker_id,
            Job.attempts == attempt,
            Job.lease_token == lease_token,
        )
        .values(locked_at=Job.locked_at, updated_at=Job.updated_at)
        .returning(Job.locked_at)
        .execution_options(synchronize_session=False)
    )
    locked_at = session.execute(statement).scalar_one_or_none()
    if locked_at is None:
        return False
    if locked_at.tzinfo is None:
        locked_at = locked_at.replace(tzinfo=timezone.utc)
    # Lock waits must not revive a lease which expired while waiting.
    if locked_at < utcnow() - timedelta(seconds=lease_seconds):
        return False
    session.execute(update(Job).where(Job.id == job_id).values(locked_at=utcnow())
        .execution_options(synchronize_session=False))
    return True


def _get_claimed_job(
    session: Session,
    *,
    job_id: str,
    worker_id: str,
    attempt: int,
    lease_token: str,
    lease_seconds: int,
) -> Job:
    if not lease_token:
        raise JobLeaseLost("Missing execution identity")
    # Also lock on SQLite, including a handler which produced no ORM changes.
    # no_autoflush prevents caller state being written before ownership checks.
    with session.no_autoflush:
        found = session.execute(update(Job).where(
            Job.id == job_id, Job.status == "running", Job.locked_by == worker_id,
            Job.attempts == attempt, Job.lease_token == lease_token,
        ).values(locked_at=Job.locked_at, updated_at=Job.updated_at)
            .returning(Job.id).execution_options(synchronize_session=False)).scalar_one_or_none()
    if found is None:
        raise JobLeaseLost(f"Job lease ownership lost: id={job_id}")
    query = select(Job).where(
        Job.id == job_id,
        Job.status == "running",
        Job.locked_by == worker_id,
        Job.attempts == attempt,
        Job.lease_token == lease_token,
    )
    if session.bind and session.bind.dialect.name == "postgresql":
        query = query.with_for_update()
    job = session.scalar(query.execution_options(populate_existing=True))
    locked_at = job.locked_at if job else None
    if locked_at is not None and locked_at.tzinfo is None:
        locked_at = locked_at.replace(tzinfo=timezone.utc)
    if (not lease_token or job is None or locked_at is None
        or locked_at < utcnow() - timedelta(seconds=lease_seconds)):
        raise JobLeaseLost(
            f"Job lease ownership lost: id={job_id} "
            f"worker={worker_id} attempt={attempt}"
        )
    return job


def fail_exhausted_leases(
    session: Session,
    *,
    lease_seconds: int,
    limit: int = 100,
) -> list[Job]:
    lease_expired = utcnow() - timedelta(seconds=lease_seconds)
    query = (
        select(Job)
        .where(
            Job.status == "running",
            Job.attempts >= Job.max_attempts,
            Job.locked_at.is_not(None),
            Job.locked_at < lease_expired,
        )
        .order_by(Job.locked_at.asc())
        .limit(limit)
    )
    if session.bind and session.bind.dialect.name == "postgresql":
        query = query.with_for_update(skip_locked=True)
    jobs = list(session.scalars(query))
    for job in jobs:
        job.status = "failed"
        job.last_error = (
            f"Worker lease expired after the final attempt ({job.attempts}/"
            f"{job.max_attempts})"
        )
        job.locked_by = None
        job.locked_at = None
    if jobs:
        session.flush()
    return jobs


def complete_job(
    session: Session,
    job: Job,
    result: dict[str, Any],
    *,
    worker_id: str,
    attempt: int,
    lease_token: str,
    lease_seconds: int,
) -> None:
    job = _get_claimed_job(
        session,
        job_id=job.id,
        worker_id=worker_id,
        attempt=attempt,
        lease_token=lease_token,
        lease_seconds=lease_seconds,
    )
    job.status = "succeeded"
    job.result_json = result
    job.last_error = None
    job.locked_by = None
    job.locked_at = None
    session.flush()


def fail_job(
    session: Session,
    job: Job,
    error: Exception | str,
    *,
    worker_id: str,
    attempt: int,
    lease_token: str,
    lease_seconds: int,
    force_terminal: bool = False,
    manual_review_reason_code: str | None = None,
    retry_after_seconds: int | None = None,
) -> Job:
    job = _get_claimed_job(
        session,
        job_id=job.id,
        worker_id=worker_id,
        attempt=attempt,
        lease_token=lease_token,
        lease_seconds=lease_seconds,
    )
    message = str(error)
    job.last_error = message[:8000]
    job.locked_by = None
    job.locked_at = None
    if manual_review_reason_code is not None:
        request_job_manual_review(
            session,
            job,
            reason_code=manual_review_reason_code,
            error=message,
            source="handler_error",
        )
    elif force_terminal or job.attempts >= job.max_attempts:
        job.status = "failed"
    else:
        job.status = "retry"
        delay_seconds = (
            min(300, max(1, retry_after_seconds))
            if retry_after_seconds is not None
            else min(300, 2 ** max(0, job.attempts - 1) * 5)
        )
        job.run_at = utcnow() + timedelta(seconds=delay_seconds)
    session.flush()
    return job
