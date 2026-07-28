from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.engine import Connection, Engine

from . import db
from .settings import Settings, get_settings


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INITIAL_REVISION = "dcf960d6d7a0"
HEAD_REVISION = "8b6c1f3a9d21"
LAYOUT_TABLES = ("content_items", "content_revisions")


def _alembic_config(connection: Connection) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.attributes["connection"] = connection
    return config


def _run_alembic(engine: Engine, operation, revision: str) -> None:
    with engine.begin() as connection:
        operation(_alembic_config(connection), revision)


def _bootstrap_unversioned_schema(engine: Engine) -> None:
    from . import entities  # noqa: F401

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if not tables:
        return
    if "alembic_version" in tables:
        with engine.connect() as connection:
            current_revision = connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version LIMIT 1"
            ).scalar_one_or_none()
        if current_revision:
            return

    expected_tables = set(db.Base.metadata.tables)
    missing_tables = expected_tables - tables
    if missing_tables:
        missing = ", ".join(sorted(missing_tables))
        raise RuntimeError(
            "检测到未受 Alembic 管理的不完整数据库，缺少表："
            f"{missing}。请先备份数据库再人工处理。"
        )

    layout_state = {
        table: "layout_json"
        in {column["name"] for column in inspector.get_columns(table)}
        for table in LAYOUT_TABLES
    }
    if all(layout_state.values()):
        revision = HEAD_REVISION
    elif not any(layout_state.values()):
        revision = INITIAL_REVISION
    else:
        raise RuntimeError(
            "数据库的结构化排版字段处于不一致状态，请先备份数据库再人工处理。"
        )

    _run_alembic(engine, command.stamp, revision)


def upgrade_database(settings: Settings | None = None) -> None:
    resolved = settings or get_settings()
    engine = db.build_engine(resolved.database_url)
    try:
        _bootstrap_unversioned_schema(engine)
        _run_alembic(engine, command.upgrade, "head")
    finally:
        engine.dispose()


def main() -> None:
    upgrade_database()
    print("ContentFlow database is up to date.")


if __name__ == "__main__":
    main()
