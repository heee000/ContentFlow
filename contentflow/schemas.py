from __future__ import annotations

from datetime import datetime
import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, StrictBool, model_validator, field_serializer, field_validator

from .channel_config import validate_channel_config
from .connector_errors import CONNECTOR_JOB_TYPES, STAGES, public_connector_error
from .metric_values import MetricValues
from .generation_intents import utc_timestamp


Platform = Literal["xiaohongshu", "douyin", "wechat"]
WorkspaceRole = Literal["viewer", "editor", "reviewer", "admin"]
EvidenceReason = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
]
ManualReviewNote = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=8, max_length=2000)
]


class UTCResponseModel(BaseModel):
    @field_validator("*", mode="after", check_fields=False)
    @classmethod
    def normalize_response_timestamp(cls, value):
        # Persisted application timestamps are UTC. SQLite drops tzinfo on
        # read; restore that contract without changing storage or request rules.
        return utc_timestamp(value) if isinstance(value, datetime) else value


class ORMModel(UTCResponseModel):
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
    context: str


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


class MemberResponse(UTCResponseModel):
    id: str
    user_id: str
    email: EmailStr
    display_name: str
    role: WorkspaceRole
    created_at: datetime


class AuditLogResponse(UTCResponseModel):
    id: str
    action: str
    entity_type: str
    entity_id: str | None
    actor_user_id: str | None
    actor_display_name: str | None
    request_id: str | None
    metadata_json: dict[str, Any]
    chain_sequence: int
    entry_hash: str
    integrity_version: int
    created_at: datetime


class AuditIntegrityResponse(UTCResponseModel):
    valid: bool
    checked_entries: int
    head_sequence: int
    head_hash: str | None
    first_invalid_sequence: int | None
    reason: str | None
    verified_at: datetime


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
    approval_policy: Literal["dual_control", "single_operator_private"] = "dual_control"
    active: ActivePromptSetResponse
    builtin: ActivePromptSetResponse
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
    approval_policy: Literal["dual_control", "single_operator_private"] = "dual_control"
    active_suite: PromptEvalSuiteResponse | None
    suites: list[PromptEvalSuiteResponse]
    runs: list[PromptEvalRunResponse]


class SessionResponse(BaseModel):
    user: UserResponse
    workspace: WorkspaceResponse
    role: str
    context: str


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
    style_skill_id: str = Field(default="builtin:editorial", max_length=80)
    style_notes: str = Field(default="", max_length=2000)
    quality_profile: Literal["standard", "deep"] = "deep"
    image_source: Literal["manual", "generate", "search", "hybrid"] = "manual"
    image_search_query: str = Field(default="", max_length=500)


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
    style_skill_id: str | None = Field(default=None, max_length=80)
    style_notes: str | None = Field(default=None, max_length=2000)
    quality_profile: Literal["standard", "deep"] | None = None
    image_source: Literal["manual", "generate", "search", "hybrid"] | None = None
    image_search_query: str | None = Field(default=None, max_length=500)
    status: Literal["draft", "active", "archived"] | None = None

    @model_validator(mode="after")
    def reject_explicit_null(self):
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("修改字段不能为 null；不修改的字段请省略")
        return self


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


class StyleSkillInstall(BaseModel):
    manifest: dict[str, Any]


class StyleSkillStatusUpdate(BaseModel):
    status: Literal["enabled", "disabled"]


class StyleSkillResponse(UTCResponseModel):
    id: str
    source: Literal["builtin", "workspace"]
    status: Literal["enabled", "disabled"]
    manifest: dict[str, Any]
    manifest_sha256: str
    created_at: datetime | None
    updated_at: datetime | None


class WorkflowRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_campaign_updated_at: datetime
    provider: str | None = Field(default=None, max_length=80)
    regenerate_platforms: list[Platform] = Field(default_factory=list, max_length=3)

    @field_validator("expected_campaign_updated_at")
    @classmethod
    def normalize_expected_time(cls, value):
        return utc_timestamp(value)

    @field_validator("regenerate_platforms")
    @classmethod
    def unique_targets(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("重新生成的平台不能重复")
        return value


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
    generation_json: dict[str, Any]
    approved_by: str | None
    approved_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def label_review_binding(self):
        from .review_evidence import review_presentation
        self.review_json = review_presentation(self)
        return self


class ContentUpdate(BaseModel):
    expected_version: int = Field(ge=1, strict=True)
    title: str | None = Field(default=None, max_length=500)
    body: str | None = Field(default=None, max_length=20_000)
    hashtags: list[str] | None = None
    call_to_action: str | None = Field(default=None, max_length=500)
    layout_json: dict[str, Any] | None = None

    @model_validator(mode="after")
    def reject_explicit_null(self):
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("修改字段不能为 null；不修改的字段请省略")
        return self


class ContentReviewEvidenceResponse(ORMModel):
    id: str
    content_item_id: str
    content_version: int
    content_sha256: str
    event: str
    model_binding: str
    snapshot_json: dict[str, Any]
    snapshot_sha256: str
    actor_user_id: str | None
    created_at: datetime


class ContentRevisionResponse(ORMModel):
    id: str
    content_item_id: str
    version: int
    title: str
    body: str
    hashtags: list[str]
    call_to_action: str
    layout_json: dict[str, Any]
    generation_json: dict[str, Any]
    changed_by: str | None
    change_reason: str
    created_at: datetime


class ReviewDecision(BaseModel):
    expected_version: int = Field(ge=1, strict=True)
    decision: Literal["approve", "reject"]
    reason: str = Field(default="", max_length=2000)
    acknowledge_review_warnings: StrictBool = False


class AssetSelectionRequest(BaseModel):
    candidate_id: str | None = Field(default=None, min_length=1, max_length=64)
    acknowledge_license_check: bool = False


class AssetSourceChangeRequest(BaseModel):
    source: Literal["manual", "generate", "search"]


class AssetCapabilitiesResponse(BaseModel):
    image_generation_available: bool
    image_search_available: bool
    video_generation_available: bool


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
    script_confirmation_required: Literal[1, 2] = 1
    credentials: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_config(self):
        validate_channel_config(self.platform, self.config, incoming=True)
        return self


class ChannelResponse(ORMModel):
    id: str
    platform: str
    display_name: str
    status: str
    config_json: dict[str, Any]
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime


class PublishPreviewRequest(BaseModel):
    content_item_id: str
    channel_id: str
    scheduled_at: datetime | None = None
    publish_now: bool = False
    delivery_mode: Literal["connector", "script", "manual_export"] = "connector"
    request_id: str = Field(
        min_length=8,
        max_length=80,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )


class PublishScheduleRequest(PublishPreviewRequest):
    preview_token: str = Field(min_length=64, max_length=1200)


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
    response_json: dict[str, Any] = Field(default_factory=dict, exclude=True)
    id: str
    content_item_id: str
    channel_id: str
    request_id: str | None = None
    status: str
    scheduled_at: datetime
    delivery_mode: str
    publish_timing: str
    retry_safe: bool
    failure_stage: str | None
    external_id: str | None
    external_url: str | None
    script_confirmation_required: int
    script_confirmation_count: int
    script_confirmation_decision: str | None
    script_evidence_count: int
    script_confirmation_expires_at: datetime | None
    script_confirmation_expired: bool
    script_requested_by_user_id: str | None
    attempts: int
    script_package_available: bool
    error: str | None
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @field_serializer("error")
    def safe_error(self, value):
        # Also protects historical rows without altering forensic evidence.
        return public_connector_error(retry_safe=self.retry_safe,
            uncertain=self.status == "reconciliation_required",
            diagnostic=self.response_json.get("dispatch_diagnostic")) if value else None

    @field_serializer("failure_stage")
    def safe_failure_stage(self, value):
        return value if value in STAGES else None


class PublishEvidenceResponse(ORMModel):
    id: str
    publish_job_id: str
    script_attempt_id: str
    package_sha256: str
    kind: str
    original_filename: str
    source_sha256: str
    object_sha256: str
    mime_type: str
    size_bytes: int
    uploaded_by_user_id: str
    created_at: datetime


class PublishConfirmationResponse(ORMModel):
    id: str
    script_attempt_id: str
    package_sha256: str
    external_id: str | None
    external_url: str | None
    decision: str
    reason: str
    confirmed_by_user_id: str
    evidence_manifest_sha256: str
    created_at: datetime


class MetricInput(MetricValues):
    model_config = ConfigDict(extra="forbid")

    publish_job_id: str
    captured_at: datetime | None = None
    raw: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_raw_json(self):
        # SQLite JSON serialization otherwise accepts NaN/Infinity, unlike PG.
        json.dumps(self.raw, allow_nan=False)
        return self


class JobContextResponse(BaseModel):
    campaign_id: str | None = None
    campaign_name: str | None = None
    product_name: str | None = None
    content_item_id: str | None = None
    content_title: str | None = None
    platform: str | None = None


class JobManualReviewAction(BaseModel):
    decision: Literal["retry", "abandon"]
    provider_checked: Literal[True]
    note: ManualReviewNote


class JobManualReviewResponse(ORMModel):
    id: str
    reason_code: str
    context_json: dict[str, Any]
    requested_at: datetime
    resolved_at: datetime | None
    resolved_by_user_id: str | None
    provider_checked: bool
    decision: Literal["retry", "abandon"] | None
    note: str | None


class ProviderInvocationAttemptResponse(UTCResponseModel):
    id: str
    invocation_id: str
    request_key: str
    entity_type: str
    entity_id: str
    provider_kind: Literal["text", "embedding", "media", "search"]
    provider_name: str
    model_name: str
    operation: str
    request_sha256: str
    request_bytes: int
    attempt_number: int
    status: Literal[
        "started",
        "succeeded",
        "outcome_unknown",
        "late_succeeded",
        "late_failed",
    ]
    idempotency_key_sent: bool
    provider_request_id: str | None
    provider_request_id_source: str | None
    response_sha256: str | None
    response_bytes: int | None
    response_model: str | None
    usage_source: Literal["not_reported", "provider_reported"]
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    error_type: str | None
    started_at: datetime
    completed_at: datetime | None


class JobResponse(ORMModel):
    id: str
    job_type: str
    status: str
    attempts: int
    max_attempts: int
    run_at: datetime
    last_error: str | None
    result_json: dict[str, Any]
    context: JobContextResponse = Field(default_factory=JobContextResponse)
    manual_review: JobManualReviewResponse | None = None
    created_at: datetime
    updated_at: datetime

    @field_serializer("last_error")
    def safe_platform_error(self, value):
        if value and self.job_type in CONNECTOR_JOB_TYPES:
            return public_connector_error(uncertain=self.job_type == "publish.reconcile",
                diagnostic=self.result_json.get("connector_diagnostic"))
        return value


class WorkerQueueHealthResponse(BaseModel):
    queued: int
    retry: int
    running: int
    manual_review: int
    failed: int
    ready: int
    oldest_ready_age_seconds: float | None
    oldest_manual_review_age_seconds: float | None


class WorkerHealthResponse(UTCResponseModel):
    status: Literal["healthy", "degraded", "unavailable"]
    checked_at: datetime
    active_workers: int
    stale_workers: int
    stopped_workers: int
    issues: list[str]
    thresholds: dict[str, int]
    queue: WorkerQueueHealthResponse


class StorageUsageResponse(UTCResponseModel):
    used_bytes: int
    used_objects: int
    reserved_bytes: int
    reserved_objects: int
    unverified_objects: int
    max_bytes: int
    max_objects: int
    delete_pending_objects: int
    missing_objects: int
    integrity_error_objects: int
    abandoned_reservations: int
    last_reconciled_at: datetime | None


class StorageObjectAllocationResponse(ORMModel):
    id: str
    owner_type: str
    owner_id: str
    category: str
    filename: str
    status: Literal[
        "reserved",
        "active",
        "delete_pending",
        "missing",
        "integrity_error",
        "deleted",
        "abandoned",
    ]
    checksum: str | None
    size_bytes: int
    size_verified: bool
    mime_type: str | None
    reserved_until: datetime | None
    delete_attempts: int
    last_error: str | None
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime


class StorageReconcileRequest(BaseModel):
    delete_orphans: bool = False
