from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(80))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Workspace(TimestampMixin, Base):
    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    created_by: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )


class Membership(TimestampMixin, Base):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "user_id", name="uq_membership_workspace_user"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(24), default="editor")


class AuthSession(TimestampMixin, Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        Index(
            "ix_auth_sessions_user_active",
            "user_id",
            "revoked_at",
            "expires_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    refresh_token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    last_used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    revoke_reason: Mapped[str | None] = mapped_column(String(120))
    user_agent_hash: Mapped[str | None] = mapped_column(String(64))
    client_ip_hash: Mapped[str | None] = mapped_column(String(64))


class AuthRefreshTokenHistory(Base):
    __tablename__ = "auth_refresh_token_history"
    __table_args__ = (
        Index(
            "ix_auth_refresh_token_history_session",
            "auth_session_id",
            "rotated_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    auth_session_id: Mapped[str] = mapped_column(
        ForeignKey("auth_sessions.id", ondelete="CASCADE")
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    rotated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )


class AuthRateLimit(TimestampMixin, Base):
    __tablename__ = "auth_rate_limits"
    __table_args__ = (
        Index(
            "ix_auth_rate_limits_scope_expires",
            "scope",
            "expires_at",
        ),
    )

    key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    scope: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    window_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    blocked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )


class PromptRelease(TimestampMixin, Base):
    __tablename__ = "prompt_releases"
    __table_args__ = (
        CheckConstraint(
            "release_number > 0",
            name="release_number_positive",
        ),
        CheckConstraint(
            "status IN ('draft', 'approved', 'active', 'retired', 'rejected')",
            name="status",
        ),
        UniqueConstraint(
            "workspace_id",
            "release_number",
            name="uq_prompt_release_workspace_number",
        ),
        Index(
            "uq_prompt_release_workspace_active",
            "workspace_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
        Index(
            "ix_prompt_releases_workspace_status",
            "workspace_id",
            "status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    release_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), default="draft", nullable=False, index=True
    )
    prompts_json: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False)
    prompt_hashes_json: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False)
    change_summary: Mapped[str] = mapped_column(String(500), nullable=False)
    review_note: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    reviewed_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    activated_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PromptEvalSuite(TimestampMixin, Base):
    __tablename__ = "prompt_eval_suites"
    __table_args__ = (
        CheckConstraint("version_number > 0", name="version_number_positive"),
        CheckConstraint(
            "status IN ('draft', 'active', 'retired')",
            name="status",
        ),
        UniqueConstraint(
            "workspace_id",
            "version_number",
            name="uq_prompt_eval_suite_workspace_version",
        ),
        Index(
            "uq_prompt_eval_suite_workspace_active",
            "workspace_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
        Index(
            "ix_prompt_eval_suites_workspace_status",
            "workspace_id",
            "status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), default="draft", nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    cases_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    suite_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    activated_by_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PromptEvalRun(TimestampMixin, Base):
    __tablename__ = "prompt_eval_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'passed', 'failed', 'error')",
            name="status",
        ),
        Index(
            "ix_prompt_eval_runs_workspace_created",
            "workspace_id",
            "created_at",
        ),
        Index(
            "ix_prompt_eval_runs_release_suite",
            "prompt_release_id",
            "suite_id",
            "status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    prompt_release_id: Mapped[str] = mapped_column(
        ForeignKey("prompt_releases.id", ondelete="RESTRICT"), index=True
    )
    suite_id: Mapped[str] = mapped_column(
        ForeignKey("prompt_eval_suites.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[str] = mapped_column(
        String(24), default="queued", nullable=False, index=True
    )
    requested_provider: Mapped[str] = mapped_column(String(80), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(80))
    model: Mapped[str | None] = mapped_column(String(160))
    prompt_hashes_json: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False)
    suite_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Campaign(TimestampMixin, Base):
    __tablename__ = "campaigns"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(String(160))
    product_name: Mapped[str] = mapped_column(String(160))
    objective: Mapped[str] = mapped_column(Text)
    audience: Mapped[str] = mapped_column(Text)
    platforms: Mapped[list[str]] = mapped_column(JSON, default=list)
    tone: Mapped[str] = mapped_column(String(200), default="清楚、可信")
    status: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    brief: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class KnowledgeDocument(TimestampMixin, Base):
    __tablename__ = "knowledge_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    source_type: Mapped[str] = mapped_column(String(32), default="upload")
    storage_uri: Mapped[str | None] = mapped_column(Text)
    checksum: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class KnowledgeChunk(TimestampMixin, Base):
    __tablename__ = "knowledge_chunks_v2"
    __table_args__ = (
        UniqueConstraint(
            "document_id", "chunk_index", name="uq_knowledge_chunk_position"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"), index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(JSON, default=list)
    embedding_model: Mapped[str] = mapped_column(String(120), default="hash-256")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class WorkflowRun(TimestampMixin, Base):
    __tablename__ = "workflow_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    current_stage: Mapped[str] = mapped_column(String(48), default="queued")
    provider: Mapped[str] = mapped_column(String(80), default="mock")
    trace_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    request_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ContentItem(TimestampMixin, Base):
    __tablename__ = "content_items"
    __table_args__ = (
        Index("ix_content_items_workspace_status", "workspace_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    campaign_id: Mapped[str] = mapped_column(
        ForeignKey("campaigns.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), index=True
    )
    platform: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text)
    hashtags: Mapped[list[str]] = mapped_column(JSON, default=list)
    call_to_action: Mapped[str] = mapped_column(Text, default="")
    layout_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        server_default=text("'{}'"),
    )
    status: Mapped[str] = mapped_column(String(32), default="needs_review", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    source_chunk_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    review_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    approved_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ContentRevision(Base):
    __tablename__ = "content_revisions"
    __table_args__ = (
        UniqueConstraint(
            "content_item_id",
            "version",
            name="uq_content_revision_version",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    content_item_id: Mapped[str] = mapped_column(
        ForeignKey("content_items.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text)
    hashtags: Mapped[list[str]] = mapped_column(JSON, default=list)
    call_to_action: Mapped[str] = mapped_column(Text, default="")
    layout_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        server_default=text("'{}'"),
    )
    changed_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    change_reason: Mapped[str] = mapped_column(String(120), default="generated")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class Asset(TimestampMixin, Base):
    __tablename__ = "assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    content_item_id: Mapped[str | None] = mapped_column(
        ForeignKey("content_items.id", ondelete="SET NULL"), index=True
    )
    kind: Mapped[str] = mapped_column(String(32), index=True)
    provider: Mapped[str] = mapped_column(String(80), default="upload")
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    prompt: Mapped[str | None] = mapped_column(Text)
    storage_uri: Mapped[str | None] = mapped_column(Text)
    mime_type: Mapped[str | None] = mapped_column(String(120))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    external_task_id: Mapped[str | None] = mapped_column(String(255), index=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)


class ChannelConnection(TimestampMixin, Base):
    __tablename__ = "channel_connections"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "platform", "display_name", name="uq_channel_display_name"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    platform: Mapped[str] = mapped_column(String(32), index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(24), default="disconnected")
    credential_ciphertext: Mapped[str | None] = mapped_column(Text)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PublishJob(TimestampMixin, Base):
    __tablename__ = "publish_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    content_item_id: Mapped[str] = mapped_column(
        ForeignKey("content_items.id", ondelete="CASCADE"), index=True
    )
    channel_id: Mapped[str] = mapped_column(
        ForeignKey("channel_connections.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="scheduled", index=True)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str] = mapped_column(String(120), unique=True)
    external_id: Mapped[str | None] = mapped_column(String(255), index=True)
    external_url: Mapped[str | None] = mapped_column(Text)
    request_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    response_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def delivery_mode(self) -> str:
        mode = (self.request_json or {}).get("delivery_mode")

        if mode in {"connector", "script", "manual_export"}:
            return str(mode)
        return "connector"

    @property
    def script_confirmation_required(self) -> int:
        value = (self.response_json or {}).get("script_confirmation_required")
        return 2 if value == 2 else 1

    @property
    def script_confirmation_count(self) -> int:
        value = (self.response_json or {}).get("script_confirmation_count")
        return max(0, int(value)) if isinstance(value, int) else 0

    @property
    def script_confirmation_decision(self) -> str | None:
        value = (self.response_json or {}).get("script_confirmation_decision")
        return value if isinstance(value, str) else None

    @property
    def script_evidence_count(self) -> int:
        value = (self.response_json or {}).get("script_evidence_count")
        return max(0, int(value)) if isinstance(value, int) else 0

    @property
    def script_package_available(self) -> bool:
        response_json = self.response_json or {}
        package_uri = response_json.get("package_uri")
        package_sha256 = response_json.get("package_sha256")
        return (
            isinstance(package_uri, str)
            and isinstance(package_sha256, str)
            and len(package_sha256) == 64
            and all(character in "0123456789abcdef" for character in package_sha256)
        )


class PublishEvidence(Base):
    __tablename__ = "publish_evidence_items"
    __table_args__ = (
        UniqueConstraint(
            "publish_job_id",
            "script_attempt_id",
            "object_sha256",
            name="uq_publish_evidence_attempt_object",
        ),
        CheckConstraint(
            "kind IN ('screenshot', 'platform_export')",
            name="kind",
        ),
        CheckConstraint("size_bytes > 0", name="size_bytes_positive"),
        CheckConstraint(
            "length(package_sha256) = 64 AND length(source_sha256) = 64 "
            "AND length(object_sha256) = 64",
            name="sha256_lengths",
        ),
        Index(
            "ix_publish_evidence_attempt_created",
            "publish_job_id",
            "script_attempt_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    publish_job_id: Mapped[str] = mapped_column(
        ForeignKey("publish_jobs.id", ondelete="CASCADE"), index=True
    )
    script_attempt_id: Mapped[str] = mapped_column(String(36), index=True)
    package_sha256: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(24), index=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    storage_uri: Mapped[str] = mapped_column(Text)
    source_sha256: Mapped[str] = mapped_column(String(64))
    object_sha256: Mapped[str] = mapped_column(String(64), index=True)
    mime_type: Mapped[str] = mapped_column(String(80))
    size_bytes: Mapped[int] = mapped_column(Integer)
    uploaded_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )


class PublishConfirmation(Base):
    __tablename__ = "publish_confirmations"
    __table_args__ = (
        UniqueConstraint(
            "publish_job_id",
            "script_attempt_id",
            "confirmed_by_user_id",
            name="uq_publish_confirmation_attempt_user",
        ),
        CheckConstraint(
            "decision IN ('confirmed_published', 'confirmed_not_published')",
            name="decision",
        ),
        CheckConstraint(
            "length(package_sha256) = 64 AND length(evidence_manifest_sha256) = 64",
            name="sha256_lengths",
        ),
        Index(
            "ix_publish_confirmation_attempt_created",
            "publish_job_id",
            "script_attempt_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    publish_job_id: Mapped[str] = mapped_column(
        ForeignKey("publish_jobs.id", ondelete="CASCADE"), index=True
    )
    script_attempt_id: Mapped[str] = mapped_column(String(36), index=True)
    package_sha256: Mapped[str] = mapped_column(String(64), index=True)
    decision: Mapped[str] = mapped_column(String(32), index=True)
    reason: Mapped[str] = mapped_column(Text)
    external_id: Mapped[str | None] = mapped_column(String(255))
    external_url: Mapped[str | None] = mapped_column(Text)
    evidence_manifest_sha256: Mapped[str] = mapped_column(String(64), index=True)
    confirmed_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )


class MetricSnapshot(Base):
    __tablename__ = "metric_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "publish_job_id", "captured_at", name="uq_metric_snapshot_capture"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    publish_job_id: Mapped[str] = mapped_column(
        ForeignKey("publish_jobs.id", ondelete="CASCADE"), index=True
    )
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    impressions: Mapped[float] = mapped_column(Float, default=0)
    clicks: Mapped[float] = mapped_column(Float, default=0)
    likes: Mapped[float] = mapped_column(Float, default=0)
    comments: Mapped[float] = mapped_column(Float, default=0)
    shares: Mapped[float] = mapped_column(Float, default=0)
    raw_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    actor_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    action: Mapped[str] = mapped_column(String(120), index=True)
    entity_type: Mapped[str] = mapped_column(String(80), index=True)
    entity_id: Mapped[str | None] = mapped_column(String(80), index=True)
    request_id: Mapped[str | None] = mapped_column(String(64), index=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class Job(TimestampMixin, Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_claim", "status", "run_at", "locked_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True
    )
    job_type: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=4)
    run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    locked_by: Mapped[str | None] = mapped_column(String(120), index=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True)


class WorkerNode(Base):
    __tablename__ = "worker_nodes"
    __table_args__ = (
        Index(
            "ix_worker_nodes_status_heartbeat",
            "status",
            "heartbeat_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False)
    process_id: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), default="online", nullable=False, index=True
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
