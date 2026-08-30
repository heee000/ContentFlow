from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select

from contentflow import db
from contentflow.bootstrap_admin import add_workspace_admin, bootstrap_workspace_admin
from contentflow.entities import AuditLog, Membership, User, Workspace
from contentflow.migrate import upgrade_database
from contentflow.settings import Settings


class BootstrapAdminTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database = Path(self.temp_dir.name) / "bootstrap.db"
        self.settings = Settings(
            _env_file=None,
            database_url=f"sqlite:///{database.as_posix()}",
            allow_registration=False,
        )
        upgrade_database(self.settings)
        db.configure_database(self.settings.database_url)

    def tearDown(self):
        db.engine.dispose()
        self.temp_dir.cleanup()

    @patch("contentflow.bootstrap_admin.hash_password", return_value="safe-test-hash")
    def test_two_offline_admins_are_created_without_open_registration(self, _hash):
        workspace_slug, first_user_id = bootstrap_workspace_admin(
            self.settings,
            email="owner@example.com",
            password="first-password-123",
            display_name="Owner",
            workspace_name="Public Test",
        )
        second_user_id = add_workspace_admin(
            self.settings,
            workspace_slug=workspace_slug,
            email="reviewer@example.com",
            password="second-password-123",
            display_name="Reviewer",
        )
        with db.SessionLocal() as session:
            users = list(session.scalars(select(User).order_by(User.email)))
            workspace = session.scalar(
                select(Workspace).where(Workspace.slug == workspace_slug)
            )
            memberships = list(
                session.scalars(
                    select(Membership).where(Membership.workspace_id == workspace.id)
                )
            )
            actions = set(session.scalars(select(AuditLog.action)))
        self.assertEqual({user.id for user in users}, {first_user_id, second_user_id})
        self.assertEqual({membership.role for membership in memberships}, {"admin"})
        self.assertEqual(len(memberships), 2)
        self.assertIn("bootstrap.workspace_admin.create", actions)
        self.assertIn("bootstrap.workspace_admin.add", actions)

    @patch("contentflow.bootstrap_admin.hash_password", return_value="safe-test-hash")
    def test_bootstrap_refuses_nonempty_database_and_existing_email(self, _hash):
        workspace_slug, _ = bootstrap_workspace_admin(
            self.settings,
            email="owner@example.com",
            password="first-password-123",
            display_name="Owner",
            workspace_name="Public Test",
        )
        with self.assertRaisesRegex(RuntimeError, "empty database"):
            bootstrap_workspace_admin(
                self.settings,
                email="another@example.com",
                password="another-password-123",
                display_name="Another",
                workspace_name="Another",
            )
        with self.assertRaisesRegex(RuntimeError, "existing user email"):
            add_workspace_admin(
                self.settings,
                workspace_slug=workspace_slug,
                email="owner@example.com",
                password="another-password-123",
                display_name="Owner Again",
            )

    def test_bootstrap_refuses_when_public_registration_is_enabled(self):
        settings = self.settings.model_copy(update={"allow_registration": True})
        with self.assertRaisesRegex(RuntimeError, "registration"):
            bootstrap_workspace_admin(
                settings,
                email="owner@example.com",
                password="first-password-123",
                display_name="Owner",
                workspace_name="Public Test",
            )


if __name__ == "__main__":
    unittest.main()
