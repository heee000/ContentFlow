"""add governed prompt releases

Revision ID: b84e0d3f7c92
Revises: a73f9c2e4b61
Create Date: 2026-08-10 15:30:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b84e0d3f7c92"
down_revision: Union[str, None] = "a73f9c2e4b61"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "prompt_releases",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("release_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("prompts_json", sa.JSON(), nullable=False),
        sa.Column("prompt_hashes_json", sa.JSON(), nullable=False),
        sa.Column("change_summary", sa.String(length=500), nullable=False),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("reviewed_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("activated_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "release_number > 0",
            name="release_number_positive",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'approved', 'active', 'retired', 'rejected')",
            name="status",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_prompt_releases_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_prompt_releases_created_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by_user_id"],
            ["users.id"],
            name=op.f("fk_prompt_releases_reviewed_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["activated_by_user_id"],
            ["users.id"],
            name=op.f("fk_prompt_releases_activated_by_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_prompt_releases")),
        sa.UniqueConstraint(
            "workspace_id",
            "release_number",
            name="uq_prompt_release_workspace_number",
        ),
    )
    op.create_index(
        op.f("ix_prompt_releases_workspace_id"),
        "prompt_releases",
        ["workspace_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_prompt_releases_status"),
        "prompt_releases",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_prompt_releases_created_by_user_id"),
        "prompt_releases",
        ["created_by_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_prompt_releases_reviewed_by_user_id"),
        "prompt_releases",
        ["reviewed_by_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_prompt_releases_activated_by_user_id"),
        "prompt_releases",
        ["activated_by_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_prompt_releases_workspace_status",
        "prompt_releases",
        ["workspace_id", "status"],
        unique=False,
    )
    op.create_index(
        "uq_prompt_release_workspace_active",
        "prompt_releases",
        ["workspace_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
        sqlite_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_prompt_release_workspace_active",
        table_name="prompt_releases",
    )
    op.drop_index(
        "ix_prompt_releases_workspace_status",
        table_name="prompt_releases",
    )
    op.drop_index(
        op.f("ix_prompt_releases_activated_by_user_id"),
        table_name="prompt_releases",
    )
    op.drop_index(
        op.f("ix_prompt_releases_reviewed_by_user_id"),
        table_name="prompt_releases",
    )
    op.drop_index(
        op.f("ix_prompt_releases_created_by_user_id"),
        table_name="prompt_releases",
    )
    op.drop_index(
        op.f("ix_prompt_releases_status"),
        table_name="prompt_releases",
    )
    op.drop_index(
        op.f("ix_prompt_releases_workspace_id"),
        table_name="prompt_releases",
    )
    op.drop_table("prompt_releases")
