"""add version-bound content review evidence

Revision ID: b6c7d8e9f0a1
Revises: a5b6c7d8e9f0
"""

from alembic import op
import sqlalchemy as sa

revision = "b6c7d8e9f0a1"
down_revision = "a5b6c7d8e9f0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "content_review_evidence",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "workspace_id",
            sa.String(36),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "content_item_id",
            sa.String(36),
            sa.ForeignKey("content_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("content_version", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("event", sa.String(32), nullable=False),
        sa.Column("model_binding", sa.String(32), nullable=False),
        sa.Column("snapshot_json", sa.JSON(), nullable=False),
        sa.Column("snapshot_sha256", sa.String(64), nullable=False),
        sa.Column(
            "actor_user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_content_review_evidence_page",
        "content_review_evidence",
        ["workspace_id", "content_item_id", "created_at", "id"],
    )


def downgrade():
    op.drop_index(
        "ix_content_review_evidence_page", table_name="content_review_evidence"
    )
    op.drop_table("content_review_evidence")
