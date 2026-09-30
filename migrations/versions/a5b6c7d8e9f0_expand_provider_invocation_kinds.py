"""expand provider invocation kinds

Revision ID: a5b6c7d8e9f0
Revises: f4a5b6c7d8e9
Create Date: 2026-09-03 11:20:00
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "a5b6c7d8e9f0"
down_revision: Union[str, None] = "f4a5b6c7d8e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _replace_provider_kind_constraint(expression: str) -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(
            "provider_invocations",
            recreate="always",
        ) as batch_op:
            batch_op.drop_constraint(
                op.f("ck_provider_invocations_provider_kind"),
                type_="check",
            )
            batch_op.create_check_constraint("provider_kind", expression)
        return
    op.drop_constraint(
        op.f("ck_provider_invocations_provider_kind"),
        "provider_invocations",
        type_="check",
    )
    op.create_check_constraint(
        "provider_kind",
        "provider_invocations",
        expression,
    )


def upgrade() -> None:
    _replace_provider_kind_constraint(
        "provider_kind IN ('text', 'embedding', 'media', 'search')"
    )


def downgrade() -> None:
    _replace_provider_kind_constraint(
        "provider_kind IN ('text', 'embedding')"
    )
