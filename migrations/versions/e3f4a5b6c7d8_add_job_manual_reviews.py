"""add durable job manual review workflow

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-09-03 23:59:00
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e3f4a5b6c7d8"
down_revision: Union[str, None] = "d2e3f4a5b6c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "job_manual_reviews",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=True),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("reason_code", sa.String(length=80), nullable=False),
        sa.Column("context_json", sa.JSON(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("provider_checked", sa.Boolean(), nullable=False),
        sa.Column("decision", sa.String(length=24), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "decision IS NULL OR decision IN ('retry', 'abandon')",
            name="decision",
        ),
        sa.CheckConstraint(
            "length(reason_code) > 0",
            name="reason_code_non_empty",
        ),
        sa.CheckConstraint(
            "((resolved_at IS NULL AND decision IS NULL AND note IS NULL "
            "AND provider_checked = false) OR "
            "(resolved_at IS NOT NULL AND decision IS NOT NULL "
            "AND length(note) >= 8 AND provider_checked = true))",
            name="resolution_consistent",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_job_manual_reviews_job_id",
        "job_manual_reviews",
        ["job_id"],
        unique=False,
    )
    op.create_index(
        "ix_job_manual_reviews_reason_code",
        "job_manual_reviews",
        ["reason_code"],
        unique=False,
    )
    op.create_index(
        "ix_job_manual_reviews_requested_at",
        "job_manual_reviews",
        ["requested_at"],
        unique=False,
    )
    op.create_index(
        "ix_job_manual_reviews_resolved_by_user_id",
        "job_manual_reviews",
        ["resolved_by_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_job_manual_reviews_workspace_id",
        "job_manual_reviews",
        ["workspace_id"],
        unique=False,
    )
    op.create_index(
        "ix_job_manual_reviews_workspace_requested_page",
        "job_manual_reviews",
        ["workspace_id", "requested_at", "id"],
        unique=False,
    )
    op.create_index(
        "uq_job_manual_reviews_open_job",
        "job_manual_reviews",
        ["job_id"],
        unique=True,
        postgresql_where=sa.text("resolved_at IS NULL"),
        sqlite_where=sa.text("resolved_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_job_manual_reviews_open_job",
        table_name="job_manual_reviews",
        postgresql_where=sa.text("resolved_at IS NULL"),
        sqlite_where=sa.text("resolved_at IS NULL"),
    )
    op.drop_index(
        "ix_job_manual_reviews_workspace_requested_page",
        table_name="job_manual_reviews",
    )
    op.drop_index(
        "ix_job_manual_reviews_workspace_id",
        table_name="job_manual_reviews",
    )
    op.drop_index(
        "ix_job_manual_reviews_resolved_by_user_id",
        table_name="job_manual_reviews",
    )
    op.drop_index(
        "ix_job_manual_reviews_requested_at",
        table_name="job_manual_reviews",
    )
    op.drop_index(
        "ix_job_manual_reviews_reason_code",
        table_name="job_manual_reviews",
    )
    op.drop_index(
        "ix_job_manual_reviews_job_id",
        table_name="job_manual_reviews",
    )
    op.drop_table("job_manual_reviews")
