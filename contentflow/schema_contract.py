"""Dependency-free schema coordinates shared by runtime and release preflight."""

HEAD_REVISION = "f0a1b2c3d4e5"
MINIMUM_PUBLIC_TABLE_COUNT = 35


def validate_public_restore_contract(script: str) -> list[str]:
    errors: list[str] = []
    if f'test "$revision" = "{HEAD_REVISION}"' not in script:
        errors.append("public-test restore must require the current Alembic head")
    if f'test "$tables" -ge {MINIMUM_PUBLIC_TABLE_COUNT}' not in script:
        errors.append("public-test restore table threshold does not match the current schema")
    return errors
