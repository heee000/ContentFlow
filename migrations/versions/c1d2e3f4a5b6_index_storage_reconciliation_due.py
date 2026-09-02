"""track deletion queue age and index storage reconciliation due time

Revision ID: c1d2e3f4a5b6
Revises: b0c1d2e3f4a5
Create Date: 2026-09-03 23:45:00
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, None] = "b0c1d2e3f4a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "storage_object_allocations",
        sa.Column("delete_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.get_bind().execute(
        sa.text(
            "UPDATE storage_object_allocations "
            "SET delete_requested_at = updated_at "
            "WHERE status = 'delete_pending'"
        )
    )
    op.create_index(
        "ix_workspace_storage_usage_reconciliation_due",
        "workspace_storage_usage",
        ["last_reconciled_at", "workspace_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workspace_storage_usage_reconciliation_due",
        table_name="workspace_storage_usage",
    )
    op.drop_column("storage_object_allocations", "delete_requested_at")
