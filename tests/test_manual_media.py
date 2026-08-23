from __future__ import annotations

import io
import tempfile
import unittest
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import func, select

from contentflow import db
from contentflow.api import create_app
from contentflow.entities import Asset, Campaign, ContentItem, Job, User, WorkflowRun
from contentflow.settings import Settings


class ManualMediaFlowTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.settings = Settings(
            database_url=f"sqlite:///{(root / 'manual-media.db').as_posix()}",
            secret_key="manual-media-test-secret",
            storage_backend="local",
            local_storage_dir=root / "storage",
            allow_registration=True,
            image_provider="manual",
            video_provider="manual",
        )
        self.client = TestClient(create_app(self.settings))
        self.client.__enter__()
        registered = self.client.post(
            "/api/v1/auth/register",
            json={
                "email": "manual-media@example.com",
                "password": "a-secure-password",
                "display_name": "Manual Media Owner",
                "workspace_name": "Manual Media Workspace",
            },
        )
        self.assertEqual(registered.status_code, 201, registered.text)
        self.headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
        self.workspace_id = registered.json()["workspace_id"]
        suffix = uuid.uuid4().hex[:8]
        with db.SessionLocal() as session:
            user = session.scalar(
                select(User).where(User.email == "manual-media@example.com")
            )
            campaign = Campaign(
                workspace_id=self.workspace_id,
                created_by=user.id,
                name=f"人工封面-{suffix}",
                product_name="路线助手",
                objective="验证人工真实封面",
                audience="周末出行用户",
                platforms=["wechat"],
            )
            session.add(campaign)
            session.flush()
            run = WorkflowRun(
                workspace_id=self.workspace_id,
                campaign_id=campaign.id,
                status="awaiting_review",
                current_stage="human_review",
                trace_id=f"trace-{suffix}",
            )
            session.add(run)
            session.flush()
            content = ContentItem(
                workspace_id=self.workspace_id,
                campaign_id=campaign.id,
                run_id=run.id,
                platform="wechat",
                title="北京周末 CityWalk",
                body="一条经过人工核验的城市漫步路线。",
                status="needs_review",
                version=1,
            )
            session.add(content)
            session.flush()
            asset = Asset(
                workspace_id=self.workspace_id,
                content_item_id=content.id,
                kind="image",
                provider="manual",
                status="planned",
                prompt="真实城市街景封面",
                metadata_json={"content_version": 1},
            )
            session.add(asset)
            session.commit()
            self.content_id = content.id
            self.asset_id = asset.id

    def tearDown(self):
        self.client.__exit__(None, None, None)
        db.engine.dispose()
        self.temp_dir.cleanup()

    def test_approval_waits_for_safe_real_cover_and_fills_same_asset(self):
        reviewed = self.client.post(
            f"/api/v1/contents/{self.content_id}/review",
            headers=self.headers,
            json={
                "decision": "approve",
                "reason": "文案与事实已人工确认",
                "expected_version": 1,
            },
        )
        self.assertEqual(reviewed.status_code, 200, reviewed.text)
        with db.SessionLocal() as session:
            asset = session.get(Asset, self.asset_id)
            self.assertEqual(asset.status, "awaiting_upload")
            self.assertEqual(asset.provider, "manual")
            generation_jobs = session.scalar(
                select(func.count(Job.id)).where(Job.job_type == "asset.generate")
            )
            self.assertEqual(generation_jobs, 0)

        invalid = self.client.post(
            "/api/v1/assets/upload",
            headers=self.headers,
            data={"asset_id": self.asset_id},
            files={"file": ("fake.png", b"not-an-image", "image/png")},
        )
        self.assertEqual(invalid.status_code, 415, invalid.text)
        with db.SessionLocal() as session:
            self.assertEqual(
                session.get(Asset, self.asset_id).status, "awaiting_upload"
            )

        cover = io.BytesIO()
        Image.new("RGB", (32, 20), color=(35, 96, 148)).save(cover, format="PNG")
        uploaded = self.client.post(
            "/api/v1/assets/upload",
            headers=self.headers,
            data={"asset_id": self.asset_id},
            files={
                "file": (
                    "real-citywalk-cover.png",
                    cover.getvalue(),
                    "image/png",
                )
            },
        )
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        payload = uploaded.json()
        self.assertEqual(payload["id"], self.asset_id)
        self.assertEqual(payload["provider"], "manual-upload")
        self.assertEqual(payload["status"], "ready")
        self.assertFalse(payload["metadata_json"]["manual_upload_required"])
        self.assertEqual(len(payload["metadata_json"]["source_checksum"]), 64)
        with db.SessionLocal() as session:
            asset_count = session.scalar(select(func.count(Asset.id)))
            self.assertEqual(asset_count, 1)

        downloaded = self.client.get(
            f"/api/v1/assets/{self.asset_id}/download",
            headers=self.headers,
        )
        self.assertEqual(downloaded.status_code, 200, downloaded.text)
        with Image.open(io.BytesIO(downloaded.content)) as decoded:
            self.assertEqual(decoded.size, (32, 20))
            self.assertEqual(decoded.format, "PNG")

        duplicate = self.client.post(
            "/api/v1/assets/upload",
            headers=self.headers,
            data={"asset_id": self.asset_id},
            files={"file": ("duplicate.png", cover.getvalue(), "image/png")},
        )
        self.assertEqual(duplicate.status_code, 409, duplicate.text)

    def test_upload_requires_approval_and_current_content_version(self):
        blocked = self.client.post(
            "/api/v1/assets/upload",
            headers=self.headers,
            data={"asset_id": self.asset_id},
            files={"file": ("cover.png", b"not-read", "image/png")},
        )
        self.assertEqual(blocked.status_code, 409, blocked.text)

        reviewed = self.client.post(
            f"/api/v1/contents/{self.content_id}/review",
            headers=self.headers,
            json={
                "decision": "approve",
                "reason": "文案已核验",
                "expected_version": 1,
            },
        )
        self.assertEqual(reviewed.status_code, 200, reviewed.text)
        with db.SessionLocal() as session:
            content = session.get(ContentItem, self.content_id)
            content.version = 2
            session.commit()

        stale = self.client.post(
            "/api/v1/assets/upload",
            headers=self.headers,
            data={"asset_id": self.asset_id},
            files={"file": ("cover.png", b"not-read", "image/png")},
        )
        self.assertEqual(stale.status_code, 409, stale.text)

    def test_manual_asset_kind_does_not_accept_a_different_media_type(self):
        reviewed = self.client.post(
            f"/api/v1/contents/{self.content_id}/review",
            headers=self.headers,
            json={
                "decision": "approve",
                "reason": "文案已核验",
                "expected_version": 1,
            },
        )
        self.assertEqual(reviewed.status_code, 200, reviewed.text)
        wrong_image_type = self.client.post(
            "/api/v1/assets/upload",
            headers=self.headers,
            data={"asset_id": self.asset_id},
            files={"file": ("cover.mp4", b"video", "video/mp4")},
        )
        self.assertEqual(wrong_image_type.status_code, 415, wrong_image_type.text)

        with db.SessionLocal() as session:
            storyboard = Asset(
                workspace_id=self.workspace_id,
                content_item_id=self.content_id,
                kind="video_storyboard",
                provider="manual",
                status="awaiting_upload",
                metadata_json={"content_version": 1},
            )
            session.add(storyboard)
            session.commit()
            storyboard_id = storyboard.id
        wrong_storyboard_type = self.client.post(
            "/api/v1/assets/upload",
            headers=self.headers,
            data={"asset_id": storyboard_id},
            files={"file": ("storyboard.mp4", b"video", "video/mp4")},
        )
        self.assertEqual(
            wrong_storyboard_type.status_code,
            415,
            wrong_storyboard_type.text,
        )


if __name__ == "__main__":
    unittest.main()
