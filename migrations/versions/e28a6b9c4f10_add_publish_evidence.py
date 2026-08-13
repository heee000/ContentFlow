"""add governed script publish evidence

Revision ID: e28a6b9c4f10
Revises: c95f1e4a8d73
Create Date: 2026-08-13 21:15:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e28a6b9c4f10"
down_revision: Union[str, None] = "c95f1e4a8d73"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "publish_evidence_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("publish_job_id", sa.String(length=36), nullable=False),
        sa.Column("script_attempt_id", sa.String(length=36), nullable=False),
        sa.Column("package_sha256", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("storage_uri", sa.Text(), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("object_sha256", sa.String(length=64), nullable=False),
        sa.Column("mime_type", sa.String(length=80), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("uploaded_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('screenshot', 'platform_export')",
            name="kind",
        ),
        sa.CheckConstraint(
            "length(package_sha256) = 64 AND length(source_sha256) = 64 "
            "AND length(object_sha256) = 64",
            name="sha256_lengths",
        ),
        sa.CheckConstraint("size_bytes > 0", name="size_bytes_positive"),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_publish_evidence_items_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["publish_job_id"],
            ["publish_jobs.id"],
            name=op.f("fk_publish_evidence_items_publish_job_id_publish_jobs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by_user_id"],
            ["users.id"],
            name=op.f("fk_publish_evidence_items_uploaded_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_publish_evidence_items")),
        sa.UniqueConstraint(
            "publish_job_id",
            "script_attempt_id",
            "object_sha256",
            name="uq_publish_evidence_attempt_object",
        ),
    )
    for column in (
        "workspace_id",
        "publish_job_id",
        "script_attempt_id",
        "package_sha256",
        "kind",
        "object_sha256",
        "uploaded_by_user_id",
        "created_at",
    ):
        op.create_index(
            op.f(f"ix_publish_evidence_items_{column}"),
            "publish_evidence_items",
            [column],
            unique=False,
        )
    op.create_index(
        "ix_publish_evidence_attempt_created",
        "publish_evidence_items",
        ["publish_job_id", "script_attempt_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "publish_confirmations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("publish_job_id", sa.String(length=36), nullable=False),
        sa.Column("script_attempt_id", sa.String(length=36), nullable=False),
        sa.Column("package_sha256", sa.String(length=64), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("external_url", sa.Text(), nullable=True),
        sa.Column("evidence_manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("confirmed_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "decision IN ('confirmed_published', 'confirmed_not_published')",
            name="decision",
        ),
        sa.CheckConstraint(
            "length(package_sha256) = 64 AND length(evidence_manifest_sha256) = 64",
            name="sha256_lengths",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name=op.f("fk_publish_confirmations_workspace_id_workspaces"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["publish_job_id"],
            ["publish_jobs.id"],
            name=op.f("fk_publish_confirmations_publish_job_id_publish_jobs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_by_user_id"],
            ["users.id"],
            name=op.f("fk_publish_confirmations_confirmed_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_publish_confirmations")),
        sa.UniqueConstraint(
            "publish_job_id",
            "script_attempt_id",
            "confirmed_by_user_id",
            name="uq_publish_confirmation_attempt_user",
        ),
    )
    for column in (
        "workspace_id",
        "publish_job_id",
        "script_attempt_id",
        "package_sha256",
        "decision",
        "evidence_manifest_sha256",
        "confirmed_by_user_id",
        "created_at",
    ):
        op.create_index(
            op.f(f"ix_publish_confirmations_{column}"),
            "publish_confirmations",
            [column],
            unique=False,
        )
    op.create_index(
        "ix_publish_confirmation_attempt_created",
        "publish_confirmations",
        ["publish_job_id", "script_attempt_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_publish_confirmation_attempt_created",
        table_name="publish_confirmations",
    )
    for column in reversed(
        (
            "workspace_id",
            "publish_job_id",
            "script_attempt_id",
            "package_sha256",
            "decision",
            "evidence_manifest_sha256",
            "confirmed_by_user_id",
            "created_at",
        )
    ):
        op.drop_index(
            op.f(f"ix_publish_confirmations_{column}"),
            table_name="publish_confirmations",
        )
    op.drop_table("publish_confirmations")

    op.drop_index(
        "ix_publish_evidence_attempt_created",
        table_name="publish_evidence_items",
    )
    for column in reversed(
        (
            "workspace_id",
            "publish_job_id",
            "script_attempt_id",
            "package_sha256",
            "kind",
            "object_sha256",
            "uploaded_by_user_id",
            "created_at",
        )
    ):
        op.drop_index(
            op.f(f"ix_publish_evidence_items_{column}"),
            table_name="publish_evidence_items",
        )
    op.drop_table("publish_evidence_items")
