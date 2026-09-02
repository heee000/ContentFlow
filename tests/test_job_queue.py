from __future__ import annotations

import logging
import tempfile
import threading
import unittest
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import ANY, patch

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
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
    DatabaseErrorKind,
    LeaseHeartbeat,
    Worker,
    WorkerDatabaseUnavailable,
    WorkerNodeHeartbeat,
    classify_database_error,
    configure_worker_logging,
    database_error_sqlstate,
    logger as worker_logger,
    sanitized_database_error,
)


class PostgresTestError(Exception):
    def __init__(self, sqlstate: str, message: str = "sensitive database detail"):
        super().__init__(message)
        self.sqlstate = sqlstate


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

    @staticmethod
    def _database_unavailable_error() -> OperationalError:
        return OperationalError(
            "SELECT redacted",
            {},
            OSError("postgresql://sensitive-host database unavailable"),
        )

    @staticmethod
    def _postgres_error(
        sqlstate: str,
        *,
        connection_invalidated: bool = False,
    ) -> OperationalError:
        return OperationalError(
            "SELECT secret_column FROM private_table",
            {"token": "sensitive-parameter"},
            PostgresTestError(sqlstate),
            connection_invalidated=connection_invalidated,
        )

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

    def test_idle_worker_throttles_maintenance_sweeps(self):
        settings = Settings(
            database_url="sqlite://",
            secret_key="storage-sweep-throttle-test-secret",
            local_storage_dir=Path(self.temp_dir.name) / "storage",
            storage_reconcile_schedule_poll_seconds=60,
        )
        worker = Worker(
            settings=settings,
            worker_id="storage-sweep-throttle-worker",
            session_factory=self.session_factory,
        )

        with patch(
            "contentflow.worker.schedule_due_storage_reconciliations",
            return_value=0,
        ) as storage_sweep, patch(
            "contentflow.worker.schedule_pending_publish_reconciliations",
            return_value=0,
        ) as publish_sweep:
            self.assertFalse(worker.run_once())
            self.assertFalse(worker.run_once())

        storage_sweep.assert_called_once_with(
            ANY,
            settings=settings,
        )
        publish_sweep.assert_called_once_with(
            ANY,
            settings=settings,
        )

    def test_database_outage_during_handler_is_not_recorded_as_domain_failure(self):
        with self.session_factory() as session:
            queued = enqueue_job(
                session,
                job_type="test.database-outage",
                payload={},
                workspace_id=None,
                idempotency_key="test.database-outage",
            )
            session.commit()
            job_id = queued.id

        def unavailable_handler(_session, _payload, _settings):
            raise self._database_unavailable_error()

        worker = Worker(
            settings=Settings(
                database_url="sqlite://",
                secret_key="database-outage-test-secret",
                local_storage_dir=Path(self.temp_dir.name) / "storage",
            ),
            worker_id="database-outage-worker",
            session_factory=self.session_factory,
            handlers={"test.database-outage": unavailable_handler},
        )

        with self.assertRaises(OperationalError):
            worker.run_once()

        with self.session_factory() as session:
            claimed = session.get(Job, job_id)
            self.assertEqual(claimed.status, "running")
            self.assertEqual(claimed.attempts, 1)
            self.assertIsNone(claimed.last_error)

    def test_postgres_sqlstate_classifier_separates_recovery_categories(self):
        expected = {
            "08006": DatabaseErrorKind.AVAILABILITY,
            "57P01": DatabaseErrorKind.AVAILABILITY,
            "53300": DatabaseErrorKind.AVAILABILITY,
            "40001": DatabaseErrorKind.TRANSACTION_RETRYABLE,
            "40P01": DatabaseErrorKind.TRANSACTION_RETRYABLE,
            "55P03": DatabaseErrorKind.LOCK_CONTENTION,
            "57014": DatabaseErrorKind.QUERY_INTERRUPTED,
            "28P01": DatabaseErrorKind.PERMANENT,
            "42P01": DatabaseErrorKind.PERMANENT,
        }

        for sqlstate, kind in expected.items():
            with self.subTest(sqlstate=sqlstate):
                error = self._postgres_error(sqlstate)
                self.assertEqual(database_error_sqlstate(error), sqlstate)
                self.assertEqual(classify_database_error(error), kind)

        self.assertEqual(
            classify_database_error(self._database_unavailable_error()),
            DatabaseErrorKind.AVAILABILITY,
        )
        self.assertEqual(
            classify_database_error(
                self._postgres_error("57P05", connection_invalidated=True)
            ),
            DatabaseErrorKind.AVAILABILITY,
        )

    def test_database_error_summary_never_contains_sql_parameters_or_driver_text(self):
        error = self._postgres_error("40001")

        summary = sanitized_database_error(error)

        self.assertIn("kind=transaction_retryable", summary)
        self.assertIn("sqlstate=40001", summary)
        self.assertIn("error_type=OperationalError", summary)
        self.assertNotIn("secret_column", summary)
        self.assertNotIn("sensitive-parameter", summary)
        self.assertNotIn("sensitive database detail", summary)

    def test_transaction_conflict_requeues_job_with_sanitized_error(self):
        with self.session_factory() as session:
            queued = enqueue_job(
                session,
                job_type="test.transaction-conflict",
                payload={},
                workspace_id=None,
                idempotency_key="test.transaction-conflict",
            )
            session.commit()
            job_id = queued.id

        def conflicting_handler(_session, _payload, _settings):
            raise self._postgres_error("40001")

        worker = Worker(
            settings=Settings(
                database_url="sqlite://",
                secret_key="transaction-conflict-test-secret",
                local_storage_dir=Path(self.temp_dir.name) / "storage",
            ),
            worker_id="transaction-conflict-worker",
            session_factory=self.session_factory,
            handlers={"test.transaction-conflict": conflicting_handler},
        )

        with (
            patch.object(worker_logger, "disabled", False),
            self.assertLogs(worker_logger, level=logging.ERROR) as captured,
        ):
            self.assertTrue(worker.run_once())

        with self.session_factory() as session:
            current = session.get(Job, job_id)
            self.assertEqual(current.status, "retry")
            self.assertEqual(current.attempts, 1)
            self.assertIn("kind=transaction_retryable", current.last_error)
            self.assertIn("sqlstate=40001", current.last_error)
            self.assertNotIn("secret_column", current.last_error)
            self.assertNotIn("sensitive-parameter", current.last_error)
        messages = "\n".join(captured.output)
        self.assertIn("kind=transaction_retryable", messages)
        self.assertNotIn("sensitive database detail", messages)

    def test_permanent_database_error_fails_job_without_retry(self):
        with self.session_factory() as session:
            queued = enqueue_job(
                session,
                job_type="test.permanent-database-error",
                payload={},
                workspace_id=None,
                idempotency_key="test.permanent-database-error",
            )
            session.commit()
            job_id = queued.id

        def invalid_database_handler(_session, _payload, _settings):
            raise self._postgres_error("28P01")

        worker = Worker(
            settings=Settings(
                database_url="sqlite://",
                secret_key="permanent-database-error-test-secret",
                local_storage_dir=Path(self.temp_dir.name) / "storage",
            ),
            worker_id="permanent-database-error-worker",
            session_factory=self.session_factory,
            handlers={"test.permanent-database-error": invalid_database_handler},
        )

        self.assertTrue(worker.run_once())

        with self.session_factory() as session:
            current = session.get(Job, job_id)
            self.assertEqual(current.status, "failed")
            self.assertEqual(current.attempts, 1)
            self.assertIn("kind=permanent", current.last_error)
            self.assertIn("sqlstate=28P01", current.last_error)
            self.assertNotIn("sensitive database detail", current.last_error)

    def test_worker_retries_database_outage_then_recovers(self):
        settings = Settings(
            database_url="sqlite://",
            secret_key="database-recovery-test-secret",
            local_storage_dir=Path(self.temp_dir.name) / "storage",
            worker_database_retry_initial_seconds=0.1,
            worker_database_retry_max_seconds=0.1,
            worker_database_retry_max_attempts=2,
            worker_database_retry_jitter_ratio=0,
        )
        worker = Worker(
            settings=settings,
            worker_id="database-recovery-worker",
            session_factory=self.session_factory,
        )
        worker._next_storage_reconciliation_sweep_at = 123.0
        worker._next_publish_reconciliation_sweep_at = 456.0
        calls = 0

        def flaky_run_once():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise self._database_unavailable_error()
            worker.request_stop()
            return False

        with (
            patch.object(worker_logger, "disabled", False),
            patch.object(worker, "run_once", side_effect=flaky_run_once),
            self.assertLogs(worker_logger, level=logging.INFO) as captured,
        ):
            worker.run_forever()

        self.assertEqual(calls, 2)
        self.assertEqual(worker._next_storage_reconciliation_sweep_at, 0.0)
        self.assertEqual(worker._next_publish_reconciliation_sweep_at, 0.0)
        messages = "\n".join(captured.output)
        self.assertIn("database operation unavailable", messages)
        self.assertIn("database operation recovered", messages)
        self.assertNotIn("sensitive-host", messages)

    def test_worker_retries_transient_sqlstate_but_not_permanent_sqlstate(self):
        settings = Settings(
            database_url="sqlite://",
            secret_key="sqlstate-worker-recovery-test-secret",
            local_storage_dir=Path(self.temp_dir.name) / "storage",
            worker_database_retry_initial_seconds=0.1,
            worker_database_retry_max_seconds=0.1,
            worker_database_retry_max_attempts=2,
            worker_database_retry_jitter_ratio=0,
        )
        recovering_worker = Worker(
            settings=settings,
            worker_id="sqlstate-recovery-worker",
            session_factory=self.session_factory,
        )

        recovery_calls = 0

        def flaky_sqlstate_run_once():
            nonlocal recovery_calls
            recovery_calls += 1
            if recovery_calls == 1:
                raise self._postgres_error("40P01")
            recovering_worker.request_stop()
            return False

        with (
            patch.object(worker_logger, "disabled", False),
            patch.object(
                recovering_worker,
                "run_once",
                side_effect=flaky_sqlstate_run_once,
            ) as run_once,
            self.assertLogs(worker_logger, level=logging.INFO) as captured,
        ):
            recovering_worker.run_forever()

        self.assertEqual(run_once.call_count, 2)
        messages = "\n".join(captured.output)
        self.assertIn("database operation retryable", messages)
        self.assertIn("kind=transaction_retryable", messages)
        self.assertIn("sqlstate=40P01", messages)
        self.assertNotIn("sensitive database detail", messages)

        permanent_worker = Worker(
            settings=settings,
            worker_id="sqlstate-permanent-worker",
            session_factory=self.session_factory,
        )
        permanent_error = self._postgres_error("28P01")
        with patch.object(
            permanent_worker,
            "run_once",
            side_effect=permanent_error,
        ) as run_once, self.assertRaises(OperationalError):
            permanent_worker.run_forever()
        self.assertEqual(run_once.call_count, 1)

    def test_worker_database_retry_delay_is_exponential_capped_and_jittered(self):
        worker = Worker(
            settings=Settings(
                database_url="sqlite://",
                secret_key="database-delay-test-secret",
                local_storage_dir=Path(self.temp_dir.name) / "storage",
                worker_database_retry_initial_seconds=1,
                worker_database_retry_max_seconds=5,
                worker_database_retry_max_attempts=8,
                worker_database_retry_jitter_ratio=0.2,
            ),
            session_factory=self.session_factory,
        )

        with patch("contentflow.worker.random.uniform", return_value=0):
            self.assertEqual(
                [worker._database_retry_delay(attempt) for attempt in range(1, 6)],
                [1, 2, 4, 5, 5],
            )
        with patch("contentflow.worker.random.uniform", return_value=-0.2):
            self.assertEqual(worker._database_retry_delay(1), 0.8)
        with patch("contentflow.worker.random.uniform", return_value=0.2):
            self.assertEqual(worker._database_retry_delay(1), 1.2)

    def test_worker_node_database_error_log_is_redacted(self):
        def unavailable_session_factory():
            raise self._database_unavailable_error()

        heartbeat = WorkerNodeHeartbeat(
            session_factory=unavailable_session_factory,
            worker_id="redacted-heartbeat-worker",
            interval_seconds=10,
        )

        with (
            patch.object(worker_logger, "disabled", False),
            self.assertLogs(worker_logger, level=logging.ERROR) as captured,
        ):
            self.assertFalse(heartbeat.pulse())

        messages = "\n".join(captured.output)
        self.assertIn("heartbeat database unavailable", messages)
        self.assertNotIn("sensitive-host", messages)

    def test_worker_database_retry_wait_is_interruptible(self):
        settings = Settings(
            database_url="sqlite://",
            secret_key="database-stop-test-secret",
            local_storage_dir=Path(self.temp_dir.name) / "storage",
            worker_database_retry_initial_seconds=30,
            worker_database_retry_max_seconds=30,
            worker_database_retry_max_attempts=2,
            worker_database_retry_jitter_ratio=0,
        )
        worker = Worker(
            settings=settings,
            worker_id="database-stop-worker",
            session_factory=self.session_factory,
        )
        attempted = threading.Event()

        def unavailable_run_once():
            attempted.set()
            raise self._database_unavailable_error()

        with patch.object(worker, "run_once", side_effect=unavailable_run_once):
            thread = threading.Thread(target=worker.run_forever, daemon=True)
            thread.start()
            self.assertTrue(attempted.wait(timeout=1))
            started = time.monotonic()
            worker.request_stop(15)
            thread.join(timeout=1)

        self.assertFalse(thread.is_alive())
        self.assertLess(time.monotonic() - started, 1)

    def test_worker_exits_after_bounded_database_retries(self):
        settings = Settings(
            database_url="sqlite://",
            secret_key="database-exhaustion-test-secret",
            local_storage_dir=Path(self.temp_dir.name) / "storage",
            worker_database_retry_initial_seconds=0.1,
            worker_database_retry_max_seconds=0.1,
            worker_database_retry_max_attempts=1,
            worker_database_retry_jitter_ratio=0,
        )
        worker = Worker(
            settings=settings,
            worker_id="database-exhaustion-worker",
            session_factory=self.session_factory,
        )

        with (
            patch.object(worker_logger, "disabled", False),
            patch.object(
                worker,
                "run_once",
                side_effect=self._database_unavailable_error(),
            ) as run_once,
            self.assertLogs(worker_logger, level=logging.ERROR) as captured,
            self.assertRaisesRegex(
                WorkerDatabaseUnavailable,
                "retry budget exhausted",
            ),
        ):
            worker.run_forever()

        self.assertEqual(run_once.call_count, 2)
        self.assertNotIn("sensitive-host", "\n".join(captured.output))

    def test_worker_does_not_retry_non_availability_database_error(self):
        worker = Worker(
            settings=Settings(
                database_url="sqlite://",
                secret_key="database-integrity-test-secret",
                local_storage_dir=Path(self.temp_dir.name) / "storage",
            ),
            worker_id="database-integrity-worker",
            session_factory=self.session_factory,
        )
        integrity_error = IntegrityError(
            "INSERT redacted",
            {},
            ValueError("constraint failed"),
        )
        self.assertEqual(
            classify_database_error(integrity_error),
            DatabaseErrorKind.PERMANENT,
        )

        with patch.object(
            worker,
            "run_once",
            side_effect=integrity_error,
        ) as run_once, self.assertRaises(IntegrityError):
            worker.run_forever()

        self.assertEqual(run_once.call_count, 1)

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
