"""add worker node heartbeats

Revision ID: c9e7b4a2d610
Revises: 8b6c1f3a9d21
Create Date: 2026-08-08 21:30:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c9e7b4a2d610"
down_revision: Union[str, None] = "8b6c1f3a9d21"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "worker_nodes",
        sa.Column("id", sa.String(length=120), nullable=False),
        sa.Column("hostname", sa.String(length=255), nullable=False),
        sa.Column("process_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=24),
            server_default="online",
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "metadata_json",
            sa.JSON(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_worker_nodes")),
    )
    op.create_index(
        op.f("ix_worker_nodes_heartbeat_at"),
        "worker_nodes",
        ["heartbeat_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_worker_nodes_status"),
        "worker_nodes",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_worker_nodes_status_heartbeat",
        "worker_nodes",
        ["status", "heartbeat_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_worker_nodes_status_heartbeat",
        table_name="worker_nodes",
    )
    op.drop_index(
        op.f("ix_worker_nodes_status"),
        table_name="worker_nodes",
    )
    op.drop_index(
        op.f("ix_worker_nodes_heartbeat_at"),
        table_name="worker_nodes",
    )
    op.drop_table("worker_nodes")
