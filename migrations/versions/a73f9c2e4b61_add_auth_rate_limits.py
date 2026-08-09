"""add shared authentication rate limits

Revision ID: a73f9c2e4b61
Revises: f4c2d8e7a190
Create Date: 2026-08-09 04:15:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a73f9c2e4b61"
down_revision: Union[str, None] = "f4c2d8e7a190"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "auth_rate_limits",
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("scope", sa.String(length=40), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column(
            "window_started_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("blocked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "key_hash",
            name=op.f("pk_auth_rate_limits"),
        ),
    )
    op.create_index(
        op.f("ix_auth_rate_limits_scope"),
        "auth_rate_limits",
        ["scope"],
        unique=False,
    )
    op.create_index(
        op.f("ix_auth_rate_limits_blocked_until"),
        "auth_rate_limits",
        ["blocked_until"],
        unique=False,
    )
    op.create_index(
        op.f("ix_auth_rate_limits_expires_at"),
        "auth_rate_limits",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_auth_rate_limits_scope_expires",
        "auth_rate_limits",
        ["scope", "expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_auth_rate_limits_scope_expires",
        table_name="auth_rate_limits",
    )
    op.drop_index(
        op.f("ix_auth_rate_limits_expires_at"),
        table_name="auth_rate_limits",
    )
    op.drop_index(
        op.f("ix_auth_rate_limits_blocked_until"),
        table_name="auth_rate_limits",
    )
    op.drop_index(
        op.f("ix_auth_rate_limits_scope"),
        table_name="auth_rate_limits",
    )
    op.drop_table("auth_rate_limits")
