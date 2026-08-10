"""add versioned prompt evaluation gates

Revision ID: c95f1e4a8d73
Revises: b84e0d3f7c92
Create Date: 2026-08-10 16:45:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c95f1e4a8d73"
down_revision: Union[str, None] = "b84e0d3f7c92"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "prompt_eval_suites",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("cases_json", sa.JSON(), nullable=False),
        sa.Column("suite_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("activated_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "version_number > 0",
            name="version_number_positive",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'retired')",
            name="status",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_prompt_eval_suites_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_prompt_eval_suites_created_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["activated_by_user_id"],
            ["users.id"],
            name=op.f("fk_prompt_eval_suites_activated_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_prompt_eval_suites")),
        sa.UniqueConstraint(
            "workspace_id",
            "version_number",
            name="uq_prompt_eval_suite_workspace_version",
        ),
    )
    op.create_index(
        op.f("ix_prompt_eval_suites_workspace_id"),
        "prompt_eval_suites",
        ["workspace_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_prompt_eval_suites_status"),
        "prompt_eval_suites",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_prompt_eval_suites_suite_hash"),
        "prompt_eval_suites",
        ["suite_hash"],
        unique=False,
    )
    op.create_index(
        op.f("ix_prompt_eval_suites_created_by_user_id"),
        "prompt_eval_suites",
        ["created_by_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_prompt_eval_suites_activated_by_user_id"),
        "prompt_eval_suites",
        ["activated_by_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_prompt_eval_suites_workspace_status",
        "prompt_eval_suites",
        ["workspace_id", "status"],
        unique=False,
    )
    op.create_index(
        "uq_prompt_eval_suite_workspace_active",
        "prompt_eval_suites",
        ["workspace_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
        sqlite_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "prompt_eval_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("prompt_release_id", sa.String(length=36), nullable=False),
        sa.Column("suite_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("requested_provider", sa.String(length=80), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=True),
        sa.Column("model", sa.String(length=160), nullable=True),
        sa.Column("prompt_hashes_json", sa.JSON(), nullable=False),
        sa.Column("suite_hash", sa.String(length=64), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'passed', 'failed', 'error')",
            name="status",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_prompt_eval_runs_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["prompt_release_id"],
            ["prompt_releases.id"],
            name=op.f("fk_prompt_eval_runs_prompt_release_id_prompt_releases"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["suite_id"],
            ["prompt_eval_suites.id"],
            name=op.f("fk_prompt_eval_runs_suite_id_prompt_eval_suites"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_prompt_eval_runs_created_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_prompt_eval_runs")),
    )
    op.create_index(
        op.f("ix_prompt_eval_runs_workspace_id"),
        "prompt_eval_runs",
        ["workspace_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_prompt_eval_runs_prompt_release_id"),
        "prompt_eval_runs",
        ["prompt_release_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_prompt_eval_runs_suite_id"),
        "prompt_eval_runs",
        ["suite_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_prompt_eval_runs_status"),
        "prompt_eval_runs",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_prompt_eval_runs_created_by_user_id"),
        "prompt_eval_runs",
        ["created_by_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_prompt_eval_runs_workspace_created",
        "prompt_eval_runs",
        ["workspace_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_prompt_eval_runs_release_suite",
        "prompt_eval_runs",
        ["prompt_release_id", "suite_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_prompt_eval_runs_release_suite",
        table_name="prompt_eval_runs",
    )
    op.drop_index(
        "ix_prompt_eval_runs_workspace_created",
        table_name="prompt_eval_runs",
    )
    op.drop_index(
        op.f("ix_prompt_eval_runs_created_by_user_id"),
        table_name="prompt_eval_runs",
    )
    op.drop_index(
        op.f("ix_prompt_eval_runs_status"),
        table_name="prompt_eval_runs",
    )
    op.drop_index(
        op.f("ix_prompt_eval_runs_suite_id"),
        table_name="prompt_eval_runs",
    )
    op.drop_index(
        op.f("ix_prompt_eval_runs_prompt_release_id"),
        table_name="prompt_eval_runs",
    )
    op.drop_index(
        op.f("ix_prompt_eval_runs_workspace_id"),
        table_name="prompt_eval_runs",
    )
    op.drop_table("prompt_eval_runs")

    op.drop_index(
        "uq_prompt_eval_suite_workspace_active",
        table_name="prompt_eval_suites",
    )
    op.drop_index(
        "ix_prompt_eval_suites_workspace_status",
        table_name="prompt_eval_suites",
    )
    op.drop_index(
        op.f("ix_prompt_eval_suites_activated_by_user_id"),
        table_name="prompt_eval_suites",
    )
    op.drop_index(
        op.f("ix_prompt_eval_suites_created_by_user_id"),
        table_name="prompt_eval_suites",
    )
    op.drop_index(
        op.f("ix_prompt_eval_suites_suite_hash"),
        table_name="prompt_eval_suites",
    )
    op.drop_index(
        op.f("ix_prompt_eval_suites_status"),
        table_name="prompt_eval_suites",
    )
    op.drop_index(
        op.f("ix_prompt_eval_suites_workspace_id"),
        table_name="prompt_eval_suites",
    )
    op.drop_table("prompt_eval_suites")
