from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from contentflow.migrate import HEAD_REVISION, upgrade_database
from contentflow.settings import Settings


class MigrationTest(unittest.TestCase):
    def test_initial_schema_upgrades_and_downgrades(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "migration.db"
            url = f"sqlite:///{database.as_posix()}"
            previous = os.environ.get("CONTENTFLOW_DATABASE_URL")
            os.environ["CONTENTFLOW_DATABASE_URL"] = url
            try:
                config = Config("alembic.ini")
                command.upgrade(config, "head")
                engine = create_engine(url)
                tables = set(inspect(engine).get_table_names())
                self.assertIn("content_items", tables)
                self.assertIn("content_revisions", tables)
                self.assertIn("publish_jobs", tables)
                self.assertIn("publish_evidence_items", tables)
                self.assertIn("publish_confirmations", tables)
                self.assertIn("audit_logs", tables)
                self.assertIn("worker_nodes", tables)
                self.assertIn("auth_sessions", tables)
                self.assertIn("auth_refresh_token_history", tables)
                self.assertIn("auth_rate_limits", tables)
                self.assertIn("prompt_releases", tables)
                self.assertIn("prompt_eval_suites", tables)
                self.assertIn("prompt_eval_runs", tables)
                prompt_checks = {
                    item["name"]
                    for item in inspect(engine).get_check_constraints("prompt_releases")
                }
                self.assertIn(
                    "ck_prompt_releases_release_number_positive",
                    prompt_checks,
                )
                self.assertIn("ck_prompt_releases_status", prompt_checks)
                prompt_indexes = {
                    item["name"]: item
                    for item in inspect(engine).get_indexes("prompt_releases")
                }
                self.assertTrue(
                    prompt_indexes["uq_prompt_release_workspace_active"]["unique"]
                )
                eval_suite_checks = {
                    item["name"]
                    for item in inspect(engine).get_check_constraints(
                        "prompt_eval_suites"
                    )
                }
                self.assertIn(
                    "ck_prompt_eval_suites_version_number_positive",
                    eval_suite_checks,
                )
                self.assertIn("ck_prompt_eval_suites_status", eval_suite_checks)
                eval_suite_indexes = {
                    item["name"]: item
                    for item in inspect(engine).get_indexes("prompt_eval_suites")
                }
                self.assertTrue(
                    eval_suite_indexes["uq_prompt_eval_suite_workspace_active"][
                        "unique"
                    ]
                )
                eval_run_checks = {
                    item["name"]
                    for item in inspect(engine).get_check_constraints(
                        "prompt_eval_runs"
                    )
                }
                self.assertIn("ck_prompt_eval_runs_status", eval_run_checks)
                evidence_checks = {
                    item["name"]
                    for item in inspect(engine).get_check_constraints(
                        "publish_evidence_items"
                    )
                }
                self.assertIn("ck_publish_evidence_items_kind", evidence_checks)
                self.assertIn(
                    "ck_publish_evidence_items_size_bytes_positive",
                    evidence_checks,
                )
                self.assertIn(
                    "ck_publish_evidence_items_sha256_lengths",
                    evidence_checks,
                )
                evidence_unique = {
                    item["name"]
                    for item in inspect(engine).get_unique_constraints(
                        "publish_evidence_items"
                    )
                }
                self.assertIn("uq_publish_evidence_attempt_object", evidence_unique)
                evidence_indexes = {
                    item["name"]
                    for item in inspect(engine).get_indexes("publish_evidence_items")
                }
                self.assertIn("ix_publish_evidence_attempt_created", evidence_indexes)
                confirmation_checks = {
                    item["name"]
                    for item in inspect(engine).get_check_constraints(
                        "publish_confirmations"
                    )
                }
                self.assertIn(
                    "ck_publish_confirmations_decision",
                    confirmation_checks,
                )
                self.assertIn(
                    "ck_publish_confirmations_sha256_lengths",
                    confirmation_checks,
                )
                confirmation_unique = {
                    item["name"]
                    for item in inspect(engine).get_unique_constraints(
                        "publish_confirmations"
                    )
                }
                self.assertIn(
                    "uq_publish_confirmation_attempt_user",
                    confirmation_unique,
                )
                columns = {
                    column["name"]
                    for column in inspect(engine).get_columns("content_items")
                }
                self.assertIn("layout_json", columns)
                command.downgrade(config, "base")
                remaining = set(inspect(engine).get_table_names())
                self.assertNotIn("content_items", remaining)
                engine.dispose()
            finally:
                if previous is None:
                    os.environ.pop("CONTENTFLOW_DATABASE_URL", None)
                else:
                    os.environ["CONTENTFLOW_DATABASE_URL"] = previous

    def test_unversioned_legacy_schema_is_safely_adopted_and_upgraded(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "legacy.db"
            url = f"sqlite:///{database.as_posix()}"
            previous = os.environ.get("CONTENTFLOW_DATABASE_URL")
            os.environ["CONTENTFLOW_DATABASE_URL"] = url
            try:
                config = Config("alembic.ini")
                command.upgrade(config, "dcf960d6d7a0")
                engine = create_engine(url)
                with engine.begin() as connection:
                    connection.execute(text("DELETE FROM alembic_version"))
                engine.dispose()

                upgrade_database(
                    Settings(
                        database_url=url,
                        secret_key="migration-test-secret",
                        local_storage_dir=Path(temp_dir) / "storage",
                    )
                )

                engine = create_engine(url)
                inspector = inspect(engine)
                for table in ("content_items", "content_revisions"):
                    columns = {
                        column["name"] for column in inspector.get_columns(table)
                    }
                    self.assertIn("layout_json", columns)
                with engine.connect() as connection:
                    revision = connection.scalar(
                        text("SELECT version_num FROM alembic_version")
                    )
                self.assertEqual(revision, HEAD_REVISION)
                engine.dispose()
            finally:
                if previous is None:
                    os.environ.pop("CONTENTFLOW_DATABASE_URL", None)
                else:
                    os.environ["CONTENTFLOW_DATABASE_URL"] = previous

    def test_unversioned_previous_head_schema_adds_worker_registry(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "previous-head.db"
            url = f"sqlite:///{database.as_posix()}"
            previous = os.environ.get("CONTENTFLOW_DATABASE_URL")
            os.environ["CONTENTFLOW_DATABASE_URL"] = url
            try:
                config = Config("alembic.ini")
                command.upgrade(config, "8b6c1f3a9d21")
                engine = create_engine(url)
                with engine.begin() as connection:
                    connection.execute(text("DELETE FROM alembic_version"))
                engine.dispose()

                upgrade_database(
                    Settings(
                        database_url=url,
                        secret_key="migration-test-secret",
                        local_storage_dir=Path(temp_dir) / "storage",
                    )
                )

                engine = create_engine(url)
                self.assertIn("worker_nodes", inspect(engine).get_table_names())
                with engine.connect() as connection:
                    revision = connection.scalar(
                        text("SELECT version_num FROM alembic_version")
                    )
                self.assertEqual(revision, HEAD_REVISION)
                engine.dispose()
            finally:
                if previous is None:
                    os.environ.pop("CONTENTFLOW_DATABASE_URL", None)
                else:
                    os.environ["CONTENTFLOW_DATABASE_URL"] = previous

    def test_unversioned_worker_head_schema_adds_auth_sessions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "worker-head.db"
            url = f"sqlite:///{database.as_posix()}"
            previous = os.environ.get("CONTENTFLOW_DATABASE_URL")
            os.environ["CONTENTFLOW_DATABASE_URL"] = url
            try:
                config = Config("alembic.ini")
                command.upgrade(config, "c9e7b4a2d610")
                engine = create_engine(url)
                with engine.begin() as connection:
                    connection.execute(text("DELETE FROM alembic_version"))
                engine.dispose()

                upgrade_database(
                    Settings(
                        database_url=url,
                        secret_key="migration-test-secret",
                        local_storage_dir=Path(temp_dir) / "storage",
                    )
                )

                engine = create_engine(url)
                tables = set(inspect(engine).get_table_names())
                self.assertIn("worker_nodes", tables)
                self.assertIn("auth_sessions", tables)
                self.assertIn("auth_refresh_token_history", tables)
                self.assertIn("auth_rate_limits", tables)
                self.assertIn("prompt_releases", tables)
                with engine.connect() as connection:
                    revision = connection.scalar(
                        text("SELECT version_num FROM alembic_version")
                    )
                self.assertEqual(revision, HEAD_REVISION)
                engine.dispose()
            finally:
                if previous is None:
                    os.environ.pop("CONTENTFLOW_DATABASE_URL", None)
                else:
                    os.environ["CONTENTFLOW_DATABASE_URL"] = previous

    def test_unversioned_auth_head_schema_adds_rate_limits(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "auth-head.db"
            url = f"sqlite:///{database.as_posix()}"
            previous = os.environ.get("CONTENTFLOW_DATABASE_URL")
            os.environ["CONTENTFLOW_DATABASE_URL"] = url
            try:
                config = Config("alembic.ini")
                command.upgrade(config, "f4c2d8e7a190")
                engine = create_engine(url)
                with engine.begin() as connection:
                    connection.execute(text("DELETE FROM alembic_version"))
                engine.dispose()

                upgrade_database(
                    Settings(
                        database_url=url,
                        secret_key="migration-test-secret",
                        local_storage_dir=Path(temp_dir) / "storage",
                    )
                )

                engine = create_engine(url)
                tables = set(inspect(engine).get_table_names())
                self.assertIn("auth_sessions", tables)
                self.assertIn("auth_refresh_token_history", tables)
                self.assertIn("auth_rate_limits", tables)
                self.assertIn("prompt_releases", tables)
                with engine.connect() as connection:
                    revision = connection.scalar(
                        text("SELECT version_num FROM alembic_version")
                    )
                self.assertEqual(revision, HEAD_REVISION)
                engine.dispose()
            finally:
                if previous is None:
                    os.environ.pop("CONTENTFLOW_DATABASE_URL", None)
                else:
                    os.environ["CONTENTFLOW_DATABASE_URL"] = previous

    def test_unversioned_rate_limit_head_adds_prompt_registry(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "rate-limit-head.db"
            url = f"sqlite:///{database.as_posix()}"
            previous = os.environ.get("CONTENTFLOW_DATABASE_URL")
            os.environ["CONTENTFLOW_DATABASE_URL"] = url
            try:
                config = Config("alembic.ini")
                command.upgrade(config, "a73f9c2e4b61")
                engine = create_engine(url)
                with engine.begin() as connection:
                    connection.execute(text("DELETE FROM alembic_version"))
                engine.dispose()

                upgrade_database(
                    Settings(
                        database_url=url,
                        secret_key="migration-test-secret",
                        local_storage_dir=Path(temp_dir) / "storage",
                    )
                )

                engine = create_engine(url)
                self.assertIn(
                    "prompt_releases",
                    inspect(engine).get_table_names(),
                )
                with engine.connect() as connection:
                    revision = connection.scalar(
                        text("SELECT version_num FROM alembic_version")
                    )
                self.assertEqual(revision, HEAD_REVISION)
                engine.dispose()
            finally:
                if previous is None:
                    os.environ.pop("CONTENTFLOW_DATABASE_URL", None)
                else:
                    os.environ["CONTENTFLOW_DATABASE_URL"] = previous

    def test_unversioned_prompt_release_head_adds_prompt_eval_tables(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "prompt-release-head.db"
            url = f"sqlite:///{database.as_posix()}"
            previous = os.environ.get("CONTENTFLOW_DATABASE_URL")
            os.environ["CONTENTFLOW_DATABASE_URL"] = url
            try:
                config = Config("alembic.ini")
                command.upgrade(config, "b84e0d3f7c92")
                engine = create_engine(url)
                with engine.begin() as connection:
                    connection.execute(text("DELETE FROM alembic_version"))
                engine.dispose()

                upgrade_database(
                    Settings(
                        database_url=url,
                        secret_key="migration-test-secret",
                        local_storage_dir=Path(temp_dir) / "storage",
                    )
                )

                engine = create_engine(url)
                tables = set(inspect(engine).get_table_names())
                self.assertIn("prompt_eval_suites", tables)
                self.assertIn("prompt_eval_runs", tables)
                with engine.connect() as connection:
                    revision = connection.scalar(
                        text("SELECT version_num FROM alembic_version")
                    )
                self.assertEqual(revision, HEAD_REVISION)
                engine.dispose()
            finally:
                if previous is None:
                    os.environ.pop("CONTENTFLOW_DATABASE_URL", None)
                else:
                    os.environ["CONTENTFLOW_DATABASE_URL"] = previous

    def test_unversioned_prompt_eval_head_adds_publish_evidence_tables(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "prompt-eval-head.db"
            url = f"sqlite:///{database.as_posix()}"
            previous = os.environ.get("CONTENTFLOW_DATABASE_URL")
            os.environ["CONTENTFLOW_DATABASE_URL"] = url
            try:
                config = Config("alembic.ini")
                command.upgrade(config, "c95f1e4a8d73")
                engine = create_engine(url)
                with engine.begin() as connection:
                    connection.execute(text("DELETE FROM alembic_version"))
                engine.dispose()

                upgrade_database(
                    Settings(
                        database_url=url,
                        secret_key="migration-test-secret",
                        local_storage_dir=Path(temp_dir) / "storage",
                    )
                )

                engine = create_engine(url)
                tables = set(inspect(engine).get_table_names())
                self.assertIn("publish_evidence_items", tables)
                self.assertIn("publish_confirmations", tables)
                with engine.connect() as connection:
                    revision = connection.scalar(
                        text("SELECT version_num FROM alembic_version")
                    )
                self.assertEqual(revision, HEAD_REVISION)
                engine.dispose()
            finally:
                if previous is None:
                    os.environ.pop("CONTENTFLOW_DATABASE_URL", None)
                else:
                    os.environ["CONTENTFLOW_DATABASE_URL"] = previous

    def test_unversioned_partial_publish_evidence_tables_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "partial-publish-evidence.db"
            url = f"sqlite:///{database.as_posix()}"
            previous = os.environ.get("CONTENTFLOW_DATABASE_URL")
            os.environ["CONTENTFLOW_DATABASE_URL"] = url
            try:
                config = Config("alembic.ini")
                command.upgrade(config, "head")
                engine = create_engine(url)
                with engine.begin() as connection:
                    connection.execute(text("DELETE FROM alembic_version"))
                    connection.execute(text("DROP TABLE publish_confirmations"))
                engine.dispose()

                with self.assertRaisesRegex(
                    RuntimeError,
                    "publication evidence tables are incomplete",
                ):
                    upgrade_database(
                        Settings(
                            database_url=url,
                            secret_key="migration-test-secret",
                            local_storage_dir=Path(temp_dir) / "storage",
                        )
                    )
            finally:
                if previous is None:
                    os.environ.pop("CONTENTFLOW_DATABASE_URL", None)
                else:
                    os.environ["CONTENTFLOW_DATABASE_URL"] = previous

    def test_unversioned_partial_prompt_eval_tables_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "partial-prompt-eval.db"
            url = f"sqlite:///{database.as_posix()}"
            previous = os.environ.get("CONTENTFLOW_DATABASE_URL")
            os.environ["CONTENTFLOW_DATABASE_URL"] = url
            try:
                config = Config("alembic.ini")
                command.upgrade(config, "head")
                engine = create_engine(url)
                with engine.begin() as connection:
                    connection.execute(text("DELETE FROM alembic_version"))
                    connection.execute(text("DROP TABLE prompt_eval_runs"))
                engine.dispose()

                with self.assertRaisesRegex(
                    RuntimeError,
                    "prompt evaluation tables are incomplete",
                ):
                    upgrade_database(
                        Settings(
                            database_url=url,
                            secret_key="migration-test-secret",
                            local_storage_dir=Path(temp_dir) / "storage",
                        )
                    )
            finally:
                if previous is None:
                    os.environ.pop("CONTENTFLOW_DATABASE_URL", None)
                else:
                    os.environ["CONTENTFLOW_DATABASE_URL"] = previous

    def test_unversioned_partial_auth_tables_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "partial-auth.db"
            url = f"sqlite:///{database.as_posix()}"
            previous = os.environ.get("CONTENTFLOW_DATABASE_URL")
            os.environ["CONTENTFLOW_DATABASE_URL"] = url
            try:
                config = Config("alembic.ini")
                command.upgrade(config, "c9e7b4a2d610")
                engine = create_engine(url)
                with engine.begin() as connection:
                    connection.execute(text("DELETE FROM alembic_version"))
                    connection.execute(
                        text("CREATE TABLE auth_sessions (id VARCHAR(36) PRIMARY KEY)")
                    )
                    connection.execute(
                        text(
                            "CREATE TABLE auth_refresh_token_history "
                            "(id VARCHAR(36) PRIMARY KEY)"
                        )
                    )
                engine.dispose()

                with self.assertRaisesRegex(RuntimeError, "缺少列"):
                    upgrade_database(
                        Settings(
                            database_url=url,
                            secret_key="migration-test-secret",
                            local_storage_dir=Path(temp_dir) / "storage",
                        )
                    )
            finally:
                if previous is None:
                    os.environ.pop("CONTENTFLOW_DATABASE_URL", None)
                else:
                    os.environ["CONTENTFLOW_DATABASE_URL"] = previous


if __name__ == "__main__":
    unittest.main()
