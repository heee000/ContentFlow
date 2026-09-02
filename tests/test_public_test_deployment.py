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
