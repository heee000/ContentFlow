"""add tamper-evident audit hash chains

Revision ID: 6d4e8f9a0b1c
Revises: 1a2b3c4d5e6f
Create Date: 2026-09-02 12:00:00
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "6d4e8f9a0b1c"
down_revision: Union[str, None] = "1a2b3c4d5e6f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INTEGRITY_VERSION = 1
GENESIS_HASH = "0" * 64


def _chain_scope(workspace_id: str | None) -> str:
    return f"workspace:{workspace_id}" if workspace_id else "system"


def _timestamp(value: datetime | str) -> str:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise RuntimeError("Existing audit metadata is not a JSON object")
    return value


def _entry_hash(
    *,
    row: Any,
    chain_scope: str,
    chain_sequence: int,
    previous_hash: str,
) -> str:
    payload = {
        "action": row.action,
        "actor_user_id": row.actor_user_id,
        "chain_scope": chain_scope,
        "chain_sequence": chain_sequence,
        "created_at": _timestamp(row.created_at),
        "entity_id": row.entity_id,
        "entity_type": row.entity_type,
        "event_id": row.id,
        "integrity_version": INTEGRITY_VERSION,
        "metadata": _metadata(row.metadata_json),
        "previous_hash": previous_hash,
        "request_id": row.request_id,
        "workspace_id": row.workspace_id,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def upgrade() -> None:
    op.create_table(
        "audit_chain_heads",
        sa.Column("chain_scope", sa.String(length=80), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=True),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("head_hash", sa.String(length=64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("sequence >= 0", name="sequence_non_negative"),
        sa.CheckConstraint("length(head_hash) = 64", name="head_hash_length"),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_audit_chain_heads_workspace_id_workspaces"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint(
            "chain_scope",
            name=op.f("pk_audit_chain_heads"),
        ),
    )
    op.create_index(
        op.f("ix_audit_chain_heads_workspace_id"),
        "audit_chain_heads",
        ["workspace_id"],
        unique=False,
    )

    with op.batch_alter_table("audit_logs") as batch_op:
        batch_op.add_column(sa.Column("chain_scope", sa.String(80), nullable=True))
        batch_op.add_column(sa.Column("chain_sequence", sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column("previous_hash", sa.String(64), nullable=True))
        batch_op.add_column(sa.Column("entry_hash", sa.String(64), nullable=True))
        batch_op.add_column(sa.Column("integrity_version", sa.Integer(), nullable=True))

    connection = op.get_bind()
    audit_logs = sa.table(
        "audit_logs",
        sa.column("id", sa.String(36)),
        sa.column("workspace_id", sa.String(36)),
        sa.column("actor_user_id", sa.String(36)),
        sa.column("action", sa.String(120)),
        sa.column("entity_type", sa.String(80)),
        sa.column("entity_id", sa.String(80)),
        sa.column("request_id", sa.String(64)),
        sa.column("metadata_json", sa.JSON()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("chain_scope", sa.String(80)),
        sa.column("chain_sequence", sa.BigInteger()),
        sa.column("previous_hash", sa.String(64)),
        sa.column("entry_hash", sa.String(64)),
        sa.column("integrity_version", sa.Integer()),
    )
    rows = connection.execute(
        sa.select(
            audit_logs.c.id,
            audit_logs.c.workspace_id,
            audit_logs.c.actor_user_id,
            audit_logs.c.action,
            audit_logs.c.entity_type,
            audit_logs.c.entity_id,
            audit_logs.c.request_id,
            audit_logs.c.metadata_json,
            audit_logs.c.created_at,
        ).order_by(audit_logs.c.created_at, audit_logs.c.id)
    )
    heads: dict[str, dict[str, Any]] = {}
    for row in rows:
        scope = _chain_scope(row.workspace_id)
        head = heads.setdefault(
            scope,
            {
                "workspace_id": row.workspace_id,
                "sequence": 0,
                "head_hash": GENESIS_HASH,
                "updated_at": row.created_at,
            },
        )
        sequence = int(head["sequence"]) + 1
        previous_hash = str(head["head_hash"])
        entry_hash = _entry_hash(
            row=row,
            chain_scope=scope,
            chain_sequence=sequence,
            previous_hash=previous_hash,
        )
        connection.execute(
            sa.update(audit_logs)
            .where(audit_logs.c.id == row.id)
            .values(
                chain_scope=scope,
                chain_sequence=sequence,
                previous_hash=previous_hash,
                entry_hash=entry_hash,
                integrity_version=INTEGRITY_VERSION,
            )
        )
        head.update(
            sequence=sequence,
            head_hash=entry_hash,
            updated_at=row.created_at,
        )

    audit_chain_heads = sa.table(
        "audit_chain_heads",
        sa.column("chain_scope", sa.String(80)),
        sa.column("workspace_id", sa.String(36)),
        sa.column("sequence", sa.BigInteger()),
        sa.column("head_hash", sa.String(64)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    if heads:
        connection.execute(
            sa.insert(audit_chain_heads),
            [
                {
                    "chain_scope": scope,
                    **head,
                }
                for scope, head in sorted(heads.items())
            ],
        )

    with op.batch_alter_table("audit_logs") as batch_op:
        batch_op.alter_column(
            "chain_scope",
            existing_type=sa.String(80),
            nullable=False,
        )
        batch_op.alter_column(
            "chain_sequence",
            existing_type=sa.BigInteger(),
            nullable=False,
        )
        batch_op.alter_column(
            "previous_hash",
            existing_type=sa.String(64),
            nullable=False,
        )
        batch_op.alter_column(
            "entry_hash",
            existing_type=sa.String(64),
            nullable=False,
        )
        batch_op.alter_column(
            "integrity_version",
            existing_type=sa.Integer(),
            nullable=False,
        )
        batch_op.create_unique_constraint(
            "uq_audit_log_chain_sequence",
            ["chain_scope", "chain_sequence"],
        )
        batch_op.create_check_constraint(
            "chain_sequence_positive",
            "chain_sequence > 0",
        )
        batch_op.create_check_constraint(
            "integrity_version",
            "integrity_version = 1",
        )
        batch_op.create_check_constraint(
            "hash_lengths",
            "length(previous_hash) = 64 AND length(entry_hash) = 64",
        )
        batch_op.create_index(
            op.f("ix_audit_logs_chain_scope"),
            ["chain_scope"],
            unique=False,
        )
        batch_op.create_index(
            op.f("ix_audit_logs_entry_hash"),
            ["entry_hash"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("audit_logs") as batch_op:
        batch_op.drop_index(op.f("ix_audit_logs_entry_hash"))
        batch_op.drop_index(op.f("ix_audit_logs_chain_scope"))
        batch_op.drop_constraint(
            op.f("ck_audit_logs_hash_lengths"),
            type_="check",
        )
        batch_op.drop_constraint(
            op.f("ck_audit_logs_integrity_version"),
            type_="check",
        )
        batch_op.drop_constraint(
            op.f("ck_audit_logs_chain_sequence_positive"),
            type_="check",
        )
        batch_op.drop_constraint(
            "uq_audit_log_chain_sequence",
            type_="unique",
        )
        batch_op.drop_column("integrity_version")
        batch_op.drop_column("entry_hash")
        batch_op.drop_column("previous_hash")
        batch_op.drop_column("chain_sequence")
        batch_op.drop_column("chain_scope")
    op.drop_index(
        op.f("ix_audit_chain_heads_workspace_id"),
        table_name="audit_chain_heads",
    )
    op.drop_table("audit_chain_heads")
