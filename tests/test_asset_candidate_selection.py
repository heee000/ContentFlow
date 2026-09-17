from __future__ import annotations

import io
import json
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
from contentflow.entities import (
    Asset,
    Campaign,
    ContentItem,
    Job,
    Membership,
    ProviderInvocation,
    ProviderInvocationAttempt,
    User,
    WorkflowRun,
)
from contentflow.settings import Settings
from contentflow.media_providers import MediaProviderError
from contentflow.worker import Worker, handle_asset_download


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
        self.assertEqual(payload["status"], "queued")
        self.assertFalse(payload["metadata_json"]["selected"])
        self.assertEqual(
            payload["metadata_json"]["pending_candidate_selection"]["candidate_id"],
            "candidate-1",
        )

        with db.SessionLocal() as session:
            download_job = session.scalar(
                select(Job).where(
                    Job.job_type == "asset.download",
                    Job.payload_json["asset_id"].as_string() == self.searched_asset_id,
                )
            )
            self.assertIsNotNone(download_job)
            self.assertEqual(download_job.status, "queued")
            download_job_id = download_job.id
            download_payload = dict(download_job.payload_json)

        worker = Worker(
            settings=self.settings,
            session_factory=db.SessionLocal,
            worker_id="asset-download-worker",
        )
        with patch(
            "contentflow.worker.download_generated_media",
            return_value=self.image_bytes(),
        ):
            self.assertTrue(worker.run_once())

        detail = self.client.get(
            f"/api/v1/assets/{self.searched_asset_id}",
            headers=self.headers,
        )
        self.assertEqual(detail.status_code, 200, detail.text)
        payload = detail.json()
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
            download_job = session.get(Job, download_job_id)
            invocation = session.scalar(
                select(ProviderInvocation).where(
                    ProviderInvocation.entity_id == self.searched_asset_id,
                    ProviderInvocation.operation == "search.download",
                )
            )
            attempt = session.scalar(
                select(ProviderInvocationAttempt).where(
                    ProviderInvocationAttempt.invocation_id == invocation.id
                )
            )
            self.assertTrue(searched.metadata_json["selected"])
            self.assertFalse(generated.metadata_json["selected"])
            self.assertTrue(searched.storage_uri.startswith("file:"))
            self.assertEqual(download_job.status, "succeeded")
            self.assertEqual(invocation.job_id, download_job_id)
            self.assertEqual(attempt.status, "succeeded")
            self.assertEqual(attempt.response_bytes, len(self.image_bytes()))
            serialized = json.dumps(
                {
                    "request_sha256": invocation.request_sha256,
                    "response_sha256": attempt.response_sha256,
                }
            )
        self.assertNotIn("upload.wikimedia.org", serialized)

        evidence = self.client.get(
            f"/api/v1/assets/{self.searched_asset_id}/provider-invocations",
            headers=self.headers,
        )
        self.assertEqual(evidence.status_code, 200, evidence.text)
        self.assertEqual(len(evidence.json()), 1)
        self.assertEqual(evidence.json()[0]["provider_kind"], "media")
        self.assertEqual(evidence.json()[0]["operation"], "search.download")

        with db.SessionLocal() as session, patch(
            "contentflow.worker.download_generated_media",
            side_effect=AssertionError("completed download must not be repeated"),
        ):
            self.assertEqual(
                handle_asset_download(session, download_payload, self.settings)["status"],
                "ready",
            )

        downloaded = self.client.get(
            f"/api/v1/assets/{self.searched_asset_id}/download",
            headers=self.headers,
        )
        self.assertEqual(downloaded.status_code, 200, downloaded.text)
        with Image.open(io.BytesIO(downloaded.content)) as decoded:
            self.assertEqual(decoded.size, (48, 30))

    def test_download_retry_preserves_selection_and_creates_a_new_job_each_time(self):
        selected = self.client.post(
            f"/api/v1/assets/{self.searched_asset_id}/select",
            headers=self.headers,
            json={"candidate_id": "candidate-1", "acknowledge_license_check": True},
        )
        self.assertEqual(selected.status_code, 200, selected.text)
        worker = Worker(settings=self.settings, session_factory=db.SessionLocal)
        retry_ids = []
        for sequence in (1, 2):
            with patch(
                "contentflow.worker.download_generated_media",
                side_effect=MediaProviderError("download rejected", retryable=False),
            ):
                self.assertTrue(worker.run_once())
            retried = self.client.post(
                f"/api/v1/assets/{self.searched_asset_id}/retry",
                headers=self.headers,
            )
            self.assertEqual(retried.status_code, 202, retried.text)
            job = retried.json()
            self.assertEqual(job["job_type"], "asset.download")
            self.assertEqual(job["status"], "queued")
            retry_ids.append(job["id"])
            with db.SessionLocal() as session:
                asset = session.get(Asset, self.searched_asset_id)
                self.assertEqual(asset.metadata_json["retry_sequence"], sequence)
                self.assertEqual(
                    asset.metadata_json["pending_candidate_selection"]["candidate_id"],
                    "candidate-1",
                )
        self.assertEqual(len(set(retry_ids)), 2)

    def test_asset_evidence_is_workspace_scoped_and_reviewer_only(self):
        other = self.client.post(
            "/api/v1/auth/register",
            json={
                "email": "other-selector@example.com",
                "password": "a-secure-password",
                "display_name": "Other Workspace",
                "workspace_name": "Other Workspace",
            },
        )
        self.assertEqual(other.status_code, 201, other.text)
        response = self.client.get(
            f"/api/v1/assets/{self.searched_asset_id}/provider-invocations",
            headers={"Authorization": f"Bearer {other.json()['access_token']}"},
        )
        self.assertEqual(response.status_code, 404, response.text)
        with db.SessionLocal() as session:
            membership = session.scalar(
                select(Membership).where(Membership.workspace_id == self.workspace_id)
            )
            membership.role = "editor"
            session.commit()
        response = self.client.get(
            f"/api/v1/assets/{self.searched_asset_id}/provider-invocations",
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 403, response.text)


if __name__ == "__main__":
    unittest.main()
