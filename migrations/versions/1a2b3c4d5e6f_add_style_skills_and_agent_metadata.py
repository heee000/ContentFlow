"""add style skills and content agent metadata

Revision ID: 1a2b3c4d5e6f
Revises: e28a6b9c4f10
Create Date: 2026-08-25 00:30:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "1a2b3c4d5e6f"
down_revision: Union[str, None] = "e28a6b9c4f10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "style_skills",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("version", sa.String(length=40), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("manifest_json", sa.JSON(), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("installed_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('enabled', 'disabled')", name="status"),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_style_skills_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["installed_by_user_id"],
            ["users.id"],
            name=op.f("fk_style_skills_installed_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_style_skills")),
        sa.UniqueConstraint(
            "workspace_id",
            "slug",
            "version",
            name="uq_style_skill_workspace_slug_version",
        ),
    )
    for column in (
        "workspace_id",
        "status",
        "manifest_sha256",
        "installed_by_user_id",
    ):
        op.create_index(
            op.f(f"ix_style_skills_{column}"),
            "style_skills",
            [column],
            unique=False,
        )
    op.create_index(
        "ix_style_skills_workspace_status",
        "style_skills",
        ["workspace_id", "status"],
        unique=False,
    )

    op.add_column(
        "content_items",
        sa.Column(
            "generation_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.add_column(
        "content_revisions",
        sa.Column(
            "generation_json",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("content_revisions", "generation_json")
    op.drop_column("content_items", "generation_json")
    op.drop_index(
        "ix_style_skills_workspace_status",
        table_name="style_skills",
    )
    for column in reversed(
        ("workspace_id", "status", "manifest_sha256", "installed_by_user_id")
    ):
        op.drop_index(
            op.f(f"ix_style_skills_{column}"),
            table_name="style_skills",
        )
    op.drop_table("style_skills")
