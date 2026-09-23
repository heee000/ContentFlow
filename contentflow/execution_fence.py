"""Attempt-scoped worker fencing, separate from provider outcome evidence.

Checks cannot recall a remote request already sent. Business writes serialize
against claim/recovery on the Job row; late provider evidence remains writable.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import wraps

from sqlalchemy import event, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from .entities import Job


class JobLeaseLost(RuntimeError):
    """The claimed worker attempt no longer has authority to act."""


PROVIDER_EVIDENCE_SESSION = "contentflow_provider_evidence"


@dataclass(frozen=True)
class ExecutionFence:
    bind: Engine
    job_id: str
    workspace_id: str | None
    worker_id: str
    attempt: int
    lease_token: str
    lease_seconds: int
    heartbeat_lost: Callable[[], bool]

    def lost(self) -> JobLeaseLost:
        return JobLeaseLost(
            f"Job execution authority lost: id={self.job_id} "
            f"worker={self.worker_id} attempt={self.attempt}"
        )

    def conditions(self):
        return (
            Job.id == self.job_id,
            Job.workspace_id == self.workspace_id,
            Job.status == "running",
            Job.locked_by == self.worker_id,
            Job.attempts == self.attempt,
            Job.lease_token == self.lease_token,
            Job.locked_at >= datetime.now(timezone.utc) - timedelta(seconds=self.lease_seconds),
        )

    def check(self) -> None:
        if not self.lease_token or self.heartbeat_lost():
            raise self.lost()
        # A separate read avoids the handler's identity map and transaction
        # snapshot. Do not renew an expired lease just because a check runs.
        with Session(self.bind) as session:
            locked_at = session.scalar(select(Job.locked_at).where(*self.conditions()))
            if not self.timestamp_is_live(locked_at):
                raise self.lost()
        if self.heartbeat_lost():
            raise self.lost()

    def timestamp_is_live(self, value: datetime | None) -> bool:
        if value is None:
            return False
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value >= datetime.now(timezone.utc) - timedelta(seconds=self.lease_seconds)

    def fence_write(self, session: Session) -> None:
        if not self.lease_token or self.heartbeat_lost() or session.get_bind() is not self.bind:
            raise self.lost()
        # Conditional no-op UPDATE takes the row/write lock in this same
        # transaction. A SELECT-only check followed by a write has a TOCTOU gap.
        # Core connection avoids autoflush/event recursion and leaves ORM state
        # untouched. Neither timestamp nor lease is extended here.
        result = session.connection().execute(
            update(Job.__table__).where(*self.conditions())
            .values(locked_at=Job.locked_at, updated_at=Job.updated_at)
            .returning(Job.locked_at)
        )
        # The lock may have taken time to acquire: a bound cutoff calculated
        # before waiting is insufficient. Recheck the returned lease at now.
        locked_at = result.scalar_one_or_none()
        if not self.timestamp_is_live(locked_at) or self.heartbeat_lost():
            raise self.lost()


_execution: ContextVar[ExecutionFence | None] = ContextVar("contentflow_execution", default=None)


@contextmanager
def execution_scope(fence: ExecutionFence) -> Iterator[None]:
    token = _execution.set(fence)
    try:
        fence.check()
        yield
    finally:
        _execution.reset(token)


def assert_execution_active() -> None:
    fence = _execution.get()
    if fence is not None:
        fence.check()


def guarded_operation(function):
    """Check before one storage/SDK operation; an in-flight operation may finish."""
    @wraps(function)
    def guarded(*args, **kwargs):
        assert_execution_active()
        return function(*args, **kwargs)
    return guarded


def execution_is_stale() -> bool:
    try:
        assert_execution_active()
    except JobLeaseLost:
        return True
    return False


def fence_domain_write(session: Session) -> None:
    fence = _execution.get()
    if fence is not None:
        fence.fence_write(session)


def _guard_session(session: Session) -> None:
    # Only the provider ledger uses this explicit evidence-only session. Its
    # start() is fenced separately; finish() must record late/unknown outcomes.
    if not session.info.get(PROVIDER_EVIDENCE_SESSION):
        fence_domain_write(session)


@event.listens_for(Session, "before_flush")
def _before_flush(session, _flush_context, _instances):
    _guard_session(session)


@event.listens_for(Session, "before_commit")
def _before_commit(session):
    # Recheck even if the handler already flushed, or used a progress session.
    _guard_session(session)


@event.listens_for(Session, "do_orm_execute")
def _before_execute(state):
    # Core DML and text SQL through Session.execute do not emit before_flush.
    # Conservative for text SQL (including reads): no unguarded raw DML path.
    if not state.is_select and not state.execution_options.get("contentflow_readonly"):
        _guard_session(state.session)
