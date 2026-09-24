from __future__ import annotations

from pathlib import Path
from runpy import run_path
import subprocess
import sys
import json
from unittest.mock import patch

import pytest

from contentflow.migrate import (
    HEAD_REVISION,
    MINIMUM_PUBLIC_TABLE_COUNT,
    validate_public_restore_contract,
)


validate_worker_runtime_environment = run_path(
    "scripts/validate_public_test_deployment.py"
)["validate_worker_runtime_environment"]


def test_container_default_is_runtime_only_and_never_runs_a_migration():
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    commands = [json.loads(line.removeprefix("CMD ")) for line in dockerfile.splitlines() if line.startswith("CMD ")]
    assert commands == [["uvicorn", "contentflow.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]]
    import yaml
    for path in ("docker-compose.yml", "deploy/public-test/compose.yml", "deploy/private-test/compose.app.yml"):
        api = yaml.safe_load(Path(path).read_text(encoding="utf-8"))["services"]["api"]
        assert api.get("command", commands[0]) == commands[0]


def test_release_preflight_runs_without_installed_application_or_python_dependencies(tmp_path):
    files = ("scripts/validate_public_test_deployment.py", "contentflow/schema_contract.py")
    workflow = Path(".github/workflows/deploy-public-test.yml").read_text(encoding="utf-8")
    for name in files:
        assert name in workflow
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(Path(name).read_text(encoding="utf-8"), encoding="utf-8")
    # -I -S removes workspace/user packages and site dependencies. Even the
    # application package itself is absent from this release-layout fixture.
    result = subprocess.run([sys.executable, "-I", "-S", str(tmp_path / files[0]), "--help"],
        cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "--print-embedding-provider" in result.stdout


@pytest.mark.parametrize("api_mode,worker_mode,valid", [("openai-compatible", "openai-compatible", True),
    ("bge-m3-local", "bge-m3-local", True), ("openai-compatible", "bge-m3-local", False),
    ("hash", "hash", False), (None, None, False)])
def test_preflight_requires_explicit_matching_embedding_modes(api_mode, worker_mode, valid):
    validate = run_path("scripts/validate_public_test_deployment.py")["validate_document"]
    document = {"services": {"api": {"environment": {"CONTENTFLOW_EMBEDDING_PROVIDER": api_mode}},
        "worker": {"environment": {"CONTENTFLOW_EMBEDDING_PROVIDER": worker_mode}}}}
    document["services"].update({name: {} for name in ("postgres", "web", "caddy", "embedding-bootstrap", "backup-db", "restic")})
    for service in document["services"].values():
        service["image"] = "test/image@sha256:" + "1" * 64
    relevant = [error for error in validate(document, caddyfile="") if "embedding" in error.lower()]
    assert bool(relevant) is not valid


def test_compose_failure_does_not_echo_secret_bearing_diagnostics():
    render = run_path("scripts/validate_public_test_deployment.py")["_render_compose"]
    failure = subprocess.CalledProcessError(1, "docker", stderr="TEST-ONLY-SECRET")
    with patch("subprocess.run", side_effect=failure), pytest.raises(RuntimeError) as captured:
        render(Path("dummy.yml"), Path("synthetic.env"))
    assert "TEST-ONLY-SECRET" not in str(captured.value)
    assert captured.value.__suppress_context__


def test_public_restore_contract_tracks_current_schema() -> None:
    script = Path("deploy/public-test/verify-backup.sh").read_text(
        encoding="utf-8"
    )
    assert validate_public_restore_contract(script) == []
    assert f'test "$revision" = "{HEAD_REVISION}"' in script
    assert f'test "$tables" -ge {MINIMUM_PUBLIC_TABLE_COUNT}' in script


def test_public_restore_contract_rejects_stale_schema_guards() -> None:
    errors = validate_public_restore_contract(
        'test "$revision" = "old-head"\n'
        'test "$tables" -ge 1\n'
    )
    assert len(errors) == 2


def test_compose_files_plumb_operational_runtime_bounds() -> None:
    required_keys = (
        "CONTENTFLOW_WORKSPACE_STORAGE_MAX_BYTES",
        "CONTENTFLOW_WORKSPACE_STORAGE_MAX_OBJECTS",
        "CONTENTFLOW_STORAGE_RESERVATION_TTL_MINUTES",
        "CONTENTFLOW_STORAGE_CLEANUP_BATCH_SIZE",
        "CONTENTFLOW_STORAGE_DELETE_MAX_ATTEMPTS",
        "CONTENTFLOW_STORAGE_ORPHAN_GRACE_SECONDS",
        "CONTENTFLOW_STORAGE_RECONCILE_SCHEDULE_ENABLED",
        "CONTENTFLOW_STORAGE_RECONCILE_INTERVAL_HOURS",
        "CONTENTFLOW_STORAGE_RECONCILE_SCHEDULE_BATCH_SIZE",
        "CONTENTFLOW_STORAGE_RECONCILE_SCHEDULE_POLL_SECONDS",
        "CONTENTFLOW_PUBLISH_RECONCILIATION_INITIAL_DELAY_SECONDS",
        "CONTENTFLOW_PUBLISH_RECONCILIATION_MAX_ATTEMPTS",
        "CONTENTFLOW_PUBLISH_RECONCILIATION_SWEEP_POLL_SECONDS",
        "CONTENTFLOW_PUBLISH_RECONCILIATION_SWEEP_BATCH_SIZE",
        "CONTENTFLOW_WORKER_POLL_SECONDS",
        "CONTENTFLOW_WORKER_LEASE_SECONDS",
        "CONTENTFLOW_WORKER_MAX_ATTEMPTS",
        "CONTENTFLOW_WORKER_HEARTBEAT_SECONDS",
        "CONTENTFLOW_WORKER_STALE_SECONDS",
        "CONTENTFLOW_WORKER_QUEUE_STALL_SECONDS",
        "CONTENTFLOW_WORKER_DATABASE_RETRY_INITIAL_SECONDS",
        "CONTENTFLOW_WORKER_DATABASE_RETRY_MAX_SECONDS",
        "CONTENTFLOW_WORKER_DATABASE_RETRY_MAX_ATTEMPTS",
        "CONTENTFLOW_WORKER_DATABASE_RETRY_JITTER_RATIO",
    )
    local_compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    public_compose = Path("deploy/public-test/compose.yml").read_text(encoding="utf-8")
    public_env = Path("deploy/public-test/env.example").read_text(encoding="utf-8")
    for key in required_keys:
        assert sum(
            line.strip().startswith(f"{key}:")
            for line in local_compose.splitlines()
        ) == 2
        assert sum(
            line.strip().startswith(f"{key}:")
            for line in public_compose.splitlines()
        ) == 1
        assert f"{key}=" in public_env


def test_public_worker_runtime_validation_fails_closed() -> None:
    valid = {
        "CONTENTFLOW_WORKER_POLL_SECONDS": "1",
        "CONTENTFLOW_WORKER_LEASE_SECONDS": "300",
        "CONTENTFLOW_WORKER_MAX_ATTEMPTS": "4",
        "CONTENTFLOW_WORKER_HEARTBEAT_SECONDS": "10",
        "CONTENTFLOW_WORKER_STALE_SECONDS": "45",
        "CONTENTFLOW_WORKER_QUEUE_STALL_SECONDS": "300",
        "CONTENTFLOW_WORKER_DATABASE_RETRY_INITIAL_SECONDS": "1",
        "CONTENTFLOW_WORKER_DATABASE_RETRY_MAX_SECONDS": "30",
        "CONTENTFLOW_WORKER_DATABASE_RETRY_MAX_ATTEMPTS": "8",
        "CONTENTFLOW_WORKER_DATABASE_RETRY_JITTER_RATIO": "0.2",
    }
    assert validate_worker_runtime_environment(valid) == []

    invalid_cases = (
        ({"CONTENTFLOW_WORKER_POLL_SECONDS": "nan"}, "positive number"),
        ({"CONTENTFLOW_WORKER_DATABASE_RETRY_MAX_ATTEMPTS": "0"}, "positive integer"),
        ({"CONTENTFLOW_WORKER_DATABASE_RETRY_JITTER_RATIO": "1.1"}, "between 0 and 1"),
        (
            {
                "CONTENTFLOW_WORKER_DATABASE_RETRY_INITIAL_SECONDS": "31",
                "CONTENTFLOW_WORKER_DATABASE_RETRY_MAX_SECONDS": "30",
            },
            "maximum must not be less",
        ),
        (
            {
                "CONTENTFLOW_WORKER_HEARTBEAT_SECONDS": "10",
                "CONTENTFLOW_WORKER_STALE_SECONDS": "20",
            },
            "stale threshold",
        ),
    )
    for overrides, expected in invalid_cases:
        candidate = {**valid, **overrides}
        assert any(
            expected in error
            for error in validate_worker_runtime_environment(candidate)
        )
