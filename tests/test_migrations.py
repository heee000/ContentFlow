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
                self.assertIn("audit_logs", tables)
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
                        column["name"]
                        for column in inspector.get_columns(table)
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


if __name__ == "__main__":
    unittest.main()
