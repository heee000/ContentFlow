"""add provider invocation ledger

Revision ID: f4a5b6c7d8e9
Revises: e3f4a5b6c7d8
Create Date: 2026-09-03 10:15:00
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f4a5b6c7d8e9"
down_revision: Union[str, None] = "e3f4a5b6c7d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "provider_invocations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=True),
        sa.Column("entity_type", sa.String(length=80), nullable=False),
        sa.Column("entity_id", sa.String(length=80), nullable=False),
        sa.Column("provider_kind", sa.String(length=24), nullable=False),
        sa.Column("provider_name", sa.String(length=80), nullable=False),
        sa.Column("model_name", sa.String(length=160), nullable=False),
        sa.Column("operation", sa.String(length=80), nullable=False),
        sa.Column("request_key", sa.String(length=64), nullable=False),
        sa.Column("request_sha256", sa.String(length=64), nullable=False),
        sa.Column("request_bytes", sa.Integer(), nullable=False),
        sa.Column("last_status", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "provider_kind IN ('text', 'embedding')",
            name="provider_kind",
        ),
        sa.CheckConstraint(
            "last_status IN ('started', 'succeeded', 'outcome_unknown', "
            "'late_succeeded', 'late_failed')",
            name="last_status",
        ),
        sa.CheckConstraint(
            "length(request_key) = 64",
            name="request_key_length",
        ),
        sa.CheckConstraint(
            "length(request_sha256) = 64",
            name="request_sha256_length",
        ),
        sa.CheckConstraint(
            "request_bytes >= 0",
            name="request_bytes_non_negative",
        ),
        sa.CheckConstraint(
            "length(entity_type) > 0",
            name="entity_type_non_empty",
        ),
        sa.CheckConstraint(
            "length(entity_id) > 0",
            name="entity_id_non_empty",
        ),
        sa.CheckConstraint(
            "length(operation) > 0",
            name="operation_non_empty",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "request_key",
            name="uq_provider_invocations_request_key",
        ),
    )
    for column in (
        "entity_id",
        "entity_type",
        "job_id",
        "last_status",
        "operation",
        "provider_kind",
        "provider_name",
        "workspace_id",
    ):
        op.create_index(
            f"ix_provider_invocations_{column}",
            "provider_invocations",
            [column],
            unique=False,
        )
    op.create_index(
        "ix_provider_invocations_workspace_created_page",
        "provider_invocations",
        ["workspace_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_provider_invocations_job_created_page",
        "provider_invocations",
        ["job_id", "created_at", "id"],
        unique=False,
    )

    op.create_table(
        "provider_invocation_attempts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("invocation_id", sa.String(length=36), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("idempotency_key_sent", sa.Boolean(), nullable=False),
        sa.Column("provider_request_id", sa.String(length=255), nullable=True),
        sa.Column(
            "provider_request_id_source",
            sa.String(length=40),
            nullable=True,
        ),
        sa.Column("response_sha256", sa.String(length=64), nullable=True),
        sa.Column("response_bytes", sa.Integer(), nullable=True),
        sa.Column("response_model", sa.String(length=160), nullable=True),
        sa.Column("usage_source", sa.String(length=24), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("error_type", sa.String(length=160), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "attempt_number > 0",
            name="attempt_number_positive",
        ),
        sa.CheckConstraint(
            "status IN ('started', 'succeeded', 'outcome_unknown', "
            "'late_succeeded', 'late_failed')",
            name="status",
        ),
        sa.CheckConstraint(
            "usage_source IN ('not_reported', 'provider_reported')",
            name="usage_source",
        ),
        sa.CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name="input_tokens_non_negative",
        ),
        sa.CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name="output_tokens_non_negative",
        ),
        sa.CheckConstraint(
            "total_tokens IS NULL OR total_tokens >= 0",
            name="total_tokens_non_negative",
        ),
        sa.CheckConstraint(
            "response_bytes IS NULL OR response_bytes >= 0",
            name="response_bytes_non_negative",
        ),
        sa.CheckConstraint(
            "response_sha256 IS NULL OR length(response_sha256) = 64",
            name="response_sha256_length",
        ),
        sa.CheckConstraint(
            "provider_request_id IS NULL OR length(provider_request_id) > 0",
            name="provider_request_id_non_empty",
        ),
        sa.CheckConstraint(
            "((status = 'started' AND completed_at IS NULL) OR "
            "(status <> 'started' AND completed_at IS NOT NULL))",
            name="completion_consistent",
        ),
        sa.ForeignKeyConstraint(
            ["invocation_id"],
            ["provider_invocations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "invocation_id",
            "attempt_number",
            name="uq_provider_invocation_attempt_number",
        ),
    )
    for column in (
        "invocation_id",
        "provider_request_id",
        "started_at",
        "status",
    ):
        op.create_index(
            f"ix_provider_invocation_attempts_{column}",
            "provider_invocation_attempts",
            [column],
            unique=False,
        )
    op.create_index(
        "ix_provider_invocation_attempts_invocation_started_page",
        "provider_invocation_attempts",
        ["invocation_id", "started_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_provider_invocation_attempts_status_started",
        "provider_invocation_attempts",
        ["status", "started_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_provider_invocation_attempts_status_started",
        table_name="provider_invocation_attempts",
    )
    op.drop_index(
        "ix_provider_invocation_attempts_invocation_started_page",
        table_name="provider_invocation_attempts",
    )
    for column in (
        "status",
        "started_at",
        "provider_request_id",
        "invocation_id",
    ):
        op.drop_index(
            f"ix_provider_invocation_attempts_{column}",
            table_name="provider_invocation_attempts",
        )
    op.drop_table("provider_invocation_attempts")

    op.drop_index(
        "ix_provider_invocations_job_created_page",
        table_name="provider_invocations",
    )
    op.drop_index(
        "ix_provider_invocations_workspace_created_page",
        table_name="provider_invocations",
    )
    for column in (
        "workspace_id",
        "provider_name",
        "provider_kind",
        "operation",
        "last_status",
        "job_id",
        "entity_type",
        "entity_id",
    ):
        op.drop_index(
            f"ix_provider_invocations_{column}",
            table_name="provider_invocations",
        )
    op.drop_table("provider_invocations")
