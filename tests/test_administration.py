from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from contentflow import db
from contentflow.api import create_app
from contentflow.settings import Settings


class AdministrationTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        settings = Settings(
            database_url=f"sqlite:///{(root / 'administration.db').as_posix()}",
            secret_key="administration-test-secret",
            local_storage_dir=root / "storage",
            allow_registration=True,
        )
        self.client = TestClient(create_app(settings))
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
        self.owner_headers = {
            "Authorization": f"Bearer {owner.json()['access_token']}"
        }
        self.primary_workspace_id = owner.json()["workspace_id"]

        member = self.client.post(
            "/api/v1/auth/register",
            json={
                "email": "member@example.com",
                "password": "member-password",
                "display_name": "Member",
                "workspace_name": "Personal Workspace",
            },
        )
        self.assertEqual(member.status_code, 201, member.text)

    def tearDown(self):
        self.client.__exit__(None, None, None)
        db.engine.dispose()
        self.temp_dir.cleanup()

    def test_workspace_member_role_and_audit_flow(self):
        added = self.client.post(
            "/api/v1/admin/members",
            headers=self.owner_headers,
            json={"email": "member@example.com", "role": "editor"},
        )
        self.assertEqual(added.status_code, 201, added.text)
        membership_id = added.json()["id"]

        member_login = self.client.post(
            "/api/v1/auth/login",
            json={
                "email": "member@example.com",
                "password": "member-password",
                "workspace_id": self.primary_workspace_id,
            },
        )
        self.assertEqual(member_login.status_code, 200, member_login.text)
        member_headers = {
            "Authorization": f"Bearer {member_login.json()['access_token']}"
        }

        workspaces = self.client.get(
            "/api/v1/auth/workspaces",
            headers=member_headers,
        )
        self.assertEqual(workspaces.status_code, 200, workspaces.text)
        self.assertEqual(len(workspaces.json()), 2)
        self.assertEqual(
            next(
                item
                for item in workspaces.json()
                if item["id"] == self.primary_workspace_id
            )["role"],
            "editor",
        )

        forbidden = self.client.get(
            "/api/v1/admin/members",
            headers=member_headers,
        )
        self.assertEqual(forbidden.status_code, 403, forbidden.text)

        promoted = self.client.patch(
            f"/api/v1/admin/members/{membership_id}",
            headers=self.owner_headers,
            json={"role": "reviewer"},
        )
        self.assertEqual(promoted.status_code, 200, promoted.text)
        self.assertEqual(promoted.json()["role"], "reviewer")

        switched = self.client.post(
            f"/api/v1/auth/switch/{self.primary_workspace_id}",
            headers=member_headers,
        )
        self.assertEqual(switched.status_code, 200, switched.text)
        self.assertEqual(switched.json()["role"], "reviewer")

        audit_logs = self.client.get(
            "/api/v1/admin/audit-logs",
            headers=self.owner_headers,
        )
        self.assertEqual(audit_logs.status_code, 200, audit_logs.text)
        actions = {item["action"] for item in audit_logs.json()}
        self.assertIn("member.add", actions)
        self.assertIn("member.role_update", actions)

        owner_membership = next(
            item
            for item in self.client.get(
                "/api/v1/admin/members",
                headers=self.owner_headers,
            ).json()
            if item["email"] == "owner@example.com"
        )
        last_admin = self.client.patch(
            f"/api/v1/admin/members/{owner_membership['id']}",
            headers=self.owner_headers,
            json={"role": "editor"},
        )
        self.assertEqual(last_admin.status_code, 409, last_admin.text)

        removed = self.client.delete(
            f"/api/v1/admin/members/{membership_id}",
            headers=self.owner_headers,
        )
        self.assertEqual(removed.status_code, 204, removed.text)
        removed_access = self.client.get(
            "/api/v1/auth/session",
            headers=member_headers,
        )
        self.assertEqual(removed_access.status_code, 401, removed_access.text)

    def test_create_workspace_returns_immediately_usable_token(self):
        created = self.client.post(
            "/api/v1/auth/workspaces",
            headers=self.owner_headers,
            json={"name": "Second Workspace"},
        )
        self.assertEqual(created.status_code, 201, created.text)
        new_headers = {
            "Authorization": f"Bearer {created.json()['access_token']}"
        }
        session = self.client.get(
            "/api/v1/auth/session",
            headers=new_headers,
        )
        self.assertEqual(session.status_code, 200, session.text)
        self.assertEqual(session.json()["workspace"]["name"], "Second Workspace")
        self.assertEqual(session.json()["role"], "admin")


if __name__ == "__main__":
    unittest.main()
