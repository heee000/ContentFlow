from __future__ import annotations

import json
import tempfile
from copy import deepcopy
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import select

from contentflow import db
from contentflow.api import create_app
from contentflow.entities import Job, PromptEvalRun, PromptEvalSuite
from contentflow.prompts import PROMPTS
from contentflow.settings import Settings
from contentflow.worker import Worker


class BrokenEvalProvider:
    provider_name = "broken-eval"
    model_name = "broken-eval-v1"
    last_call_metadata = {"usage_source": "not_reported"}

    def complete_json(self, _stage, _payload, *, system_prompt=None):
        del system_prompt
        raise RuntimeError("PRIVATE-PROVIDER-SECRET must never be persisted")


class PromptEvalGovernanceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.settings = Settings(
            database_url=f"sqlite:///{(root / 'prompt-eval.db').as_posix()}",
            secret_key="prompt-eval-test-secret",
            local_storage_dir=root / "storage",
            allow_registration=True,
            text_provider="mock",
        )
        self.client = TestClient(create_app(self.settings))
        self.client.__enter__()

        owner = self.client.post(
            "/api/v1/auth/register",
            json={
                "email": "eval-owner@example.com",
                "password": "owner-password",
                "display_name": "Eval Owner",
                "workspace_name": "Eval Workspace",
            },
        )
        self.assertEqual(owner.status_code, 201, owner.text)
        self.owner_headers = {"Authorization": f"Bearer {owner.json()['access_token']}"}
        self.workspace_id = owner.json()["workspace_id"]

        reviewer = self.client.post(
            "/api/v1/auth/register",
            json={
                "email": "eval-reviewer@example.com",
                "password": "reviewer-password",
                "display_name": "Eval Reviewer",
                "workspace_name": "Reviewer Personal",
            },
        )
        self.assertEqual(reviewer.status_code, 201, reviewer.text)
        self.reviewer_personal_headers = {
            "Authorization": f"Bearer {reviewer.json()['access_token']}"
        }
        added = self.client.post(
            "/api/v1/admin/members",
            headers=self.owner_headers,
            json={"email": "eval-reviewer@example.com", "role": "admin"},
        )
        self.assertEqual(added.status_code, 201, added.text)
        reviewer_login = self.client.post(
            "/api/v1/auth/login",
            json={
                "email": "eval-reviewer@example.com",
                "password": "reviewer-password",
                "workspace_id": self.workspace_id,
            },
        )
        self.assertEqual(reviewer_login.status_code, 200, reviewer_login.text)
        self.reviewer_headers = {
            "Authorization": f"Bearer {reviewer_login.json()['access_token']}"
        }
        self.worker = Worker(
            settings=self.settings,
            session_factory=db.SessionLocal,
            worker_id="prompt-eval-test-worker",
        )

    def tearDown(self):
        self.client.__exit__(None, None, None)
        db.engine.dispose()
        self.temp_dir.cleanup()

    @staticmethod
    def cases(*, failing: bool = False) -> list[dict[str, object]]:
        brief = {
            "product_name": "ContentFlow",
            "city": "北京",
            "must_include": ["PRIVATE-EVAL-CONTEXT-789"],
            "product_facts": ["整理内容工作流"],
            "call_to_action": "查看完整路线",
        }
        plan_case: dict[str, object] = {
            "name": "plan-contract",
            "stage": "plan",
            "input_json": {"brief": brief, "knowledge": []},
            "required_paths": ["content_angle", "key_message", "posting_window"],
        }
        if failing:
            plan_case["expected_values"] = {"risk_level": "critical"}
        return [
            plan_case,
            {
                "name": "wechat-generation-contract",
                "stage": "generate",
                "input_json": {
                    "brief": brief,
                    "platform": "wechat",
                    "plan": {},
                    "knowledge": [],
                },
                "required_paths": ["title", "body", "layout"],
                "required_substrings": ["ContentFlow"],
            },
            {
                "name": "review-contract",
                "stage": "review",
                "input_json": {
                    "brief": brief,
                    "platform": "wechat",
                    "content": {"title": "测试", "body": "测试正文"},
                    "knowledge": [],
                },
                "required_paths": ["risk_level"],
                "expected_values": {"passed": True},
            },
        ]

    @staticmethod
    def prompts(label: str) -> dict[str, str]:
        return {
            stage: f"{prompt}\n\nEval release marker: {label}-{stage}."
            for stage, prompt in PROMPTS.items()
        }

    def create_suite(self, name: str, *, failing: bool = False) -> dict:
        response = self.client.post(
            "/api/v1/admin/prompt-eval/suites",
            headers=self.owner_headers,
            json={
                "name": name,
                "description": "Versioned deterministic gold evaluation suite",
                "cases": self.cases(failing=failing),
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def activate_suite(self, suite_id: str) -> dict:
        response = self.client.post(
            f"/api/v1/admin/prompt-eval/suites/{suite_id}/activate",
            headers=self.reviewer_headers,
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def create_release(self, label: str) -> dict:
        response = self.client.post(
            "/api/v1/admin/prompt-releases",
            headers=self.owner_headers,
            json={
                "prompts": self.prompts(label),
                "change_summary": f"{label} prompt evaluation change",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def evaluate(self, release_id: str) -> dict:
        response = self.client.post(
            f"/api/v1/admin/prompt-releases/{release_id}/evaluate",
            headers=self.owner_headers,
            json={"provider": "mock"},
        )
        self.assertEqual(response.status_code, 202, response.text)
        self.assertTrue(self.worker.run_once())
        state = self.client.get(
            "/api/v1/admin/prompt-eval",
            headers=self.owner_headers,
        )
        self.assertEqual(state.status_code, 200, state.text)
        return next(
            item for item in state.json()["runs"] if item["id"] == response.json()["id"]
        )

    def test_passed_eval_is_required_for_approval_and_activation(self):
        empty = self.client.get(
            "/api/v1/admin/prompt-eval",
            headers=self.owner_headers,
        )
        self.assertEqual(empty.status_code, 200, empty.text)
        self.assertIsNone(empty.json()["active_suite"])

        oversized_cases = self.cases()
        oversized_cases[0]["required_substrings"] = ["x" * 10_001]
        oversized = self.client.post(
            "/api/v1/admin/prompt-eval/suites",
            headers=self.owner_headers,
            json={
                "name": "Oversized assertion suite",
                "cases": oversized_cases,
            },
        )
        self.assertEqual(oversized.status_code, 422, oversized.text)
        self.assertIn("单项不能超过", oversized.text)

        suite = self.create_suite("Gold contract suite")
        own_activation = self.client.post(
            f"/api/v1/admin/prompt-eval/suites/{suite['id']}/activate",
            headers=self.owner_headers,
        )
        self.assertEqual(own_activation.status_code, 409, own_activation.text)
        self.activate_suite(suite["id"])

        release = self.create_release("passing")
        blocked = self.client.post(
            f"/api/v1/admin/prompt-releases/{release['id']}/approve",
            headers=self.reviewer_headers,
            json={"note": "reviewed before eval"},
        )
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertIn("尚未通过当前评测套件", blocked.text)

        run = self.evaluate(release["id"])
        self.assertEqual(run["status"], "passed", run)
        self.assertEqual(run["result_json"]["case_count"], 3)
        self.assertEqual(run["result_json"]["passed_count"], 3)
        serialized_run = json.dumps(run, ensure_ascii=False)
        self.assertNotIn("PRIVATE-EVAL-CONTEXT-789", serialized_run)
        self.assertNotIn("从零散地点到一条可执行", serialized_run)

        with db.SessionLocal() as session:
            stored_run = session.get(PromptEvalRun, run["id"])
            self.assertIsNotNone(stored_run)
            stored_run.provider = "exploratory-provider"
            session.commit()
        wrong_target = self.client.post(
            f"/api/v1/admin/prompt-releases/{release['id']}/approve",
            headers=self.reviewer_headers,
            json={"note": "wrong model evidence must not count"},
        )
        self.assertEqual(wrong_target.status_code, 409, wrong_target.text)
        self.assertIn("目标模型门禁", wrong_target.text)
        rerun = self.evaluate(release["id"])
        self.assertEqual(rerun["status"], "passed", rerun)

        approved = self.client.post(
            f"/api/v1/admin/prompt-releases/{release['id']}/approve",
            headers=self.reviewer_headers,
            json={"note": "independent review after eval"},
        )
        self.assertEqual(approved.status_code, 200, approved.text)
        activated = self.client.post(
            f"/api/v1/admin/prompt-releases/{release['id']}/activate",
            headers=self.owner_headers,
        )
        self.assertEqual(activated.status_code, 200, activated.text)
        self.assertEqual(activated.json()["status"], "active")

        audit = self.client.get(
            "/api/v1/admin/audit-logs",
            headers=self.owner_headers,
        )
        self.assertEqual(audit.status_code, 200, audit.text)
        serialized_audit = json.dumps(audit.json(), ensure_ascii=False)
        self.assertIn("prompt_eval.complete", serialized_audit)
        self.assertNotIn("PRIVATE-EVAL-CONTEXT-789", serialized_audit)

        isolated = self.client.get(
            "/api/v1/admin/prompt-eval",
            headers=self.reviewer_personal_headers,
        )
        self.assertEqual(isolated.status_code, 200, isolated.text)
        self.assertEqual(isolated.json()["suites"], [])
        cross_tenant = self.client.post(
            f"/api/v1/admin/prompt-releases/{release['id']}/evaluate",
            headers=self.reviewer_personal_headers,
            json={"provider": "mock"},
        )
        self.assertEqual(cross_tenant.status_code, 404, cross_tenant.text)

    def test_failed_eval_and_suite_rotation_block_stale_evidence(self):
        failing_suite = self.create_suite("Failing regression suite", failing=True)
        self.activate_suite(failing_suite["id"])
        failed_release = self.create_release("failing")
        failed_run = self.evaluate(failed_release["id"])
        self.assertEqual(failed_run["status"], "failed", failed_run)
        self.assertEqual(failed_run["result_json"]["failed_count"], 1)
        blocked = self.client.post(
            f"/api/v1/admin/prompt-releases/{failed_release['id']}/approve",
            headers=self.reviewer_headers,
            json={"note": "must remain blocked"},
        )
        self.assertEqual(blocked.status_code, 409, blocked.text)

        passing_suite = self.create_suite("Passing suite version two")
        self.activate_suite(passing_suite["id"])
        stale_release = self.create_release("stale-evidence")
        self.assertEqual(self.evaluate(stale_release["id"])["status"], "passed")

        rotated_suite = self.create_suite("Rotated suite version three")
        self.activate_suite(rotated_suite["id"])
        stale_approval = self.client.post(
            f"/api/v1/admin/prompt-releases/{stale_release['id']}/approve",
            headers=self.reviewer_headers,
            json={"note": "old evidence must not count"},
        )
        self.assertEqual(stale_approval.status_code, 409, stale_approval.text)
        self.assertIn("eval-v3", stale_approval.text)

    def test_suite_tampering_and_provider_errors_fail_closed_without_secrets(self):
        tampered = self.create_suite("Tamper detection suite")
        with db.SessionLocal() as session:
            stored = session.get(PromptEvalSuite, tampered["id"])
            self.assertIsNotNone(stored)
            tampered_cases = deepcopy(stored.cases_json)
            tampered_cases[0]["required_paths"] = ["invented.path"]
            stored.cases_json = tampered_cases
            session.commit()
        activation = self.client.post(
            f"/api/v1/admin/prompt-eval/suites/{tampered['id']}/activate",
            headers=self.reviewer_headers,
        )
        self.assertEqual(activation.status_code, 409, activation.text)
        self.assertIn("完整性校验失败", activation.text)

        valid_suite = self.create_suite("Provider failure suite")
        self.activate_suite(valid_suite["id"])
        release = self.create_release("provider-error")
        queued = self.client.post(
            f"/api/v1/admin/prompt-releases/{release['id']}/evaluate",
            headers=self.owner_headers,
            json={"provider": "mock"},
        )
        self.assertEqual(queued.status_code, 202, queued.text)
        with db.SessionLocal() as session:
            job = session.scalar(
                select(Job).where(
                    Job.job_type == "prompt_eval.execute",
                    Job.status == "queued",
                )
            )
            self.assertIsNotNone(job)
            job.max_attempts = 1
            session.commit()

        with patch(
            "contentflow.prompt_eval.build_text_provider",
            return_value=BrokenEvalProvider(),
        ):
            self.assertTrue(self.worker.run_once())

        state = self.client.get(
            "/api/v1/admin/prompt-eval",
            headers=self.owner_headers,
        )
        run = next(
            item for item in state.json()["runs"] if item["id"] == queued.json()["id"]
        )
        self.assertEqual(run["status"], "error", run)
        self.assertEqual(
            run["error"],
            "AI prompt evaluation failed (RuntimeError)",
        )
        serialized = json.dumps(run, ensure_ascii=False)
        self.assertNotIn("PRIVATE-PROVIDER-SECRET", serialized)
        self.assertEqual(
            run["result_json"]["ai_provenance"]["failed_invocations"],
            1,
        )
        audit = self.client.get(
            "/api/v1/admin/audit-logs",
            headers=self.owner_headers,
        )
        self.assertNotIn(
            "PRIVATE-PROVIDER-SECRET",
            json.dumps(audit.json(), ensure_ascii=False),
        )


if __name__ == "__main__":
    unittest.main()
