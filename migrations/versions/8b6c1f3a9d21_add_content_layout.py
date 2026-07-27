"""add structured content layout

Revision ID: 8b6c1f3a9d21
Revises: dcf960d6d7a0
Create Date: 2026-07-27 22:05:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "8b6c1f3a9d21"
down_revision: Union[str, None] = "dcf960d6d7a0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("content_items") as batch_op:
        batch_op.add_column(
            sa.Column(
                "layout_json",
                sa.JSON(),
                server_default=sa.text("'{}'"),
                nullable=False,
            )
        )
    with op.batch_alter_table("content_revisions") as batch_op:
        batch_op.add_column(
            sa.Column(
                "layout_json",
                sa.JSON(),
                server_default=sa.text("'{}'"),
                nullable=False,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("content_revisions") as batch_op:
        batch_op.drop_column("layout_json")
    with op.batch_alter_table("content_items") as batch_op:
        batch_op.drop_column("layout_json")
