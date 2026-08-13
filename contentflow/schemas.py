from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints


Platform = Literal["xiaohongshu", "douyin", "wechat"]
WorkspaceRole = Literal["viewer", "editor", "reviewer", "admin"]
EvidenceReason = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    display_name: str = Field(min_length=1, max_length=80)
    workspace_name: str = Field(min_length=1, max_length=120)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    workspace_id: str | None = None


class TokenResponse(BaseModel):
    access_token: str | None = None
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


PromptReleaseStatus = Literal["draft", "approved", "active", "retired", "rejected"]


class PromptReleaseCreate(BaseModel):
    prompts: dict[str, str]
    change_summary: str = Field(min_length=3, max_length=500)


class PromptReviewRequest(BaseModel):
    note: str = Field(default="", max_length=1000)


class PromptReleaseResponse(ORMModel):
    id: str
    workspace_id: str
    release_number: int
    version: str
    status: PromptReleaseStatus
    prompts: dict[str, str]
    prompt_hashes: dict[str, str]
    change_summary: str
    review_note: str | None
    created_by_user_id: str
    reviewed_by_user_id: str | None
    activated_by_user_id: str | None
    reviewed_at: datetime | None
    activated_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ActivePromptSetResponse(BaseModel):
    source: Literal["builtin", "workspace_release"]
    version: str
    release_id: str | None
    prompts: dict[str, str]
    prompt_hashes: dict[str, str]


class PromptGovernanceResponse(BaseModel):
    active: ActivePromptSetResponse
    governance_required: bool
    ready_for_generation: bool
    generation_block_reason: str | None
    releases: list[PromptReleaseResponse]


class PromptEvalCaseInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    stage: Literal["plan", "generate", "review"]
    input_json: dict[str, Any]
    required_paths: list[str] = Field(default_factory=list, max_length=100)
    expected_values: dict[str, Any] = Field(default_factory=dict)
    required_substrings: list[str] = Field(default_factory=list, max_length=100)
    forbidden_substrings: list[str] = Field(default_factory=list, max_length=100)
    max_output_bytes: int = Field(default=100_000, ge=256, le=1_000_000)


class PromptEvalSuiteCreate(BaseModel):
    name: str = Field(min_length=3, max_length=160)
    description: str = Field(default="", max_length=2000)
    cases: list[PromptEvalCaseInput] = Field(min_length=3, max_length=60)


class PromptEvalRequest(BaseModel):
    provider: str | None = Field(default=None, max_length=80)


class PromptEvalSuiteResponse(ORMModel):
    id: str
    workspace_id: str
    version_number: int
    version: str
    status: Literal["draft", "active", "retired"]
    name: str
    description: str
    cases: list[dict[str, Any]]
    suite_hash: str
    created_by_user_id: str
    activated_by_user_id: str | None
    activated_at: datetime | None
    created_at: datetime
    updated_at: datetime


class PromptEvalRunResponse(ORMModel):
    id: str
    workspace_id: str
    prompt_release_id: str
    suite_id: str
    status: Literal["queued", "running", "passed", "failed", "error"]
    requested_provider: str
    provider: str | None
    model: str | None
    prompt_hashes: dict[str, str]
    suite_hash: str
    result_json: dict[str, Any]
    error: str | None
    created_by_user_id: str
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class PromptEvalGovernanceResponse(BaseModel):
    active_suite: PromptEvalSuiteResponse | None
    suites: list[PromptEvalSuiteResponse]
    runs: list[PromptEvalRunResponse]


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
    provider: str | None = Field(default=None, max_length=80)
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
    expected_version: int = Field(ge=1)
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
    expected_version: int = Field(ge=1)
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
    connection_mode: Literal["connector", "script", "manual_export"] = "connector"
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
    delivery_mode: Literal["connector", "script", "manual_export"] = "connector"


class PublishReconcileRequest(BaseModel):
    decision: Literal["confirmed_published", "confirmed_not_published"]
    reason: EvidenceReason
    external_id: str | None = Field(default=None, max_length=255)
    external_url: str | None = Field(default=None, max_length=2000)


class PublishScriptResultRequest(BaseModel):
    decision: Literal["confirmed_published", "confirmed_not_published"]
    reason: EvidenceReason
    external_id: str | None = Field(default=None, max_length=255)
    external_url: str | None = Field(default=None, max_length=2000)


class PublishJobResponse(ORMModel):
    id: str
    content_item_id: str
    channel_id: str
    status: str
    scheduled_at: datetime
    delivery_mode: str
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


class WorkerQueueHealthResponse(BaseModel):
    queued: int
    retry: int
    running: int
    failed: int
    ready: int
    oldest_ready_age_seconds: float | None


class WorkerHealthResponse(BaseModel):
    status: Literal["healthy", "degraded", "unavailable"]
    checked_at: datetime
    active_workers: int
    stale_workers: int
    stopped_workers: int
    issues: list[str]
    thresholds: dict[str, int]
    queue: WorkerQueueHealthResponse
