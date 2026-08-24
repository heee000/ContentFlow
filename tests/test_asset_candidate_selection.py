from __future__ import annotations

import io
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from contentflow import db
from contentflow.api import create_app
from contentflow.entities import Asset, Campaign, ContentItem, User, WorkflowRun
from contentflow.settings import Settings


class AssetCandidateSelectionTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.settings = Settings(
            environment="development",
            database_url=f"sqlite:///{(root / 'asset-selection.db').as_posix()}",
            secret_key="asset-selection-test-secret",
            local_storage_dir=root / "storage",
            storage_backend="local",
            require_governed_prompts=False,
            metrics_enabled=False,
            embedding_provider="hash",
            text_provider="mock",
            image_provider="mock",
            video_provider="mock",
            image_search_provider="openverse",
            openverse_api_base="https://api.openverse.org/v1",
            image_search_download_allowed_hosts=["upload.wikimedia.org"],
        )
        self.client = TestClient(create_app(self.settings))
        self.client.__enter__()
        registered = self.client.post(
            "/api/v1/auth/register",
            json={
                "email": "asset-selector@example.com",
                "password": "a-secure-password",
                "display_name": "Asset Selector",
                "workspace_name": "Asset Selection Workspace",
            },
        )
        self.assertEqual(registered.status_code, 201, registered.text)
        self.headers = {
            "Authorization": f"Bearer {registered.json()['access_token']}"
        }
        self.workspace_id = registered.json()["workspace_id"]

        suffix = uuid.uuid4().hex[:8]
        with db.SessionLocal() as session:
            user = session.scalar(
                select(User).where(User.email == "asset-selector@example.com")
            )
            campaign = Campaign(
                workspace_id=self.workspace_id,
                created_by=user.id,
                name=f"混合素材候选-{suffix}",
                product_name="ContentFlow",
                objective="验证开放图库候选选择",
                audience="内容运营人员",
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
                title="高质量内容工作流",
                body="经过人工核验的测试内容。",
                status="approved",
                version=1,
                approved_by=user.id,
                approved_at=datetime.now(timezone.utc),
            )
            session.add(content)
            session.flush()
            searched = Asset(
                workspace_id=self.workspace_id,
                content_item_id=content.id,
                kind="image",
                provider="openverse",
                status="awaiting_selection",
                prompt="高质量社媒内容工作台",
                metadata_json={
                    "content_version": 1,
                    "candidate_group": "cover",
                    "optional_candidate": True,
                    "selected": False,
                    "search_candidates": [
                        {
                            "id": "candidate-1",
                            "title": "Open licensed cover",
                            "creator": "Example creator",
                            "license": "by-sa",
                            "license_version": "4.0",
                            "license_url": (
                                "https://creativecommons.org/licenses/by-sa/4.0/"
                            ),
                            "landing_url": (
                                "https://commons.wikimedia.org/wiki/File:Example.jpg"
                            ),
                            "download_url": (
                                "https://upload.wikimedia.org/wikipedia/"
                                "commons/example.jpg"
                            ),
                        }
                    ],
                },
            )
            generated = Asset(
                workspace_id=self.workspace_id,
                content_item_id=content.id,
                kind="image",
                provider="mock",
                status="ready",
                prompt="AI generated cover",
                storage_uri="file:///unused-generated-cover.png",
                mime_type="image/png",
                size_bytes=16,
                metadata_json={
                    "content_version": 1,
                    "candidate_group": "cover",
                    "optional_candidate": True,
                    "selected": True,
                },
            )
            session.add_all([searched, generated])
            session.commit()
            self.searched_asset_id = searched.id
            self.generated_asset_id = generated.id

    def tearDown(self):
        self.client.__exit__(None, None, None)
        db.engine.dispose()
        self.temp_dir.cleanup()

    @staticmethod
    def image_bytes() -> bytes:
        output = io.BytesIO()
        Image.new("RGB", (48, 30), color=(40, 90, 150)).save(
            output,
            format="PNG",
        )
        return output.getvalue()

    def test_openverse_selection_requires_acknowledgement_and_is_exclusive(self):
        rejected = self.client.post(
            f"/api/v1/assets/{self.searched_asset_id}/select",
            headers=self.headers,
            json={
                "candidate_id": "candidate-1",
                "acknowledge_license_check": False,
            },
        )
        self.assertEqual(rejected.status_code, 422, rejected.text)

        with patch(
            "contentflow.routers.assets.download_generated_media",
            return_value=self.image_bytes(),
        ):
            selected = self.client.post(
                f"/api/v1/assets/{self.searched_asset_id}/select",
                headers=self.headers,
                json={
                    "candidate_id": "candidate-1",
                    "acknowledge_license_check": True,
                },
            )
        self.assertEqual(selected.status_code, 200, selected.text)
        payload = selected.json()
        self.assertEqual(payload["status"], "ready")
        self.assertTrue(payload["metadata_json"]["selected"])
        self.assertNotIn(
            "download_url",
            payload["metadata_json"]["selected_candidate"],
        )
        self.assertEqual(
            len(payload["metadata_json"]["license_checked_by_user_id"]),
            36,
        )

        with db.SessionLocal() as session:
            searched = session.get(Asset, self.searched_asset_id)
            generated = session.get(Asset, self.generated_asset_id)
            self.assertTrue(searched.metadata_json["selected"])
            self.assertFalse(generated.metadata_json["selected"])
            self.assertTrue(searched.storage_uri.startswith("file:"))

        downloaded = self.client.get(
            f"/api/v1/assets/{self.searched_asset_id}/download",
            headers=self.headers,
        )
        self.assertEqual(downloaded.status_code, 200, downloaded.text)
        with Image.open(io.BytesIO(downloaded.content)) as decoded:
            self.assertEqual(decoded.size, (48, 30))


if __name__ == "__main__":
    unittest.main()
