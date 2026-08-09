from __future__ import annotations

import logging
import tempfile
import threading
import unittest
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from contentflow.db import Base, build_engine
from contentflow.entities import Job, WorkerNode
from contentflow.job_queue import (
    JobLeaseLost,
    claim_next_job,
    complete_job,
    enqueue_job,
    renew_job_lease,
)
from contentflow.settings import Settings
from contentflow.worker import (
    LeaseHeartbeat,
    Worker,
    WorkerNodeHeartbeat,
    configure_worker_logging,
    logger as worker_logger,
)


class JobQueueLeaseTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_dir.name) / "queue.db"
        self.engine = build_engine(f"sqlite:///{database_path.as_posix()}")
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
            future=True,
        )

    def tearDown(self):
        self.engine.dispose()
        self.temp_dir.cleanup()

    def _claim_job(self) -> tuple[str, int]:
        with self.session_factory() as session:
            job = enqueue_job(
                session,
                job_type="test.lease",
                payload={"value": 1},
                workspace_id=None,
            )
            session.commit()
            job_id = job.id

        with self.session_factory() as session:
            job = claim_next_job(
                session,
                worker_id="worker-a",
                lease_seconds=30,
            )
            self.assertIsNotNone(job)
            attempt = job.attempts
            session.commit()
        return job_id, attempt

    def test_lease_renewal_requires_current_owner_and_attempt(self):
        job_id, attempt = self._claim_job()

        with self.session_factory() as session:
            renewed = renew_job_lease(
                session,
                job_id=job_id,
                worker_id="worker-a",
                attempt=attempt,
            )
            self.assertTrue(renewed)
            session.commit()

        with self.session_factory() as session:
            current = session.get(Job, job_id)
            self.assertIsNotNone(current.locked_at)
            self.assertLessEqual(
                current.locked_at, datetime.now(timezone.utc).replace(tzinfo=None)
            )

        with self.session_factory() as session:
            self.assertFalse(
                renew_job_lease(
                    session,
                    job_id=job_id,
                    worker_id="worker-b",
                    attempt=attempt,
                )
            )
            session.rollback()

    def test_heartbeat_renews_active_job_in_independent_session(self):
        job_id, attempt = self._claim_job()
        with self.session_factory() as session:
            before = session.get(Job, job_id).locked_at
        self.assertIsNotNone(before)

        with LeaseHeartbeat(
            session_factory=self.session_factory,
            job_id=job_id,
            worker_id="worker-a",
            attempt=attempt,
            lease_seconds=3,
        ) as heartbeat:
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                with self.session_factory() as session:
                    renewed_at = session.get(Job, job_id).locked_at
                if renewed_at > before:
                    break
                time.sleep(0.05)
            else:
                self.fail("heartbeat did not renew the lease before the deadline")
        self.assertFalse(heartbeat.lost)

    def test_worker_node_heartbeat_records_online_updates_and_stop(self):
        with WorkerNodeHeartbeat(
            session_factory=self.session_factory,
            worker_id="node-heartbeat-worker",
            interval_seconds=0.05,
            hostname="test-host",
            process_id=123,
        ):
            with self.session_factory() as session:
                node = session.get(WorkerNode, "node-heartbeat-worker")
                self.assertEqual(node.status, "online")
                self.assertEqual(node.hostname, "test-host")
                self.assertEqual(node.process_id, 123)
                before = node.heartbeat_at

            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                with self.session_factory() as session:
                    renewed_at = session.get(
                        WorkerNode, "node-heartbeat-worker"
                    ).heartbeat_at
                if renewed_at > before:
                    break
                time.sleep(0.02)
            else:
                self.fail("worker node heartbeat did not advance")

        with self.session_factory() as session:
            stopped = session.get(WorkerNode, "node-heartbeat-worker")
            self.assertEqual(stopped.status, "stopped")
            self.assertIsNotNone(stopped.stopped_at)

    def test_idle_worker_stop_wakes_long_poll(self):
        settings = Settings(
            database_url="sqlite://",
            secret_key="graceful-shutdown-test-secret",
            local_storage_dir=Path(self.temp_dir.name) / "storage",
            worker_poll_seconds=30,
            worker_lease_seconds=3,
        )
        worker = Worker(
            settings=settings,
            worker_id="idle-worker",
            session_factory=self.session_factory,
        )
        thread = threading.Thread(target=worker.run_forever, daemon=True)
        thread.start()
        time.sleep(0.05)

        started = time.monotonic()
        worker.request_stop(15)
        thread.join(timeout=1)

        self.assertFalse(thread.is_alive())
        self.assertTrue(worker.stop_requested)
        self.assertLess(time.monotonic() - started, 1)
        with self.session_factory() as session:
            node = session.get(WorkerNode, "idle-worker")
            self.assertEqual(node.status, "stopped")
            self.assertIsNotNone(node.stopped_at)

    def test_worker_logging_is_reenabled_after_migrations(self):
        previous_disabled = worker_logger.disabled
        previous_level = worker_logger.level
        try:
            worker_logger.disabled = True
            worker_logger.setLevel(logging.WARNING)
            with patch("contentflow.worker.logging.basicConfig") as basic_config:
                configure_worker_logging()

            basic_config.assert_called_once_with(level=logging.INFO, force=True)
            self.assertFalse(worker_logger.disabled)
            self.assertEqual(worker_logger.level, logging.INFO)
        finally:
            worker_logger.disabled = previous_disabled
            worker_logger.setLevel(previous_level)

    def test_worker_finishes_inflight_job_without_claiming_another(self):
        started = threading.Event()
        release = threading.Event()

        def drain_handler(_session, payload, _settings):
            started.set()
            if not release.wait(timeout=2):
                raise TimeoutError("test did not release the in-flight handler")
            return {"value": payload["value"]}

        settings = Settings(
            database_url="sqlite://",
            secret_key="graceful-shutdown-test-secret",
            local_storage_dir=Path(self.temp_dir.name) / "storage",
            worker_poll_seconds=30,
            worker_lease_seconds=3,
        )
        with self.session_factory() as session:
            for value in (1, 2):
                enqueue_job(
                    session,
                    job_type="test.drain",
                    payload={"value": value},
                    workspace_id=None,
                    idempotency_key=f"test.drain:{value}",
                )
            session.commit()

        worker = Worker(
            settings=settings,
            worker_id="draining-worker",
            session_factory=self.session_factory,
            handlers={"test.drain": drain_handler},
        )
        thread = threading.Thread(target=worker.run_forever, daemon=True)
        thread.start()
        self.assertTrue(started.wait(timeout=1))

        worker.request_stop(15)
        release.set()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())

        with self.session_factory() as session:
            jobs = list(
                session.scalars(
                    select(Job)
                    .where(Job.job_type == "test.drain")
                    .order_by(Job.created_at)
                )
            )
        self.assertEqual(
            sorted(job.status for job in jobs),
            ["queued", "succeeded"],
        )
        succeeded = next(job for job in jobs if job.status == "succeeded")
        queued = next(job for job in jobs if job.status == "queued")
        self.assertIn(succeeded.result_json["value"], {1, 2})
        self.assertEqual(queued.attempts, 0)
        self.assertIsNone(queued.locked_by)

    def test_stale_worker_cannot_complete_reclaimed_attempt(self):
        job_id, attempt = self._claim_job()

        with self.session_factory() as session:
            current = session.get(Job, job_id)
            current.locked_by = "worker-b"
            current.attempts = attempt + 1
            session.commit()

        with self.session_factory() as session:
            stale_view = session.get(Job, job_id)
            with self.assertRaises(JobLeaseLost):
                complete_job(
                    session,
                    stale_view,
                    {"unsafe": True},
                    worker_id="worker-a",
                    attempt=attempt,
                )
            session.rollback()

        with self.session_factory() as session:
            current = session.get(Job, job_id)
            self.assertEqual(current.status, "running")
            self.assertEqual(current.locked_by, "worker-b")
            self.assertEqual(current.attempts, attempt + 1)
            self.assertEqual(current.result_json, {})


if __name__ == "__main__":
    unittest.main()
