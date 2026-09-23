"""Receipt migration preserves legacy work and adopts only complete schemas."""

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from contentflow.db import build_engine
from contentflow.entities import (
    Campaign,
    GenerationIntent,
    User,
    WorkflowRun,
    Workspace,
)
from contentflow.migrate import HEAD_REVISION, upgrade_database
from contentflow.settings import Settings


def migrate(engine, operation, revision):
    config = Config("alembic.ini")
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        operation(config, revision)


def test_generation_receipt_migration_preserves_old_runs_and_rolls_back_in_isolation(
    tmp_path,
):
    engine = build_engine(f"sqlite:///{(tmp_path / 'migration.db').as_posix()}")
    try:
        migrate(engine, command.upgrade, "c7d8e9f0a1b2")
        with Session(engine) as session:
            user = User(
                email="TEST-generation@example.com",
                display_name="TEST",
                password_hash="TEST",
            )
            session.add(user)
            session.flush()
            workspace = Workspace(
                name="TEST", slug="test-generation", created_by=user.id
            )
            session.add(workspace)
            session.flush()
            campaign = Campaign(
                workspace_id=workspace.id,
                created_by=user.id,
                name="TEST",
                product_name="TEST",
                objective="TEST",
                audience="TEST",
                platforms=["wechat"],
            )
            session.add(campaign)
            session.flush()
            run = WorkflowRun(
                workspace_id=workspace.id,
                campaign_id=campaign.id,
                trace_id="TEST",
                request_json={"legacy": "preserved"},
            )
            session.add(run)
            session.commit()
            run_id, workspace_id = run.id, workspace.id
        migrate(engine, command.upgrade, "head")
        with Session(engine) as session:
            assert session.get(WorkflowRun, run_id).request_json == {
                "legacy": "preserved"
            }
            assert list(session.scalars(select(GenerationIntent))) == []
            session.add(
                GenerationIntent(
                    workspace_id=workspace_id,
                    request_id="TEST-ONLY-new-intent",
                    request_sha256="a" * 64,
                    run_id=run_id,
                )
            )
            session.commit()
        assert inspect(engine).get_pk_constraint("generation_intents")[
            "constrained_columns"
        ] == ["workspace_id", "request_id"]
        migrate(engine, command.downgrade, "c7d8e9f0a1b2")
        assert "generation_intents" not in inspect(engine).get_table_names()
        with Session(engine) as session:
            assert session.get(WorkflowRun, run_id).request_json == {
                "legacy": "preserved"
            }
    finally:
        engine.dispose()


@pytest.mark.parametrize("version", ["c7d8e9f0a1b2", "head"])
def test_unversioned_receipt_migration_does_not_stamp_missing_table_as_current(
    tmp_path, version
):
    url = f"sqlite:///{(tmp_path / 'adoption.db').as_posix()}"
    engine = build_engine(url)
    try:
        migrate(engine, command.upgrade, version)
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM alembic_version"))
        upgrade_database(
            Settings(
                _env_file=None,
                database_url=url,
                secret_key="TEST",
                local_storage_dir=tmp_path / "storage",
            )
        )
        assert "generation_intents" in inspect(engine).get_table_names()
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == HEAD_REVISION
            )
    finally:
        engine.dispose()


def test_unversioned_receipt_columns_without_constraints_are_not_adopted(tmp_path):
    url = f"sqlite:///{(tmp_path / 'incomplete.db').as_posix()}"
    engine = build_engine(url)
    try:
        migrate(engine, command.upgrade, "head")
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM alembic_version"))
            connection.execute(text("DROP TABLE generation_intents"))
            connection.execute(
                text(
                    "CREATE TABLE generation_intents (workspace_id VARCHAR(36), request_id VARCHAR(128), request_sha256 VARCHAR(64), run_id VARCHAR(36), requested_by VARCHAR(36), created_at DATETIME, updated_at DATETIME)"
                )
            )
        with pytest.raises(
            RuntimeError, match="Generation receipt schema is incomplete"
        ):
            upgrade_database(
                Settings(
                    _env_file=None,
                    database_url=url,
                    secret_key="TEST",
                    local_storage_dir=tmp_path / "storage",
                )
            )
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM alembic_version")) == 0
    finally:
        engine.dispose()
