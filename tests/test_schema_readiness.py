"""Schema drift must not become healthy startup or silently trigger DDL."""

from pathlib import Path
import logging
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from contentflow import db
from contentflow.api import create_app
from contentflow.migrate import upgrade_database
from contentflow.settings import Settings
from contentflow.worker import Worker, main as worker_main


@pytest.fixture
def settings(tmp_path):
    value = Settings(_env_file=None, database_url=f"sqlite:///{(tmp_path / 'schema.db').as_posix()}",
        local_storage_dir=tmp_path / "storage", metrics_enabled=False, require_governed_prompts=False,
        text_provider="mock", image_provider="mock", video_provider="mock", embedding_provider="hash")
    yield value
    if db.engine is not None:
        db.engine.dispose()


def corrupt(engine, mode):
    with engine.begin() as connection:
        if mode == "old":
            connection.execute(text("UPDATE alembic_version SET version_num='e9f0a1b2c3d4'"))
        elif mode == "future":
            connection.execute(text("UPDATE alembic_version SET version_num='test-future-revision'"))
        elif mode == "multiple":
            connection.execute(text("INSERT INTO alembic_version VALUES ('test-second-head')"))
        elif mode == "unversioned":
            connection.execute(text("DROP TABLE alembic_version"))
        elif mode == "missing_table":
            connection.execute(text("DROP TABLE auth_rate_limits"))
        elif mode == "missing_column":
            connection.execute(text("ALTER TABLE jobs DROP COLUMN lease_token"))
        else:
            raise AssertionError(mode)


@pytest.mark.parametrize("mode", ["empty", "unversioned", "old", "future", "multiple", "missing_table", "missing_column"])
def test_production_api_refuses_incompatible_schema_without_ddl(settings, mode):
    if mode != "empty":
        upgrade_database(settings)
    engine = db.configure_database(settings.database_url)
    if mode != "empty":
        corrupt(engine, mode)
    tables_before = inspect(engine).get_table_names()
    # Isolated SQLite exercises the production lifecycle branch, not production
    # configuration validation (which independently requires PostgreSQL/S3).
    production = settings.model_copy(update={"environment": "production"})
    with patch("contentflow.api.upgrade_database", side_effect=AssertionError("runtime DDL")), \
         patch("contentflow.db.create_schema", side_effect=AssertionError("runtime create_all")):
        with pytest.raises(RuntimeError, match="schema"):
            with TestClient(create_app(production)):
                pass
    assert inspect(engine).get_table_names() == tables_before
    engine.dispose()


@pytest.mark.parametrize("mode", ["unversioned", "old", "future", "multiple", "missing_table", "missing_column"])
def test_readiness_rechecks_schema_drift_after_successful_startup(settings, mode):
    with TestClient(create_app(settings)) as client:
        assert client.get("/health/ready").status_code == 200
        corrupt(db.engine, mode)
        response = client.get("/health/ready")
        assert response.status_code == 503, response.text
        assert response.json()["status"] == "not_ready"
        assert response.json()["schema"] == "incompatible"
        assert response.headers["cache-control"] == "no-store"
        assert client.get("/health/live").status_code == 200


@pytest.mark.parametrize("component", ["database", "storage"])
def test_readiness_dependency_failures_are_503_and_redacted(settings, component, caplog):
    secret = "TEST-ONLY-private-host/password-token"
    with TestClient(create_app(settings)) as client:
        if component == "database":
            failure = patch("contentflow.db.SessionLocal", side_effect=OperationalError(secret, {}, Exception(secret)))
        else:
            storage = Mock()
            storage.check.side_effect = RuntimeError(secret)
            failure = patch("contentflow.api.build_object_storage", return_value=storage)
        with failure:
            response = client.get("/health/ready")
        assert response.status_code == 503
        assert response.json()[component] == "unavailable"
        assert secret not in response.text
        assert secret not in caplog.text


def test_production_worker_cli_does_not_create_an_empty_database(settings):
    engine = db.configure_database(settings.database_url)
    production = settings.model_copy(update={"environment": "production"})
    try:
        with patch("contentflow.worker.get_settings", return_value=production), \
             patch("sys.argv", ["contentflow-worker", "--once"]), \
             patch("contentflow.worker.Worker") as worker:
            with pytest.raises(RuntimeError, match="schema"):
                worker_main()
            worker.assert_not_called()
        assert inspect(engine).get_table_names() == []
    finally:
        engine.dispose()


def test_production_worker_constructor_refuses_missing_schema(settings):
    production = settings.model_copy(update={"environment": "production"})
    with pytest.raises(RuntimeError, match="schema"):
        with Worker(settings=production):
            pass
    # Constructor failure must close its owned engine, including on Windows.
    source = Path(settings.database_url.removeprefix("sqlite:///"))
    if source.exists():
        source.rename(source.with_suffix(".closed"))


def test_production_worker_checks_revision_before_claiming_the_next_job(settings):
    upgrade_database(settings)
    engine = db.configure_database(settings.database_url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    production = settings.model_copy(update={"environment": "production"})
    with Worker(settings=production, session_factory=factory) as worker:
        corrupt(engine, "future")
        with patch("contentflow.worker.claim_next_job") as claim:
            with pytest.raises(RuntimeError, match="schema"):
                worker.run_once()
            claim.assert_not_called()


def test_valid_production_startup_uses_migrations_without_runtime_create_all(settings, caplog):
    upgrade_database(settings)
    production = settings.model_copy(update={"environment": "production"})
    with patch("contentflow.db.create_schema", side_effect=AssertionError("runtime create_all")), \
         patch("contentflow.api.upgrade_database", side_effect=AssertionError("runtime migration")):
        with TestClient(create_app(production)) as client:
            caplog.set_level(logging.INFO, logger="contentflow.api")
            response = client.get("/health/ready")
            assert response.status_code == 200, response.text
            assert response.json()["schema"] == "ok"
            assert not logging.getLogger("contentflow.api").disabled
            assert '"event": "http.request"' in caplog.text


def test_development_initializes_only_through_migrations(settings):
    with patch("contentflow.db.create_schema", side_effect=AssertionError("migration omissions must not be hidden by create_all")):
        with TestClient(create_app(settings)) as client:
            assert client.get("/health/ready").status_code == 200


def test_schema_verification_succeeds_with_database_writes_forbidden(settings):
    from contentflow.database_schema import verify_database_schema

    upgrade_database(settings)
    engine = db.configure_database(settings.database_url)
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA query_only=ON")
        try:
            verify_database_schema(sessionmaker(bind=connection))
        finally:
            connection.exec_driver_sql("PRAGMA query_only=OFF")


def test_wrapped_database_outage_keeps_worker_recovery_classification():
    from contentflow.database_schema import SchemaCompatibilityError, verify_database_schema
    from contentflow.worker import DatabaseErrorKind, classify_database_error, sanitized_database_error

    secret = "TEST-ONLY-private-dsn-token"
    failure = OperationalError(secret, {}, Exception(secret))
    with pytest.raises(SchemaCompatibilityError) as captured:
        verify_database_schema(Mock(side_effect=failure))
    assert classify_database_error(captured.value) == DatabaseErrorKind.AVAILABILITY
    assert secret not in str(captured.value)
    assert secret not in sanitized_database_error(captured.value)
    assert captured.value.__suppress_context__


def test_production_worker_cli_accepts_a_migrated_database_without_ddl(settings):
    upgrade_database(settings)
    production = settings.model_copy(update={"environment": "production"})
    with patch("contentflow.worker.get_settings", return_value=production), \
         patch("sys.argv", ["contentflow-worker", "--once"]), \
         patch("contentflow.db.create_schema", side_effect=AssertionError("runtime create_all")), \
         patch("contentflow.worker.Worker") as worker:
        worker_main()
        worker.return_value.run_once.assert_called_once_with()
        assert not logging.getLogger("contentflow.worker").disabled


def test_development_worker_cli_migrates_before_starting(settings):
    with patch("contentflow.worker.get_settings", return_value=settings), \
         patch("sys.argv", ["contentflow-worker", "--once"]), \
         patch("contentflow.db.create_schema", side_effect=AssertionError("runtime create_all")), \
         patch("contentflow.worker.Worker") as worker:
        worker_main()
        worker.return_value.run_once.assert_called_once_with()
        assert "alembic_version" in inspect(db.engine).get_table_names()
        assert not logging.getLogger("contentflow.worker").disabled


@pytest.mark.parametrize("migrated", [False, True])
def test_production_admin_cli_does_not_implicitly_migrate_or_prompt_before_validation(settings, migrated):
    from types import SimpleNamespace
    from contentflow.bootstrap_admin import main as bootstrap_main

    if migrated:
        upgrade_database(settings)
    config = SimpleNamespace(production=True, database_url=settings.database_url, validate_runtime=lambda: None)
    with patch("contentflow.bootstrap_admin.Settings", return_value=config), \
         patch("contentflow.bootstrap_admin.upgrade_database", side_effect=AssertionError("implicit production migration")), \
         patch("contentflow.bootstrap_admin._read_password", return_value="TEST-ONLY-password") as password, \
         patch("contentflow.bootstrap_admin.bootstrap_workspace_admin", return_value=("test", "user")) as create, \
         patch("sys.argv", ["bootstrap", "bootstrap-workspace", "--email", "test@example.com",
             "--display-name", "Test", "--workspace-name", "Test"]):
        if migrated:
            assert bootstrap_main() == 0
            create.assert_called_once()
        else:
            with pytest.raises(RuntimeError, match="schema"):
                bootstrap_main()
            password.assert_not_called()
            create.assert_not_called()
