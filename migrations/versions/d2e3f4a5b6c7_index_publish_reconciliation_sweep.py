"""index publish reconciliation recovery sweep

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-09-03 23:58:00
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "d2e3f4a5b6c7"
down_revision: Union[str, None] = "c1d2e3f4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_publish_jobs_reconciliation_sweep",
        "publish_jobs",
        ["status", "updated_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_publish_jobs_reconciliation_sweep",
        table_name="publish_jobs",
    )
