"""Real PostgreSQL lock ordering, revocation and post-wait expiry gates."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Event
import time
import uuid

import pytest
from sqlalchemy import select, text

from contentflow.entities import Job
from contentflow.execution_fence import ExecutionFence, JobLeaseLost, execution_scope
from contentflow.job_queue import renew_job_lease
import test_postgres_integration as pg

postgres_harness = pg.postgres_harness
pytestmark = pytest.mark.skipif(not pg.TEST_DATABASE_URL, reason="Dedicated PostgreSQL test URL is required")


def seed(h, lease_seconds=60):
    with h.sessions() as session:
        owner = Job(job_type="test.fence", status="running", locked_by="test-worker", attempts=1,
            locked_at=datetime.now(timezone.utc), idempotency_key=str(uuid.uuid4()), payload_json={})
        marker = Job(job_type="test.marker", status="succeeded", idempotency_key=str(uuid.uuid4()), payload_json={})
        session.add_all([owner, marker])
        session.commit()
        fence = ExecutionFence(bind=h.engine, job_id=owner.id, workspace_id=None, worker_id="test-worker",
            attempt=1, lease_token=owner.lease_token, lease_seconds=lease_seconds, heartbeat_lost=lambda: False)
        return owner.id, marker.id, fence, owner.locked_at


def wait_for_database_lock(h, pid):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with h.engine.connect() as connection:
            event = connection.scalar(text("SELECT wait_event_type FROM pg_stat_activity WHERE pid=:pid"), {"pid": pid})
        if event == "Lock":
            return
        Event().wait(0.02)
    raise AssertionError("The competing statement never reached a PostgreSQL lock wait")


def test_postgres_revocation_wins_against_cached_domain_state(postgres_harness):
    h = postgres_harness
    owner_id, marker_id, fence, _ = seed(h)

    def revoke():
        with h.sessions() as session:
            session.get(Job, owner_id).lease_token = uuid.uuid4().hex
            session.get(Job, marker_id).result_json = {"winner": "new"}
            session.commit()

    with h.sessions() as stale, execution_scope(fence):
        cached = stale.get(Job, marker_id)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(revoke).result(timeout=10)
        cached.result_json = {"winner": "old"}
        with pytest.raises(JobLeaseLost):
            stale.commit()
        stale.rollback()
    with h.sessions() as session:
        assert session.get(Job, marker_id).result_json == {"winner": "new"}


def test_postgres_domain_commit_and_revocation_serialize_on_claim(postgres_harness):
    h = postgres_harness
    owner_id, marker_id, fence, _ = seed(h)
    write_locked, release_write, revoke_started = Event(), Event(), Event()
    observed = {}

    def write():
        with h.sessions() as session, execution_scope(fence):
            session.get(Job, marker_id).result_json = {"winner": "old-before-revocation"}
            session.flush()
            write_locked.set()
            assert release_write.wait(timeout=15)
            session.commit()

    def revoke():
        assert write_locked.wait(timeout=10)
        with h.sessions() as session:
            session.execute(text("SET statement_timeout = '15s'"))
            observed["pid"] = session.scalar(text("SELECT pg_backend_pid()"))
            revoke_started.set()
            owner = session.scalar(select(Job).where(Job.id == owner_id).with_for_update())
            owner.lease_token = uuid.uuid4().hex
            assert session.get(Job, marker_id).result_json == {"winner": "old-before-revocation"}
            session.get(Job, marker_id).result_json = {"winner": "new"}
            session.commit()

    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(write)
        revoker = pool.submit(revoke)
        try:
            assert revoke_started.wait(timeout=10)
            wait_for_database_lock(h, observed["pid"])
        finally:
            release_write.set()
        writer.result(timeout=15)
        revoker.result(timeout=15)
    with h.sessions() as session:
        assert session.get(Job, marker_id).result_json == {"winner": "new"}


@pytest.mark.parametrize("action", ["domain_write", "heartbeat"])
def test_postgres_lock_wait_cannot_extend_expired_authority(postgres_harness, action):
    h = postgres_harness
    owner_id, marker_id, fence, locked_at = seed(h, lease_seconds=3)
    started = Event()
    observed = {}

    def late_statement():
        with h.sessions() as session:
            session.execute(text("SET statement_timeout = '15s'"))
            observed["pid"] = session.scalar(text("SELECT pg_backend_pid()"))
            if action == "heartbeat":
                started.set()
                assert not renew_job_lease(session, job_id=owner_id, worker_id=fence.worker_id,
                    attempt=1, lease_seconds=3, lease_token=fence.lease_token)
            else:
                with execution_scope(fence):
                    session.get(Job, marker_id).result_json = {"stale": True}
                    started.set()
                    with pytest.raises(JobLeaseLost):
                        session.commit()
            session.rollback()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with h.sessions() as blocker:
            blocker.scalar(select(Job).where(Job.id == owner_id).with_for_update())
            future = pool.submit(late_statement)
            try:
                assert started.wait(timeout=10)
                wait_for_database_lock(h, observed["pid"])
                remaining = max(0, 3 - (datetime.now(timezone.utc) - locked_at).total_seconds())
                Event().wait(remaining + 0.05)
            finally:
                blocker.rollback()
        future.result(timeout=15)
    with h.sessions() as session:
        assert session.get(Job, marker_id).result_json == {}
        assert session.get(Job, owner_id).locked_at == locked_at
