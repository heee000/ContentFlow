from __future__ import annotations

import io
import tempfile
import unittest
import uuid
from unittest.mock import patch
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import delete, func, select

from contentflow import db
from contentflow.api import create_app
from contentflow.entities import (
    Asset,
    Campaign,
    ContentItem,
    Job,
    JobManualReview,
    Membership,
    ProviderInvocationAttempt,
    StorageObjectAllocation,
    User,
    WorkflowRun,
    WorkspaceStorageUsage,
)
from contentflow.object_storage import build_object_storage
from contentflow.job_queue import enqueue_job, request_job_manual_review
from contentflow.models import CampaignBrief
from contentflow.media_providers import media_provider_profile_fingerprint
from contentflow.provider_invocations import ProviderInvocationLedger
from contentflow.settings import Settings
from contentflow.worker import Worker
from contentflow.workflow import build_asset_tasks


class ManualMediaFlowTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.settings = Settings(
            _env_file=None,
            environment="test",
            database_url=f"sqlite:///{(root / 'manual-media.db').as_posix()}",
            secret_key="manual-media-test-secret",
            storage_backend="local",
            local_storage_dir=root / "storage",
            allow_registration=True,
            image_provider="manual",
            video_provider="manual",
            require_governed_prompts=False,
            embedding_provider="hash",
            text_provider="mock",
            image_search_provider="openverse",
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
                "acknowledge_review_warnings": True,
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
                "reason": "已人工核验当前合成稿件及规则例外",
                "acknowledge_review_warnings": True,
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

    def test_failed_asset_replacement_deletes_old_object_via_retryable_job(self):
        reviewed = self.client.post(
            f"/api/v1/contents/{self.content_id}/review",
            headers=self.headers,
            json={
                "decision": "approve",
                "reason": "已人工核验当前合成稿件及规则例外",
                "acknowledge_review_warnings": True,
                "expected_version": 1,
            },
        )
        self.assertEqual(reviewed.status_code, 200, reviewed.text)
        first_cover = io.BytesIO()
        Image.new("RGB", (24, 16), color=(10, 20, 30)).save(
            first_cover,
            format="PNG",
        )
        first = self.client.post(
            "/api/v1/assets/upload",
            headers=self.headers,
            data={"asset_id": self.asset_id},
            files={"file": ("first.png", first_cover.getvalue(), "image/png")},
        )
        self.assertEqual(first.status_code, 201, first.text)
        with db.SessionLocal() as session:
            asset = session.get(Asset, self.asset_id)
            old_uri = asset.storage_uri
            asset.status = "failed"
            session.commit()

        second_cover = io.BytesIO()
        Image.new("RGB", (24, 16), color=(90, 80, 70)).save(
            second_cover,
            format="PNG",
        )
        second = self.client.post(
            "/api/v1/assets/upload",
            headers=self.headers,
            data={"asset_id": self.asset_id},
            files={"file": ("second.png", second_cover.getvalue(), "image/png")},
        )
        self.assertEqual(second.status_code, 201, second.text)
        new_uri = second.json()["storage_uri"]
        self.assertNotEqual(new_uri, old_uri)

        with db.SessionLocal() as session:
            allocations = list(
                session.scalars(
                    select(StorageObjectAllocation)
                    .where(StorageObjectAllocation.owner_id == self.asset_id)
                    .order_by(StorageObjectAllocation.created_at.asc())
                )
            )
            usage = session.get(WorkspaceStorageUsage, self.workspace_id)
            self.assertEqual(
                [allocation.status for allocation in allocations],
                ["delete_pending", "active"],
            )
            self.assertEqual(usage.used_objects, 2)
        storage = build_object_storage(self.settings)
        self.assertIsNotNone(old_uri)
        storage.read(old_uri)

        with Worker(settings=self.settings) as worker:
            self.assertTrue(worker.run_once())
        with self.assertRaises(FileNotFoundError):
            storage.read(old_uri)
        self.assertIsNotNone(storage.read(new_uri))
        with db.SessionLocal() as session:
            usage = session.get(WorkspaceStorageUsage, self.workspace_id)
            self.assertEqual(usage.used_objects, 1)

    def test_new_manual_asset_is_rejected_at_current_version_quota(self):
        reviewed = self.client.post(
            f"/api/v1/contents/{self.content_id}/review",
            headers=self.headers,
            json={
                "decision": "approve",
                "reason": "已人工核验当前合成稿件及规则例外",
                "acknowledge_review_warnings": True,
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
                "reason": "已人工核验当前合成稿件及规则例外",
                "acknowledge_review_warnings": True,
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
                "acknowledge_review_warnings": True,
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
            # The test simulates a completed search, including its queue state.
            search_job = session.scalar(select(Job).where(Job.job_type == "asset.search"))
            search_job.status = "succeeded"
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

    def seed_unknown_generation(self, *, review=True, ledger=True):
        self.settings.image_provider = "http"
        with db.SessionLocal() as session:
            session.get(ContentItem, self.content_id).status = "approved"
            asset = session.get(Asset, self.asset_id)
            asset.provider = "http"
            asset.status = "failed"
            job = enqueue_job(
                session, job_type="asset.generate", payload={"asset_id": asset.id},
                workspace_id=self.workspace_id, idempotency_key=f"unknown:{uuid.uuid4()}",
            )
            job.status = "failed"
            job_id = job.id
            session.commit()
        if ledger:
            call_ledger = ProviderInvocationLedger(db.engine)
            handle = call_ledger.start(
                workspace_id=self.workspace_id, job_id=job_id, entity_type="asset",
                entity_id=self.asset_id, provider_kind="media", provider_name="http",
                model_name="offline-test", operation="media.generate", ordinal=1,
                request_sha256="a" * 64, request_bytes=10, idempotency_key_sent=True,
            )
            call_ledger.finish(
                handle, status="outcome_unknown", call_metadata={}, error_type="TimeoutError"
            )
        if review:
            with db.SessionLocal() as session:
                request_job_manual_review(
                    session, session.get(Job, job_id), reason_code="test_unknown",
                    error="synthetic unknown outcome", source="test",
                )
                session.commit()
        return job_id

    def test_all_asset_entry_points_preserve_unresolved_review(self):
        job_id = self.seed_unknown_generation()
        for role in ("editor", "reviewer"):
            with self.subTest(role=role):
                with db.SessionLocal() as session:
                    session.scalar(select(Membership).where(
                        Membership.workspace_id == self.workspace_id
                    )).role = role
                    session.commit()
                responses = [
                    self.client.post(f"/api/v1/assets/{self.asset_id}/retry", headers=self.headers),
                    self.client.post(f"/api/v1/jobs/{job_id}/retry", headers=self.headers),
                    *[self.client.post(f"/api/v1/assets/{self.asset_id}/source",
                        headers=self.headers, json={"source": source})
                      for source in ("generate", "manual", "search")],
                    self.client.post("/api/v1/assets/upload", headers=self.headers,
                        data={"asset_id": self.asset_id},
                        files={"file": ("cover.png", b"not-read", "image/png")}),
                ]
                self.assertEqual([r.status_code for r in responses], [409] * len(responses))
        with db.SessionLocal() as session:
            self.assertEqual(session.scalar(select(func.count(Job.id))), 1)
            self.assertEqual(session.get(Asset, self.asset_id).status, "failed")
            self.assertIsNone(session.scalar(select(JobManualReview)).resolved_at)

    def test_edit_and_reapproval_cannot_hide_old_unknown_generation(self):
        self.seed_unknown_generation()
        edited = self.client.patch(f"/api/v1/contents/{self.content_id}",
            headers=self.headers, json={"body": "新的正文，旧请求仍需核对", "expected_version": 1})
        self.assertEqual(edited.status_code, 200, edited.text)
        reviewed = self.client.post(f"/api/v1/contents/{self.content_id}/review",
            headers=self.headers, json={"decision": "approve", "expected_version": 2,
                "acknowledge_review_warnings": True, "reason": "明确核验合成稿件，仍不得绕过素材未知门禁"})
        self.assertEqual(reviewed.status_code, 409, reviewed.text)
        with db.SessionLocal() as session:
            self.assertEqual(session.get(ContentItem, self.content_id).status, "needs_review")
            self.assertEqual(session.scalar(select(func.count(Job.id))), 1)

    def test_unknown_ledger_can_be_escalated_without_claiming_it_was_checked(self):
        job_id = self.seed_unknown_generation(review=False)
        rejected = self.client.post(f"/api/v1/assets/{self.asset_id}/retry", headers=self.headers)
        self.assertEqual(rejected.status_code, 409, rejected.text)
        for _ in range(2):
            escalated = self.client.post(f"/api/v1/jobs/{job_id}/request-manual-review", headers=self.headers)
            self.assertEqual(escalated.status_code, 200, escalated.text)
            self.assertEqual(escalated.json()["status"], "manual_review")
        with db.SessionLocal() as session:
            self.assertEqual(session.scalar(select(func.count(JobManualReview.id))), 1)
            self.assertFalse(session.scalar(select(JobManualReview)).provider_checked)
        resolved = self.client.post(f"/api/v1/jobs/{job_id}/manual-review", headers=self.headers,
            json={"decision": "retry", "provider_checked": True, "note": "offline fixture: operator checked"})
        self.assertEqual(resolved.status_code, 200, resolved.text)
        self.assertEqual(resolved.json()["status"], "retry")
        duplicate = self.client.post(f"/api/v1/assets/{self.asset_id}/retry", headers=self.headers)
        self.assertEqual(duplicate.status_code, 409, duplicate.text)
        with db.SessionLocal() as session:
            self.assertEqual(session.scalar(select(func.count(Job.id))), 1)
            self.assertEqual(session.scalar(select(ProviderInvocationAttempt)).status, "outcome_unknown")

    def test_verified_abandon_allows_a_new_operation_but_not_future_unknowns(self):
        job_id = self.seed_unknown_generation()
        resolved = self.client.post(f"/api/v1/jobs/{job_id}/manual-review", headers=self.headers,
            json={"decision": "abandon", "provider_checked": True, "note": "offline fixture: checked and abandoned"})
        self.assertEqual(resolved.status_code, 200, resolved.text)
        retry = self.client.post(f"/api/v1/assets/{self.asset_id}/retry", headers=self.headers)
        self.assertEqual(retry.status_code, 202, retry.text)
        self.assertNotEqual(retry.json()["id"], job_id)
        with db.SessionLocal() as session:
            session.get(Job, retry.json()["id"]).status = "failed"
            session.get(Asset, self.asset_id).status = "failed"
            session.scalar(select(ProviderInvocationAttempt)).started_at = datetime.now(timezone.utc)
            session.commit()
        blocked = self.client.post(f"/api/v1/assets/{self.asset_id}/retry", headers=self.headers)
        self.assertEqual(blocked.status_code, 409, blocked.text)

    def test_manual_review_escalation_is_reviewer_and_workspace_scoped(self):
        job_id = self.seed_unknown_generation(review=False)
        with db.SessionLocal() as session:
            session.scalar(select(Membership)).role = "editor"
            session.commit()
        denied = self.client.post(f"/api/v1/jobs/{job_id}/request-manual-review", headers=self.headers)
        self.assertEqual(denied.status_code, 403, denied.text)
        other = self.client.post("/api/v1/auth/register", json={
            "email": "other-audit@example.com", "password": "other-audit-password",
            "display_name": "Other", "workspace_name": "Other workspace",
        })
        other_headers = {"Authorization": f"Bearer {other.json()['access_token']}"}
        missing = self.client.post(f"/api/v1/jobs/{job_id}/request-manual-review", headers=other_headers)
        self.assertEqual(missing.status_code, 404, missing.text)
        with db.SessionLocal() as session:
            self.assertEqual(session.scalar(select(func.count(JobManualReview.id))), 0)

    def test_image_source_matrix_respects_explicit_choice(self):
        brief = CampaignBrief.from_dict({
            "campaign_name": "source matrix", "product_name": "fixture",
            "goal": "offline source selection", "audience": "test", "platforms": ["wechat"],
        })
        for configured in ("manual", "http", "mock"):
            for source in ("manual", "generate", "search", "hybrid"):
                with self.subTest(configured=configured, source=source):
                    self.settings.image_provider = configured
                    tasks = build_asset_tasks("wechat", brief, {}, {"title": "fixture", "body": "fixture"},
                        image_source=source)
                    with db.SessionLocal() as session:
                        session.execute(delete(Job))
                        session.execute(delete(Asset))
                        session.get(ContentItem, self.content_id).status = "needs_review"
                        for task in tasks:
                            session.add(Asset(
                                workspace_id=self.workspace_id, content_item_id=self.content_id,
                                content_version=1, kind=task["type"], provider=task["provider"],
                                status="planned", metadata_json=task,
                            ))
                        session.commit()
                    result = self.client.post(f"/api/v1/contents/{self.content_id}/review",
                        headers=self.headers, json={"decision": "approve", "expected_version": 1,
                            "acknowledge_review_warnings": True, "reason": "已人工核验本版本，测试指定素材来源"})
                    self.assertEqual(result.status_code, 200, result.text)
                    with db.SessionLocal() as session:
                        assets = list(session.scalars(select(Asset)))
                        generated = session.scalar(select(func.count(Job.id)).where(Job.job_type == "asset.generate"))
                        searched = session.scalar(select(func.count(Job.id)).where(Job.job_type == "asset.search"))
                        self.assertEqual(generated, int(source in {"generate", "hybrid"} and configured != "manual"))
                        self.assertEqual(searched, int(source in {"search", "hybrid"}))
                        if source == "manual":
                            self.assertEqual([(a.provider, a.status) for a in assets], [("manual", "awaiting_upload")])
                        if configured == "manual" and source in {"generate", "hybrid"}:
                            failed = next(a for a in assets if a.provider == "configured-image-generation")
                            self.assertEqual(failed.status, "failed")

    def test_uploaded_source_survives_edit_with_ai_globally_enabled(self):
        self.settings.image_provider = "http"
        self.settings.video_provider = "http"
        with db.SessionLocal() as session:
            asset = session.get(Asset, self.asset_id)
            asset.provider = "manual-upload"
            asset.status = "ready"
            session.add(Asset(
                workspace_id=self.workspace_id, content_item_id=self.content_id,
                content_version=1, kind="video_storyboard", provider="manual", status="ready",
            ))
            session.commit()
        edited = self.client.patch(f"/api/v1/contents/{self.content_id}", headers=self.headers,
            json={"body": "编辑后的内容仍保留人工来源", "expected_version": 1})
        self.assertEqual(edited.status_code, 200, edited.text)
        approved = self.client.post(f"/api/v1/contents/{self.content_id}/review", headers=self.headers,
            json={"decision": "approve", "expected_version": 2,
                "acknowledge_review_warnings": True, "reason": "已核验修改版本，测试素材来源保留"})
        self.assertEqual(approved.status_code, 200, approved.text)
        with db.SessionLocal() as session:
            assets = list(session.scalars(select(Asset).where(Asset.content_version == 2)))
            self.assertEqual(len(assets), 2)
            self.assertTrue(all(a.provider == "manual" and a.status == "awaiting_upload" for a in assets))
            self.assertEqual(session.scalar(select(func.count(Job.id))), 0)

    def test_existing_ai_source_does_not_silently_become_manual(self):
        with db.SessionLocal() as session:
            asset = session.get(Asset, self.asset_id)
            asset.provider = "http"
            session.commit()
        approved = self.client.post(f"/api/v1/contents/{self.content_id}/review", headers=self.headers,
            json={"decision": "approve", "expected_version": 1,
                "acknowledge_review_warnings": True, "reason": "已人工核验本版本，测试现有生成来源"})
        self.assertEqual(approved.status_code, 200, approved.text)
        with db.SessionLocal() as session:
            asset = session.get(Asset, self.asset_id)
            self.assertEqual((asset.provider, asset.status), ("http", "failed"))
            self.assertTrue(asset.metadata_json["provider_configuration_required"])
            self.assertEqual(session.scalar(select(func.count(Job.id))), 0)

    def test_worker_rejects_provider_drift_before_any_external_call(self):
        private = "TEST_ONLY_PRIVATE_CONFIGURATION"
        self.settings.media_api_base = f"https://media.example/{private}"
        self.settings.image_model = private
        for selected, configured in (("http", "manual"), ("http", "mock"), ("mock", "http")):
            with self.subTest(selected=selected, configured=configured):
                self.settings.image_provider = configured
                with db.SessionLocal() as session:
                    session.get(ContentItem, self.content_id).status = "approved"
                    asset = session.get(Asset, self.asset_id)
                    asset.provider = selected
                    asset.status = "queued"
                    job = enqueue_job(
                        session, job_type="asset.generate", payload={"asset_id": asset.id},
                        workspace_id=self.workspace_id, idempotency_key=uuid.uuid4().hex,
                    )
                    session.commit()
                    job_id = job.id
                with patch("contentflow.worker.build_media_provider") as provider, self.assertLogs(
                    "contentflow.worker", level="ERROR"
                ) as logs:
                    self.assertTrue(Worker(settings=self.settings, session_factory=db.SessionLocal).run_once())
                    provider.assert_not_called()
                with db.SessionLocal() as session:
                    asset = session.get(Asset, self.asset_id)
                    job = session.get(Job, job_id)
                    self.assertEqual((asset.provider, asset.status), (selected, "failed"))
                    self.assertEqual(job.status, "failed")
                    self.assertIn("配置与已批准的来源不一致", asset.error)
                    self.assertIn("media_source_configuration_changed", asset.error)
                    self.assertIn("请恢复对应配置", asset.error)
                    self.assertEqual(job.last_error, asset.error)
                    self.assertNotIn(private, asset.error + "".join(logs.output))

    def test_worker_poll_configuration_failure_keeps_safe_recovery_guidance(self):
        self.settings.image_provider = "http"
        self.settings.image_model = "TEST_ONLY_ORIGINAL_MODEL"
        original_profile = media_provider_profile_fingerprint(self.settings, "image")
        self.settings.image_model = "TEST_ONLY_CHANGED_PRIVATE_MODEL"
        for profile in (None, original_profile):
            with self.subTest(missing_profile=profile is None):
                with db.SessionLocal() as session:
                    session.get(ContentItem, self.content_id).status = "approved"
                    asset = session.get(Asset, self.asset_id)
                    asset.provider = "http"
                    asset.status = "processing"
                    asset.external_task_id = "TEST_ONLY_REMOTE_TASK"
                    asset.metadata_json = {"media_provider_profile_fingerprint": profile}
                    job = enqueue_job(session, job_type="asset.poll",
                        payload={"asset_id": asset.id}, workspace_id=self.workspace_id,
                        idempotency_key=uuid.uuid4().hex)
                    session.commit()
                    job_id = job.id
                with patch("contentflow.worker.build_media_provider") as provider, self.assertLogs(
                    "contentflow.worker", level="ERROR"
                ) as logs:
                    self.assertTrue(Worker(settings=self.settings, session_factory=db.SessionLocal).run_once())
                    provider.assert_not_called()
                with db.SessionLocal() as session:
                    asset = session.get(Asset, self.asset_id)
                    job = session.get(Job, job_id)
                    self.assertEqual((asset.status, job.status), ("failed", "failed"))
                    self.assertIn("media_poll_configuration_changed", asset.error)
                    self.assertIn("请人工核对原任务", asset.error)
                    self.assertIn("确认远端结果前不要重新生成", asset.error)
                    self.assertEqual(job.last_error, asset.error)
                    self.assertNotIn(self.settings.image_model, asset.error + "".join(logs.output))
                    self.assertNotIn(asset.external_task_id, asset.error + "".join(logs.output))


if __name__ == "__main__":
    unittest.main()
