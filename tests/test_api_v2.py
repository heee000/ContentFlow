from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from contentflow import db
from contentflow.api import create_app
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
        session_response = self.client.get(
            "/api/v1/auth/session", headers=self.headers
        )
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

        jobs = self.client.get("/api/v1/jobs", headers=self.headers)
        self.assertEqual(jobs.status_code, 200)
        self.assertEqual(jobs.json()[0]["job_type"], "workflow.execute")

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


if __name__ == "__main__":
    unittest.main()
