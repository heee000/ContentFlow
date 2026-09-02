"""add indexed asset content versions

Revision ID: 9a7b2c3d4e5f
Revises: 8f6a1b2c3d4e
Create Date: 2026-09-03 21:00:00
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9a7b2c3d4e5f"
down_revision: Union[str, None] = "8f6a1b2c3d4e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "assets",
        sa.Column(
            "content_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        json_value = "metadata_json ->> 'content_version'"
        connection.execute(
            sa.text(
                "UPDATE assets "
                f"SET content_version = CAST({json_value} AS INTEGER) "
                f"WHERE {json_value} ~ '^[1-9][0-9]*$' "
                f"AND (char_length({json_value}) < 10 "
                f"OR (char_length({json_value}) = 10 "
                f"AND {json_value} <= '2147483647'))"
            )
        )
    elif connection.dialect.name == "sqlite":
        json_value = (
            "CAST(json_extract("
            "CASE WHEN json_valid(metadata_json) THEN metadata_json ELSE '{}' END, "
            "'$.content_version') AS TEXT)"
        )
        connection.execute(
            sa.text(
                "UPDATE assets "
                f"SET content_version = CAST({json_value} AS INTEGER) "
                f"WHERE {json_value} GLOB '[1-9]*' "
                f"AND {json_value} NOT GLOB '*[^0-9]*' "
                f"AND (length({json_value}) < 10 "
                f"OR (length({json_value}) = 10 "
                f"AND {json_value} <= '2147483647'))"
            )
        )
    else:
        raise RuntimeError(
            "asset content-version backfill supports only PostgreSQL and SQLite"
        )
    op.create_index(
        "ix_assets_workspace_item_version_status",
        "assets",
        ["workspace_id", "content_item_id", "content_version", "status", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_assets_workspace_item_version_status",
        table_name="assets",
    )
    op.drop_column("assets", "content_version")
