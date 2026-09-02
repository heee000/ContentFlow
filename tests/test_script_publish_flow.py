from __future__ import annotations

import hashlib
import io
import tempfile
import unittest
import uuid
import zipfile
from PIL import Image
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from contentflow import db
from contentflow.api import create_app
from contentflow.entities import (
    Asset,
    AuditLog,
    Campaign,
    ContentItem,
    Job,
    PublishJob,
    User,
    WorkflowRun,
)
from contentflow.object_storage import build_object_storage
from contentflow.settings import Settings
from contentflow.worker import Worker


class ScriptPublishFlowTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.settings = Settings(
            _env_file=None,
            environment="development",
            database_url=f"sqlite:///{(root / 'script-publish.db').as_posix()}",
            secret_key="script-publish-test-secret",
            local_storage_dir=root / "storage",
            storage_backend="local",
            allow_registration=True,
            require_governed_prompts=False,
            metrics_enabled=False,
            embedding_provider="hash",
            text_provider="mock",
            image_provider="mock",
            video_provider="mock",
        )
        self.client = TestClient(create_app(self.settings))
        self.client.__enter__()
        registered = self.client.post(
            "/api/v1/auth/register",
            json={
                "email": "script-publisher@example.com",
                "password": "a-secure-password",
                "display_name": "Script Publisher",
                "workspace_name": "Script Publish Workspace",
            },
        )
        self.assertEqual(registered.status_code, 201, registered.text)
        self.headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
        self.workspace_id = registered.json()["workspace_id"]
        self.worker = Worker(
            settings=self.settings,
            session_factory=db.SessionLocal,
            worker_id="script-publish-worker",
        )
        self.content_id = self._create_approved_content()
        channel = self.client.post(
            "/api/v1/channels",
            headers=self.headers,
            json={
                "platform": "xiaohongshu",
                "display_name": "本机脚本账号",
                "connection_mode": "script",
                "credentials": {},
                "config": {},
            },
        )
        self.assertEqual(channel.status_code, 201, channel.text)
        self.assertEqual(channel.json()["status"], "script_only")
        self.channel_id = channel.json()["id"]

    def tearDown(self):
        self.client.__exit__(None, None, None)
        db.engine.dispose()
        self.temp_dir.cleanup()

    def _create_approved_content(self) -> str:
        suffix = uuid.uuid4().hex[:10]
        storage = build_object_storage(self.settings)
        stored = storage.put(
            workspace_id=self.workspace_id,
            category="assets",
            filename="approved-cover.png",
            stream=io.BytesIO(b"\x89PNG\r\napproved-script-cover"),
            content_type="image/png",
        )
        with db.SessionLocal() as session:
            user = session.scalar(
                select(User).where(User.email == "script-publisher@example.com")
            )
            self.assertIsNotNone(user)
            campaign = Campaign(
                workspace_id=self.workspace_id,
                created_by=user.id,
                name=f"脚本发布-{suffix}",
                product_name="测试产品",
                objective="验证脚本发布安全回退",
                audience="测试用户",
                platforms=["xiaohongshu"],
                status="active",
            )
            session.add(campaign)
            session.flush()
            run = WorkflowRun(
                workspace_id=self.workspace_id,
                campaign_id=campaign.id,
                status="awaiting_review",
                current_stage="human_review",
                provider="mock",
                trace_id=f"script-{suffix}",
            )
            session.add(run)
            session.flush()
            content = ContentItem(
                workspace_id=self.workspace_id,
                campaign_id=campaign.id,
                run_id=run.id,
                platform="xiaohongshu",
                title="脚本发布安全测试",
                body="该内容已经人工审核，脚本只辅助填充。",
                hashtags=["测试", "安全发布"],
                call_to_action="最终提交前再次核对",
                status="approved",
                version=1,
                approved_by=user.id,
                approved_at=datetime.now(timezone.utc),
            )
            session.add(content)
            session.flush()
            session.add(
                Asset(
                    workspace_id=self.workspace_id,
                    content_item_id=content.id,
                    kind="image",
                    provider="upload",
                    status="ready",
                    storage_uri=stored.uri,
                    mime_type="image/png",
                    size_bytes=stored.size_bytes,
                    metadata_json={"content_version": 1},
                )
            )
            session.commit()
            return content.id

    def _add_reviewer(self, email: str, display_name: str) -> dict[str, str]:
        registered = self.client.post(
            "/api/v1/auth/register",
            json={
                "email": email,
                "password": "a-second-secure-password",
                "display_name": display_name,
                "workspace_name": f"{display_name} Workspace",
            },
        )
        self.assertEqual(registered.status_code, 201, registered.text)
        isolated_headers = {
            "Authorization": f"Bearer {registered.json()['access_token']}"
        }
        member = self.client.post(
            "/api/v1/admin/members",
            headers=self.headers,
            json={"email": email, "role": "reviewer"},
        )
        self.assertEqual(member.status_code, 201, member.text)
        switched = self.client.post(
            f"/api/v1/auth/switch/{self.workspace_id}",
            headers=isolated_headers,
        )
        self.assertEqual(switched.status_code, 200, switched.text)
        return {"Authorization": f"Bearer {switched.json()['access_token']}"}

    def _schedule(self, *, mode: str = "script", channel_id: str | None = None) -> dict:
        response = self.client.post(
            "/api/v1/publishing/jobs",
            headers=self.headers,
            json={
                "content_item_id": self.content_id,
                "channel_id": channel_id or self.channel_id,
                "delivery_mode": mode,
                "scheduled_at": (
                    datetime.now(timezone.utc) + timedelta(minutes=1)
                ).isoformat(),
            },
        )
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()

    def _make_dispatch_due(self, publish_job_id: str) -> str:
        with db.SessionLocal() as session:
            queue_job = session.scalar(
                select(Job).where(
                    Job.idempotency_key == f"publish.dispatch:{publish_job_id}"
                )
            )
            self.assertIsNotNone(queue_job)
            queue_job.run_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            session.commit()
            return queue_job.id

    def _prepare_script_job(self) -> dict:
        scheduled = self._schedule()
        self._make_dispatch_due(scheduled["id"])
        self.assertTrue(self.worker.run_once())
        current = self.client.get(
            "/api/v1/publishing/jobs", headers=self.headers
        ).json()[0]
        self.assertEqual(current["status"], "script_ready")
        return scheduled

    def _upload_json_evidence(self, publish_job_id: str, name: str, data: bytes):
        return self.client.post(
            f"/api/v1/publishing/jobs/{publish_job_id}/evidence",
            headers=self.headers,
            data={"kind": "platform_export"},
            files={"file": (name, data, "application/json")},
        )

    def test_script_channel_rejects_credentials_and_remote_test(self):
        credentialed = self.client.post(
            "/api/v1/channels",
            headers=self.headers,
            json={
                "platform": "douyin",
                "display_name": "不应保存凭据",
                "connection_mode": "script",
                "credentials": {"access_token": "secret", "open_id": "user"},
            },
        )
        self.assertEqual(credentialed.status_code, 422, credentialed.text)
        tested = self.client.post(
            f"/api/v1/channels/{self.channel_id}/test",
            headers=self.headers,
        )
        self.assertEqual(tested.status_code, 409, tested.text)

    def test_script_evidence_item_quota_rejects_before_object_write(self):
        self.settings.publish_evidence_max_items = 2
        scheduled = self._prepare_script_job()

        first = self._upload_json_evidence(
            scheduled["id"], "first.json", b'{"proof":"first"}'
        )
        second = self._upload_json_evidence(
            scheduled["id"], "second.json", b'{"proof":"second"}'
        )
        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(second.status_code, 201, second.text)
        files_before = {
            path
            for path in self.settings.local_storage_dir.rglob("*")
            if path.is_file()
        }

        rejected = self._upload_json_evidence(
            scheduled["id"], "third.json", b'{"proof":"third"}'
        )

        self.assertEqual(rejected.status_code, 409, rejected.text)
        self.assertIn("item quota", rejected.json()["error"]["message"])
        evidence = self.client.get(
            f"/api/v1/publishing/jobs/{scheduled['id']}/evidence",
            headers=self.headers,
        )
        self.assertEqual(evidence.status_code, 200, evidence.text)
        self.assertEqual(len(evidence.json()), 2)
        files_after = {
            path
            for path in self.settings.local_storage_dir.rglob("*")
            if path.is_file()
        }
        self.assertEqual(files_after, files_before)

    def test_script_evidence_byte_quota_rejects_before_object_write(self):
        self.settings.publish_evidence_max_bytes = 256
        self.settings.publish_evidence_max_total_bytes = 300
        scheduled = self._prepare_script_job()
        payload = b'{"proof":"' + (b"a" * 180) + b'"}'
        first = self._upload_json_evidence(scheduled["id"], "first.json", payload)
        self.assertEqual(first.status_code, 201, first.text)
        files_before = {
            path
            for path in self.settings.local_storage_dir.rglob("*")
            if path.is_file()
        }

        rejected = self._upload_json_evidence(
            scheduled["id"],
            "second.json",
            b'{"proof":"' + (b"b" * 180) + b'"}',
        )

        self.assertEqual(rejected.status_code, 413, rejected.text)
        self.assertIn("storage quota", rejected.json()["error"]["message"])
        evidence = self.client.get(
            f"/api/v1/publishing/jobs/{scheduled['id']}/evidence",
            headers=self.headers,
        )
        self.assertEqual(len(evidence.json()), 1)
        files_after = {
            path
            for path in self.settings.local_storage_dir.rglob("*")
            if path.is_file()
        }
        self.assertEqual(files_after, files_before)

    def test_script_schedule_worker_download_and_human_result(self):
        scheduled = self._schedule()
        self.assertEqual(scheduled["delivery_mode"], "script")
        queue_job_id = self._make_dispatch_due(scheduled["id"])
        self.assertTrue(self.worker.run_once())

        current = self.client.get(
            "/api/v1/publishing/jobs", headers=self.headers
        ).json()[0]
        self.assertEqual(current["status"], "script_ready")
        self.assertEqual(current["delivery_mode"], "script")
        self.assertFalse(current["script_confirmation_expired"])
        self.assertIsNotNone(current["script_requested_by_user_id"])
        self.assertGreater(
            datetime.fromisoformat(current["script_confirmation_expires_at"]),
            datetime.now(timezone.utc),
        )
        self.assertIsNone(current["external_id"])
        artifact = self.client.get(
            f"/api/v1/publishing/jobs/{scheduled['id']}/artifact",
            headers=self.headers,
        )
        self.assertEqual(artifact.status_code, 200, artifact.text)
        self.assertEqual(
            artifact.headers["x-contentflow-artifact-sha256"],
            hashlib.sha256(artifact.content).hexdigest(),
        )
        automatic_metrics = self.client.post(
            f"/api/v1/metrics/pull/{scheduled['id']}", headers=self.headers
        )
        self.assertEqual(automatic_metrics.status_code, 409, automatic_metrics.text)
        missing_evidence = self.client.post(
            f"/api/v1/publishing/jobs/{scheduled['id']}/script-result",
            headers=self.headers,
            json={
                "decision": "confirmed_published",
                "reason": "运营人员已在平台后台按标题和时间核对",
                "external_id": "script-post-001",
            },
        )
        self.assertEqual(missing_evidence.status_code, 409, missing_evidence.text)

        evidence_buffer = io.BytesIO()
        Image.new("RGB", (12, 8), color=(13, 89, 140)).save(
            evidence_buffer,
            format="PNG",
        )
        evidence = self.client.post(
            f"/api/v1/publishing/jobs/{scheduled['id']}/evidence",
            headers=self.headers,
            data={"kind": "screenshot"},
            files={
                "file": (
                    "platform-result.png",
                    evidence_buffer.getvalue(),
                    "text/html",
                )
            },
        )
        self.assertEqual(evidence.status_code, 201, evidence.text)
        evidence_payload = evidence.json()
        self.assertEqual(evidence_payload["mime_type"], "image/png")
        evidence_list = self.client.get(
            f"/api/v1/publishing/jobs/{scheduled['id']}/evidence",
            headers=self.headers,
            params={"limit": 1},
        )
        self.assertEqual(evidence_list.status_code, 200, evidence_list.text)
        self.assertEqual(evidence_list.headers["x-contentflow-page-limit"], "1")
        self.assertEqual(
            [item["id"] for item in evidence_list.json()],
            [evidence_payload["id"]],
        )
        evidence_download = self.client.get(
            (
                f"/api/v1/publishing/jobs/{scheduled['id']}/evidence/"
                f"{evidence_payload['id']}/download"
            ),
            headers=self.headers,
        )
        self.assertEqual(evidence_download.status_code, 200, evidence_download.text)
        self.assertEqual(
            evidence_download.headers["x-contentflow-evidence-sha256"],
            hashlib.sha256(evidence_download.content).hexdigest(),
        )
        self.assertIn("人工指标", automatic_metrics.json()["error"]["message"])
        with zipfile.ZipFile(io.BytesIO(artifact.content)) as archive:
            self.assertIn("publish_assistant.py", archive.namelist())
            runner = archive.read("publish_assistant.py").decode("utf-8")
            self.assertNotIn(".click(", runner)

        reviewer_headers = self._add_reviewer(
            "result-reviewer@example.com", "Result Reviewer"
        )
        result = self.client.post(
            f"/api/v1/publishing/jobs/{scheduled['id']}/script-result",
            headers=reviewer_headers,
            json={
                "decision": "confirmed_published",
                "reason": "运营人员已在平台后台按标题和时间核对",
                "external_id": "script-post-001",
            },
        )
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["status"], "script_published")
        blank_reason = self.client.post(
            f"/api/v1/publishing/jobs/{scheduled['id']}/script-result",
            headers=self.headers,
            json={"decision": "confirmed_not_published", "reason": "   "},
        )
        self.assertEqual(blank_reason.status_code, 422, blank_reason.text)
        with db.SessionLocal() as session:
            queue_job = session.get(Job, queue_job_id)
            actions = set(
                session.scalars(
                    select(AuditLog.action).where(AuditLog.entity_id == scheduled["id"])
                )
            )
            self.assertEqual(queue_job.status, "succeeded")
            self.assertIn("publish.script_package_ready", actions)
            self.assertIn("publish.script_result", actions)

    def test_expired_script_attempt_is_blocked_cleaned_and_rebuilt(self):
        scheduled = self._schedule()
        self._make_dispatch_due(scheduled["id"])
        self.assertTrue(self.worker.run_once())
        storage = build_object_storage(self.settings)

        with db.SessionLocal() as session:
            publish_job = session.get(PublishJob, scheduled["id"])
            requester = session.scalar(
                select(User).where(User.email == "script-publisher@example.com")
            )
            self.assertIsNotNone(publish_job)
            self.assertIsNotNone(requester)
            response_json = dict(publish_job.response_json or {})
            old_attempt_id = response_json["script_attempt_id"]
            old_package_uri = response_json["package_uri"]
            self.assertEqual(response_json["script_requested_by_user_id"], requester.id)
            response_json["script_confirmation_expires_at"] = (
                datetime.now(timezone.utc) - timedelta(minutes=1)
            ).isoformat()
            publish_job.response_json = response_json
            session.commit()

        artifact = self.client.get(
            f"/api/v1/publishing/jobs/{scheduled['id']}/artifact",
            headers=self.headers,
        )
        self.assertEqual(artifact.status_code, 409, artifact.text)

        evidence_buffer = io.BytesIO()
        Image.new("RGB", (8, 8), color=(120, 40, 20)).save(
            evidence_buffer,
            format="PNG",
        )
        expired_evidence = self.client.post(
            f"/api/v1/publishing/jobs/{scheduled['id']}/evidence",
            headers=self.headers,
            data={"kind": "screenshot"},
            files={"file": ("expired.png", evidence_buffer.getvalue(), "image/png")},
        )
        self.assertEqual(expired_evidence.status_code, 409, expired_evidence.text)

        reviewer_headers = self._add_reviewer(
            "expiry-reviewer@example.com", "Expiry Reviewer"
        )
        expired_confirmation = self.client.post(
            f"/api/v1/publishing/jobs/{scheduled['id']}/script-result",
            headers=reviewer_headers,
            json={
                "decision": "confirmed_not_published",
                "reason": "Expired attempts must not accept a decision",
            },
        )
        self.assertEqual(
            expired_confirmation.status_code, 409, expired_confirmation.text
        )

        rebuilt = self.client.post(
            f"/api/v1/publishing/jobs/{scheduled['id']}/script-package",
            headers=self.headers,
        )
        self.assertEqual(rebuilt.status_code, 202, rebuilt.text)
        self.assertEqual(rebuilt.json()["status"], "scheduled")
        self.assertFalse(rebuilt.json()["script_package_available"])
        with self.assertRaises(FileNotFoundError):
            storage.read(old_package_uri)

        self.assertTrue(self.worker.run_once())
        current = next(
            item
            for item in self.client.get(
                "/api/v1/publishing/jobs", headers=self.headers
            ).json()
            if item["id"] == scheduled["id"]
        )
        self.assertEqual(current["status"], "script_ready")
        self.assertFalse(current["script_confirmation_expired"])
        with db.SessionLocal() as session:
            current_job = session.get(PublishJob, scheduled["id"])
            self.assertNotEqual(
                current_job.response_json["script_attempt_id"], old_attempt_id
            )
            actions = set(
                session.scalars(
                    select(AuditLog.action).where(AuditLog.entity_id == scheduled["id"])
                )
            )
            self.assertIn("publish.script_attempt_expired", actions)

    def test_confirmed_pre_publish_failure_can_switch_to_script(self):
        scheduled = self._schedule()
        with db.SessionLocal() as session:
            publish_job = session.get(PublishJob, scheduled["id"])
            publish_job.status = "failed"
            publish_job.request_json = {
                "content_version": 1,
                "delivery_mode": "connector",
            }
            publish_job.error = "连接器在远程调用前验证失败"
            queue_job = session.scalar(
                select(Job).where(
                    Job.idempotency_key == f"publish.dispatch:{scheduled['id']}"
                )
            )
            queue_job.status = "failed"
            queue_job.last_error = publish_job.error
            session.commit()

        switched = self.client.post(
            f"/api/v1/publishing/jobs/{scheduled['id']}/script-package",
            headers=self.headers,
        )
        self.assertEqual(switched.status_code, 202, switched.text)
        self.assertEqual(switched.json()["delivery_mode"], "script")
        self.assertEqual(switched.json()["status"], "scheduled")
        self.assertTrue(self.worker.run_once())
        current = self.client.get(
            "/api/v1/publishing/jobs", headers=self.headers
        ).json()[0]
        self.assertEqual(current["status"], "script_ready")

    def test_uncertain_api_outcome_cannot_switch_to_script(self):
        scheduled = self._schedule()
        with db.SessionLocal() as session:
            publish_job = session.get(PublishJob, scheduled["id"])
            publish_job.status = "reconciliation_required"
            publish_job.error = "平台调用结果不确定"
            queue_job = session.scalar(
                select(Job).where(
                    Job.idempotency_key == f"publish.dispatch:{scheduled['id']}"
                )
            )
            queue_job.status = "failed"
            queue_job.last_error = publish_job.error
            session.commit()

        switched = self.client.post(
            f"/api/v1/publishing/jobs/{scheduled['id']}/script-package",
            headers=self.headers,
        )
        self.assertEqual(switched.status_code, 409, switched.text)
        self.assertIn("以免重复发布", switched.json()["error"]["message"])
        with db.SessionLocal() as session:
            publish_job = session.get(PublishJob, scheduled["id"])
            self.assertEqual(publish_job.status, "reconciliation_required")

    def test_two_distinct_reviewers_confirm_the_same_frozen_evidence(self):
        channel = self.client.post(
            "/api/v1/channels",
            headers=self.headers,
            json={
                "platform": "xiaohongshu",
                "display_name": "Two-reviewer script account",
                "connection_mode": "script",
                "script_confirmation_required": 2,
                "credentials": {},
                "config": {},
            },
        )
        self.assertEqual(channel.status_code, 201, channel.text)
        scheduled = self._schedule(channel_id=channel.json()["id"])
        self._make_dispatch_due(scheduled["id"])
        self.assertTrue(self.worker.run_once())

        evidence_buffer = io.BytesIO()
        Image.new("RGB", (10, 10), color=(60, 120, 30)).save(
            evidence_buffer,
            format="PNG",
        )
        evidence = self.client.post(
            f"/api/v1/publishing/jobs/{scheduled['id']}/evidence",
            headers=self.headers,
            data={"kind": "screenshot"},
            files={
                "file": (
                    "two-person-proof.png",
                    evidence_buffer.getvalue(),
                    "image/png",
                )
            },
        )
        self.assertEqual(evidence.status_code, 201, evidence.text)

        requester_confirmation = self.client.post(
            f"/api/v1/publishing/jobs/{scheduled['id']}/script-result",
            headers=self.headers,
            json={
                "decision": "confirmed_published",
                "reason": "The requester must not confirm the same attempt",
                "external_id": "two-reviewer-post",
            },
        )
        self.assertEqual(
            requester_confirmation.status_code, 409, requester_confirmation.text
        )
        first_headers = self._add_reviewer(
            "first-reviewer@example.com", "First Reviewer"
        )

        first = self.client.post(
            f"/api/v1/publishing/jobs/{scheduled['id']}/script-result",
            headers=first_headers,
            json={
                "decision": "confirmed_published",
                "reason": "First reviewer checked the platform timestamp and title",
                "external_id": "two-reviewer-post",
            },
        )
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["status"], "script_confirmation_pending")
        self.assertEqual(first.json()["script_confirmation_count"], 1)
        self.assertEqual(first.json()["script_confirmation_required"], 2)

        same_reviewer = self.client.post(
            f"/api/v1/publishing/jobs/{scheduled['id']}/script-result",
            headers=first_headers,
            json={
                "decision": "confirmed_published",
                "reason": "The same reviewer cannot supply both confirmations",
                "external_id": "two-reviewer-post",
            },
        )
        self.assertEqual(same_reviewer.status_code, 409, same_reviewer.text)
        frozen_evidence = self.client.post(
            f"/api/v1/publishing/jobs/{scheduled['id']}/evidence",
            headers=self.headers,
            data={"kind": "platform_export"},
            files={"file": ("later.json", b"{}", "application/json")},
        )
        self.assertEqual(frozen_evidence.status_code, 409, frozen_evidence.text)

        registered = self.client.post(
            "/api/v1/auth/register",
            json={
                "email": "second-reviewer@example.com",
                "password": "a-second-secure-password",
                "display_name": "Second Reviewer",
                "workspace_name": "Second Reviewer Workspace",
            },
        )
        self.assertEqual(registered.status_code, 201, registered.text)
        second_headers = {
            "Authorization": f"Bearer {registered.json()['access_token']}"
        }
        cross_workspace_list = self.client.get(
            f"/api/v1/publishing/jobs/{scheduled['id']}/evidence",
            headers=second_headers,
        )
        self.assertEqual(cross_workspace_list.status_code, 404)
        cross_workspace_download = self.client.get(
            f"/api/v1/publishing/jobs/{scheduled['id']}/evidence/"
            f"{evidence.json()['id']}/download",
            headers=second_headers,
        )
        self.assertEqual(cross_workspace_download.status_code, 404)
        member = self.client.post(
            "/api/v1/admin/members",
            headers=self.headers,
            json={"email": "second-reviewer@example.com", "role": "reviewer"},
        )
        self.assertEqual(member.status_code, 201, member.text)
        switched = self.client.post(
            f"/api/v1/auth/switch/{self.workspace_id}",
            headers=second_headers,
        )
        self.assertEqual(switched.status_code, 200, switched.text)
        switched_headers = {
            "Authorization": f"Bearer {switched.json()['access_token']}"
        }

        disagreement = self.client.post(
            f"/api/v1/publishing/jobs/{scheduled['id']}/script-result",
            headers=switched_headers,
            json={
                "decision": "confirmed_not_published",
                "reason": "Second reviewer supplied a conflicting decision",
            },
        )
        self.assertEqual(disagreement.status_code, 409, disagreement.text)
        second = self.client.post(
            f"/api/v1/publishing/jobs/{scheduled['id']}/script-result",
            headers=switched_headers,
            json={
                "decision": "confirmed_published",
                "reason": "Second reviewer independently checked the same platform record",
                "external_id": "two-reviewer-post",
            },
        )
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json()["status"], "script_published")
        self.assertEqual(second.json()["script_confirmation_count"], 2)

        confirmations = self.client.get(
            f"/api/v1/publishing/jobs/{scheduled['id']}/confirmations",
            headers=self.headers,
        )
        self.assertEqual(confirmations.status_code, 200, confirmations.text)
        self.assertEqual(confirmations.headers["x-contentflow-page-limit"], "100")
        confirmation_items = confirmations.json()
        self.assertEqual(len(confirmation_items), 2)
        self.assertEqual(
            len({item["confirmed_by_user_id"] for item in confirmation_items}),
            2,
        )
        self.assertEqual(
            len({item["evidence_manifest_sha256"] for item in confirmation_items}),
            1,
        )


if __name__ == "__main__":
    unittest.main()
