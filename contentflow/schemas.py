from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


Platform = Literal["xiaohongshu", "douyin", "wechat"]
WorkspaceRole = Literal["viewer", "editor", "reviewer", "admin"]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=1, max_length=80)
    workspace_name: str = Field(min_length=1, max_length=120)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    workspace_id: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    workspace_id: str
    role: str


class UserResponse(ORMModel):
    id: str
    email: EmailStr
    display_name: str
    is_active: bool


class WorkspaceResponse(ORMModel):
    id: str
    name: str
    slug: str


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class WorkspaceAccessResponse(BaseModel):
    id: str
    name: str
    slug: str
    role: WorkspaceRole


class MemberCreate(BaseModel):
    email: EmailStr
    role: WorkspaceRole = "editor"


class MemberUpdate(BaseModel):
    role: WorkspaceRole


class MemberResponse(BaseModel):
    id: str
    user_id: str
    email: EmailStr
    display_name: str
    role: WorkspaceRole
    created_at: datetime


class AuditLogResponse(BaseModel):
    id: str
    action: str
    entity_type: str
    entity_id: str | None
    actor_user_id: str | None
    actor_display_name: str | None
    request_id: str | None
    metadata_json: dict[str, Any]
    created_at: datetime


class SessionResponse(BaseModel):
    user: UserResponse
    workspace: WorkspaceResponse
    role: str


class CampaignCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    product_name: str = Field(min_length=1, max_length=160)
    objective: str = Field(min_length=5, max_length=4000)
    audience: str = Field(min_length=3, max_length=4000)
    platforms: list[Platform] = Field(min_length=1)
    tone: str = Field(default="清楚、可信、不过度承诺", max_length=200)
    city: str = Field(default="北京", max_length=80)
    must_include: list[str] = Field(default_factory=list)
    forbidden_phrases: list[str] = Field(default_factory=list)
    call_to_action: str = Field(default="", max_length=500)
    product_facts: list[str] = Field(default_factory=list)


class CampaignUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    product_name: str | None = Field(default=None, min_length=1, max_length=160)
    objective: str | None = Field(default=None, min_length=5, max_length=4000)
    audience: str | None = Field(default=None, min_length=3, max_length=4000)
    platforms: list[Platform] | None = None
    tone: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=80)
    must_include: list[str] | None = None
    forbidden_phrases: list[str] | None = None
    call_to_action: str | None = Field(default=None, max_length=500)
    product_facts: list[str] | None = None
    status: Literal["draft", "active", "archived"] | None = None


class CampaignResponse(ORMModel):
    id: str
    workspace_id: str
    name: str
    product_name: str
    objective: str
    audience: str
    platforms: list[str]
    tone: str
    status: str
    brief: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class WorkflowRunRequest(BaseModel):
    provider: str | None = None
    regenerate_platforms: list[Platform] = Field(default_factory=list)


class WorkflowRunResponse(ORMModel):
    id: str
    campaign_id: str
    workspace_id: str
    status: str
    current_stage: str
    provider: str
    trace_id: str
    request_json: dict[str, Any]
    result_json: dict[str, Any]
    error: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ContentResponse(ORMModel):
    id: str
    campaign_id: str
    run_id: str
    platform: str
    title: str
    body: str
    hashtags: list[str]
    call_to_action: str
    layout_json: dict[str, Any]
    status: str
    version: int
    source_chunk_ids: list[str]
    review_json: dict[str, Any]
    approved_by: str | None
    approved_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ContentUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=500)
    body: str | None = Field(default=None, max_length=20_000)
    hashtags: list[str] | None = None
    call_to_action: str | None = Field(default=None, max_length=500)
    layout_json: dict[str, Any] | None = None


class ContentRevisionResponse(ORMModel):
    id: str
    content_item_id: str
    version: int
    title: str
    body: str
    hashtags: list[str]
    call_to_action: str
    layout_json: dict[str, Any]
    changed_by: str | None
    change_reason: str
    created_at: datetime


class ReviewDecision(BaseModel):
    decision: Literal["approve", "reject"]
    reason: str = Field(default="", max_length=2000)


class AssetResponse(ORMModel):
    id: str
    content_item_id: str | None
    kind: str
    provider: str
    status: str
    prompt: str | None
    storage_uri: str | None
    mime_type: str | None
    size_bytes: int | None
    external_task_id: str | None
    metadata_json: dict[str, Any]
    error: str | None
    created_at: datetime
    updated_at: datetime


class KnowledgeDocumentResponse(ORMModel):
    id: str
    name: str
    source_type: str
    storage_uri: str | None
    checksum: str
    status: str
    metadata_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class ChannelCreate(BaseModel):
    platform: Platform
    display_name: str = Field(min_length=1, max_length=120)
    credentials: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)


class ChannelResponse(ORMModel):
    id: str
    platform: str
    display_name: str
    status: str
    config_json: dict[str, Any]
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime


class PublishScheduleRequest(BaseModel):
    content_item_id: str
    channel_id: str
    scheduled_at: datetime


class PublishJobResponse(ORMModel):
    id: str
    content_item_id: str
    channel_id: str
    status: str
    scheduled_at: datetime
    external_id: str | None
    external_url: str | None
    attempts: int
    error: str | None
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime


class MetricInput(BaseModel):
    publish_job_id: str
    captured_at: datetime | None = None
    impressions: float = Field(default=0, ge=0)
    clicks: float = Field(default=0, ge=0)
    likes: float = Field(default=0, ge=0)
    comments: float = Field(default=0, ge=0)
    shares: float = Field(default=0, ge=0)
    raw: dict[str, Any] = Field(default_factory=dict)


class JobResponse(ORMModel):
    id: str
    job_type: str
    status: str
    attempts: int
    max_attempts: int
    run_at: datetime
    last_error: str | None
    result_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime
