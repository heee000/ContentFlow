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

    def test_new_manual_asset_is_rejected_at_current_version_quota(self):
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
        self.settings.asset_max_items_per_content_version = 1
        with db.SessionLocal() as session:
            asset = session.get(Asset, self.asset_id)
            asset.status = "ready"
            session.commit()
        files_before = {
            path
            for path in self.settings.local_storage_dir.rglob("*")
            if path.is_file()
        }

        rejected = self.client.post(
            "/api/v1/assets/upload",
            headers=self.headers,
            data={"content_item_id": self.content_id, "kind": "video"},
            files={"file": ("not-read.mp4", b"not-read", "video/mp4")},
        )

        self.assertEqual(rejected.status_code, 409, rejected.text)
        self.assertIn("配置上限", rejected.json()["error"]["message"])
        with db.SessionLocal() as session:
            asset_count = session.scalar(
                select(func.count(Asset.id)).where(
                    Asset.content_item_id == self.content_id,
                    Asset.content_version == 1,
                )
            )
        self.assertEqual(asset_count, 1)
        files_after = {
            path
            for path in self.settings.local_storage_dir.rglob("*")
            if path.is_file()
        }
        self.assertEqual(files_after, files_before)

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

    def test_cover_source_can_change_after_approval_without_forcing_upload(self):
        capabilities = self.client.get(
            "/api/v1/assets/capabilities",
            headers=self.headers,
        )
        self.assertEqual(capabilities.status_code, 200, capabilities.text)
        self.assertFalse(capabilities.json()["image_generation_available"])
        self.assertTrue(capabilities.json()["image_search_available"])

        reviewed = self.client.post(
            f"/api/v1/contents/{self.content_id}/review",
            headers=self.headers,
            json={
                "decision": "approve",
                "reason": "文案已核验，可以选择封面路线",
                "expected_version": 1,
            },
        )
        self.assertEqual(reviewed.status_code, 200, reviewed.text)

        unavailable = self.client.post(
            f"/api/v1/assets/{self.asset_id}/source",
            headers=self.headers,
            json={"source": "generate"},
        )
        self.assertEqual(unavailable.status_code, 409, unavailable.text)
        self.assertIn("尚未配置", unavailable.json()["error"]["message"])

        searched = self.client.post(
            f"/api/v1/assets/{self.asset_id}/source",
            headers=self.headers,
            json={"source": "search"},
        )
        self.assertEqual(searched.status_code, 200, searched.text)
        searched_payload = searched.json()
        self.assertEqual(searched_payload["provider"], "openverse")
        self.assertEqual(searched_payload["status"], "queued")
        self.assertEqual(searched_payload["metadata_json"]["media_source"], "search")
        self.assertFalse(
            searched_payload["metadata_json"]["manual_upload_required"]
        )

        concurrent_change = self.client.post(
            f"/api/v1/assets/{self.asset_id}/source",
            headers=self.headers,
            json={"source": "manual"},
        )
        self.assertEqual(concurrent_change.status_code, 409, concurrent_change.text)

        with db.SessionLocal() as session:
            asset = session.get(Asset, self.asset_id)
            asset.status = "awaiting_selection"
            asset.metadata_json = {
                **asset.metadata_json,
                "search_candidates": [{"id": "discarded-candidate"}],
            }
            session.commit()

        manual = self.client.post(
            f"/api/v1/assets/{self.asset_id}/source",
            headers=self.headers,
            json={"source": "manual"},
        )
        self.assertEqual(manual.status_code, 200, manual.text)
        manual_payload = manual.json()
        self.assertEqual(manual_payload["provider"], "manual")
        self.assertEqual(manual_payload["status"], "awaiting_upload")
        self.assertTrue(manual_payload["metadata_json"]["manual_upload_required"])
        self.assertNotIn("search_candidates", manual_payload["metadata_json"])
        self.assertEqual(manual_payload["metadata_json"]["source_revision"], 2)

        with db.SessionLocal() as session:
            source_jobs = list(
                session.scalars(
                    select(Job).where(
                        Job.job_type == "asset.search",
                        Job.workspace_id == self.workspace_id,
                    )
                )
            )
            self.assertEqual(len(source_jobs), 1)


if __name__ == "__main__":
    unittest.main()
