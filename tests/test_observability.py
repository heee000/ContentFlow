from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from prometheus_client import CONTENT_TYPE_LATEST
from sqlalchemy import select

from contentflow import db
from contentflow.api import create_app
from contentflow.entities import (
    Job,
    JobManualReview,
    ProviderInvocation,
    ProviderInvocationAttempt,
    StorageObjectAllocation,
    WorkerNode,
    WorkspaceStorageUsage,
)
from contentflow.settings import Settings


class ObservabilityTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.metrics_token = "metrics-test-token-" + "m" * 32
        self.settings = Settings(
            _env_file=None,
            environment="development",
            database_url=f"sqlite:///{(root / 'metrics.db').as_posix()}",
            secret_key="metrics-test-secret",
            local_storage_dir=root / "storage",
            storage_backend="local",
            allow_registration=True,
            require_governed_prompts=False,
            embedding_provider="hash",
            text_provider="mock",
            image_provider="mock",
            video_provider="mock",
            metrics_enabled=True,
            metrics_bearer_token=self.metrics_token,
        )
        self.client = TestClient(create_app(self.settings))
        self.client.__enter__()
        registered = self.client.post(
            "/api/v1/auth/register",
            json={
                "email": "metrics-owner@example.com",
                "password": "metrics-owner-password",
                "display_name": "Metrics Owner",
                "workspace_name": "Metrics Workspace",
            },
        )
        self.assertEqual(registered.status_code, 201, registered.text)
        self.headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        db.engine.dispose()
        self.temp_dir.cleanup()

    @property
    def metrics_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.metrics_token}"}

    def test_metrics_are_protected_and_use_low_cardinality_labels(self):
        missing = self.client.get("/metrics")
        self.assertEqual(missing.status_code, 401, missing.text)
        self.assertEqual(missing.headers["www-authenticate"], "Bearer")
        self.assertEqual(missing.headers["cache-control"], "no-store")
        wrong = self.client.get(
            "/metrics",
            headers={"Authorization": "Bearer wrong-token"},
        )
        self.assertEqual(wrong.status_code, 401, wrong.text)
        self.assertNotIn(self.metrics_token, wrong.text)

        campaign = self.client.post(
            "/api/v1/campaigns",
            headers=self.headers,
            json={
                "name": "指标测试活动",
                "product_name": "ContentFlow",
                "objective": "验证低基数指标和数据库队列快照",
                "audience": "平台运维人员",
                "platforms": ["wechat"],
            },
        )
        self.assertEqual(campaign.status_code, 201, campaign.text)
        campaign_id = campaign.json()["id"]
        run = self.client.post(
            f"/api/v1/campaigns/{campaign_id}/runs",
            headers=self.headers,
            json={},
        )
        self.assertEqual(run.status_code, 202, run.text)
        listed = self.client.get(
            f"/api/v1/campaigns/{campaign_id}/runs",
            headers=self.headers,
        )
        self.assertEqual(listed.status_code, 200, listed.text)
        now = datetime.now(timezone.utc)
        with db.SessionLocal() as session:
            usage = session.scalar(select(WorkspaceStorageUsage))
            self.assertIsNotNone(usage)
            usage.used_bytes = 12
            usage.used_objects = 1
            usage.last_reconciled_at = now - timedelta(days=2)
            session.add(
                StorageObjectAllocation(
                    workspace_id=usage.workspace_id,
                    owner_type="metrics_test",
                    owner_id="metrics-object",
                    category="metrics",
                    filename="metrics.bin",
                    status="delete_pending",
                    storage_uri="file:///metrics-workspace/metrics.bin",
                    checksum="a" * 64,
                    size_bytes=12,
                    size_verified=True,
                    mime_type="application/octet-stream",
                    delete_requested_at=now - timedelta(days=2),
                    updated_at=now - timedelta(days=2),
                )
            )
            session.add(
                Job(
                    workspace_id=usage.workspace_id,
                    job_type="storage.reconcile",
                    status="failed",
                    run_at=now - timedelta(days=1),
                    idempotency_key="metrics-storage-reconcile-failed",
                )
            )
            manual_job = Job(
                workspace_id=usage.workspace_id,
                job_type="workflow.execute",
                status="manual_review",
                run_at=now - timedelta(hours=2),
                idempotency_key="metrics-manual-review",
            )
            session.add(manual_job)
            session.flush()
            session.add(
                JobManualReview(
                    workspace_id=usage.workspace_id,
                    job_id=manual_job.id,
                    reason_code="provider_outcome_unknown_after_error",
                    context_json={},
                    requested_at=now - timedelta(hours=2),
                )
            )
            invocation = ProviderInvocation(
                workspace_id=usage.workspace_id,
                job_id=manual_job.id,
                entity_type="workflow_run",
                entity_id="metrics-run",
                provider_kind="text",
                provider_name="openai-compatible",
                model_name="metrics-model",
                operation="text.plan",
                request_key="b" * 64,
                request_sha256="c" * 64,
                request_bytes=512,
                last_status="outcome_unknown",
            )
            session.add(invocation)
            session.flush()
            session.add(
                ProviderInvocationAttempt(
                    invocation_id=invocation.id,
                    attempt_number=1,
                    status="outcome_unknown",
                    idempotency_key_sent=True,
                    usage_source="not_reported",
                    started_at=now - timedelta(hours=2),
                    completed_at=now - timedelta(hours=2),
                    error_type="worker_lease_expired",
                )
            )
            session.add(
                WorkerNode(
                    id="metrics-worker",
                    hostname="metrics-host",
                    process_id=1234,
                    status="online",
                    started_at=now,
                    heartbeat_at=now,
                    metadata_json={},
                )
            )
            session.add(
                WorkerNode(
                    id="metrics-stale-worker",
                    hostname="metrics-stale-host",
                    process_id=5678,
                    status="online",
                    started_at=now - timedelta(minutes=10),
                    heartbeat_at=now - timedelta(minutes=10),
                    metadata_json={},
                )
            )
            session.commit()

        response = self.client.get("/metrics", headers=self.metrics_headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"], CONTENT_TYPE_LATEST)
        self.assertEqual(response.headers["cache-control"], "no-store")
        body = response.text
        self.assertIn("contentflow_build_info", body)
        self.assertIn("contentflow_http_requests_total", body)
        self.assertIn(
            'route="/api/v1/campaigns/{campaign_id}/runs"',
            body,
        )
        self.assertNotIn(campaign_id, body)
        self.assertIn('contentflow_queue_jobs{status="queued"} 1.0', body)
        self.assertIn(
            'contentflow_queue_jobs{status="manual_review"} 1.0',
            body,
        )
        self.assertIn("contentflow_queue_ready_jobs 1.0", body)
        manual_review_age = next(
            float(line.rsplit(" ", 1)[1])
            for line in body.splitlines()
            if line.startswith(
                "contentflow_job_manual_review_oldest_age_seconds "
            )
        )
        self.assertGreater(manual_review_age, 119 * 60)
        self.assertIn(
            'contentflow_provider_invocation_attempts{status="outcome_unknown"} 1.0',
            body,
        )
        self.assertIn(
            "contentflow_provider_invocation_unresolved_outcome_unknown 1.0",
            body,
        )
        provider_unknown_age = next(
            float(line.rsplit(" ", 1)[1])
            for line in body.splitlines()
            if line.startswith(
                "contentflow_provider_invocation_outcome_unknown_oldest_age_seconds "
            )
        )
        self.assertGreater(provider_unknown_age, 119 * 60)
        self.assertIn('contentflow_worker_nodes{state="active"} 1.0', body)
        self.assertIn('contentflow_worker_nodes{state="stale"} 1.0', body)
        self.assertIn("contentflow_publish_reconciliation_required 0.0", body)
        self.assertIn(
            'contentflow_storage_allocations{status="delete_pending"} 1.0',
            body,
        )
        self.assertIn(
            'contentflow_storage_usage_bytes{state="used"} 12.0',
            body,
        )
        self.assertIn(
            "contentflow_storage_reconciliation_scheduler_enabled 1.0",
            body,
        )
        self.assertIn(
            "contentflow_storage_reconciliation_overdue_workspaces 1.0",
            body,
        )
        self.assertIn(
            "contentflow_storage_reconciliation_failed_jobs 1.0",
            body,
        )
        self.assertIn(
            "contentflow_storage_delete_pending_oldest_age_seconds",
            body,
        )
        oldest_delete_age = next(
            float(line.rsplit(" ", 1)[1])
            for line in body.splitlines()
            if line.startswith(
                "contentflow_storage_delete_pending_oldest_age_seconds "
            )
        )
        self.assertGreater(oldest_delete_age, 47 * 60 * 60)
        self.assertNotIn("metrics-workspace", body)

        schema = self.client.get("/openapi.json").json()
        self.assertNotIn("/metrics", schema["paths"])
        arbitrary_method = self.client.request("TENANT-VERB", "/health/live")
        self.assertEqual(arbitrary_method.status_code, 405, arbitrary_method.text)
        unknown_status = f"tenant-{campaign_id}"
        with db.SessionLocal() as session:
            queue_job = session.scalar(select(Job))
            self.assertIsNotNone(queue_job)
            queue_job.status = unknown_status
            session.commit()
        bounded = self.client.get("/metrics", headers=self.metrics_headers)
        self.assertEqual(bounded.status_code, 200, bounded.text)
        self.assertIn('method="OTHER"', bounded.text)
        self.assertIn('contentflow_queue_jobs{status="unknown"} 1.0', bounded.text)
        self.assertNotIn(unknown_status, bounded.text)

    def test_metrics_collection_failure_is_safe_and_retryable(self):
        with patch(
            "contentflow.observability.DatabaseOperationalCollector._status_counts",
            side_effect=RuntimeError("database-secret-detail"),
        ):
            response = self.client.get("/metrics", headers=self.metrics_headers)
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertNotIn("database-secret-detail", response.text)


class DisabledObservabilityTest(unittest.TestCase):
    def test_disabled_endpoint_looks_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = Settings(
                _env_file=None,
                environment="development",
                database_url=f"sqlite:///{(root / 'disabled-metrics.db').as_posix()}",
                secret_key="disabled-metrics-test-secret",
                local_storage_dir=root / "storage",
                storage_backend="local",
                require_governed_prompts=False,
                embedding_provider="hash",
                text_provider="mock",
                image_provider="mock",
                video_provider="mock",
                metrics_enabled=False,
            )
            with TestClient(create_app(settings)) as client:
                response = client.get("/metrics")
                self.assertEqual(response.status_code, 404, response.text)
                self.assertEqual(response.headers["cache-control"], "no-store")
            db.engine.dispose()


if __name__ == "__main__":
    unittest.main()
