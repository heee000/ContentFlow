from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from contentflow import db
from contentflow.api import create_app
from contentflow.entities import (
    ChannelConnection,
    ContentItem,
    Job,
    MetricSnapshot,
    PublishJob,
)
from contentflow.settings import Settings


class ApiV2Test(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        settings = Settings(
            database_url=f"sqlite:///{(root / 'api.db').as_posix()}",
            secret_key="test-secret",
            local_storage_dir=root / "storage",
            allow_registration=True,
        )
        self.client = TestClient(create_app(settings))
        self.client.__enter__()
        registered = self.client.post(
            "/api/v1/auth/register",
            json={
                "email": "owner@example.com",
                "password": "a-secure-password",
                "display_name": "Owner",
                "workspace_name": "Test Workspace",
            },
        )
        self.assertEqual(registered.status_code, 201, registered.text)
        self.token = registered.json()["access_token"]
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        db.engine.dispose()
        self.temp_dir.cleanup()

    def test_authenticated_campaign_and_job_flow(self):
        session_response = self.client.get("/api/v1/auth/session", headers=self.headers)
        self.assertEqual(session_response.status_code, 200)

        created = self.client.post(
            "/api/v1/campaigns",
            headers=self.headers,
            json={
                "name": "夜游内容计划",
                "product_name": "星图地图",
                "objective": "帮助用户完成夜游路线规划",
                "audience": "北京年轻用户",
                "platforms": ["xiaohongshu", "douyin", "wechat"],
                "must_include": ["地点整理", "路线确认"],
                "forbidden_phrases": ["百分百准确"],
                "call_to_action": "打开星图地图规划路线",
            },
        )
        self.assertEqual(created.status_code, 201, created.text)
        campaign_id = created.json()["id"]

        updated = self.client.patch(
            f"/api/v1/campaigns/{campaign_id}",
            headers=self.headers,
            json={
                "name": "北京夜游内容计划",
                "city": "北京",
                "product_facts": ["支持多地点路线规划"],
                "status": "archived",
            },
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["name"], "北京夜游内容计划")
        self.assertEqual(
            updated.json()["brief"]["product_facts"],
            ["支持多地点路线规划"],
        )
        archived_run = self.client.post(
            f"/api/v1/campaigns/{campaign_id}/runs",
            headers=self.headers,
            json={},
        )
        self.assertEqual(archived_run.status_code, 409, archived_run.text)

        restored = self.client.patch(
            f"/api/v1/campaigns/{campaign_id}",
            headers=self.headers,
            json={"status": "active"},
        )
        self.assertEqual(restored.status_code, 200, restored.text)

        run = self.client.post(
            f"/api/v1/campaigns/{campaign_id}/runs",
            headers=self.headers,
            json={},
        )
        self.assertEqual(run.status_code, 202, run.text)
        self.assertEqual(run.json()["status"], "queued")

        second_run = self.client.post(
            f"/api/v1/campaigns/{campaign_id}/runs",
            headers=self.headers,
            json={},
        )
        self.assertEqual(second_run.status_code, 202, second_run.text)

        recent_runs = self.client.get(
            f"/api/v1/campaigns/{campaign_id}/runs?limit=1",
            headers=self.headers,
        )
        self.assertEqual(recent_runs.status_code, 200, recent_runs.text)
        self.assertEqual(len(recent_runs.json()), 1)
        self.assertEqual(recent_runs.json()[0]["id"], second_run.json()["id"])

        workspace_runs = self.client.get(
            "/api/v1/runs?limit=100",
            headers=self.headers,
        )
        self.assertEqual(workspace_runs.status_code, 200, workspace_runs.text)
        self.assertEqual(
            [item["id"] for item in workspace_runs.json()[:2]],
            [second_run.json()["id"], run.json()["id"]],
        )

        invalid_limit = self.client.get(
            f"/api/v1/campaigns/{campaign_id}/runs?limit=101",
            headers=self.headers,
        )
        self.assertEqual(invalid_limit.status_code, 422, invalid_limit.text)

        jobs = self.client.get("/api/v1/jobs", headers=self.headers)
        self.assertEqual(jobs.status_code, 200)
        self.assertEqual(jobs.json()[0]["job_type"], "workflow.execute")
        self.assertEqual(jobs.json()[0]["context"]["campaign_id"], campaign_id)
        self.assertEqual(
            jobs.json()[0]["context"]["campaign_name"],
            "北京夜游内容计划",
        )
        self.assertEqual(jobs.json()[0]["context"]["product_name"], "星图地图")
        self.assertNotIn("payload_json", jobs.json()[0])

        metrics_channel = self.client.post(
            "/api/v1/channels",
            headers=self.headers,
            json={
                "platform": "xiaohongshu",
                "display_name": "指标测试导出",
                "credentials": {},
                "config": {"export_format": "zip"},
            },
        )
        self.assertEqual(metrics_channel.status_code, 201, metrics_channel.text)
        workspace_id = session_response.json()["workspace"]["id"]
        with db.SessionLocal() as session:
            content = ContentItem(
                workspace_id=workspace_id,
                campaign_id=campaign_id,
                run_id=run.json()["id"],
                platform="xiaohongshu",
                title="指标归属测试",
                body="用于验证项目筛选只汇总当前项目。",
            )
            session.add(content)
            session.flush()
            publish = PublishJob(
                workspace_id=workspace_id,
                content_item_id=content.id,
                channel_id=metrics_channel.json()["id"],
                status="published",
                scheduled_at=datetime.now(timezone.utc),
                idempotency_key="metrics-campaign-filter",
            )
            session.add(publish)
            session.flush()
            session.add(
                MetricSnapshot(
                    workspace_id=workspace_id,
                    publish_job_id=publish.id,
                    impressions=100,
                    clicks=8,
                    likes=5,
                    comments=2,
                    shares=1,
                )
            )
            session.commit()

        project_metrics = self.client.get(
            f"/api/v1/metrics/summary?campaign_id={campaign_id}",
            headers=self.headers,
        )
        self.assertEqual(project_metrics.status_code, 200, project_metrics.text)
        self.assertEqual(project_metrics.json()["sample_count"], 1)
        self.assertEqual(project_metrics.json()["impressions"], 100)
        unrelated_metrics = self.client.get(
            "/api/v1/metrics/summary?campaign_id=unrelated-campaign",
            headers=self.headers,
        )
        self.assertEqual(unrelated_metrics.status_code, 200, unrelated_metrics.text)
        self.assertEqual(unrelated_metrics.json()["sample_count"], 0)

    def test_knowledge_upload_and_export_channel(self):
        uploaded = self.client.post(
            "/api/v1/knowledge/documents",
            headers=self.headers,
            files={
                "file": (
                    "brand.md",
                    b"# Brand\n\nUse verified product facts only.",
                    "text/markdown",
                )
            },
        )
        self.assertEqual(uploaded.status_code, 202, uploaded.text)
        self.assertEqual(uploaded.json()["status"], "pending")

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
        self.assertEqual(channel.json()["status"], "export_only")
        self.assertNotIn("credential_ciphertext", channel.json())

    def test_channel_credentials_must_match_runtime_requirements(self):
        missing_open_id = self.client.post(
            "/api/v1/channels",
            headers=self.headers,
            json={
                "platform": "douyin",
                "display_name": "抖音缺少 Open ID",
                "credentials": {"access_token": "token"},
                "config": {},
            },
        )
        self.assertEqual(missing_open_id.status_code, 422, missing_open_id.text)
        self.assertIn("open_id", missing_open_id.text)

        config_open_id = self.client.post(
            "/api/v1/channels",
            headers=self.headers,
            json={
                "platform": "douyin",
                "display_name": "抖音完整连接",
                "credentials": {"access_token": "token"},
                "config": {"open_id": "open-id"},
            },
        )
        self.assertEqual(config_open_id.status_code, 201, config_open_id.text)

        blank_wechat_secret = self.client.post(
            "/api/v1/channels",
            headers=self.headers,
            json={
                "platform": "wechat",
                "display_name": "公众号空密钥",
                "credentials": {"app_id": "app-id", "app_secret": "   "},
                "config": {},
            },
        )
        self.assertEqual(blank_wechat_secret.status_code, 422, blank_wechat_secret.text)
        self.assertIn("app_secret", blank_wechat_secret.text)

    def test_failed_connector_test_can_be_enqueued_again(self):
        channel = self.client.post(
            "/api/v1/channels",
            headers=self.headers,
            json={
                "platform": "wechat",
                "display_name": "可重试连接",
                "credentials": {"app_id": "retry-app", "app_secret": "retry-secret"},
                "config": {},
            },
        )
        self.assertEqual(channel.status_code, 201, channel.text)
        channel_id = channel.json()["id"]

        first = self.client.post(
            f"/api/v1/channels/{channel_id}/test",
            headers=self.headers,
        )
        self.assertEqual(first.status_code, 202, first.text)
        duplicate = self.client.post(
            f"/api/v1/channels/{channel_id}/test",
            headers=self.headers,
        )
        self.assertEqual(duplicate.status_code, 202, duplicate.text)
        self.assertEqual(duplicate.json()["id"], first.json()["id"])

        with db.SessionLocal() as session:
            previous_job = session.get(Job, first.json()["id"])
            previous_job.status = "failed"
            previous_job.attempts = previous_job.max_attempts
            stored_channel = session.get(ChannelConnection, channel_id)
            stored_channel.status = "invalid"
            session.commit()

        retried = self.client.post(
            f"/api/v1/channels/{channel_id}/test",
            headers=self.headers,
        )
        self.assertEqual(retried.status_code, 202, retried.text)
        self.assertNotEqual(retried.json()["id"], first.json()["id"])
        channels = self.client.get("/api/v1/channels", headers=self.headers)
        self.assertEqual(channels.status_code, 200, channels.text)
        stored = next(item for item in channels.json() if item["id"] == channel_id)
        self.assertEqual(stored["status"], "pending_test")

    def test_dedicated_local_frontend_origin_is_allowed(self):
        response = self.client.options(
            "/api/v1/auth/login",
            headers={
                "Origin": "http://localhost:3001",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.headers.get("access-control-allow-origin"),
            "http://localhost:3001",
        )

    def test_security_headers_and_api_cache_policy(self):
        response = self.client.get("/api/v1/auth/workspaces")
        self.assertEqual(response.status_code, 401, response.text)
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(response.headers["x-frame-options"], "DENY")
        self.assertEqual(
            response.headers["referrer-policy"],
            "strict-origin-when-cross-origin",
        )
        self.assertEqual(response.headers["cache-control"], "no-store")


if __name__ == "__main__":
    unittest.main()
