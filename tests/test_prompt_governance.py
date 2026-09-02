from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import select

from contentflow import db
from contentflow.api import create_app
from contentflow.entities import (
    Campaign,
    Job,
    PromptEvalRun,
    PromptRelease,
    WorkflowRun,
)
from contentflow.prompt_governance import (
    PromptIntegrityError,
    resolve_active_prompt_set,
)
from contentflow.prompts import PROMPTS, calculate_prompt_hashes
from contentflow.settings import Settings
from contentflow.worker import Worker
from contentflow.workflow_service import execute_workflow_run


class PromptGovernanceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.settings = Settings(
            _env_file=None,
            environment="development",
            database_url=f"sqlite:///{(root / 'prompt-governance.db').as_posix()}",
            secret_key="prompt-governance-test-secret",
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

        owner = self.client.post(
            "/api/v1/auth/register",
            json={
                "email": "owner@example.com",
                "password": "owner-password",
                "display_name": "Owner",
                "workspace_name": "Primary Workspace",
            },
        )
        self.assertEqual(owner.status_code, 201, owner.text)
        self.owner_headers = {"Authorization": f"Bearer {owner.json()['access_token']}"}
        self.workspace_id = owner.json()["workspace_id"]

        reviewer = self.client.post(
            "/api/v1/auth/register",
            json={
                "email": "reviewer@example.com",
                "password": "reviewer-password",
                "display_name": "Reviewer",
                "workspace_name": "Reviewer Workspace",
            },
        )
        self.assertEqual(reviewer.status_code, 201, reviewer.text)
        self.reviewer_personal_headers = {
            "Authorization": f"Bearer {reviewer.json()['access_token']}"
        }
        added = self.client.post(
            "/api/v1/admin/members",
            headers=self.owner_headers,
            json={"email": "reviewer@example.com", "role": "admin"},
        )
        self.assertEqual(added.status_code, 201, added.text)
        reviewer_login = self.client.post(
            "/api/v1/auth/login",
            json={
                "email": "reviewer@example.com",
                "password": "reviewer-password",
                "workspace_id": self.workspace_id,
            },
        )
        self.assertEqual(reviewer_login.status_code, 200, reviewer_login.text)
        self.reviewer_headers = {
            "Authorization": f"Bearer {reviewer_login.json()['access_token']}"
        }
        suite = self.client.post(
            "/api/v1/admin/prompt-eval/suites",
            headers=self.owner_headers,
            json={
                "name": "Prompt governance gold suite",
                "description": "Deterministic approval gate fixture",
                "cases": self.eval_cases(),
            },
        )
        self.assertEqual(suite.status_code, 201, suite.text)
        activated = self.client.post(
            f"/api/v1/admin/prompt-eval/suites/{suite.json()['id']}/activate",
            headers=self.reviewer_headers,
        )
        self.assertEqual(activated.status_code, 200, activated.text)
        self.worker = Worker(
            settings=self.settings,
            session_factory=db.SessionLocal,
            worker_id="prompt-governance-worker",
        )

    def tearDown(self):
        self.client.__exit__(None, None, None)
        db.engine.dispose()
        self.temp_dir.cleanup()

    @staticmethod
    def eval_cases() -> list[dict[str, object]]:
        brief = {
            "product_name": "ContentFlow",
            "city": "北京",
            "must_include": ["人工复核"],
            "product_facts": ["整理内容工作流"],
            "call_to_action": "查看完整路线",
        }
        return [
            {
                "name": "plan-contract",
                "stage": "plan",
                "input_json": {"brief": brief, "knowledge": []},
                "required_paths": [
                    "content_angle",
                    "key_message",
                    "posting_window",
                ],
            },
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
            stage: f"{prompt}\n\nRelease marker: {label}-{stage}."
            for stage, prompt in PROMPTS.items()
        }

    def create_release(self, label: str):
        response = self.client.post(
            "/api/v1/admin/prompt-releases",
            headers=self.owner_headers,
            json={
                "prompts": self.prompts(label),
                "change_summary": f"{label} release change summary",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def approve(self, release_id: str):
        evaluation = self.client.post(
            f"/api/v1/admin/prompt-releases/{release_id}/evaluate",
            headers=self.owner_headers,
            json={"provider": "mock"},
        )
        self.assertEqual(evaluation.status_code, 202, evaluation.text)
        self.assertTrue(self.worker.run_once())
        eval_state = self.client.get(
            "/api/v1/admin/prompt-eval",
            headers=self.owner_headers,
        )
        self.assertEqual(eval_state.status_code, 200, eval_state.text)
        run = next(
            item
            for item in eval_state.json()["runs"]
            if item["id"] == evaluation.json()["id"]
        )
        self.assertEqual(run["status"], "passed", run)
        response = self.client.post(
            f"/api/v1/admin/prompt-releases/{release_id}/approve",
            headers=self.reviewer_headers,
            json={"note": "Independent review passed"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def activate(self, release_id: str):
        response = self.client.post(
            f"/api/v1/admin/prompt-releases/{release_id}/activate",
            headers=self.owner_headers,
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_two_person_approval_activation_rollback_and_tenant_isolation(self):
        baseline = self.client.get(
            "/api/v1/admin/prompt-releases",
            headers=self.owner_headers,
        )
        self.assertEqual(baseline.status_code, 200, baseline.text)
        self.assertEqual(baseline.json()["active"]["source"], "builtin")
        self.assertFalse(baseline.json()["governance_required"])
        self.assertTrue(baseline.json()["ready_for_generation"])
        self.assertIsNone(baseline.json()["generation_block_reason"])
        self.assertEqual(baseline.json()["releases"], [])

        invalid = self.client.post(
            "/api/v1/admin/prompt-releases",
            headers=self.owner_headers,
            json={
                "prompts": {"plan": "only one stage is not sufficient"},
                "change_summary": "invalid release",
            },
        )
        self.assertEqual(invalid.status_code, 422, invalid.text)
        blank_summary = self.client.post(
            "/api/v1/admin/prompt-releases",
            headers=self.owner_headers,
            json={
                "prompts": self.prompts("blank-summary"),
                "change_summary": "   ",
            },
        )
        self.assertEqual(blank_summary.status_code, 422, blank_summary.text)

        first = self.create_release("first")
        own_approval = self.client.post(
            f"/api/v1/admin/prompt-releases/{first['id']}/approve",
            headers=self.owner_headers,
            json={"note": "self approval"},
        )
        self.assertEqual(own_approval.status_code, 409, own_approval.text)
        self.assertEqual(self.approve(first["id"])["status"], "approved")
        activated_first = self.activate(first["id"])
        self.assertEqual(activated_first["status"], "active")

        active = self.client.get(
            "/api/v1/admin/prompt-releases",
            headers=self.owner_headers,
        ).json()
        self.assertEqual(active["active"]["source"], "workspace_release")
        self.assertEqual(active["active"]["release_id"], first["id"])
        self.assertEqual(active["active"]["version"], "workspace-r1")
        self.assertEqual(
            active["active"]["prompt_hashes"],
            calculate_prompt_hashes(self.prompts("first")),
        )

        isolated = self.client.get(
            "/api/v1/admin/prompt-releases",
            headers=self.reviewer_personal_headers,
        )
        self.assertEqual(isolated.status_code, 200, isolated.text)
        self.assertEqual(isolated.json()["releases"], [])
        cross_tenant = self.client.post(
            f"/api/v1/admin/prompt-releases/{first['id']}/activate",
            headers=self.reviewer_personal_headers,
        )
        self.assertEqual(cross_tenant.status_code, 404, cross_tenant.text)

        second = self.create_release("second")
        self.approve(second["id"])
        self.activate(second["id"])
        states = {
            item["id"]: item["status"]
            for item in self.client.get(
                "/api/v1/admin/prompt-releases",
                headers=self.owner_headers,
            ).json()["releases"]
        }
        self.assertEqual(states[first["id"]], "retired")
        self.assertEqual(states[second["id"]], "active")

        rollback = self.activate(first["id"])
        self.assertEqual(rollback["status"], "active")
        states = {
            item["id"]: item["status"]
            for item in self.client.get(
                "/api/v1/admin/prompt-releases",
                headers=self.owner_headers,
            ).json()["releases"]
        }
        self.assertEqual(states[first["id"]], "active")
        self.assertEqual(states[second["id"]], "retired")

        rejected = self.create_release("rejected")
        rejected_response = self.client.post(
            f"/api/v1/admin/prompt-releases/{rejected['id']}/reject",
            headers=self.reviewer_headers,
            json={"note": "Evaluation regression"},
        )
        self.assertEqual(rejected_response.status_code, 200, rejected_response.text)
        self.assertEqual(rejected_response.json()["status"], "rejected")
        rejected_activation = self.client.post(
            f"/api/v1/admin/prompt-releases/{rejected['id']}/activate",
            headers=self.owner_headers,
        )
        self.assertEqual(rejected_activation.status_code, 409)

        audit_logs = self.client.get(
            "/api/v1/admin/audit-logs",
            headers=self.owner_headers,
        ).json()
        serialized_audit = json.dumps(audit_logs, ensure_ascii=False)
        self.assertIn("prompt_release.rollback", serialized_audit)
        self.assertIn("prompt_release.reject", serialized_audit)
        self.assertNotIn("Release marker: first-plan", serialized_audit)

        tampered = self.create_release("tampered-before-activation")
        self.approve(tampered["id"])
        with db.SessionLocal() as session:
            stored = session.get(PromptRelease, tampered["id"])
            stored.prompt_hashes_json = {
                **stored.prompt_hashes_json,
                "review": "f" * 64,
            }
            session.commit()
        blocked_activation = self.client.post(
            f"/api/v1/admin/prompt-releases/{tampered['id']}/activate",
            headers=self.owner_headers,
        )
        self.assertEqual(blocked_activation.status_code, 409)
        self.assertIn("完整性校验失败", blocked_activation.text)

    def test_active_release_drives_workflow_provenance_and_detects_tampering(self):
        release = self.create_release("runtime")
        self.approve(release["id"])
        self.activate(release["id"])

        with db.SessionLocal() as session:
            campaign = Campaign(
                workspace_id=self.workspace_id,
                created_by=release["created_by_user_id"],
                name="Prompt runtime campaign",
                product_name="ContentFlow",
                objective="验证已审批 Prompt 进入运行时",
                audience="企业内容运营人员",
                platforms=["wechat"],
                status="active",
                brief={"city": "北京"},
            )
            session.add(campaign)
            session.flush()
            run = WorkflowRun(
                workspace_id=self.workspace_id,
                campaign_id=campaign.id,
                status="queued",
                current_stage="queued",
                provider="mock",
                trace_id="prompt-governance-runtime-trace",
                request_json={"provider": "mock"},
            )
            session.add(run)
            session.flush()

            result = execute_workflow_run(session, run, self.settings)
            provenance = result["ai_provenance"]
            self.assertEqual(provenance["prompt_source"], "workspace_release")
            self.assertEqual(provenance["prompt_release_id"], release["id"])
            self.assertEqual(provenance["prompt_set_version"], "workspace-r1")
            self.assertEqual(
                provenance["prompt_hashes"],
                calculate_prompt_hashes(self.prompts("runtime")),
            )
            session.rollback()

        with db.SessionLocal() as session:
            passed_eval = session.scalar(
                select(PromptEvalRun).where(
                    PromptEvalRun.prompt_release_id == release["id"],
                    PromptEvalRun.status == "passed",
                )
            )
            self.assertIsNotNone(passed_eval)
            passed_eval.provider = "stale-model-provider"
            session.commit()

        with db.SessionLocal() as session:
            campaign = Campaign(
                workspace_id=self.workspace_id,
                created_by=release["created_by_user_id"],
                name="Prompt model drift campaign",
                product_name="ContentFlow",
                objective="验证目标模型变更后运行时失败关闭",
                audience="企业内容运营人员",
                platforms=["wechat"],
                status="active",
                brief={"city": "北京"},
            )
            session.add(campaign)
            session.flush()
            drift_run = WorkflowRun(
                workspace_id=self.workspace_id,
                campaign_id=campaign.id,
                status="queued",
                current_stage="queued",
                provider="mock",
                trace_id="prompt-model-drift-trace",
                request_json={"provider": "mock"},
            )
            session.add(drift_run)
            session.flush()
            with self.assertRaisesRegex(ValueError, "目标模型门禁"):
                execute_workflow_run(session, drift_run, self.settings)
            session.rollback()

        with db.SessionLocal() as session:
            stored = session.get(PromptRelease, release["id"])
            stored.prompt_hashes_json = {
                **stored.prompt_hashes_json,
                "plan": "0" * 64,
            }
            session.commit()

        integrity_response = self.client.get(
            "/api/v1/admin/prompt-releases",
            headers=self.owner_headers,
        )
        self.assertEqual(integrity_response.status_code, 409)
        self.assertIn("完整性校验失败", integrity_response.text)

        with db.SessionLocal() as session:
            with self.assertRaises(PromptIntegrityError):
                resolve_active_prompt_set(session, self.workspace_id)

    def test_required_governance_blocks_builtin_prompt_before_enqueue(self):
        self.settings.require_governed_prompts = True
        governance = self.client.get(
            "/api/v1/admin/prompt-releases",
            headers=self.owner_headers,
        )
        self.assertEqual(governance.status_code, 200, governance.text)
        self.assertTrue(governance.json()["governance_required"])
        self.assertFalse(governance.json()["ready_for_generation"])
        self.assertIn("受治理 Prompt", governance.json()["generation_block_reason"])

        campaign = self.client.post(
            "/api/v1/campaigns",
            headers=self.owner_headers,
            json={
                "name": "生产 Prompt 门禁活动",
                "product_name": "ContentFlow",
                "objective": "验证未完成治理时禁止生成",
                "audience": "企业内容运营人员",
                "platforms": ["wechat"],
            },
        )
        self.assertEqual(campaign.status_code, 201, campaign.text)
        invalid_provider = self.client.post(
            f"/api/v1/campaigns/{campaign.json()['id']}/runs",
            headers=self.owner_headers,
            json={"provider": "x" * 81},
        )
        self.assertEqual(invalid_provider.status_code, 422, invalid_provider.text)
        blocked = self.client.post(
            f"/api/v1/campaigns/{campaign.json()['id']}/runs",
            headers=self.owner_headers,
            json={"provider": "mock"},
        )
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertIn("受治理 Prompt", blocked.text)
        with db.SessionLocal() as session:
            queued = session.scalar(
                select(Job).where(Job.job_type == "workflow.execute")
            )
            self.assertIsNone(queued)

        with (
            patch("contentflow.workflow_service.build_text_provider") as text_provider,
            patch(
                "contentflow.workflow_service.build_embedding_provider"
            ) as embedding_provider,
            db.SessionLocal() as session,
        ):
            direct_run = WorkflowRun(
                workspace_id=self.workspace_id,
                campaign_id=campaign.json()["id"],
                status="queued",
                current_stage="queued",
                provider="mock",
                trace_id="governed-prompt-runtime-guard",
                request_json={"provider": "mock"},
            )
            session.add(direct_run)
            session.flush()
            with self.assertRaisesRegex(ValueError, "受治理 Prompt"):
                execute_workflow_run(session, direct_run, self.settings)
            session.rollback()
            text_provider.assert_not_called()
            embedding_provider.assert_not_called()


if __name__ == "__main__":
    unittest.main()
