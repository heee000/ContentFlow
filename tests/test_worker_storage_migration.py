"""Staged-write schema preservation, adoption and unsafe downgrade rejection."""

from datetime import datetime, timezone

from alembic import command
import pytest
from sqlalchemy import MetaData, Table, inspect, text
from sqlalchemy.orm import Session

from contentflow.db import build_engine
from contentflow.entities import StorageObjectAllocation, User, Workspace
from contentflow.migrate import HEAD_REVISION, upgrade_database
from contentflow.settings import Settings
from test_generation_migration import migrate


def test_upgrade_preserves_old_storage_and_refuses_to_drop_unresolved_staging(tmp_path):
    engine = build_engine(f"sqlite:///{(tmp_path / 'staging.db').as_posix()}")
    try:
        migrate(engine, command.upgrade, "e9f0a1b2c3d4")
        with Session(engine) as session:
            user = User(email="TEST-storage@example.com", password_hash="TEST", display_name="TEST")
            session.add(user)
            session.flush()
            workspace = Workspace(name="TEST", slug="TEST-storage", created_by=user.id)
            session.add(workspace)
            session.flush()
            workspace_id = workspace.id
            session.commit()
        with engine.begin() as connection:
            table = Table("storage_object_allocations", MetaData(), autoload_with=connection)
            connection.execute(table.insert().values(id="legacy", workspace_id=workspace_id, owner_type="test",
                owner_id="legacy", category="test", filename="legacy.bin", status="active", storage_uri="file:///TEST/legacy.bin",
                checksum="a" * 64, size_bytes=2, size_verified=True, created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc)))
        migrate(engine, command.upgrade, "head")
        with Session(engine) as session:
            legacy = session.get(StorageObjectAllocation, "legacy")
            assert legacy.status == "active" and legacy.size_bytes == 2 and legacy.write_job_id is None
            legacy.status = "staging"
            legacy.write_job_id = "test-job"
            legacy.write_lease_token = "b" * 32
            session.commit()
        with pytest.raises(RuntimeError, match="Resolve staged storage writes"):
            migrate(engine, command.downgrade, "e9f0a1b2c3d4")
        with Session(engine) as session:
            assert session.get(StorageObjectAllocation, "legacy").status == "staging"
            session.get(StorageObjectAllocation, "legacy").status = "active"
            session.commit()
        migrate(engine, command.downgrade, "e9f0a1b2c3d4")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT size_bytes FROM storage_object_allocations WHERE id='legacy'")) == 2
        assert "write_job_id" not in {c["name"] for c in inspect(engine).get_columns("storage_object_allocations")}
    finally:
        engine.dispose()


@pytest.mark.parametrize("revision", ["e9f0a1b2c3d4", "head"])
def test_unversioned_storage_adopts_only_correct_revision(tmp_path, revision):
    url = f"sqlite:///{(tmp_path / 'adopt.db').as_posix()}"
    engine = build_engine(url)
    try:
        migrate(engine, command.upgrade, revision)
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM alembic_version"))
        upgrade_database(Settings(_env_file=None, database_url=url, local_storage_dir=tmp_path / "storage"))
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == HEAD_REVISION
        assert "write_job_id" in {c["name"] for c in inspect(engine).get_columns("storage_object_allocations")}
    finally:
        engine.dispose()


def test_partial_staging_columns_cannot_be_silently_adopted(tmp_path):
    url = f"sqlite:///{(tmp_path / 'partial.db').as_posix()}"
    engine = build_engine(url)
    try:
        migrate(engine, command.upgrade, "e9f0a1b2c3d4")
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE storage_object_allocations ADD COLUMN write_job_id VARCHAR(36)"))
            connection.execute(text("DELETE FROM alembic_version"))
        with pytest.raises(RuntimeError, match="storage write schema"):
            upgrade_database(Settings(_env_file=None, database_url=url, local_storage_dir=tmp_path / "storage"))
    finally:
        engine.dispose()
