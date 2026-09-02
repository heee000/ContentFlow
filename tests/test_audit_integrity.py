from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from contentflow import db
from contentflow.api import create_app
from contentflow.entities import AuditLog
from contentflow.settings import Settings


class AuditIntegrityApiTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        settings = Settings(
            database_url=f"sqlite:///{(root / 'audit.db').as_posix()}",
            secret_key="audit-integrity-test-secret",
            local_storage_dir=root / "storage",
            allow_registration=True,
        )
        self.client = TestClient(create_app(settings))
        self.client.__enter__()
        registered = self.client.post(
            "/api/v1/auth/register",
            json={
                "email": "audit-owner@example.com",
                "password": "a-secure-password",
                "display_name": "Audit Owner",
                "workspace_name": "Audit Workspace",
            },
        )
        self.assertEqual(registered.status_code, 201, registered.text)
        self.headers = {
            "Authorization": f"Bearer {registered.json()['access_token']}"
        }
        current = self.client.get(
            "/api/v1/auth/session",
            headers=self.headers,
        )
        self.assertEqual(current.status_code, 200, current.text)
        self.workspace_id = current.json()["workspace"]["id"]

    def tearDown(self):
        self.client.__exit__(None, None, None)
        db.engine.dispose()
        self.temp_dir.cleanup()

    def test_audit_chain_is_visible_and_detects_payload_tampering(self):
        created = self.client.post(
            "/api/v1/campaigns",
            headers=self.headers,
            json={
                "name": "审计链测试活动",
                "product_name": "ContentFlow",
                "objective": "验证审计记录完整性",
                "audience": "测试人员",
                "platforms": ["wechat"],
            },
        )
        self.assertEqual(created.status_code, 201, created.text)

        integrity = self.client.get(
            "/api/v1/admin/audit-integrity",
            headers=self.headers,
        )
        self.assertEqual(integrity.status_code, 200, integrity.text)
        current = integrity.json()
        self.assertTrue(current["valid"])
        self.assertGreaterEqual(current["checked_entries"], 2)
        self.assertEqual(current["head_sequence"], current["checked_entries"])
        self.assertEqual(len(current["head_hash"]), 64)

        listed = self.client.get(
            "/api/v1/admin/audit-logs",
            headers=self.headers,
        )
        self.assertEqual(listed.status_code, 200, listed.text)
        logs = listed.json()
        self.assertEqual(
            [item["chain_sequence"] for item in logs],
            sorted(
                (item["chain_sequence"] for item in logs),
                reverse=True,
            ),
        )
        self.assertTrue(all(len(item["entry_hash"]) == 64 for item in logs))
        self.assertTrue(all(item["integrity_version"] == 1 for item in logs))

        with db.SessionLocal() as session:
            first = session.scalar(
                select(AuditLog)
                .where(AuditLog.workspace_id == self.workspace_id)
                .order_by(AuditLog.chain_sequence.asc())
            )
            self.assertIsNotNone(first)
            first.metadata_json = {"tampered": True}
            session.commit()

        tampered = self.client.get(
            "/api/v1/admin/audit-integrity",
            headers=self.headers,
        )
        self.assertEqual(tampered.status_code, 200, tampered.text)
        result = tampered.json()
        self.assertFalse(result["valid"])
        self.assertEqual(result["reason"], "entry_hash_mismatch")
        self.assertEqual(result["first_invalid_sequence"], 1)

    def test_non_admin_cannot_verify_audit_chain(self):
        member = self.client.post(
            "/api/v1/auth/register",
            json={
                "email": "audit-viewer@example.com",
                "password": "another-secure-password",
                "display_name": "Audit Viewer",
                "workspace_name": "Viewer Workspace",
            },
        )
        self.assertEqual(member.status_code, 201, member.text)
        viewer_home_headers = {
            "Authorization": f"Bearer {member.json()['access_token']}"
        }
        added = self.client.post(
            "/api/v1/admin/members",
            headers=self.headers,
            json={"email": "audit-viewer@example.com", "role": "viewer"},
        )
        self.assertEqual(added.status_code, 201, added.text)
        switched = self.client.post(
            f"/api/v1/auth/switch/{self.workspace_id}",
            headers=viewer_home_headers,
        )
        self.assertEqual(switched.status_code, 200, switched.text)
        viewer_headers = {
            "Authorization": f"Bearer {switched.json()['access_token']}"
        }
        forbidden = self.client.get(
            "/api/v1/admin/audit-integrity",
            headers=viewer_headers,
        )
        self.assertEqual(forbidden.status_code, 403, forbidden.text)

    def test_request_id_is_bounded_before_it_reaches_audit_storage(self):
        accepted = self.client.get(
            "/api/v1/auth/session",
            headers={**self.headers, "X-Request-ID": "client-request_01:retry"},
        )
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertEqual(
            accepted.headers["x-request-id"],
            "client-request_01:retry",
        )

        rejected = self.client.get(
            "/api/v1/auth/session",
            headers={**self.headers, "X-Request-ID": "x" * 65},
        )
        self.assertEqual(rejected.status_code, 200, rejected.text)
        generated = rejected.headers["x-request-id"]
        self.assertEqual(len(generated), 32)
        self.assertNotEqual(generated, "x" * 65)
