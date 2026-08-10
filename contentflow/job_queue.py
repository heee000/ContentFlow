from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .entities import Job


class JobLeaseLost(RuntimeError):
    """Raised when a worker no longer owns the job attempt it is finishing."""


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
    if existing:
        return existing

    job = Job(
        job_type=job_type,
        payload_json=payload,
        workspace_id=workspace_id,
        idempotency_key=key,
        run_at=run_at or utcnow(),
        max_attempts=max_attempts,
    )
    session.add(job)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        existing = session.scalar(select(Job).where(Job.idempotency_key == key))
        if existing:
            return existing
        raise
    return job


def claim_next_job(
    session: Session,
    *,
    worker_id: str,
    lease_seconds: int,
) -> Job | None:
    now = utcnow()
    lease_expired = now - timedelta(seconds=lease_seconds)
    query = (
        select(Job)
        .where(
            Job.run_at <= now,
            Job.attempts < Job.max_attempts,
            or_(
                Job.status.in_(["queued", "retry"]),
                (Job.status == "running") & (Job.locked_at < lease_expired),
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
    job.status = "running"
    job.locked_by = worker_id
    job.locked_at = now
    job.attempts += 1
    session.flush()
    return job


def renew_job_lease(
    session: Session,
    *,
    job_id: str,
    worker_id: str,
    attempt: int,
) -> bool:
    outcome = session.execute(
        update(Job)
        .where(
            Job.id == job_id,
            Job.status == "running",
            Job.locked_by == worker_id,
            Job.attempts == attempt,
        )
        .values(locked_at=utcnow())
        .execution_options(synchronize_session=False)
    )
    return outcome.rowcount == 1


def _get_claimed_job(
    session: Session,
    *,
    job_id: str,
    worker_id: str,
    attempt: int,
) -> Job:
    query = select(Job).where(
        Job.id == job_id,
        Job.status == "running",
        Job.locked_by == worker_id,
        Job.attempts == attempt,
    )
    if session.bind and session.bind.dialect.name == "postgresql":
        query = query.with_for_update()
    job = session.scalar(query.execution_options(populate_existing=True))
    if job is None:
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
) -> None:
    job = _get_claimed_job(
        session,
        job_id=job.id,
        worker_id=worker_id,
        attempt=attempt,
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
    force_terminal: bool = False,
    retry_after_seconds: int | None = None,
) -> Job:
    job = _get_claimed_job(
        session,
        job_id=job.id,
        worker_id=worker_id,
        attempt=attempt,
    )
    message = str(error)
    job.last_error = message[:8000]
    job.locked_by = None
    job.locked_at = None
    if force_terminal or job.attempts >= job.max_attempts:
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
