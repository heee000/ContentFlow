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
from contentflow.entities import Job, WorkerNode
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
        self.assertIn("contentflow_queue_ready_jobs 1.0", body)
        self.assertIn('contentflow_worker_nodes{state="active"} 1.0', body)
        self.assertIn('contentflow_worker_nodes{state="stale"} 1.0', body)
        self.assertIn("contentflow_publish_reconciliation_required 0.0", body)

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
