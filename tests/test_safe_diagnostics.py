"""Real error paths must stay observable without copying private payloads."""

from datetime import datetime, timezone
import logging
import traceback

import httpx
import pytest
from sqlalchemy import select
from starlette.responses import StreamingResponse

from contentflow import db
from contentflow.entities import Job
from contentflow.migrate import upgrade_database
from contentflow.settings import Settings
from contentflow.storage_ledger import _delete_rollback_objects
from contentflow.worker import Worker
import test_worker_v2 as worker_tests


PRIVATE = "TEST_ONLY_PRIVATE_PAYLOAD_DO_NOT_LOG"


@pytest.fixture
def application():
    fixture = worker_tests.WorkerIntegrationTest()
    fixture.setUp()
    fixture.settings.storage_reconcile_schedule_enabled = False
    try:
        yield fixture
    finally:
        fixture.tearDown()


def enable_capture(caplog, monkeypatch, name):
    # Verify the content safety independently of migration's disabled-logger bug.
    monkeypatch.setattr(logging.getLogger(name), "disabled", False)
    caplog.set_level(logging.ERROR, logger=name)


def test_real_sql_constraint_error_has_safe_response_and_observable_diagnostic(application, caplog, monkeypatch):
    fixture = application._create_publish_fixture(status="published")
    payload = {"publish_job_id": fixture["publish_job_id"],
        "captured_at": datetime.now(timezone.utc).isoformat(), "impressions": 1}
    assert application.client.post("/api/v1/metrics/snapshots", headers=application.headers,
        json=payload).status_code == 201
    enable_capture(caplog, monkeypatch, "contentflow.api")
    # The normal server setting must not rethrow the raw error to Uvicorn.
    response = application.client.post("/api/v1/metrics/snapshots", headers=application.headers,
        json={**payload, "raw": {"private_note": PRIVATE}})
    assert response.status_code == 500
    assert PRIVATE not in response.text
    assert response.headers["x-request-id"] == response.json()["error"]["request_id"]
    assert response.headers["cache-control"] == "no-store"
    assert "http.unhandled_error" in caplog.text and "IntegrityError" in caplog.text
    assert "ingest_metric" in caplog.text, "retain a safe code location for diagnosis"
    assert PRIVATE not in caplog.text
    assert "INSERT INTO" not in caplog.text
    assert not any(record.exc_info for record in caplog.records if record.name == "contentflow.api")


@pytest.mark.parametrize("kind", ["runtime", "nested", "group", "network"])
def test_unexpected_exception_messages_and_chains_never_escape(application, caplog, monkeypatch, kind):
    def fail():
        if kind == "network":
            raise httpx.ConnectError(PRIVATE,
                request=httpx.Request("GET", f"https://example.invalid/?token={PRIVATE}"))
        if kind == "group":
            raise ExceptionGroup(PRIVATE, [RuntimeError(PRIVATE), ValueError(PRIVATE)])
        if kind == "nested":
            try:
                raise ValueError(PRIVATE)
            except ValueError as cause:
                raise RuntimeError(PRIVATE) from cause
        raise RuntimeError(PRIVATE)

    application.client.app.add_api_route("/test-only/error", fail)
    enable_capture(caplog, monkeypatch, "contentflow.api")
    response = application.client.get("/test-only/error")
    assert response.status_code == 500
    assert PRIVATE not in response.text + caplog.text
    assert "http.unhandled_error" in caplog.text and '"frames"' in caplog.text


def test_stream_failure_is_not_false_success_or_raw_server_traceback(application, caplog, monkeypatch):
    async def stream():
        yield b"synthetic partial response"
        raise RuntimeError(PRIVATE)

    def response():
        return StreamingResponse(stream())

    application.client.app.add_api_route("/test-only/stream", response)
    enable_capture(caplog, monkeypatch, "contentflow.api")
    with pytest.raises(Exception) as caught:
        application.client.get("/test-only/stream")
    formatted = "".join(traceback.format_exception(caught.value))
    assert PRIVATE not in formatted + caplog.text
    assert "ResponseStreamFailure" in formatted
    assert "stream" in caplog.text


def test_runtime_engine_hides_sql_parameters(tmp_path):
    engine = db.build_engine(f"sqlite:///{(tmp_path / 'diagnostics.db').as_posix()}")
    try:
        assert engine.hide_parameters is True
    finally:
        engine.dispose()


def test_programmatic_migration_preserves_application_and_host_logging(tmp_path, monkeypatch):
    root = logging.getLogger()
    logger = logging.getLogger("contentflow.api")
    monkeypatch.setattr(logger, "disabled", False)
    monkeypatch.setattr(logger, "level", logging.INFO)
    handlers, level = list(root.handlers), root.level
    settings = Settings(_env_file=None, database_url=f"sqlite:///{(tmp_path / 'migration.db').as_posix()}")
    upgrade_database(settings)
    assert root.handlers == handlers and root.level == level
    assert logger.disabled is False and logger.level == logging.INFO


def test_development_api_keeps_logs_enabled(application):
    assert logging.getLogger("contentflow.api").disabled is False


def test_unknown_worker_exception_does_not_leak_to_logs_or_job_receipt(application, caplog, monkeypatch):
    with db.SessionLocal() as session:
        job = Job(workspace_id=application.workspace_id, job_type="diagnostics.test",
            payload_json={}, idempotency_key="test-diagnostics", run_at=datetime.now(timezone.utc))
        session.add(job)
        session.commit()
        job_id = job.id

    def fail(*args):
        raise RuntimeError(PRIVATE)

    enable_capture(caplog, monkeypatch, "contentflow.worker")
    with Worker(settings=application.settings, session_factory=db.SessionLocal,
            handlers={"diagnostics.test": fail}) as worker:
        assert worker.run_once()
    with db.SessionLocal() as session:
        error = session.scalar(select(Job.last_error).where(Job.id == job_id))
    assert "RuntimeError" in error
    assert PRIVATE not in error + caplog.text
    assert job_id in caplog.text and "fail" in caplog.text


def test_storage_compensation_error_has_no_raw_exception(caplog):
    class BrokenStorage:
        def delete(self, uri):
            raise RuntimeError(PRIVATE)

    class Session:
        pass

    # Use the actual callback key without invoking a real storage service.
    from contentflow.storage_ledger import _ROLLBACK_OBJECTS
    session = Session()
    session.info = {_ROLLBACK_OBJECTS: [(BrokenStorage(), "test-only-object")]}
    logger = logging.getLogger("contentflow.storage_ledger")
    previous = logger.disabled
    logger.disabled = False
    try:
        caplog.set_level(logging.ERROR, logger=logger.name)
        _delete_rollback_objects(session)
    finally:
        logger.disabled = previous
    assert "rolled-back" in caplog.text and "RuntimeError" in caplog.text
    assert PRIVATE not in caplog.text


def test_diagnostic_is_bounded_and_never_formats_error_objects():
    from contentflow.diagnostics import exception_diagnostic

    class SensitiveError(RuntimeError):
        def __str__(self):
            raise AssertionError("must not format exception values")

        def __repr__(self):
            raise AssertionError("must not format exception values")

    cause = SensitiveError(PRIVATE)
    cause.__cause__ = cause
    grouped = ExceptionGroup(PRIVATE, [cause for _ in range(100)])
    diagnostic = exception_diagnostic(grouped)
    assert len(diagnostic["exceptions"]) <= 8
    assert all(len(item["frames"]) <= 32 for item in diagnostic["exceptions"])
    assert PRIVATE not in str(diagnostic)


def test_media_configuration_receipt_uses_only_fixed_codes_and_guidance():
    from contentflow.media_providers import (
        MediaConfigurationError, MediaProviderError, media_configuration_receipt,
    )

    for code in ("media_source_configuration_changed", "media_poll_configuration_changed"):
        error = MediaConfigurationError(code)
        error.args = (PRIVATE,)
        error.__cause__ = RuntimeError(PRIVATE)
        receipt = media_configuration_receipt(error)
        assert code in receipt and PRIVATE not in receipt
        assert not error.retryable
        error.code = PRIVATE
        assert media_configuration_receipt(error) is None

    class UntrustedConfigurationError(MediaConfigurationError):
        pass

    assert media_configuration_receipt(UntrustedConfigurationError(
        "media_source_configuration_changed")) is None
    assert media_configuration_receipt(MediaProviderError(PRIVATE, retryable=False)) is None


@pytest.mark.parametrize("entry", ["alembic", "development_admin", "production_admin"])
def test_migration_and_admin_entries_preserve_existing_loggers(tmp_path, entry):
    # fileConfig legitimately owns CLI handlers. Isolate that global effect in
    # a fresh process and prove a pre-existing application logger still emits.
    from test_runtime_initialization import run_isolated

    code = '''
from pathlib import Path
from unittest.mock import patch
import logging
from sqlalchemy import inspect
from contentflow import db
from contentflow.migrate import upgrade_database, _alembic_config
from contentflow.settings import Settings
logger = logging.getLogger("contentflow.api")
logger.setLevel(logging.WARNING)
settings = Settings(_env_file=None, database_url=f"sqlite:///{Path('test.db').resolve().as_posix()}")
'''
    if entry == "alembic":
        code += '''
from alembic import command
engine = db.build_engine(settings.database_url)
try:
    with engine.begin() as connection:
        config = _alembic_config(connection)
        config.attributes.pop("configure_logger")
        command.upgrade(config, "head")
    assert "alembic_version" in inspect(engine).get_table_names()
finally:
    engine.dispose()
'''
    else:
        code += '''
from types import SimpleNamespace
from contentflow.bootstrap_admin import main
'''
        if entry == "production_admin":
            code += "upgrade_database(settings)\n"
        code += f"settings = SimpleNamespace(production={entry == 'production_admin'}, database_url=settings.database_url, validate_runtime=lambda: None)\n"
        code += '''
try:
    with patch("contentflow.bootstrap_admin.Settings", return_value=settings), \\
         patch("contentflow.bootstrap_admin._read_password", return_value="TEST-ONLY"), \\
         patch("contentflow.bootstrap_admin.bootstrap_workspace_admin", return_value=("test", "user")), \\
         patch("sys.argv", ["bootstrap", "bootstrap-workspace", "--email", "test@example.com", "--display-name", "Test", "--workspace-name", "Test"]):
        assert main() == 0
    assert "alembic_version" in inspect(db.engine).get_table_names()
finally:
    if db.engine is not None:
        db.engine.dispose()
'''
    code += '''
assert not logger.disabled
events = []
class Capture(logging.Handler):
    def emit(self, record):
        events.append(record.getMessage())
logger.addHandler(Capture())
logger.warning("TEST-ONLY observable after entry")
assert events == ["TEST-ONLY observable after entry"]
'''
    run_isolated(code, tmp_path)
