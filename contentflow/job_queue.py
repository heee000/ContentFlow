from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .entities import Job


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


def complete_job(session: Session, job: Job, result: dict[str, Any]) -> None:
    job.status = "succeeded"
    job.result_json = result
    job.last_error = None
    job.locked_by = None
    job.locked_at = None
    session.flush()


def fail_job(session: Session, job: Job, error: Exception | str) -> None:
    message = str(error)
    job.last_error = message[:8000]
    job.locked_by = None
    job.locked_at = None
    if job.attempts >= job.max_attempts:
        job.status = "failed"
    else:
        job.status = "retry"
        delay_seconds = min(300, 2 ** max(0, job.attempts - 1) * 5)
        job.run_at = utcnow() + timedelta(seconds=delay_seconds)
    session.flush()

