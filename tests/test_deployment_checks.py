"""Promotion checks must identify the candidate, not any healthy old runtime."""

from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from unittest.mock import patch

import pytest
from sqlalchemy.orm import sessionmaker

from contentflow import db
from contentflow.deployment_checks import api_is_ready, main, worker_is_ready
from contentflow.entities import WorkerNode
from contentflow.worker import WorkerNodeHeartbeat


SHA = "a" * 40


@pytest.mark.parametrize("change", [{}, {"release_sha": "b" * 40}, {"schema": "incompatible"},
    {"status": "not_ready"}, {"storage": "unavailable"}, {"database": "unavailable"}, {"schema": None}])
def test_api_promotion_requires_all_checks_and_exact_revision(change):
    payload = {"status": "ready", "database": "ok", "schema": "ok", "storage": "ok", "release_sha": SHA}
    assert api_is_ready({**payload, **change}, SHA) is (not change)
    assert not api_is_ready([], SHA)


@pytest.fixture
def sessions(tmp_path):
    engine = db.build_engine(f"sqlite:///{(tmp_path / 'nodes.db').as_posix()}")
    db.Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


@pytest.mark.parametrize("case", ["current", "old_container", "old_release", "unknown_release",
    "stale", "future", "stopped", "stopped_timestamp"])
def test_worker_promotion_rejects_other_containers_and_stale_evidence(sessions, case):
    now = datetime.now(timezone.utc)
    fields = {"id": "test", "hostname": "candidate-container", "process_id": 1,
        "status": "online", "heartbeat_at": now, "metadata_json": {"release_sha": SHA}}
    overrides = {
        "current": {}, "old_container": {"hostname": "old-container"},
        "old_release": {"metadata_json": {"release_sha": "b" * 40}},
        "unknown_release": {"metadata_json": {}},
        "stale": {"heartbeat_at": now - timedelta(minutes=5)},
        "future": {"heartbeat_at": now + timedelta(minutes=5)},
        "stopped": {"status": "stopped"}, "stopped_timestamp": {"stopped_at": now},
    }
    with sessions.begin() as session:
        session.add(WorkerNode(**{**fields, **overrides[case]}))
    assert worker_is_ready(sessions, hostname="candidate-container", release_sha=SHA, stale_seconds=45) is (case == "current")


def test_heartbeat_persists_and_updates_non_secret_release_identity(sessions):
    first = WorkerNodeHeartbeat(session_factory=sessions, worker_id="test", interval_seconds=10,
        hostname="candidate-container", release_sha="b" * 40)
    assert first.pulse()
    first.release_sha = SHA
    assert first.pulse()
    assert worker_is_ready(sessions, hostname="candidate-container", release_sha=SHA, stale_seconds=45)
    assert first.pulse("stopped")
    assert not worker_is_ready(sessions, hostname="candidate-container", release_sha=SHA, stale_seconds=45)


@pytest.mark.parametrize("body,success", [(json.dumps({"status":"ready","database":"ok","schema":"ok",
    "storage":"ok","release_sha":SHA}).encode(), True), (b"invalid-json", False), (b"x"*65537, False)],
    ids=["ready", "invalid-json", "oversized-body"])
def test_api_cli_reads_only_bounded_loopback_health(body, success):
    with patch("sys.argv", ["probe", "api", SHA]), \
         patch("contentflow.deployment_checks.urllib.request.urlopen", return_value=BytesIO(body)) as opened:
        assert main() == (0 if success else 1)
        opened.assert_called_once_with("http://127.0.0.1:8000/health/ready", timeout=5)


def test_failed_probe_does_not_print_private_exception(capsys):
    with patch("sys.argv", ["probe", "api", SHA]), \
         patch("contentflow.deployment_checks.urllib.request.urlopen", side_effect=RuntimeError("TEST-ONLY-SECRET")):
        assert main() == 1
    assert "TEST-ONLY-SECRET" not in capsys.readouterr().out
