"""add control-plane pagination indexes

Revision ID: 8f6a1b2c3d4e
Revises: 7e5f9a0b1c2d
Create Date: 2026-09-03 18:00:00
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "8f6a1b2c3d4e"
down_revision: Union[str, None] = "7e5f9a0b1c2d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PAGINATION_INDEXES = (
    (
        "memberships",
        "ix_memberships_workspace_created_page",
        ["workspace_id", "created_at", "id"],
    ),
    (
        "memberships",
        "ix_memberships_user_created_page",
        ["user_id", "created_at", "id"],
    ),
    (
        "style_skills",
        "ix_style_skills_workspace_created_page",
        ["workspace_id", "created_at", "id"],
    ),
    (
        "channel_connections",
        "ix_channel_connections_workspace_created_page",
        ["workspace_id", "created_at", "id"],
    ),
    (
        "content_revisions",
        "ix_content_revisions_item_version_page",
        ["workspace_id", "content_item_id", "version", "id"],
    ),
    (
        "publish_evidence_items",
        "ix_publish_evidence_attempt_created_page",
        ["publish_job_id", "script_attempt_id", "created_at", "id"],
    ),
    (
        "publish_confirmations",
        "ix_publish_confirmation_attempt_created_page",
        ["publish_job_id", "script_attempt_id", "created_at", "id"],
    ),
    (
        "prompt_releases",
        "ix_prompt_releases_workspace_number_page",
        ["workspace_id", "release_number", "id"],
    ),
    (
        "prompt_eval_suites",
        "ix_prompt_eval_suites_workspace_version_page",
        ["workspace_id", "version_number", "id"],
    ),
    (
        "prompt_eval_runs",
        "ix_prompt_eval_runs_workspace_created_page",
        ["workspace_id", "created_at", "id"],
    ),
    (
        "audit_logs",
        "ix_audit_logs_workspace_sequence_page",
        ["workspace_id", "chain_sequence", "id"],
    ),
)


def upgrade() -> None:
    for table_name, index_name, columns in PAGINATION_INDEXES:
        op.create_index(index_name, table_name, columns, unique=False)


def downgrade() -> None:
    for table_name, index_name, _ in reversed(PAGINATION_INDEXES):
        op.drop_index(index_name, table_name=table_name)
