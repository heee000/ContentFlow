from __future__ import annotations

import tempfile
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from contentflow import db
from contentflow.api import create_app
from contentflow.entities import Job, KnowledgeDocument, PublishJob, WorkflowRun
from contentflow.settings import Settings
from contentflow.worker import Worker


class WorkerIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.settings = Settings(
            database_url=f"sqlite:///{(root / 'worker.db').as_posix()}",
            secret_key="worker-test-secret",
            local_storage_dir=root / "storage",
            allow_registration=True,
            text_provider="mock",
            image_provider="mock",
            video_provider="mock",
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
        self.headers = {
            "Authorization": f"Bearer {registered.json()['access_token']}"
        }
        self.worker = Worker(
            settings=self.settings,
            session_factory=db.SessionLocal,
            worker_id="integration-worker",
        )

    def tearDown(self):
        self.client.__exit__(None, None, None)
        db.engine.dispose()
        self.temp_dir.cleanup()

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
        self.assertEqual(len(revisions.json()), 1)
        self.assertEqual(revisions.json()[0]["version"], 1)
        self.assertEqual(
            revisions.json()[0]["layout_json"]["cover_title"],
            "夜游路线这样排更清楚",
        )
        reviewed = self.client.post(
            f"/api/v1/contents/{content['id']}/review",
            headers=self.headers,
            json={"decision": "approve", "reason": "事实与平台格式已确认"},
        )
        self.assertEqual(reviewed.status_code, 200, reviewed.text)
        self.assertEqual(reviewed.json()["status"], "approved")
        self.assertTrue(self.worker.run_once())

        assets = self.client.get(
            f"/api/v1/assets?content_item_id={content['id']}",
            headers=self.headers,
        )
        self.assertEqual(assets.status_code, 200, assets.text)
        self.assertEqual(assets.json()[0]["status"], "ready")
        asset_download = self.client.get(
            f"/api/v1/assets/{assets.json()[0]['id']}/download",
            headers=self.headers,
        )
        self.assertEqual(asset_download.status_code, 200, asset_download.text)
        self.assertTrue(asset_download.content.startswith(b"\x89PNG"))
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
                    b"\x89PNG\r\nmanual",
                    "image/png",
                )
            },
        )
        self.assertEqual(uploaded_asset.status_code, 201, uploaded_asset.text)
        self.assertEqual(uploaded_asset.json()["provider"], "upload")
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


if __name__ == "__main__":
    unittest.main()
