"""Disposable schema upgrades and non-reusable execution identities."""

from datetime import datetime, timezone

from alembic import command
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from contentflow.db import build_engine
from contentflow.entities import Job
from contentflow.execution_fence import JobLeaseLost
from contentflow.job_queue import claim_next_job, complete_job, renew_job_lease
from contentflow.migrate import HEAD_REVISION, upgrade_database
from contentflow.settings import Settings
from test_generation_migration import migrate


def test_lease_migration_preserves_legacy_job_and_rotates_every_claim(tmp_path):
    engine = build_engine(f"sqlite:///{(tmp_path / 'lease.db').as_posix()}")
    try:
        migrate(engine, command.upgrade, "d8e9f0a1b2c3")
        with engine.begin() as connection:
            connection.execute(text("""
                INSERT INTO jobs (id, job_type, payload_json, status, attempts,
                    max_attempts, run_at, idempotency_key, result_json, created_at, updated_at)
                VALUES ('legacy', 'test.fence', '{}', 'queued', 0, 4,
                    :now, 'legacy-key', :result, :now, :now)
            """), {"now": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(" "), "result": '{"preserved":true}'})
        migrate(engine, command.upgrade, "head")
        with Session(engine, expire_on_commit=False) as session:
            assert session.get(Job, "legacy").lease_token is None
            assert session.get(Job, "legacy").result_json == {"preserved": True}
            first = claim_next_job(session, worker_id="same-worker", lease_seconds=60, manual_review_job_types=())
            session.commit()
            old_token = first.lease_token
            assert old_token and len(old_token) == 32 and first.attempts == 1
            first.status = "queued"
            first.attempts = 0
            session.commit()
            second = claim_next_job(session, worker_id="same-worker", lease_seconds=60, manual_review_job_types=())
            session.commit()
            assert second.attempts == 1 and second.lease_token != old_token
            assert not renew_job_lease(session, job_id=second.id, worker_id="same-worker", attempt=1,
                lease_seconds=60, lease_token=old_token)
            session.rollback()
            with pytest.raises(JobLeaseLost):
                complete_job(session, second, {}, worker_id="same-worker", attempt=1,
                    lease_seconds=60, lease_token=old_token)
            session.rollback()
        migrate(engine, command.downgrade, "d8e9f0a1b2c3")
        assert "lease_token" not in {c["name"] for c in inspect(engine).get_columns("jobs")}
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT idempotency_key FROM jobs WHERE id='legacy'")) == "legacy-key"
    finally:
        engine.dispose()


@pytest.mark.parametrize("version", ["d8e9f0a1b2c3", "head"])
def test_unversioned_old_and_new_lease_schemas_are_adopted_correctly(tmp_path, version):
    url = f"sqlite:///{(tmp_path / 'adopt.db').as_posix()}"
    engine = build_engine(url)
    try:
        migrate(engine, command.upgrade, version)
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM alembic_version"))
        upgrade_database(Settings(_env_file=None, database_url=url, secret_key="TEST",
            local_storage_dir=tmp_path / "storage"))
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == HEAD_REVISION
        assert "lease_token" in {c["name"] for c in inspect(engine).get_columns("jobs")}
    finally:
        engine.dispose()


def test_unversioned_wrong_lease_column_is_not_adopted(tmp_path):
    url = f"sqlite:///{(tmp_path / 'invalid.db').as_posix()}"
    engine = build_engine(url)
    try:
        migrate(engine, command.upgrade, "d8e9f0a1b2c3")
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE jobs ADD COLUMN lease_token VARCHAR(8)"))
            connection.execute(text("DELETE FROM alembic_version"))
        with pytest.raises(RuntimeError, match="lease identity schema"):
            upgrade_database(Settings(_env_file=None, database_url=url, secret_key="TEST",
                local_storage_dir=tmp_path / "storage"))
    finally:
        engine.dispose()
