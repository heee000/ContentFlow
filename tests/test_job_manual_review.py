from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from contentflow import db
from contentflow.api import create_app
from contentflow.entities import AuditLog, Job, JobManualReview
from contentflow.job_queue import enqueue_job, request_job_manual_review
from contentflow.settings import Settings


class JobManualReviewTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.settings = Settings(
            _env_file=None,
            database_url=f"sqlite:///{(root / 'manual-review.db').as_posix()}",
            secret_key="job-manual-review-test-secret",
            local_storage_dir=root / "storage",
            allow_registration=True,
            require_governed_prompts=False,
            embedding_provider="hash",
            text_provider="mock",
            image_provider="mock",
            video_provider="mock",
            metrics_enabled=False,
        )
        self.client = TestClient(create_app(self.settings))
        self.client.__enter__()
        owner = self.client.post(
            "/api/v1/auth/register",
            json={
                "email": "review-owner@example.com",
                "password": "review-owner-password",
                "display_name": "Review Owner",
                "workspace_name": "Review Workspace",
            },
        )
        self.assertEqual(owner.status_code, 201, owner.text)
        self.workspace_id = owner.json()["workspace_id"]
        self.owner_headers = {
            "Authorization": f"Bearer {owner.json()['access_token']}"
        }

        editor = self.client.post(
            "/api/v1/auth/register",
            json={
                "email": "review-editor@example.com",
                "password": "review-editor-password",
                "display_name": "Review Editor",
                "workspace_name": "Editor Workspace",
            },
        )
        self.assertEqual(editor.status_code, 201, editor.text)
        added = self.client.post(
            "/api/v1/admin/members",
            headers=self.owner_headers,
            json={"email": "review-editor@example.com", "role": "editor"},
        )
        self.assertEqual(added.status_code, 201, added.text)
        editor_login = self.client.post(
            "/api/v1/auth/login",
            json={
                "email": "review-editor@example.com",
                "password": "review-editor-password",
                "workspace_id": self.workspace_id,
            },
        )
        self.assertEqual(editor_login.status_code, 200, editor_login.text)
        self.editor_headers = {
            "Authorization": f"Bearer {editor_login.json()['access_token']}"
        }

    def tearDown(self):
        self.client.__exit__(None, None, None)
        db.engine.dispose()
        self.temp_dir.cleanup()

    def create_manual_review_job(self, suffix: str) -> str:
        with db.SessionLocal() as session:
            job = enqueue_job(
                session,
                job_type="workflow.execute",
                payload={"run_id": f"review-run-{suffix}"},
                workspace_id=self.workspace_id,
                idempotency_key=f"manual-review-{suffix}",
            )
            job.attempts = 2
            request_job_manual_review(
                session,
                job,
                reason_code="provider_outcome_unknown_after_error",
                error="Provider outcome requires operator verification",
                source="test_fixture",
            )
            session.commit()
            return job.id

    def test_review_state_requires_reviewer_and_preserves_audit_history(self):
        job_id = self.create_manual_review_job("retry")

        listed = self.client.get("/api/v1/jobs", headers=self.editor_headers)
        self.assertEqual(listed.status_code, 200, listed.text)
        item = next(job for job in listed.json() if job["id"] == job_id)
        self.assertEqual(item["status"], "manual_review")
        self.assertEqual(
            item["manual_review"]["reason_code"],
            "provider_outcome_unknown_after_error",
        )
        self.assertIsNone(item["manual_review"]["decision"])
        self.assertFalse(item["manual_review"]["provider_checked"])
        summary = self.client.get(
            "/api/v1/dashboard/summary",
            headers=self.editor_headers,
        )
        self.assertEqual(summary.status_code, 200, summary.text)
        self.assertEqual(summary.json()["jobs_manual_review"], 1)

        generic_retry = self.client.post(
            f"/api/v1/jobs/{job_id}/retry",
            headers=self.editor_headers,
        )
        self.assertEqual(generic_retry.status_code, 409, generic_retry.text)
        forbidden = self.client.post(
            f"/api/v1/jobs/{job_id}/manual-review",
            headers=self.editor_headers,
            json={
                "decision": "retry",
                "provider_checked": True,
                "note": "已检查供应商控制台，没有发现对应请求。",
            },
        )
        self.assertEqual(forbidden.status_code, 403, forbidden.text)
        unchecked = self.client.post(
            f"/api/v1/jobs/{job_id}/manual-review",
            headers=self.owner_headers,
            json={
                "decision": "retry",
                "provider_checked": False,
                "note": "尚未检查供应商控制台，不能执行重试。",
            },
        )
        self.assertEqual(unchecked.status_code, 422, unchecked.text)

        resolved = self.client.post(
            f"/api/v1/jobs/{job_id}/manual-review",
            headers=self.owner_headers,
            json={
                "decision": "retry",
                "provider_checked": True,
                "note": "已核对供应商控制台，该时间段没有请求、计费或生成结果。",
            },
        )
        self.assertEqual(resolved.status_code, 200, resolved.text)
        payload = resolved.json()
        self.assertEqual(payload["status"], "retry")
        self.assertEqual(payload["attempts"], 0)
        self.assertEqual(payload["manual_review"]["decision"], "retry")
        self.assertTrue(payload["manual_review"]["provider_checked"])
        self.assertIsNotNone(payload["manual_review"]["resolved_at"])
        self.assertIsNotNone(payload["manual_review"]["resolved_by_user_id"])
        resolved_summary = self.client.get(
            "/api/v1/dashboard/summary",
            headers=self.owner_headers,
        )
        self.assertEqual(resolved_summary.json()["jobs_manual_review"], 0)

        duplicate = self.client.post(
            f"/api/v1/jobs/{job_id}/manual-review",
            headers=self.owner_headers,
            json={
                "decision": "retry",
                "provider_checked": True,
                "note": "再次尝试处置同一条已经关闭的人工核对记录。",
            },
        )
        self.assertEqual(duplicate.status_code, 409, duplicate.text)

        with db.SessionLocal() as session:
            reviews = list(
                session.scalars(
                    select(JobManualReview).where(JobManualReview.job_id == job_id)
                )
            )
            self.assertEqual(len(reviews), 1)
            self.assertEqual(reviews[0].decision, "retry")
            actions = list(
                session.scalars(
                    select(AuditLog.action).where(AuditLog.entity_id == job_id)
                )
            )
            self.assertEqual(
                actions,
                [
                    "job.manual_review_requested",
                    "job.manual_review_resolved",
                ],
            )

    def test_abandon_is_terminal_and_legacy_unsafe_failure_cannot_bypass_review(self):
        job_id = self.create_manual_review_job("abandon")
        abandoned = self.client.post(
            f"/api/v1/jobs/{job_id}/manual-review",
            headers=self.owner_headers,
            json={
                "decision": "abandon",
                "provider_checked": True,
                "note": "供应商控制台已有对应计费记录，保留失败状态并停止重复调用。",
            },
        )
        self.assertEqual(abandoned.status_code, 200, abandoned.text)
        self.assertEqual(abandoned.json()["status"], "failed")
        self.assertEqual(
            abandoned.json()["manual_review"]["decision"],
            "abandon",
        )
        self.assertIn("Provider outcome", abandoned.json()["last_error"])

        with db.SessionLocal() as session:
            legacy = Job(
                workspace_id=self.workspace_id,
                job_type="workflow.execute",
                status="failed",
                payload_json={"run_id": "legacy-run"},
                attempts=1,
                idempotency_key="legacy-unsafe-failure",
                last_error="Legacy provider outcome unknown",
            )
            session.add(legacy)
            session.commit()
            legacy_id = legacy.id
        legacy_retry = self.client.post(
            f"/api/v1/jobs/{legacy_id}/retry",
            headers=self.owner_headers,
        )
        self.assertEqual(legacy_retry.status_code, 409, legacy_retry.text)


if __name__ == "__main__":
    unittest.main()
