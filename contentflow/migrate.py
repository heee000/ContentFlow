from __future__ import annotations

from pathlib import Path
import sys

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.engine import Connection, Engine

from . import db
from .settings import Settings, get_settings
from .schema_contract import (
    HEAD_REVISION as HEAD_REVISION,
    MINIMUM_PUBLIC_TABLE_COUNT as MINIMUM_PUBLIC_TABLE_COUNT,
    validate_public_restore_contract as validate_public_restore_contract,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INITIAL_REVISION = "dcf960d6d7a0"
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
JOB_MANUAL_REVIEW_TABLE = "job_manual_reviews"
JOB_MANUAL_REVIEW_REVISION = "e3f4a5b6c7d8"
PROVIDER_INVOCATION_TABLE = "provider_invocations"
PROVIDER_INVOCATION_ATTEMPT_TABLE = "provider_invocation_attempts"
PROVIDER_INVOCATION_REVISION = "f4a5b6c7d8e9"
AUDIT_CHAIN_COLUMNS = {
    "chain_scope",
    "chain_sequence",
    "previous_hash",
    "entry_hash",
    "integrity_version",
}


def _alembic_config(connection: Connection) -> Config:
    # Editable/source runs and installed wheels have different asset locations.
    # Never select migration code from the process's arbitrary working directory.
    roots = (PROJECT_ROOT, Path(sys.prefix) / "share" / "contentflow")
    root = next((candidate for candidate in roots
                 if (candidate / "alembic.ini").is_file()
                 and (candidate / "migrations" / "env.py").is_file()), None)
    if root is None:
        raise RuntimeError("ContentFlow migration assets are missing; reinstall the complete package")
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations").replace("%", "%%"))
    config.attributes["connection"] = connection
    # Embedded migration must not replace host handlers or disable application
    # loggers. Standalone Alembic remains responsible for its own CLI logging.
    config.attributes["configure_logger"] = False
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
        "generation_intents",
        "content_review_evidence",
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
        JOB_MANUAL_REVIEW_TABLE,
        PROVIDER_INVOCATION_TABLE,
        PROVIDER_INVOCATION_ATTEMPT_TABLE,
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
        if table_name == STORAGE_OBJECT_ALLOCATION_TABLE:
            # These belong to the later worker-write revision, not the original
            # storage table. Validate its all-or-nothing shape below.
            expected_columns -= {"write_job_id", "write_lease_token"}
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
    job_manual_review_exists = JOB_MANUAL_REVIEW_TABLE in tables
    provider_invocation_exists = PROVIDER_INVOCATION_TABLE in tables
    provider_invocation_attempt_exists = PROVIDER_INVOCATION_ATTEMPT_TABLE in tables
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

    if job_manual_review_exists and not workspace_storage_usage_exists:
        raise RuntimeError(
            "The job manual review table exists without the storage ledger "
            "migration. Back up the database and repair the schema before "
            "continuing."
        )

    if provider_invocation_exists != provider_invocation_attempt_exists:
        raise RuntimeError(
            "The provider invocation ledger tables are incomplete. Back up the "
            "database and repair the schema before continuing."
        )
    if provider_invocation_exists and not job_manual_review_exists:
        raise RuntimeError(
            "The provider invocation ledger exists without the job manual review "
            "migration. Back up the database and repair the schema before continuing."
        )

    if "content_review_evidence" in tables:
        kind_checks = inspector.get_check_constraints(PROVIDER_INVOCATION_TABLE) if provider_invocation_exists else []
        if not any("'media'" in entry.get("sqltext", "") and "'search'" in entry.get("sqltext", "") for entry in kind_checks):
            raise RuntimeError("审核证据表缺少前置调用账本约束；请先备份并人工修复未版本化数据库")
        metric_columns = {column["name"] for column in inspector.get_columns("metric_snapshots")}
        if "validation_status" in metric_columns:
            constraints = {item["name"] for item in inspector.get_check_constraints("metric_snapshots")}
            if not {"ck_metric_snapshots_validation_status", "ck_metric_snapshots_counters_bounded"} <= constraints:
                raise RuntimeError("Metric validation schema is incomplete; repair before adoption")
            revision = "c7d8e9f0a1b2"
        else:
            revision = "b6c7d8e9f0a1"
    elif provider_invocation_exists:
        revision = PROVIDER_INVOCATION_REVISION
    elif job_manual_review_exists:
        revision = JOB_MANUAL_REVIEW_REVISION
    elif workspace_storage_usage_exists:
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

    if "generation_intents" in tables:
        primary = inspector.get_pk_constraint("generation_intents")["constrained_columns"]
        unique = inspector.get_unique_constraints("generation_intents")
        foreign = {
            (tuple(item["constrained_columns"]), item["referred_table"], item.get("options", {}).get("ondelete"))
            for item in inspector.get_foreign_keys("generation_intents")
        }
        if revision != "c7d8e9f0a1b2" or primary != ["workspace_id", "request_id"] or not any(
            item["column_names"] == ["run_id"] for item in unique
        ) or not {
            (("workspace_id",), "workspaces", "CASCADE"),
            (("run_id",), "workflow_runs", "RESTRICT"),
            (("requested_by",), "users", "SET NULL"),
        } <= foreign:
            raise RuntimeError("Generation receipt schema is incomplete; back up and repair before adoption")
        revision = "d8e9f0a1b2c3"

    lease_column = next((item for item in inspector.get_columns("jobs")
        if item["name"] == "lease_token"), None)
    if lease_column is not None:
        if (revision != "d8e9f0a1b2c3" or not lease_column["nullable"]
            or getattr(lease_column["type"], "length", None) != 32):
            raise RuntimeError("Job lease identity schema is incomplete; back up before adoption")
        revision = "e9f0a1b2c3d4"

    storage_columns = {item["name"]: item for item in inspector.get_columns(STORAGE_OBJECT_ALLOCATION_TABLE)} if storage_object_allocation_exists else {}
    write_columns = {"write_job_id": 36, "write_lease_token": 32}
    if set(write_columns) & storage_columns.keys():
        checks = {item["name"]: item["sqltext"] for item in inspector.get_check_constraints(STORAGE_OBJECT_ALLOCATION_TABLE)}
        if (revision != "e9f0a1b2c3d4" or any(name not in storage_columns
                or not storage_columns[name]["nullable"]
                or getattr(storage_columns[name]["type"], "length", None) != size
                for name, size in write_columns.items())
            or "'staging'" not in checks.get("ck_storage_object_allocations_status", "")
            or "write_lease_token" not in checks.get("ck_storage_object_allocations_staging_identity", "")):
            raise RuntimeError("Worker storage write schema is incomplete; back up before adoption")
        revision = HEAD_REVISION

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
