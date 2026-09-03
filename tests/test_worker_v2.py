from __future__ import annotations

import tempfile
import unittest
import zipfile
import uuid
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from contentflow import db
from contentflow.api import create_app
from contentflow.connectors import ConnectorPublishError, ConnectorResult
from contentflow.entities import (
    Asset,
    AuditLog,
    Campaign,
    ChannelConnection,
    ContentItem,
    Job,
    KnowledgeDocument,
    PublishJob,
    User,
    WorkspaceStorageUsage,
    WorkflowRun,
)
from contentflow.settings import Settings
from contentflow.worker import (
    Worker,
    handle_publish_dispatch,
    handle_publish_reconcile,
    mark_domain_failure,
    publish_reconciliation_job_key,
    schedule_pending_publish_reconciliations,
)


class WorkerIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.settings = Settings(
            _env_file=None,
            environment="development",
            database_url=f"sqlite:///{(root / 'worker.db').as_posix()}",
            secret_key="worker-test-secret",
            local_storage_dir=root / "storage",
            storage_backend="local",
            allow_registration=True,
            require_governed_prompts=False,
            metrics_enabled=False,
            embedding_provider="hash",
            text_provider="mock",
            image_provider="mock",
            video_provider="mock",
            publish_reconciliation_initial_delay_seconds=1,
            publish_reconciliation_max_attempts=2,
        )
        self.client = TestClient(create_app(self.settings))
        self.client.__enter__()
        registered = self.client.post(
            "/api/v1/auth/register",
            json={
                "email": "worker@example.com",
                "password": "a-secure-password",
                "display_name": "Worker Owner",
                "workspace_name": "Worker Workspace",
            },
        )
        self.assertEqual(registered.status_code, 201, registered.text)
        self.headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
        self.workspace_id = registered.json()["workspace_id"]
        self.worker = Worker(
            settings=self.settings,
            session_factory=db.SessionLocal,
            worker_id="integration-worker",
        )

    def tearDown(self):
        self.client.__exit__(None, None, None)
        db.engine.dispose()
        self.temp_dir.cleanup()

    def test_worker_runs_due_storage_reconciliation_in_report_only_mode(self):
        stale_at = datetime.now(timezone.utc) - timedelta(hours=25)
        with db.SessionLocal() as session:
            usage = session.get(WorkspaceStorageUsage, self.workspace_id)
            self.assertIsNotNone(usage)
            self.assertIsNotNone(usage.last_reconciled_at)
            usage.last_reconciled_at = stale_at
            session.commit()

        self.assertTrue(self.worker.run_once())

        with db.SessionLocal() as session:
            job = session.scalar(
                select(Job).where(Job.job_type == "storage.reconcile")
            )
            self.assertIsNotNone(job)
            self.assertEqual(job.status, "succeeded")
            self.assertEqual(job.payload_json["trigger"], "scheduled")
            self.assertFalse(job.payload_json["delete_orphans"])
            usage = session.get(WorkspaceStorageUsage, self.workspace_id)
            self.assertIsNotNone(usage)
            self.assertGreater(
                usage.last_reconciled_at.replace(tzinfo=timezone.utc),
                stale_at,
            )

    def _create_publish_fixture(
        self,
        *,
        status: str,
        external_id: str | None = None,
    ) -> dict[str, str]:
        suffix = uuid.uuid4().hex[:10]
        with db.SessionLocal() as session:
            user = session.scalar(
                select(User).where(User.email == "worker@example.com")
            )
            self.assertIsNotNone(user)
            campaign = Campaign(
                workspace_id=self.workspace_id,
                created_by=user.id,
                name=f"公众号发布-{suffix}",
                product_name="测试产品",
                objective="验证自动对账",
                audience="测试用户",
                platforms=["wechat"],
            )
            session.add(campaign)
            session.flush()
            workflow_run = WorkflowRun(
                workspace_id=self.workspace_id,
                campaign_id=campaign.id,
                status="completed",
                current_stage="completed",
                trace_id=f"trace-{suffix}",
            )
            session.add(workflow_run)
            session.flush()
            content = ContentItem(
                workspace_id=self.workspace_id,
                campaign_id=campaign.id,
                run_id=workflow_run.id,
                platform="wechat",
                title="自动对账测试内容",
                body="用于验证发布结果轮询。",
                status="approved",
                version=1,
                approved_by=user.id,
                approved_at=datetime.now(timezone.utc),
            )
            channel = ChannelConnection(
                workspace_id=self.workspace_id,
                platform="wechat",
                display_name=f"公众号-{suffix}",
                status="connected",
                config_json={"auto_publish": True},
            )
            session.add_all([content, channel])
            session.flush()
            asset = Asset(
                workspace_id=self.workspace_id,
                content_item_id=content.id,
                kind="image",
                status="ready",
                storage_uri=f"memory://asset/{suffix}.png",
                mime_type="image/png",
                metadata_json={"content_version": 1},
            )
            publish_job = PublishJob(
                workspace_id=self.workspace_id,
                content_item_id=content.id,
                channel_id=channel.id,
                status=status,
                scheduled_at=datetime.now(timezone.utc),
                idempotency_key=f"fixture-{suffix}",
                external_id=external_id,
                attempts=1 if status == "submitted" else 0,
                request_json={"content_version": 1},
                response_json={"submit": {"publish_id": external_id}},
            )
            session.add_all([asset, publish_job])
            session.commit()
            return {
                "publish_job_id": publish_job.id,
                "channel_id": channel.id,
                "content_id": content.id,
            }

    def test_database_conflict_after_dispatch_started_requires_reconciliation(self):
        fixture = self._create_publish_fixture(status="publishing")
        with db.SessionLocal() as session:
            queue_job = Job(
                workspace_id=self.workspace_id,
                job_type="publish.dispatch",
                status="queued",
                payload_json={"publish_job_id": fixture["publish_job_id"]},
                max_attempts=4,
                run_at=datetime.now(timezone.utc) - timedelta(seconds=1),
                idempotency_key=(
                    f"publish.dispatch:{fixture['publish_job_id']}"
                ),
            )
            session.add(queue_job)
            session.commit()
            queue_job_id = queue_job.id

        class SerializationFailure(Exception):
            sqlstate = "40001"

        def conflict_after_external_write(_session, _payload, _settings):
            raise OperationalError(
                "UPDATE private_publish_state",
                {"platform_token": "sensitive-token"},
                SerializationFailure("sensitive driver detail"),
            )

        worker = Worker(
            settings=self.settings,
            session_factory=db.SessionLocal,
            worker_id="publish-database-conflict-worker",
            handlers={"publish.dispatch": conflict_after_external_write},
        )

        self.assertTrue(worker.run_once())

        with db.SessionLocal() as session:
            publish_job = session.get(PublishJob, fixture["publish_job_id"])
            queue_job = session.get(Job, queue_job_id)
            actions = set(
                session.scalars(
                    select(AuditLog.action).where(
                        AuditLog.entity_id == fixture["publish_job_id"]
                    )
                )
            )
            reconciliation_audit = session.scalar(
                select(AuditLog).where(
                    AuditLog.entity_id == fixture["publish_job_id"],
                    AuditLog.action == "publish.reconciliation_required",
                )
            )
            self.assertEqual(publish_job.status, "reconciliation_required")
            self.assertEqual(queue_job.status, "failed")
            self.assertIsNone(queue_job.locked_by)
            self.assertIn("kind=transaction_retryable", queue_job.last_error)
            self.assertIn("sqlstate=40001", queue_job.last_error)
            self.assertNotIn("sensitive-token", queue_job.last_error)
            self.assertNotIn("sensitive driver detail", queue_job.last_error)
            self.assertIn("publish.reconciliation_required", actions)
            self.assertEqual(
                reconciliation_audit.metadata_json["reason"],
                "database_transaction_retryable_after_dispatch",
            )

    def test_database_conflict_before_dispatch_keeps_domain_state_for_retry(self):
        fixture = self._create_publish_fixture(status="scheduled")
        with db.SessionLocal() as session:
            queue_job = Job(
                workspace_id=self.workspace_id,
                job_type="publish.dispatch",
                status="queued",
                payload_json={"publish_job_id": fixture["publish_job_id"]},
                max_attempts=4,
                run_at=datetime.now(timezone.utc) - timedelta(seconds=1),
                idempotency_key=(
                    f"publish.dispatch:{fixture['publish_job_id']}"
                ),
            )
            session.add(queue_job)
            session.commit()
            queue_job_id = queue_job.id

        class SerializationFailure(Exception):
            sqlstate = "40001"

        def conflict_before_external_write(_session, _payload, _settings):
            raise OperationalError(
                "SELECT private_publish_state",
                {"platform_token": "sensitive-token"},
                SerializationFailure("sensitive driver detail"),
            )

        worker = Worker(
            settings=self.settings,
            session_factory=db.SessionLocal,
            worker_id="pre-publish-database-conflict-worker",
            handlers={"publish.dispatch": conflict_before_external_write},
        )

        self.assertTrue(worker.run_once())

        with db.SessionLocal() as session:
            publish_job = session.get(PublishJob, fixture["publish_job_id"])
            queue_job = session.get(Job, queue_job_id)
            reconciliation_count = session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.entity_id == fixture["publish_job_id"],
                    AuditLog.action == "publish.reconciliation_required",
                )
            )
            self.assertEqual(publish_job.status, "scheduled")
            self.assertEqual(queue_job.status, "retry")
            self.assertIn("kind=transaction_retryable", queue_job.last_error)
            self.assertEqual(reconciliation_count, 0)

    def test_reconciliation_sweep_skips_active_jobs_before_limit(self):
        fixtures = [
            self._create_publish_fixture(
                status="submitted",
                external_id=f"publish-fairness-{index}",
            )
            for index in range(3)
        ]
        oldest = datetime.now(timezone.utc) - timedelta(minutes=3)
        with db.SessionLocal() as session:
            for index, fixture in enumerate(fixtures):
                publish_job = session.get(PublishJob, fixture["publish_job_id"])
                self.assertIsNotNone(publish_job)
                publish_job.updated_at = oldest + timedelta(minutes=index)
                if index < 2:
                    session.add(
                        Job(
                            workspace_id=self.workspace_id,
                            job_type="publish.reconcile",
                            status="queued",
                            payload_json={
                                "publish_job_id": publish_job.id,
                                "lookup_external_id": publish_job.external_id,
                            },
                            run_at=datetime.now(timezone.utc),
                            idempotency_key=publish_reconciliation_job_key(
                                publish_job.id
                            ),
                        )
                    )
            session.commit()

        with db.SessionLocal() as session:
            self.assertEqual(
                schedule_pending_publish_reconciliations(
                    session,
                    settings=self.settings,
                    limit=1,
                ),
                1,
            )
            session.commit()

        with db.SessionLocal() as session:
            recovered = session.scalar(
                select(Job).where(
                    Job.idempotency_key
                    == publish_reconciliation_job_key(fixtures[2]["publish_job_id"])
                )
            )
            self.assertIsNotNone(recovered)
            self.assertEqual(recovered.status, "queued")
            with self.assertRaisesRegex(ValueError, "batch is invalid"):
                schedule_pending_publish_reconciliations(
                    session,
                    settings=self.settings,
                    limit=0,
                )

    def test_immediate_publish_enqueues_now_and_is_idempotent(self):
        fixture = self._create_publish_fixture(status="scheduled")
        payload = {
            "content_item_id": fixture["content_id"],
            "channel_id": fixture["channel_id"],
            "publish_now": True,
            "request_id": "immediate-request-001",
        }
        before = datetime.now(timezone.utc)
        response = self.client.post(
            "/api/v1/publishing/jobs",
            headers=self.headers,
            json=payload,
        )
        after = datetime.now(timezone.utc)
        self.assertEqual(response.status_code, 202, response.text)
        scheduled = response.json()
        self.assertEqual(scheduled["status"], "queued")
        self.assertEqual(scheduled["publish_timing"], "immediate")
        scheduled_at = datetime.fromisoformat(scheduled["scheduled_at"])
        self.assertGreaterEqual(scheduled_at, before)
        self.assertLessEqual(scheduled_at, after)
        repeated = self.client.post(
            "/api/v1/publishing/jobs",
            headers=self.headers,
            json=payload,
        )
        self.assertEqual(repeated.status_code, 202, repeated.text)
        self.assertEqual(repeated.json()["id"], scheduled["id"])

        with db.SessionLocal() as session:
            queue_job = session.scalar(
                select(Job).where(
                    Job.idempotency_key
                    == f"publish.dispatch:{scheduled['id']}"
                )
            )
            self.assertIsNotNone(queue_job)
            self.assertLessEqual(
                queue_job.run_at.replace(tzinfo=timezone.utc),
                after,
            )
        conflicting = self.client.post(
            "/api/v1/publishing/jobs",
            headers=self.headers,
            json={
                **payload,
                "request_id": "immediate-request-002",
                "scheduled_at": (after + timedelta(minutes=5)).isoformat(),
            },
        )
        self.assertEqual(conflicting.status_code, 422, conflicting.text)

        with db.SessionLocal() as session:
            channel = session.get(ChannelConnection, fixture["channel_id"])
            channel.status = "invalid"
            session.commit()
        disconnected = self.client.post(
            "/api/v1/publishing/jobs",
            headers=self.headers,
            json={
                **payload,
                "request_id": "immediate-request-003",
            },
        )
        self.assertEqual(disconnected.status_code, 409, disconnected.text)
        self.assertIn("连接测试", disconnected.json()["error"]["message"])

    def test_running_immediate_publish_cannot_be_cancelled(self):
        fixture = self._create_publish_fixture(status="queued")
        with db.SessionLocal() as session:
            queue_job = Job(
                workspace_id=self.workspace_id,
                job_type="publish.dispatch",
                status="running",
                payload_json={"publish_job_id": fixture["publish_job_id"]},
                attempts=1,
                max_attempts=4,
                run_at=datetime.now(timezone.utc),
                locked_by="worker-in-flight",
                locked_at=datetime.now(timezone.utc),
                idempotency_key=(
                    f"publish.dispatch:{fixture['publish_job_id']}"
                ),
            )
            session.add(queue_job)
            session.commit()
            queue_job_id = queue_job.id

        cancelled = self.client.post(
            f"/api/v1/publishing/jobs/{fixture['publish_job_id']}/cancel",
            headers=self.headers,
        )
        self.assertEqual(cancelled.status_code, 409, cancelled.text)
        self.assertIn("已开始执行", cancelled.json()["error"]["message"])
        with db.SessionLocal() as session:
            publish_job = session.get(PublishJob, fixture["publish_job_id"])
            queue_job = session.get(Job, queue_job_id)
            self.assertEqual(publish_job.status, "queued")
            self.assertEqual(queue_job.status, "running")

    def test_pre_write_failure_can_retry_only_after_channel_retest(self):
        fixture = self._create_publish_fixture(status="scheduled")
        with db.SessionLocal() as session:
            queue_job = Job(
                workspace_id=self.workspace_id,
                job_type="publish.dispatch",
                status="queued",
                payload_json={"publish_job_id": fixture["publish_job_id"]},
                max_attempts=4,
                run_at=datetime.now(timezone.utc) - timedelta(seconds=1),
                idempotency_key=(
                    f"publish.dispatch:{fixture['publish_job_id']}"
                ),
            )
            session.add(queue_job)
            session.commit()
            queue_job_id = queue_job.id

        class AuthenticationFailureConnector:
            reconciliation_supported = True

            def publish(self, **_kwargs):
                raise ConnectorPublishError(
                    "公众号鉴权失败（40164）：invalid ip",
                    stage="authenticate",
                    retry_safe=True,
                    invalidate_channel=True,
                )

        with patch(
            "contentflow.worker.build_connector",
            return_value=AuthenticationFailureConnector(),
        ):
            self.assertTrue(self.worker.run_once())

        with db.SessionLocal() as session:
            publish_job = session.get(PublishJob, fixture["publish_job_id"])
            channel = session.get(ChannelConnection, fixture["channel_id"])
            queue_job = session.get(Job, queue_job_id)
            self.assertEqual(publish_job.status, "failed")
            self.assertTrue(publish_job.retry_safe)
            self.assertEqual(publish_job.failure_stage, "authenticate")
            self.assertEqual(channel.status, "invalid")
            self.assertEqual(queue_job.status, "failed")
            self.assertEqual(queue_job.attempts, 1)
            actions = list(
                session.scalars(
                    select(AuditLog.action).where(
                        AuditLog.entity_id == publish_job.id
                    )
                )
            )
            self.assertIn("publish.dispatch_failed_retry_safe", actions)

        generic_retry = self.client.post(
            f"/api/v1/jobs/{queue_job_id}/retry",
            headers=self.headers,
        )
        self.assertEqual(generic_retry.status_code, 409, generic_retry.text)
        self.assertIn("安全重试", generic_retry.json()["error"]["message"])

        blocked = self.client.post(
            f"/api/v1/publishing/jobs/{fixture['publish_job_id']}/retry",
            headers=self.headers,
        )
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertIn("重新测试平台连接", blocked.json()["error"]["message"])

        with db.SessionLocal() as session:
            channel = session.get(ChannelConnection, fixture["channel_id"])
            channel.status = "connected"
            session.commit()
        retried = self.client.post(
            f"/api/v1/publishing/jobs/{fixture['publish_job_id']}/retry",
            headers=self.headers,
        )
        self.assertEqual(retried.status_code, 202, retried.text)
        self.assertEqual(retried.json()["status"], "queued")
        self.assertEqual(retried.json()["publish_timing"], "immediate")
        self.assertFalse(retried.json()["retry_safe"])

        class DraftCreatedConnector:
            reconciliation_supported = True

            def publish(self, **_kwargs):
                return ConnectorResult(
                    status="draft_created",
                    external_id="draft-after-safe-retry",
                )

        with patch(
            "contentflow.worker.build_connector",
            return_value=DraftCreatedConnector(),
        ):
            self.assertTrue(self.worker.run_once())
        with db.SessionLocal() as session:
            publish_job = session.get(PublishJob, fixture["publish_job_id"])
            queue_job = session.get(Job, queue_job_id)
            self.assertEqual(publish_job.status, "draft_created")
            self.assertEqual(
                publish_job.external_id,
                "draft-after-safe-retry",
            )
            self.assertEqual(publish_job.attempts, 2)
            self.assertEqual(queue_job.status, "succeeded")


    def test_dispatch_persists_submitted_result_before_queue_completion(self):
        fixture = self._create_publish_fixture(status="scheduled")

        class SubmittedConnector:
            reconciliation_supported = True

            def publish(self, **_kwargs):
                return ConnectorResult(
                    status="submitted",
                    external_id="publish-checkpoint-001",
                    response={"publish_id": "publish-checkpoint-001"},
                )

        with patch(
            "contentflow.worker.build_connector",
            return_value=SubmittedConnector(),
        ):
            with db.SessionLocal() as session:
                result = handle_publish_dispatch(
                    session,
                    {"publish_job_id": fixture["publish_job_id"]},
                    self.settings,
                )

        self.assertEqual(result["status"], "submitted")
        self.assertIsNotNone(result["reconciliation_job_id"])
        with db.SessionLocal() as session:
            publish_job = session.get(PublishJob, fixture["publish_job_id"])
            reconciliation_job = session.scalar(
                select(Job).where(
                    Job.idempotency_key
                    == f"publish.reconcile:{fixture['publish_job_id']}"
                )
            )
            self.assertEqual(publish_job.status, "submitted")
            self.assertEqual(
                publish_job.external_id,
                "publish-checkpoint-001",
            )
            self.assertIsNone(publish_job.published_at)
            self.assertIsNotNone(reconciliation_job)
            self.assertEqual(reconciliation_job.status, "queued")
            self.assertEqual(reconciliation_job.max_attempts, 2)

    def test_automatic_reconciliation_retries_then_publishes(self):
        fixture = self._create_publish_fixture(
            status="submitted",
            external_id="publish-poll-001",
        )
        with db.SessionLocal() as session:
            dispatch_job = Job(
                workspace_id=self.workspace_id,
                job_type="publish.dispatch",
                status="running",
                payload_json={"publish_job_id": fixture["publish_job_id"]},
                attempts=1,
                max_attempts=4,
                run_at=datetime.now(timezone.utc),
                locked_by="crashed-dispatch-worker",
                locked_at=datetime.now(timezone.utc),
                idempotency_key=f"publish.dispatch:{fixture['publish_job_id']}",
            )
            session.add(dispatch_job)
            session.commit()
            dispatch_job_id = dispatch_job.id
        responses = [
            ConnectorResult(
                status="pending",
                external_id="publish-poll-001",
                response={"publish_status": 0},
            ),
            ConnectorResult(
                status="published",
                external_id="article-001",
                external_url="https://mp.weixin.qq.com/s/article-001",
                response={"article_id": "article-001"},
            ),
        ]

        class PollingConnector:
            reconciliation_supported = True

            def reconcile(self, _publish_job):
                return responses.pop(0)

        self.assertFalse(self.worker.run_once())
        with db.SessionLocal() as session:
            reconciliation_job = session.scalar(
                select(Job).where(
                    Job.idempotency_key
                    == f"publish.reconcile:{fixture['publish_job_id']}"
                )
            )
            self.assertIsNotNone(reconciliation_job)
            self.assertEqual(reconciliation_job.max_attempts, 2)
            reconciliation_job.run_at = datetime.now(timezone.utc) - timedelta(
                seconds=1
            )
            reconciliation_job_id = reconciliation_job.id
            session.commit()

        with patch(
            "contentflow.worker.build_connector",
            return_value=PollingConnector(),
        ):
            self.assertTrue(self.worker.run_once())
            with db.SessionLocal() as session:
                publish_job = session.get(PublishJob, fixture["publish_job_id"])
                reconciliation_job = session.get(Job, reconciliation_job_id)
                self.assertEqual(publish_job.status, "submitted")
                self.assertEqual(reconciliation_job.status, "retry")
                self.assertEqual(
                    publish_job.response_json["automatic_reconciliation"]["state"],
                    "pending",
                )
                reconciliation_job.run_at = datetime.now(timezone.utc) - timedelta(
                    seconds=1
                )
                session.commit()

            self.assertTrue(self.worker.run_once())

        with db.SessionLocal() as session:
            publish_job = session.get(PublishJob, fixture["publish_job_id"])
            reconciliation_job = session.get(Job, reconciliation_job_id)
            dispatch_job = session.get(Job, dispatch_job_id)
            actions = set(
                session.scalars(
                    select(AuditLog.action).where(
                        AuditLog.entity_id == fixture["publish_job_id"]
                    )
                )
            )
            self.assertEqual(publish_job.status, "published")
            self.assertEqual(publish_job.external_id, "article-001")
            self.assertEqual(
                publish_job.external_url,
                "https://mp.weixin.qq.com/s/article-001",
            )
            self.assertIsNotNone(publish_job.published_at)
            self.assertEqual(reconciliation_job.status, "succeeded")
            self.assertEqual(reconciliation_job.attempts, 2)
            self.assertEqual(dispatch_job.status, "succeeded")
            self.assertIsNone(dispatch_job.locked_by)
            self.assertIsNone(dispatch_job.locked_at)
            self.assertEqual(
                dispatch_job.result_json["reconciled"],
                "automatic",
            )
            self.assertIn("publish.reconciliation_queued", actions)
            self.assertIn("publish.reconciliation_checked", actions)
            self.assertIn("publish.reconcile_auto", actions)

    def test_automatic_reconciliation_exhaustion_requires_manual_review(self):
        fixture = self._create_publish_fixture(
            status="submitted",
            external_id="publish-error-001",
        )

        class FailingConnector:
            reconciliation_supported = True

            def reconcile(self, _publish_job):
                raise RuntimeError("temporary platform query failure")

        self.assertFalse(self.worker.run_once())
        with db.SessionLocal() as session:
            reconciliation_job = session.scalar(
                select(Job).where(
                    Job.idempotency_key
                    == f"publish.reconcile:{fixture['publish_job_id']}"
                )
            )
            self.assertIsNotNone(reconciliation_job)
            reconciliation_job.run_at = datetime.now(timezone.utc) - timedelta(
                seconds=1
            )
            reconciliation_job_id = reconciliation_job.id
            session.commit()

        with patch(
            "contentflow.worker.build_connector",
            return_value=FailingConnector(),
        ):
            self.assertTrue(self.worker.run_once())
            with db.SessionLocal() as session:
                reconciliation_job = session.get(Job, reconciliation_job_id)
                self.assertEqual(reconciliation_job.status, "retry")
                reconciliation_job.run_at = datetime.now(timezone.utc) - timedelta(
                    seconds=1
                )
                session.commit()
            self.assertTrue(self.worker.run_once())

        with db.SessionLocal() as session:
            publish_job = session.get(PublishJob, fixture["publish_job_id"])
            reconciliation_job = session.get(Job, reconciliation_job_id)
            actions = set(
                session.scalars(
                    select(AuditLog.action).where(
                        AuditLog.entity_id == fixture["publish_job_id"]
                    )
                )
            )
            self.assertEqual(reconciliation_job.status, "failed")
            self.assertEqual(reconciliation_job.attempts, 2)
            self.assertEqual(
                publish_job.status,
                "reconciliation_required",
            )
            self.assertIn("自动发布对账未能获得确定结果", publish_job.error)
            self.assertIn("publish.reconciliation_required", actions)

    def test_stale_automatic_result_cannot_overwrite_manual_decision(self):
        fixture = self._create_publish_fixture(
            status="submitted",
            external_id="publish-race-001",
        )
        publish_job_id = fixture["publish_job_id"]

        class RacingConnector:
            reconciliation_supported = True

            def reconcile(self, _publish_job):
                with db.SessionLocal() as concurrent_session:
                    current = concurrent_session.get(PublishJob, publish_job_id)
                    current.status = "failed"
                    current.external_id = None
                    current.external_url = None
                    current.published_at = None
                    current.error = "manual decision won the race"
                    current.response_json = {
                        **dict(current.response_json or {}),
                        "manual_reconciliation": {
                            "decision": "confirmed_not_published",
                        },
                    }
                    concurrent_session.commit()
                return ConnectorResult(
                    status="published",
                    external_id="late-article-001",
                    external_url="https://mp.weixin.qq.com/s/late-article-001",
                    response={"article_id": "late-article-001"},
                )

        with patch(
            "contentflow.worker.build_connector",
            return_value=RacingConnector(),
        ):
            with db.SessionLocal() as session:
                result = handle_publish_reconcile(
                    session,
                    {"publish_job_id": publish_job_id},
                    self.settings,
                )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["ignored_remote_status"], "published")
        with db.SessionLocal() as session:
            publish_job = session.get(PublishJob, publish_job_id)
            actions = set(
                session.scalars(
                    select(AuditLog.action).where(AuditLog.entity_id == publish_job_id)
                )
            )
            self.assertEqual(publish_job.status, "failed")
            self.assertIsNone(publish_job.external_id)
            self.assertEqual(publish_job.error, "manual decision won the race")
            self.assertIn(
                "publish.reconciliation_stale_ignored",
                actions,
            )

    def test_manual_reconciliation_can_take_over_submitted_job(self):
        fixture = self._create_publish_fixture(
            status="submitted",
            external_id="publish-manual-001",
        )
        self.assertFalse(self.worker.run_once())
        with db.SessionLocal() as session:
            reconciliation_job = session.scalar(
                select(Job).where(
                    Job.idempotency_key
                    == f"publish.reconcile:{fixture['publish_job_id']}"
                )
            )
            self.assertIsNotNone(reconciliation_job)
            reconciliation_job.status = "running"
            reconciliation_job.attempts = 1
            reconciliation_job.locked_by = "slow-reconciliation-worker"
            reconciliation_job.locked_at = datetime.now(timezone.utc)
            reconciliation_job_id = reconciliation_job.id
            session.commit()

        response = self.client.post(
            (f"/api/v1/publishing/jobs/{fixture['publish_job_id']}/reconcile"),
            headers=self.headers,
            json={
                "decision": "confirmed_not_published",
                "reason": "Reviewer verified the platform did not publish it.",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "failed")

        with db.SessionLocal() as session:
            publish_job = session.get(PublishJob, fixture["publish_job_id"])
            reconciliation_job = session.get(Job, reconciliation_job_id)
            manual_result = publish_job.response_json["manual_reconciliation"]
            self.assertEqual(publish_job.status, "failed")
            self.assertEqual(reconciliation_job.status, "succeeded")
            self.assertIsNone(reconciliation_job.locked_by)
            self.assertIsNone(reconciliation_job.locked_at)
            self.assertEqual(
                reconciliation_job.result_json["reconciled"],
                "manual",
            )
            self.assertEqual(
                manual_result["reconciliation_queue_job_id"],
                reconciliation_job_id,
            )

        with db.SessionLocal() as session:
            publish_job = session.get(PublishJob, fixture["publish_job_id"])
            reconciliation_job = session.get(Job, reconciliation_job_id)
            publish_job.status = "submitted"
            publish_job.external_id = "publish-manual-002"
            publish_job.error = None
            publish_job.attempts += 1
            reconciliation_job.attempts = 2
            reconciliation_job.last_error = "obsolete reconciliation result"
            session.commit()

        # Normal dispatch requeues immediately; this direct database fixture
        # represents recovery at the next bounded maintenance sweep.
        self.worker._next_publish_reconciliation_sweep_at = 0.0
        self.assertFalse(self.worker.run_once())
        with db.SessionLocal() as session:
            reconciliation_job = session.get(Job, reconciliation_job_id)
            actions = set(
                session.scalars(
                    select(AuditLog.action).where(
                        AuditLog.entity_id == fixture["publish_job_id"]
                    )
                )
            )
            self.assertEqual(reconciliation_job.status, "queued")
            self.assertEqual(reconciliation_job.attempts, 0)
            self.assertEqual(reconciliation_job.max_attempts, 2)
            self.assertEqual(reconciliation_job.result_json, {})
            self.assertIsNone(reconciliation_job.last_error)
            self.assertIsNone(reconciliation_job.locked_by)
            self.assertIsNone(reconciliation_job.locked_at)
            self.assertEqual(
                reconciliation_job.payload_json["lookup_external_id"],
                "publish-manual-002",
            )
            self.assertIn(
                "publish.reconciliation_requeued",
                actions,
            )

    def test_failed_workflow_persists_redacted_ai_provenance(self):
        class FailingTextProvider:
            provider_name = "failing-provider"
            model_name = "failing-model"
            last_call_metadata = {"usage_source": "not_reported"}

            def complete_json(self, _stage, _payload, *, system_prompt=None):
                raise RuntimeError("private-model-error-body")

        campaign = self.client.post(
            "/api/v1/campaigns",
            headers=self.headers,
            json={
                "name": "失败证据测试",
                "product_name": "地图产品",
                "objective": "验证模型失败证据",
                "audience": "测试用户",
                "platforms": ["xiaohongshu"],
            },
        )
        self.assertEqual(campaign.status_code, 201, campaign.text)
        run = self.client.post(
            f"/api/v1/campaigns/{campaign.json()['id']}/runs",
            headers=self.headers,
            json={},
        )
        self.assertEqual(run.status_code, 202, run.text)
        with db.SessionLocal() as session:
            queue_job = session.scalar(
                select(Job).where(Job.job_type == "workflow.execute")
            )
            self.assertIsNotNone(queue_job)
            queue_job.max_attempts = 1
            session.commit()

        with patch(
            "contentflow.workflow_service.build_text_provider",
            return_value=FailingTextProvider(),
        ):
            self.assertTrue(self.worker.run_once())

        with db.SessionLocal() as session:
            workflow_run = session.get(WorkflowRun, run.json()["id"])
            self.assertEqual(workflow_run.status, "failed")
            provenance = workflow_run.result_json["ai_provenance"]
            self.assertEqual(provenance["provider"], "failing-provider")
            self.assertEqual(provenance["failed_invocations"], 1)
            self.assertEqual(provenance["invocations"][0]["error_type"], "RuntimeError")
            self.assertNotIn("private-model-error-body", str(provenance))
            self.assertEqual(workflow_run.error, "AI workflow failed (RuntimeError)")
            stored_job = session.scalar(
                select(Job).where(Job.job_type == "workflow.execute")
            )
            self.assertEqual(
                stored_job.last_error,
                "AI workflow failed (RuntimeError)",
            )
            self.assertNotIn("private-model-error-body", stored_job.last_error)

    def test_expired_workflow_lease_requires_review_without_rerunning_handler(self):
        campaign = self.client.post(
            "/api/v1/campaigns",
            headers=self.headers,
            json={
                "name": "租约恢复人工核对测试",
                "product_name": "内容产品",
                "objective": "验证中断后不重复调用模型",
                "audience": "测试用户",
                "platforms": ["xiaohongshu"],
            },
        )
        self.assertEqual(campaign.status_code, 201, campaign.text)
        run = self.client.post(
            f"/api/v1/campaigns/{campaign.json()['id']}/runs",
            headers=self.headers,
            json={},
        )
        self.assertEqual(run.status_code, 202, run.text)

        with db.SessionLocal() as session:
            queue_job = session.scalar(
                select(Job).where(
                    Job.job_type == "workflow.execute",
                    Job.payload_json["run_id"].as_string() == run.json()["id"],
                )
            )
            self.assertIsNotNone(queue_job)
            queue_job.status = "running"
            queue_job.attempts = 1
            queue_job.locked_by = "terminated-model-worker"
            queue_job.locked_at = datetime.now(timezone.utc) - timedelta(minutes=10)
            session.commit()
            queue_job_id = queue_job.id

        handler_calls = 0

        def should_not_run(_session, _payload, _settings):
            nonlocal handler_calls
            handler_calls += 1
            raise AssertionError("expired provider job must not be replayed")

        recovery_worker = Worker(
            settings=self.settings,
            session_factory=db.SessionLocal,
            worker_id="manual-review-recovery-worker",
            handlers={"workflow.execute": should_not_run},
        )
        self.assertTrue(recovery_worker.run_once())
        self.assertEqual(handler_calls, 0)

        with db.SessionLocal() as session:
            stored_job = session.get(Job, queue_job_id)
            workflow_run = session.get(WorkflowRun, run.json()["id"])
            self.assertEqual(stored_job.status, "failed")
            self.assertEqual(stored_job.attempts, 1)
            self.assertIsNone(stored_job.locked_by)
            self.assertIsNone(stored_job.locked_at)
            self.assertIn("Automatic retry was blocked", stored_job.last_error)
            self.assertEqual(workflow_run.status, "failed")
            self.assertEqual(workflow_run.current_stage, "failed")
            self.assertIn("Automatic retry was blocked", workflow_run.error)

        retried = self.client.post(
            f"/api/v1/jobs/{queue_job_id}/retry",
            headers=self.headers,
        )
        self.assertEqual(retried.status_code, 200, retried.text)
        self.assertEqual(retried.json()["status"], "retry")
        self.assertEqual(retried.json()["attempts"], 0)

    def test_knowledge_index_workflow_assets_and_export(self):
        uploaded = self.client.post(
            "/api/v1/knowledge/documents",
            headers=self.headers,
            files={
                "file": (
                    "facts.md",
                    "产品事实：支持用户整理候选地点并确认路线。".encode(),
                    "text/markdown",
                )
            },
        )
        self.assertEqual(uploaded.status_code, 202, uploaded.text)
        self.assertTrue(self.worker.run_once())
        with db.SessionLocal() as session:
            document = session.get(KnowledgeDocument, uploaded.json()["id"])
            self.assertEqual(document.status, "indexed")
            self.assertGreater(document.metadata_json["chunk_count"], 0)

        campaign = self.client.post(
            "/api/v1/campaigns",
            headers=self.headers,
            json={
                "name": "北京夜游内容计划",
                "product_name": "地图产品",
                "objective": "帮助年轻用户整理夜游路线",
                "audience": "北京年轻用户",
                "platforms": ["xiaohongshu"],
                "must_include": ["候选地点", "路线确认"],
                "call_to_action": "打开地图产品确认路线",
            },
        )
        self.assertEqual(campaign.status_code, 201, campaign.text)
        run = self.client.post(
            f"/api/v1/campaigns/{campaign.json()['id']}/runs",
            headers=self.headers,
            json={},
        )
        self.assertEqual(run.status_code, 202, run.text)
        self.assertTrue(self.worker.run_once())
        with db.SessionLocal() as session:
            workflow_run = session.get(WorkflowRun, run.json()["id"])
            self.assertEqual(workflow_run.status, "awaiting_review")
            provenance = workflow_run.result_json["ai_provenance"]
            self.assertEqual(provenance["provider"], "mock")
            self.assertEqual(provenance["model"], "mock-deterministic-v1")
            self.assertEqual(
                provenance["embedding"],
                {"provider": "hash", "model": "hash-1024"},
            )
            self.assertEqual(provenance["invocation_count"], 3)
            self.assertEqual(provenance["successful_invocations"], 3)
            self.assertEqual(provenance["failed_invocations"], 0)
            self.assertEqual(provenance["token_usage"]["source"], "not_reported")
            self.assertEqual(
                [item["stage"] for item in provenance["invocations"]],
                ["plan", "generate", "review"],
            )
            self.assertTrue(
                all(
                    len(item["input_sha256"]) == 64
                    for item in provenance["invocations"]
                )
            )

        contents = self.client.get("/api/v1/contents", headers=self.headers)
        self.assertEqual(contents.status_code, 200, contents.text)
        self.assertEqual(len(contents.json()), 1)
        content = contents.json()[0]
        self.assertEqual(content["layout_json"]["cover_title"], "夜游路线这样排更清楚")
        self.assertGreaterEqual(len(content["layout_json"]["cards"]), 3)
        revisions = self.client.get(
            f"/api/v1/contents/{content['id']}/revisions",
            headers=self.headers,
        )
        self.assertEqual(revisions.status_code, 200, revisions.text)
        self.assertEqual(revisions.headers["x-contentflow-page-limit"], "100")
        self.assertEqual(len(revisions.json()), 1)
        self.assertEqual(revisions.json()[0]["version"], 1)
        self.assertEqual(
            revisions.json()[0]["layout_json"]["cover_title"],
            "夜游路线这样排更清楚",
        )
        original_version = content["version"]
        updated = self.client.patch(
            f"/api/v1/contents/{content['id']}",
            headers=self.headers,
            json={
                "expected_version": original_version,
                "title": f"{content['title']}（人工校对）",
            },
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["version"], original_version + 1)

        stale_update = self.client.patch(
            f"/api/v1/contents/{content['id']}",
            headers=self.headers,
            json={
                "expected_version": original_version,
                "body": "旧页面不应覆盖新版本",
            },
        )
        self.assertEqual(stale_update.status_code, 409, stale_update.text)
        content = updated.json()

        stale_review = self.client.post(
            f"/api/v1/contents/{content['id']}/review",
            headers=self.headers,
            json={
                "decision": "approve",
                "reason": "旧页面审核请求",
                "expected_version": original_version,
            },
        )
        self.assertEqual(stale_review.status_code, 409, stale_review.text)
        reviewed = self.client.post(
            f"/api/v1/contents/{content['id']}/review",
            headers=self.headers,
            json={
                "decision": "approve",
                "reason": "事实与平台格式已确认",
                "expected_version": content["version"],
            },
        )
        self.assertEqual(reviewed.status_code, 200, reviewed.text)
        self.assertEqual(reviewed.json()["status"], "approved")
        self.assertTrue(self.worker.run_once())

        assets = self.client.get(
            f"/api/v1/assets?content_item_id={content['id']}",
            headers=self.headers,
        )
        self.assertEqual(assets.status_code, 200, assets.text)
        ready_assets = [item for item in assets.json() if item["status"] == "ready"]
        self.assertEqual(len(ready_assets), 1)
        asset_download = self.client.get(
            f"/api/v1/assets/{ready_assets[0]['id']}/download",
            headers=self.headers,
        )
        self.assertEqual(asset_download.status_code, 200, asset_download.text)
        self.assertTrue(asset_download.content.startswith(b"\x89PNG"))
        manual_cover = BytesIO()
        Image.new("RGB", (16, 10), color=(24, 86, 140)).save(
            manual_cover,
            format="PNG",
        )
        uploaded_asset = self.client.post(
            "/api/v1/assets/upload",
            headers=self.headers,
            data={
                "content_item_id": content["id"],
                "kind": "image",
            },
            files={
                "file": (
                    "manual-cover.png",
                    manual_cover.getvalue(),
                    "image/png",
                )
            },
        )
        self.assertEqual(uploaded_asset.status_code, 201, uploaded_asset.text)
        self.assertEqual(uploaded_asset.json()["provider"], "manual-upload")
        self.assertEqual(uploaded_asset.json()["status"], "ready")

        channel = self.client.post(
            "/api/v1/channels",
            headers=self.headers,
            json={
                "platform": "xiaohongshu",
                "display_name": "审核后导出",
                "credentials": {},
                "config": {"export_format": "zip"},
            },
        )
        self.assertEqual(channel.status_code, 201, channel.text)
        self.assertEqual(
            channel.json()["config_json"]["connection_mode"], "manual_export"
        )
        cancellable = self.client.post(
            "/api/v1/publishing/jobs",
            headers=self.headers,
            json={
                "content_item_id": content["id"],
                "channel_id": channel.json()["id"],
                "scheduled_at": (
                    datetime.now(timezone.utc) + timedelta(minutes=4)
                ).isoformat(),
            },
        )
        self.assertEqual(cancellable.status_code, 202, cancellable.text)
        cancelled = self.client.post(
            f"/api/v1/publishing/jobs/{cancellable.json()['id']}/cancel",
            headers=self.headers,
        )
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        self.assertEqual(cancelled.json()["status"], "cancelled")
        with db.SessionLocal() as session:
            cancelled_queue_job = session.scalar(
                select(Job).where(
                    Job.job_type == "publish.dispatch",
                    Job.payload_json["publish_job_id"].as_string()
                    == cancellable.json()["id"],
                )
            )
            self.assertEqual(cancelled_queue_job.status, "succeeded")
            self.assertEqual(
                cancelled_queue_job.result_json["status"],
                "cancelled",
            )

        scheduled = self.client.post(
            "/api/v1/publishing/jobs",
            headers=self.headers,
            json={
                "content_item_id": content["id"],
                "channel_id": channel.json()["id"],
                "scheduled_at": (
                    datetime.now(timezone.utc) + timedelta(minutes=1)
                ).isoformat(),
            },
        )
        self.assertEqual(scheduled.status_code, 202, scheduled.text)
        self.assertEqual(scheduled.json()["delivery_mode"], "manual_export")
        with db.SessionLocal() as session:
            queue_job = session.scalar(
                select(Job).where(
                    Job.job_type == "publish.dispatch",
                    Job.payload_json["publish_job_id"].as_string()
                    == scheduled.json()["id"],
                )
            )
            queue_job.run_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            session.commit()
        self.assertTrue(self.worker.run_once())
        with db.SessionLocal() as session:
            publish_job = session.get(PublishJob, scheduled.json()["id"])
            self.assertEqual(publish_job.status, "exported")

        artifact = self.client.get(
            f"/api/v1/publishing/jobs/{scheduled.json()['id']}/artifact",
            headers=self.headers,
        )
        self.assertEqual(artifact.status_code, 200, artifact.text)
        with zipfile.ZipFile(BytesIO(artifact.content)) as archive:
            self.assertIn("content.md", archive.namelist())
            self.assertIn("manifest.json", archive.namelist())
            self.assertTrue(
                any(name.startswith("assets/") for name in archive.namelist())
            )

        uncertain = self.client.post(
            "/api/v1/publishing/jobs",
            headers=self.headers,
            json={
                "content_item_id": content["id"],
                "channel_id": channel.json()["id"],
                "scheduled_at": (
                    datetime.now(timezone.utc) + timedelta(minutes=2)
                ).isoformat(),
            },
        )
        self.assertEqual(uncertain.status_code, 202, uncertain.text)
        with db.SessionLocal() as session:
            publish_job = session.get(PublishJob, uncertain.json()["id"])
            publish_job.status = "publishing"
            publish_job.attempts = 1
            publish_job.request_json = {
                **publish_job.request_json,
                "dispatch_token": "persisted-before-crash",
            }
            queue_job = session.scalar(
                select(Job).where(
                    Job.job_type == "publish.dispatch",
                    Job.payload_json["publish_job_id"].as_string()
                    == uncertain.json()["id"],
                )
            )
            queue_job.run_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            queue_job_id = queue_job.id
            session.commit()

        self.assertTrue(self.worker.run_once())
        with db.SessionLocal() as session:
            publish_job = session.get(PublishJob, uncertain.json()["id"])
            queue_job = session.get(Job, queue_job_id)
            self.assertEqual(publish_job.status, "reconciliation_required")
            self.assertEqual(publish_job.attempts, 1)
            self.assertEqual(queue_job.status, "failed")
        premature_retry = self.client.post(
            f"/api/v1/jobs/{queue_job_id}/retry",
            headers=self.headers,
        )
        self.assertEqual(premature_retry.status_code, 409, premature_retry.text)
        self.assertIn("人工对账", premature_retry.json()["error"]["message"])

        reconciled = self.client.post(
            f"/api/v1/publishing/jobs/{uncertain.json()['id']}/reconcile",
            headers=self.headers,
            json={
                "decision": "confirmed_published",
                "reason": "已在平台后台按标题和发布时间核对",
                "external_id": "platform-post-001",
                "external_url": "https://platform.example/posts/001",
            },
        )
        self.assertEqual(reconciled.status_code, 200, reconciled.text)
        self.assertEqual(reconciled.json()["status"], "published")

        with db.SessionLocal() as session:
            queue_job = session.get(Job, queue_job_id)
            mark_domain_failure(session, queue_job, "late worker failure")
            session.commit()
            publish_job = session.get(PublishJob, uncertain.json()["id"])
            self.assertEqual(publish_job.status, "published")
            self.assertIsNone(publish_job.error)
            self.assertEqual(queue_job.status, "succeeded")

        retried = self.client.post(
            f"/api/v1/jobs/{queue_job_id}/retry",
            headers=self.headers,
        )
        self.assertEqual(retried.status_code, 409, retried.text)
        with db.SessionLocal() as session:
            publish_job = session.get(PublishJob, uncertain.json()["id"])
            queue_job = session.get(Job, queue_job_id)
            self.assertEqual(publish_job.status, "published")
            self.assertEqual(publish_job.attempts, 1)
            self.assertEqual(publish_job.external_id, "platform-post-001")
            self.assertEqual(queue_job.status, "succeeded")

        not_published = self.client.post(
            "/api/v1/publishing/jobs",
            headers=self.headers,
            json={
                "content_item_id": content["id"],
                "channel_id": channel.json()["id"],
                "scheduled_at": (
                    datetime.now(timezone.utc) + timedelta(minutes=3)
                ).isoformat(),
            },
        )
        self.assertEqual(not_published.status_code, 202, not_published.text)
        with db.SessionLocal() as session:
            publish_job = session.get(PublishJob, not_published.json()["id"])
            publish_job.status = "reconciliation_required"
            publish_job.attempts = 1
            publish_job.error = "simulated uncertain outcome"
            queue_job = session.scalar(
                select(Job).where(
                    Job.job_type == "publish.dispatch",
                    Job.payload_json["publish_job_id"].as_string()
                    == not_published.json()["id"],
                )
            )
            queue_job.status = "failed"
            queue_job.last_error = "simulated uncertain outcome"
            queue_job.run_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            not_published_queue_id = queue_job.id
            session.commit()

        reconciled_not_published = self.client.post(
            f"/api/v1/publishing/jobs/{not_published.json()['id']}/reconcile",
            headers=self.headers,
            json={
                "decision": "confirmed_not_published",
                "reason": "平台后台和草稿箱均未找到对应内容",
            },
        )
        self.assertEqual(
            reconciled_not_published.status_code,
            200,
            reconciled_not_published.text,
        )
        self.assertEqual(reconciled_not_published.json()["status"], "failed")
        with db.SessionLocal() as session:
            queue_job = session.get(Job, not_published_queue_id)
            self.assertEqual(queue_job.status, "failed")

        retried_not_published = self.client.post(
            f"/api/v1/jobs/{not_published_queue_id}/retry",
            headers=self.headers,
        )
        self.assertEqual(retried_not_published.status_code, 200)
        self.assertTrue(self.worker.run_once())
        with db.SessionLocal() as session:
            publish_job = session.get(PublishJob, not_published.json()["id"])
            queue_job = session.get(Job, not_published_queue_id)
            self.assertEqual(publish_job.status, "exported")
            self.assertEqual(publish_job.attempts, 2)
            self.assertEqual(queue_job.status, "succeeded")

        lease_exhausted = self.client.post(
            "/api/v1/publishing/jobs",
            headers=self.headers,
            json={
                "content_item_id": content["id"],
                "channel_id": channel.json()["id"],
                "scheduled_at": (
                    datetime.now(timezone.utc) + timedelta(minutes=5)
                ).isoformat(),
            },
        )
        self.assertEqual(lease_exhausted.status_code, 202, lease_exhausted.text)
        with db.SessionLocal() as session:
            publish_job = session.get(PublishJob, lease_exhausted.json()["id"])
            publish_job.status = "publishing"
            publish_job.attempts = 1
            publish_job.request_json = {
                **publish_job.request_json,
                "dispatch_token": "persisted-before-lease-expiry",
            }
            queue_job = session.scalar(
                select(Job).where(
                    Job.job_type == "publish.dispatch",
                    Job.payload_json["publish_job_id"].as_string()
                    == lease_exhausted.json()["id"],
                )
            )
            queue_job.status = "running"
            queue_job.attempts = queue_job.max_attempts
            queue_job.locked_by = "dead-publisher"
            queue_job.locked_at = datetime.now(timezone.utc) - timedelta(minutes=10)
            lease_exhausted_queue_id = queue_job.id
            session.commit()

        self.assertTrue(self.worker.run_once())
        with db.SessionLocal() as session:
            publish_job = session.get(PublishJob, lease_exhausted.json()["id"])
            queue_job = session.get(Job, lease_exhausted_queue_id)
            self.assertEqual(publish_job.status, "reconciliation_required")
            self.assertIn("lease expired", publish_job.error)
            self.assertEqual(queue_job.status, "failed")
            self.assertIsNone(queue_job.locked_by)
            self.assertIsNone(queue_job.locked_at)

        blocked_retry = self.client.post(
            f"/api/v1/jobs/{lease_exhausted_queue_id}/retry",
            headers=self.headers,
        )
        self.assertEqual(blocked_retry.status_code, 409, blocked_retry.text)

    def test_expired_final_connector_lease_is_failed_and_propagated(self):
        channel = self.client.post(
            "/api/v1/channels",
            headers=self.headers,
            json={
                "platform": "wechat",
                "display_name": "expired-lease",
                "credentials": {"app_id": "lease-app", "app_secret": "lease-secret"},
                "config": {},
            },
        )
        self.assertEqual(channel.status_code, 201, channel.text)
        queued = self.client.post(
            f"/api/v1/channels/{channel.json()['id']}/test",
            headers=self.headers,
        )
        self.assertEqual(queued.status_code, 202, queued.text)

        with db.SessionLocal() as session:
            job = session.get(Job, queued.json()["id"])
            job.status = "running"
            job.attempts = job.max_attempts
            job.locked_by = "dead-worker"
            job.locked_at = datetime.now(timezone.utc) - timedelta(minutes=10)
            session.commit()

        self.assertTrue(self.worker.run_once())
        with db.SessionLocal() as session:
            job = session.get(Job, queued.json()["id"])
            channel_row = session.get(ChannelConnection, channel.json()["id"])
            self.assertEqual(job.status, "failed")
            self.assertIsNone(job.locked_by)
            self.assertIsNone(job.locked_at)
            self.assertIn("lease expired", job.last_error)
            self.assertEqual(channel_row.status, "invalid")


if __name__ == "__main__":
    unittest.main()
