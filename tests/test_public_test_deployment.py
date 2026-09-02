from __future__ import annotations

from pathlib import Path

from contentflow.migrate import (
    HEAD_REVISION,
    MINIMUM_PUBLIC_TABLE_COUNT,
    validate_public_restore_contract,
)


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


def test_compose_files_plumb_reconciliation_runtime_bounds() -> None:
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
