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
HEAD_REVISION = "b0c1d2e3f4a5"
MINIMUM_PUBLIC_TABLE_COUNT = 30
AUTH_RATE_LIMIT_REVISION = "a73f9c2e4b61"
LAYOUT_TABLES = ("content_items", "content_revisions")
LAYOUT_REVISION = "8b6c1f3a9d21"
WORKER_NODE_TABLE = "worker_nodes"
WORKER_NODE_REVISION = "c9e7b4a2d610"
AUTH_SESSION_TABLE = "auth_sessions"
AUTH_REFRESH_HISTORY_TABLE = "auth_refresh_token_history"
AUTH_SESSION_REVISION = "f4c2d8e7a190"
AUTH_RATE_LIMIT_TABLE = "auth_rate_limits"
PUBLISH_EVIDENCE_TABLE = "publish_evidence_items"
PUBLISH_CONFIRMATION_TABLE = "publish_confirmations"
PUBLISH_EVIDENCE_REVISION = "e28a6b9c4f10"
PROMPT_RELEASE_TABLE = "prompt_releases"
PROMPT_RELEASE_REVISION = "b84e0d3f7c92"
PROMPT_EVAL_SUITE_TABLE = "prompt_eval_suites"
PROMPT_EVAL_RUN_TABLE = "prompt_eval_runs"
PROMPT_EVAL_REVISION = "c95f1e4a8d73"
STYLE_SKILL_TABLE = "style_skills"
STYLE_SKILL_REVISION = "1a2b3c4d5e6f"
AUDIT_CHAIN_HEAD_TABLE = "audit_chain_heads"
AUDIT_CHAIN_REVISION = "6d4e8f9a0b1c"
WORKSPACE_STORAGE_USAGE_TABLE = "workspace_storage_usage"
STORAGE_OBJECT_ALLOCATION_TABLE = "storage_object_allocations"
STORAGE_LEDGER_REVISION = "b0c1d2e3f4a5"
AUDIT_CHAIN_COLUMNS = {
    "chain_scope",
    "chain_sequence",
    "previous_hash",
    "entry_hash",
    "integrity_version",
}


def validate_public_restore_contract(script: str) -> list[str]:
    """Keep public restore guards synchronized with the current schema."""
    errors: list[str] = []
    expected_revision = f'test "$revision" = "{HEAD_REVISION}"'
    expected_table_count = (
        f'test "$tables" -ge {MINIMUM_PUBLIC_TABLE_COUNT}'
    )
    if expected_revision not in script:
        errors.append("public-test restore must require the current Alembic head")
    if expected_table_count not in script:
        errors.append(
            "public-test restore table threshold does not match the current schema"
        )
    return errors


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

    incrementally_added = {
        WORKER_NODE_TABLE,
        AUTH_SESSION_TABLE,
        AUTH_REFRESH_HISTORY_TABLE,
        AUTH_RATE_LIMIT_TABLE,
        PROMPT_RELEASE_TABLE,
        PROMPT_EVAL_SUITE_TABLE,
        PROMPT_EVAL_RUN_TABLE,
        PUBLISH_EVIDENCE_TABLE,
        PUBLISH_CONFIRMATION_TABLE,
        STYLE_SKILL_TABLE,
        AUDIT_CHAIN_HEAD_TABLE,
        WORKSPACE_STORAGE_USAGE_TABLE,
        STORAGE_OBJECT_ALLOCATION_TABLE,
    }
    expected_tables = set(db.Base.metadata.tables)
    missing_tables = (expected_tables - incrementally_added) - tables
    if missing_tables:
        missing = ", ".join(sorted(missing_tables))
        raise RuntimeError(
            "检测到未受 Alembic 管理的不完整数据库，缺少表："
            f"{missing}。请先备份数据库再人工处理。"
        )
    for table_name in sorted(incrementally_added & tables):
        expected_columns = set(db.Base.metadata.tables[table_name].columns.keys())
        actual_columns = {
            column["name"] for column in inspector.get_columns(table_name)
        }
        missing_columns = expected_columns - actual_columns
        if missing_columns:
            missing = ", ".join(sorted(missing_columns))
            raise RuntimeError(
                "检测到未受 Alembic 管理的不完整增量表 "
                f"{table_name}，缺少列：{missing}。"
                "请先备份数据库再人工处理。"
            )

    layout_state = {
        table: "layout_json"
        in {column["name"] for column in inspector.get_columns(table)}
        for table in LAYOUT_TABLES
    }
    worker_node_exists = WORKER_NODE_TABLE in tables
    auth_session_exists = AUTH_SESSION_TABLE in tables
    auth_refresh_history_exists = AUTH_REFRESH_HISTORY_TABLE in tables
    auth_rate_limit_exists = AUTH_RATE_LIMIT_TABLE in tables
    prompt_release_exists = PROMPT_RELEASE_TABLE in tables
    publish_evidence_exists = PUBLISH_EVIDENCE_TABLE in tables
    publish_confirmation_exists = PUBLISH_CONFIRMATION_TABLE in tables
    prompt_eval_suite_exists = PROMPT_EVAL_SUITE_TABLE in tables
    prompt_eval_run_exists = PROMPT_EVAL_RUN_TABLE in tables
    style_skill_exists = STYLE_SKILL_TABLE in tables
    audit_chain_head_exists = AUDIT_CHAIN_HEAD_TABLE in tables
    workspace_storage_usage_exists = WORKSPACE_STORAGE_USAGE_TABLE in tables
    storage_object_allocation_exists = STORAGE_OBJECT_ALLOCATION_TABLE in tables
    audit_log_columns = {
        column["name"] for column in inspector.get_columns("audit_logs")
    }
    audit_chain_columns_present = AUDIT_CHAIN_COLUMNS & audit_log_columns
    if worker_node_exists and not all(layout_state.values()):
        raise RuntimeError(
            "The worker_nodes table exists without the preceding layout migration. "
            "Back up the database and repair the schema before continuing."
        )
    if auth_session_exists and not worker_node_exists:
        raise RuntimeError(
            "The auth_sessions table exists without the worker registry migration. "
            "Back up the database and repair the schema before continuing."
        )
    if auth_session_exists != auth_refresh_history_exists:
        raise RuntimeError(
            "The authentication session tables are incomplete. Back up the "
            "database and repair the schema before continuing."
        )
    if auth_rate_limit_exists and not auth_session_exists:
        raise RuntimeError(
            "The authentication rate-limit table exists without the session "
            "migration. Back up the database and repair the schema before "
            "continuing."
        )
    if prompt_release_exists and not auth_rate_limit_exists:
        raise RuntimeError(
            "The prompt_releases table exists without the authentication "
            "rate-limit migration. Back up the database and repair the schema "
            "before continuing."
        )
    if prompt_eval_suite_exists != prompt_eval_run_exists:
        raise RuntimeError(
            "The prompt evaluation tables are incomplete. Back up the database "
            "and repair the schema before continuing."
        )
    if prompt_eval_suite_exists and not prompt_release_exists:
        raise RuntimeError(
            "The prompt evaluation tables exist without the prompt release "
            "migration. Back up the database and repair the schema before "
            "continuing."
        )
    if publish_evidence_exists != publish_confirmation_exists:
        raise RuntimeError(
            "The publication evidence tables are incomplete. Back up the "
            "database and repair the schema before continuing."
        )
    if publish_evidence_exists and not prompt_eval_suite_exists:
        raise RuntimeError(
            "The publication evidence tables exist without the prompt "
            "evaluation migration. Back up the database and repair the "
            "schema before continuing."
        )

    if style_skill_exists and not publish_evidence_exists:
        raise RuntimeError(
            "The style_skills table exists without the publication evidence "
            "migration. Back up the database and repair the schema before continuing."
        )

    if audit_chain_head_exists and not style_skill_exists:
        raise RuntimeError(
            "The audit chain head table exists without the style skill "
            "migration. Back up the database and repair the schema before "
            "continuing."
        )
    if audit_chain_columns_present and (
        audit_chain_columns_present != AUDIT_CHAIN_COLUMNS
        or not audit_chain_head_exists
    ):
        raise RuntimeError(
            "The audit hash-chain schema is incomplete. Back up the database "
            "and repair the schema before continuing."
        )
    if audit_chain_head_exists and not AUDIT_CHAIN_COLUMNS <= audit_log_columns:
        raise RuntimeError(
            "The audit chain head table exists without all audit log chain "
            "columns. Back up the database and repair the schema before "
            "continuing."
        )

    if workspace_storage_usage_exists != storage_object_allocation_exists:
        raise RuntimeError(
            "The storage ledger tables are incomplete. Back up the database "
            "and repair the schema before continuing."
        )
    if workspace_storage_usage_exists and not audit_chain_head_exists:
        raise RuntimeError(
            "The storage ledger tables exist without the audit-chain migration. "
            "Back up the database and repair the schema before continuing."
        )

    if workspace_storage_usage_exists:
        revision = STORAGE_LEDGER_REVISION
    elif audit_chain_head_exists:
        revision = AUDIT_CHAIN_REVISION
    elif style_skill_exists:
        revision = STYLE_SKILL_REVISION
    elif publish_evidence_exists:
        revision = PUBLISH_EVIDENCE_REVISION
    elif prompt_eval_suite_exists:
        revision = PROMPT_EVAL_REVISION
    elif prompt_release_exists:
        revision = PROMPT_RELEASE_REVISION
    elif auth_rate_limit_exists:
        revision = AUTH_RATE_LIMIT_REVISION
    elif auth_session_exists:
        revision = AUTH_SESSION_REVISION
    elif worker_node_exists:
        revision = WORKER_NODE_REVISION
    elif all(layout_state.values()):
        revision = LAYOUT_REVISION
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
