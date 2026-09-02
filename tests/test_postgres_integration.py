from __future__ import annotations

import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Barrier
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from alembic import command
from fastapi import HTTPException
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from contentflow.auth_rate_limit import RateLimitKey, consume_rate_limits
from contentflow.audit import record_audit, verify_audit_chain
from contentflow.connectors import ConnectorResult
from contentflow.entities import (
    Asset,
    AuditLog,
    AuthRateLimit,
    Campaign,
    ChannelConnection,
    ContentItem,
    Job,
    PublishJob,
    User,
    WorkflowRun,
    Workspace,
)
from contentflow.migrate import HEAD_REVISION, PROJECT_ROOT
from contentflow.observability import ObservabilityMetrics
from contentflow.security import hash_rate_limit_key
from contentflow.settings import Settings
from contentflow.worker import (
    handle_publish_reconcile,
    schedule_pending_publish_reconciliations,
)


TEST_DATABASE_URL = os.getenv("CONTENTFLOW_TEST_POSTGRES_URL")
TEST_ADMIN_DATABASE_URL = os.getenv("CONTENTFLOW_TEST_POSTGRES_ADMIN_URL")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="CONTENTFLOW_TEST_POSTGRES_URL is required for PostgreSQL tests",
)


@dataclass(frozen=True)
class PostgresHarness:
    engine: Engine
    sessions: sessionmaker[Session]
    settings: Settings


@pytest.fixture(scope="module")
def postgres_harness(tmp_path_factory: pytest.TempPathFactory):
    source_url = make_url(TEST_DATABASE_URL or "")
    if source_url.get_backend_name() != "postgresql":
        pytest.fail("CONTENTFLOW_TEST_POSTGRES_URL must be a PostgreSQL URL")

    admin_url = make_url(TEST_ADMIN_DATABASE_URL) if TEST_ADMIN_DATABASE_URL else (
        source_url.set(database="postgres")
    )
    database_name = f"contentflow_test_{uuid.uuid4().hex}"
    if not re.fullmatch(r"[a-z0-9_]+", database_name):
        pytest.fail("Generated PostgreSQL database name is not safe")

    admin_engine = create_engine(
        admin_url,
        isolation_level="AUTOCOMMIT",
        pool_pre_ping=True,
    )
    test_engine: Engine | None = None
    database_created = False
    try:
        with admin_engine.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{database_name}"')
        database_created = True

        test_url = source_url.set(database=database_name)
        test_engine = create_engine(test_url, pool_pre_ping=True)
        config = Config(str(PROJECT_ROOT / "alembic.ini"))
        with test_engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")

        storage_dir = tmp_path_factory.mktemp("contentflow-postgres-storage")
        settings = Settings(
            environment="test",
            database_url=test_url.render_as_string(hide_password=False),
            secret_key="postgres-integration-test-secret-key",
            storage_backend="local",
            local_storage_dir=storage_dir,
            text_provider="mock",
            embedding_provider="hash",
            image_provider="mock",
            video_provider="mock",
            publish_reconciliation_initial_delay_seconds=1,
            publish_reconciliation_max_attempts=2,
        )
        yield PostgresHarness(
            engine=test_engine,
            sessions=sessionmaker(
                bind=test_engine,
                expire_on_commit=False,
                future=True,
            ),
            settings=settings,
        )
    finally:
        if test_engine is not None:
            test_engine.dispose()
        if database_created:
            with admin_engine.connect() as connection:
                connection.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) "
                        "FROM pg_stat_activity "
                        "WHERE datname = :database_name "
                        "AND pid <> pg_backend_pid()"
                    ),
                    {"database_name": database_name},
                )
                connection.exec_driver_sql(
                    f'DROP DATABASE IF EXISTS "{database_name}"'
                )
        admin_engine.dispose()


def _create_publish_fixture(
    harness: PostgresHarness,
    *,
    status: str,
    external_id: str | None,
) -> dict[str, str]:
    suffix = uuid.uuid4().hex
    with harness.sessions() as session:
        user = User(
            email=f"postgres-{suffix}@example.com",
            password_hash="not-used-by-this-test",
            display_name="PostgreSQL Integration Owner",
        )
        session.add(user)
        session.flush()
        workspace = Workspace(
            name=f"PostgreSQL Integration {suffix}",
            slug=f"postgres-integration-{suffix}",
            created_by=user.id,
        )
        session.add(workspace)
        session.flush()
        campaign = Campaign(
            workspace_id=workspace.id,
            created_by=user.id,
            name=f"Reconciliation {suffix}",
            product_name="ContentFlow",
            objective="Verify PostgreSQL reconciliation guarantees",
            audience="Integration tests",
            platforms=["wechat"],
        )
        session.add(campaign)
        session.flush()
        workflow_run = WorkflowRun(
            workspace_id=workspace.id,
            campaign_id=campaign.id,
            status="completed",
            current_stage="completed",
            trace_id=f"postgres-trace-{suffix}",
        )
        session.add(workflow_run)
        session.flush()
        content = ContentItem(
            workspace_id=workspace.id,
            campaign_id=campaign.id,
            run_id=workflow_run.id,
            platform="wechat",
            title="PostgreSQL reconciliation evidence",
            body="This row exists only in a disposable integration database.",
            status="approved",
            version=1,
            approved_by=user.id,
            approved_at=datetime.now(timezone.utc),
        )
        channel = ChannelConnection(
            workspace_id=workspace.id,
            platform="wechat",
            display_name=f"WeChat {suffix}",
            status="connected",
            config_json={"auto_publish": True},
        )
        session.add_all([content, channel])
        session.flush()
        asset = Asset(
            workspace_id=workspace.id,
            content_item_id=content.id,
            kind="image",
            status="ready",
            storage_uri=f"memory://postgres/{suffix}.png",
            mime_type="image/png",
            metadata_json={"content_version": 1},
        )
        publish_job = PublishJob(
            workspace_id=workspace.id,
            content_item_id=content.id,
            channel_id=channel.id,
            status=status,
            scheduled_at=datetime.now(timezone.utc),
            idempotency_key=f"postgres-publish-{suffix}",
            external_id=external_id,
            attempts=1 if status == "submitted" else 0,
            request_json={"content_version": 1},
            response_json={"submit": {"publish_id": external_id}},
        )
        session.add_all([asset, publish_job])
        session.commit()
        return {
            "workspace_id": workspace.id,
            "publish_job_id": publish_job.id,
        }


def test_postgres_migrations_reach_head(postgres_harness: PostgresHarness):
    with postgres_harness.engine.connect() as connection:
        revision = connection.scalar(
            text("SELECT version_num FROM alembic_version LIMIT 1")
        )
        vector_enabled = connection.scalar(
            text(
                "SELECT EXISTS ("
                "SELECT 1 FROM pg_extension WHERE extname = 'vector'"
                ")"
            )
        )
    tables = set(inspect(postgres_harness.engine).get_table_names())

    assert revision == HEAD_REVISION
    assert vector_enabled is True
    assert {
        "audit_chain_heads",
        "jobs",
        "publish_jobs",
        "worker_nodes",
        "knowledge_vectors",
        "auth_sessions",
        "auth_refresh_token_history",
        "auth_rate_limits",
    } <= tables


def test_postgres_serializes_concurrent_audit_chain_appends(
    postgres_harness: PostgresHarness,
):
    suffix = uuid.uuid4().hex
    with postgres_harness.sessions() as session:
        user = User(
            email=f"audit-chain-{suffix}@example.com",
            password_hash="not-used-by-this-test",
            display_name="Audit Chain Owner",
        )
        session.add(user)
        session.flush()
        workspace = Workspace(
            name=f"Audit Chain {suffix}",
            slug=f"audit-chain-{suffix}",
            created_by=user.id,
        )
        session.add(workspace)
        session.commit()
        workspace_id = workspace.id
        user_id = user.id

    barrier = Barrier(2)

    def append_event(index: int) -> None:
        with postgres_harness.sessions() as session:
            barrier.wait(timeout=10)
            record_audit(
                session,
                action=f"postgres.audit.{index}",
                entity_type="postgres_test",
                entity_id=str(index),
                workspace_id=workspace_id,
                actor_user_id=user_id,
                metadata={"index": index},
            )
            session.commit()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(append_event, index) for index in range(2)]
        for future in futures:
            future.result(timeout=15)

    with postgres_harness.sessions() as session:
        result = verify_audit_chain(session, workspace_id=workspace_id)
        entries = list(
            session.scalars(
                select(AuditLog)
                .where(AuditLog.workspace_id == workspace_id)
                .order_by(AuditLog.chain_sequence.asc())
            )
        )
        session.commit()
    assert result.valid is True
    assert result.checked_entries == 2
    assert [entry.chain_sequence for entry in entries] == [1, 2]
    assert entries[1].previous_hash == entries[0].entry_hash


def test_postgres_skip_locked_idempotency_and_terminal_convergence(
    postgres_harness: PostgresHarness,
):
    fixture = _create_publish_fixture(
        postgres_harness,
        status="submitted",
        external_id="postgres-publish-001",
    )
    publish_job_id = fixture["publish_job_id"]

    with postgres_harness.sessions() as lock_session:
        locked = lock_session.scalar(
            select(PublishJob)
            .where(PublishJob.id == publish_job_id)
            .with_for_update()
        )
        assert locked is not None
        with postgres_harness.sessions() as sweep_session:
            sweep_session.execute(text("SET LOCAL lock_timeout = '1s'"))
            assert (
                schedule_pending_publish_reconciliations(
                    sweep_session,
                    settings=postgres_harness.settings,
                )
                == 0
            )
            sweep_session.commit()
        lock_session.rollback()

    with postgres_harness.sessions() as session:
        assert (
            schedule_pending_publish_reconciliations(
                session,
                settings=postgres_harness.settings,
            )
            == 1
        )
        session.commit()
    with postgres_harness.sessions() as session:
        assert (
            schedule_pending_publish_reconciliations(
                session,
                settings=postgres_harness.settings,
            )
            == 0
        )
        reconciliation_job = session.scalar(
            select(Job).where(
                Job.idempotency_key == f"publish.reconcile:{publish_job_id}"
            )
        )
        assert reconciliation_job is not None
        reconciliation_job.status = "succeeded"
        reconciliation_job.attempts = 2
        reconciliation_job.result_json = {"reconciled": "manual"}
        publish_job = session.get(PublishJob, publish_job_id)
        assert publish_job is not None
        publish_job.external_id = "postgres-publish-002"
        dispatch_job = Job(
            workspace_id=fixture["workspace_id"],
            job_type="publish.dispatch",
            status="running",
            payload_json={"publish_job_id": publish_job_id},
            attempts=1,
            max_attempts=4,
            run_at=datetime.now(timezone.utc),
            locked_by="crashed-postgres-worker",
            locked_at=datetime.now(timezone.utc),
            idempotency_key=f"publish.dispatch:{publish_job_id}",
        )
        session.add(dispatch_job)
        session.commit()
        dispatch_job_id = dispatch_job.id

    with postgres_harness.sessions() as session:
        assert (
            schedule_pending_publish_reconciliations(
                session,
                settings=postgres_harness.settings,
            )
            == 1
        )
        session.commit()
    with postgres_harness.sessions() as session:
        reconciliation_job = session.scalar(
            select(Job).where(
                Job.idempotency_key == f"publish.reconcile:{publish_job_id}"
            )
        )
        assert reconciliation_job is not None
        assert reconciliation_job.status == "queued"
        assert reconciliation_job.attempts == 0
        assert reconciliation_job.result_json == {}
        assert reconciliation_job.payload_json["lookup_external_id"] == (
            "postgres-publish-002"
        )

    class PublishedConnector:
        reconciliation_supported = True

        def reconcile(self, _publish_job):
            return ConnectorResult(
                status="published",
                external_id="postgres-article-001",
                external_url="https://mp.weixin.qq.com/s/postgres-article-001",
                response={"article_id": "postgres-article-001"},
            )

    with patch(
        "contentflow.worker.build_connector",
        return_value=PublishedConnector(),
    ):
        with postgres_harness.sessions() as session:
            result = handle_publish_reconcile(
                session,
                {"publish_job_id": publish_job_id},
                postgres_harness.settings,
            )

    assert result["status"] == "published"
    with postgres_harness.sessions() as session:
        publish_job = session.get(PublishJob, publish_job_id)
        dispatch_job = session.get(Job, dispatch_job_id)
        actions = set(
            session.scalars(
                select(AuditLog.action).where(
                    AuditLog.entity_id == publish_job_id
                )
            )
        )
        assert publish_job is not None
        assert publish_job.status == "published"
        assert publish_job.external_id == "postgres-article-001"
        assert dispatch_job is not None
        assert dispatch_job.status == "succeeded"
        assert dispatch_job.locked_by is None
        assert dispatch_job.locked_at is None
        assert dispatch_job.result_json["reconciled"] == "automatic"
        assert "publish.reconciliation_requeued" in actions
        assert "publish.reconcile_auto" in actions


def test_postgres_remote_poll_releases_lock_and_ignores_stale_result(
    postgres_harness: PostgresHarness,
):
    fixture = _create_publish_fixture(
        postgres_harness,
        status="submitted",
        external_id="postgres-race-001",
    )
    publish_job_id = fixture["publish_job_id"]

    class RacingConnector:
        reconciliation_supported = True

        def reconcile(self, _publish_job):
            with postgres_harness.sessions() as concurrent_session:
                concurrent_session.execute(text("SET LOCAL lock_timeout = '1s'"))
                current = concurrent_session.scalar(
                    select(PublishJob)
                    .where(PublishJob.id == publish_job_id)
                    .with_for_update()
                )
                assert current is not None
                current.status = "failed"
                current.external_id = None
                current.error = "manual decision won the PostgreSQL race"
                current.response_json = {
                    **dict(current.response_json or {}),
                    "manual_reconciliation": {
                        "decision": "confirmed_not_published"
                    },
                }
                concurrent_session.commit()
            return ConnectorResult(
                status="published",
                external_id="late-postgres-article",
                external_url="https://mp.weixin.qq.com/s/late-postgres-article",
                response={"article_id": "late-postgres-article"},
            )

    with patch(
        "contentflow.worker.build_connector",
        return_value=RacingConnector(),
    ):
        with postgres_harness.sessions() as session:
            result = handle_publish_reconcile(
                session,
                {"publish_job_id": publish_job_id},
                postgres_harness.settings,
            )

    assert result["status"] == "failed"
    assert result["ignored_remote_status"] == "published"
    with postgres_harness.sessions() as session:
        publish_job = session.get(PublishJob, publish_job_id)
        actions = set(
            session.scalars(
                select(AuditLog.action).where(
                    AuditLog.entity_id == publish_job_id
                )
            )
        )
        assert publish_job is not None
        assert publish_job.status == "failed"
        assert publish_job.external_id is None
        assert publish_job.error == "manual decision won the PostgreSQL race"
        assert "publish.reconciliation_stale_ignored" in actions


def test_postgres_auth_rate_limit_serializes_same_key(
    postgres_harness: PostgresHarness,
):
    identifier = f"shared-login-{uuid.uuid4().hex}@example.com"
    barrier = Barrier(2)

    def attempt() -> int:
        with postgres_harness.sessions() as session:
            barrier.wait(timeout=10)
            try:
                consume_rate_limits(
                    session,
                    settings=postgres_harness.settings,
                    keys=[
                        RateLimitKey(
                            scope="postgres-login-account",
                            identifier=identifier,
                            max_attempts=1,
                        )
                    ],
                )
            except HTTPException as error:
                return error.status_code
            return 200

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = sorted(executor.map(lambda _: attempt(), range(2)))
    assert statuses == [200, 429]

    key_hash = hash_rate_limit_key(
        "postgres-login-account",
        identifier,
        postgres_harness.settings.secret_key,
    )
    with postgres_harness.sessions() as session:
        row = session.get(AuthRateLimit, key_hash)
        assert row is not None
        assert row.attempts == 2
        assert row.blocked_until is not None


def test_postgres_operational_metrics_collector(postgres_harness: PostgresHarness):
    metrics = ObservabilityMetrics(
        postgres_harness.settings,
        postgres_harness.sessions,
    )
    payload = metrics.render().decode("utf-8")
    assert "contentflow_queue_jobs" in payload
    assert "contentflow_queue_oldest_ready_age_seconds" in payload
    assert "contentflow_worker_nodes" in payload
    assert "contentflow_workflow_runs" in payload
    assert "contentflow_prompt_eval_runs" in payload
    assert "contentflow_publish_reconciliation_required" in payload
