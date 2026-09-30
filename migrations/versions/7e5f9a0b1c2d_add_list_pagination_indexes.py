"""add list pagination indexes

Revision ID: 7e5f9a0b1c2d
Revises: 6d4e8f9a0b1c
Create Date: 2026-09-03 09:00:00
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "7e5f9a0b1c2d"
down_revision: Union[str, None] = "6d4e8f9a0b1c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PAGINATION_INDEXES = (
    ("campaigns", "ix_campaigns_workspace_updated_page"),
    ("workflow_runs", "ix_workflow_runs_workspace_updated_page"),
    ("content_items", "ix_content_items_workspace_updated_page"),
    ("assets", "ix_assets_workspace_updated_page"),
    ("publish_jobs", "ix_publish_jobs_workspace_updated_page"),
    ("knowledge_documents", "ix_knowledge_documents_workspace_updated_page"),
    ("jobs", "ix_jobs_workspace_updated_page"),
)


def upgrade() -> None:
    for table_name, index_name in PAGINATION_INDEXES:
        op.create_index(
            index_name,
            table_name,
            ["workspace_id", "updated_at", "id"],
            unique=False,
        )


def downgrade() -> None:
    for table_name, index_name in reversed(PAGINATION_INDEXES):
        op.drop_index(index_name, table_name=table_name)
