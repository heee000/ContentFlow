"""Idempotent inserts which leave transaction ownership with the caller."""

from typing import Any

from sqlalchemy import Table
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session


def insert_on_unique_key(
    session: Session,
    table: Table,
    *,
    values: dict[str, Any],
    key: str,
) -> str | None:
    """Return the inserted ID, or None for only the specified key conflict.

    No commit, rollback or savepoint: a native INSERT starts SQLite's real
    transaction too, unlike a first-write SAVEPOINT in legacy driver mode.
    Other constraints and caller-owned pending writes must fail normally.
    """
    dialect = session.get_bind().dialect.name
    builders = {"sqlite": sqlite_insert, "postgresql": postgres_insert}
    if dialect not in builders:
        raise RuntimeError("Atomic acceptance requires SQLite or PostgreSQL")
    session.flush()
    statement = (
        builders[dialect](table)
        .values(**values)
        .on_conflict_do_nothing(index_elements=[table.c[key]])
        .returning(table.c.id)
    )
    return session.execute(statement).scalar_one_or_none()
