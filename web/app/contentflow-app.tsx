"use client";

import {
  FormEvent,
  ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  ApiError,
  api,
  apiAllPages,
  download,
  getApiBase,
  runtimeApiBaseConfigurable,
  setApiBase,
} from "@/lib/contentflow-api";

type View =
  | "dashboard"
  | "campaigns"
  | "review"
  | "assets"
  | "publishing"
  | "knowledge"
  | "channels"
  | "metrics"
  | "jobs"
  | "admin";

type Session = {
  user: { id: string; email: string; display_name: string };
  workspace: { id: string; name: string };
  role: string;
};

type Campaign = {
  id: string;
  name: string;
  product_name: string;
  objective: string;
  audience: string;
  platforms: string[];
  status: string;
  brief: {
    tone?: string;
    city?: string;
    must_include?: string[];
    forbidden_phrases?: string[];
    call_to_action?: string;
    product_facts?: string[];
    style_skill_id?: string;
    style_notes?: string;
    quality_profile?: "standard" | "deep";
    image_source?: "manual" | "generate" | "search" | "hybrid";
    image_search_query?: string;
  };
  updated_at: string;
};

type AIProvenance = {
  provider: string;
  model: string;
  prompt_source: "builtin" | "workspace_release";
  prompt_release_id: string | null;
  prompt_set_version: string;
  invocation_count: number;
  successful_invocations: number;
  failed_invocations: number;
  token_usage: {
    source: "provider_reported" | "partial" | "not_reported";
    total_tokens: number | null;
  };
};

type WorkflowRun = {
  id: string;
  campaign_id: string;
  status: string;
  current_stage: string;
  provider: string;
  trace_id: string;
  result_json: { ai_provenance?: AIProvenance };
  error: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
};

type Content = {
  id: string;
  campaign_id: string;
  platform: string;
  title: string;
  body: string;
  hashtags: string[];
  call_to_action: string;
  layout_json: Record<string, unknown>;
  status: string;
  version: number;
  review_json: Record<string, unknown>;
  generation_json: Record<string, unknown>;
  updated_at: string;
};

type ContentRevision = {
  id: string;
  content_item_id: string;
  version: number;
  title: string;
  body: string;
  hashtags: string[];
  call_to_action: string;
  layout_json: Record<string, unknown>;
  generation_json: Record<string, unknown>;
  changed_by: string | null;
  change_reason: string;
  created_at: string;
};

type Asset = {
  id: string;
  content_item_id: string | null;
  kind: string;
  provider: string;
  status: string;
  mime_type: string | null;
  size_bytes: number | null;
  metadata_json: Record<string, unknown>;
  error: string | null;
  created_at: string;
  updated_at: string;
};

type MediaSource = "manual" | "generate" | "search" | "hybrid";

type MediaCapabilities = {
  image_generation_available: boolean;
  image_search_available: boolean;
  video_generation_available: boolean;
};

type StyleSkill = {
  id: string;
  source: "builtin" | "workspace";
  status: "enabled" | "disabled";
  manifest: {
    name: string;
    slug: string;
    version: string;
    description: string;
  };
  manifest_sha256: string;
};

type ImageSearchCandidate = {
  id: string;
  title: string;
  creator: string;
  license: string;
  license_version: string;
  landing_url: string;
  thumbnail_url: string;
};

type Channel = {
  id: string;
  platform: string;
  display_name: string;
  status: string;
  config_json: Record<string, unknown>;
  updated_at: string;
};

type PublishJob = {
  id: string;
  content_item_id: string;
  channel_id: string;
  status: string;
  scheduled_at: string;
  delivery_mode: string;
  publish_timing: "immediate" | "scheduled";
  retry_safe: boolean;
  failure_stage: string | null;
  attempts: number;
  error: string | null;
  external_id: string | null;
  script_confirmation_required: number;
  script_confirmation_count: number;
  script_confirmation_decision: string | null;
  script_evidence_count: number;
  script_confirmation_expires_at: string | null;
  script_confirmation_expired: boolean;
  script_requested_by_user_id: string | null;
  script_package_available: boolean;
  updated_at: string;
};

type PublishEvidence = {
  id: string;
  publish_job_id: string;
  script_attempt_id: string;
  package_sha256: string;
  kind: string;
  original_filename: string;
  source_sha256: string;
  object_sha256: string;
  mime_type: string;
  size_bytes: number;
  uploaded_by_user_id: string;
  created_at: string;
};

type PublishConfirmation = {
  id: string;
  script_attempt_id: string;
  package_sha256: string;
  external_id: string | null;
  external_url: string | null;
  decision: string;
  reason: string;
  confirmed_by_user_id: string;
  evidence_manifest_sha256: string;
  created_at: string;

};
type KnowledgeDocument = {
  id: string;
  name: string;
  status: string;
  metadata_json: { size_bytes?: number; chunk_count?: number };
  updated_at: string;
};

type QueueJob = {
  id: string;
  job_type: string;
  status: string;
  attempts: number;
  max_attempts: number;
  run_at: string;
  last_error: string | null;
  updated_at: string;
  manual_review: {
    id: string;
    reason_code: string;
    context_json: {
      source?: string;
      possible_side_effect?: string;
      required_checks?: string[];
    };
    requested_at: string;
    resolved_at: string | null;
    resolved_by_user_id: string | null;
    provider_checked: boolean;
    decision: "retry" | "abandon" | null;
    note: string | null;
  } | null;
  context: {
    campaign_id: string | null;
    campaign_name: string | null;
    product_name: string | null;
    content_item_id: string | null;
    content_title: string | null;
    platform: string | null;
  };
};

type ProviderInvocationAttempt = {
  id: string;
  invocation_id: string;
  request_key: string;
  provider_kind: "text" | "embedding";
  provider_name: string;
  model_name: string;
  operation: string;
  request_sha256: string;
  request_bytes: number;
  attempt_number: number;
  status: "started" | "succeeded" | "outcome_unknown" | "late_succeeded" | "late_failed";
  idempotency_key_sent: boolean;
  provider_request_id: string | null;
  provider_request_id_source: string | null;
  response_sha256: string | null;
  response_bytes: number | null;
  response_model: string | null;
  usage_source: "not_reported" | "provider_reported";
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  error_type: string | null;
  started_at: string;
  completed_at: string | null;
};

type DashboardSummary = {
  campaigns: number;
  runs_active: number;
  contents_needing_review: number;
  assets_processing: number;
  publishes_scheduled: number;
  jobs_manual_review: number;
  jobs_failed: number;
};

type MetricsSummary = {
  sample_count: number;
  impressions: number;
  clicks: number;
  engagements: number;
  click_through_rate: number;
  engagement_rate: number;
  recommendations: string[];
};

type WorkspaceAccess = {
  id: string;
  name: string;
  slug: string;
  role: "viewer" | "editor" | "reviewer" | "admin";
};

type Member = {
  id: string;
  user_id: string;
  email: string;
  display_name: string;
  role: "viewer" | "editor" | "reviewer" | "admin";
  created_at: string;
};

type AuditLog = {
  id: string;
  action: string;
  entity_type: string;
  entity_id: string | null;
  actor_user_id: string | null;
  actor_display_name: string | null;
  request_id: string | null;
  metadata_json: Record<string, unknown>;
  chain_sequence: number;
  entry_hash: string;
  integrity_version: number;
  created_at: string;
};

type AuditIntegrity = {
  valid: boolean;
  checked_entries: number;
  head_sequence: number;
  head_hash: string | null;
  first_invalid_sequence: number | null;
  reason: string | null;
  verified_at: string;
};

type StorageUsage = {
  used_bytes: number;
  used_objects: number;
  reserved_bytes: number;
  reserved_objects: number;
  unverified_objects: number;
  max_bytes: number;
  max_objects: number;
  delete_pending_objects: number;
  missing_objects: number;
  integrity_error_objects: number;
  abandoned_reservations: number;
  last_reconciled_at: string | null;
};

type StorageObjectAllocation = {
  id: string;
  owner_type: string;
  owner_id: string;
  category: string;
  filename: string;
  status:
    | "reserved"
    | "active"
    | "delete_pending"
    | "missing"
    | "integrity_error"
    | "deleted"
    | "abandoned";
  checksum: string | null;
  size_bytes: number;
  size_verified: boolean;
  mime_type: string | null;
  reserved_until: string | null;
  delete_attempts: number;
  last_error: string | null;
  deleted_at: string | null;
  created_at: string;
  updated_at: string;
};


type PromptStage = "plan" | "generate" | "review";
type PromptReleaseStatus =
  | "draft"
  | "approved"
  | "active"
  | "retired"
  | "rejected";

type PromptRelease = {
  id: string;
  workspace_id: string;
  release_number: number;
  version: string;
  status: PromptReleaseStatus;
  prompts: Record<PromptStage, string>;
  prompt_hashes: Record<PromptStage, string>;
  change_summary: string;
  review_note: string | null;
  created_by_user_id: string;
  reviewed_by_user_id: string | null;
  activated_by_user_id: string | null;
  reviewed_at: string | null;
  activated_at: string | null;
  created_at: string;
  updated_at: string;
};

type PromptGovernance = {
  active: {
    source: "builtin" | "workspace_release";
    version: string;
    release_id: string | null;
    prompts: Record<PromptStage, string>;
    prompt_hashes: Record<PromptStage, string>;
  };
  builtin: {
    source: "builtin";
    version: string;
    release_id: null;
    prompts: Record<PromptStage, string>;
    prompt_hashes: Record<PromptStage, string>;
  };
  governance_required: boolean;
  ready_for_generation: boolean;
  generation_block_reason: string | null;
  releases: PromptRelease[];
};

type PromptEvalCase = {
  name: string;
  stage: PromptStage;
  input_json: Record<string, unknown>;
  required_paths?: string[];
  expected_values?: Record<string, unknown>;
  required_substrings?: string[];
  forbidden_substrings?: string[];
  max_output_bytes?: number;
};

type PromptEvalSuite = {
  id: string;
  workspace_id: string;
  version_number: number;
  version: string;
  status: "draft" | "active" | "retired";
  name: string;
  description: string;
  cases: PromptEvalCase[];
  suite_hash: string;
  created_by_user_id: string;
  activated_by_user_id: string | null;
  activated_at: string | null;
  created_at: string;
  updated_at: string;
};

type PromptEvalRun = {
  id: string;
  workspace_id: string;
  prompt_release_id: string;
  suite_id: string;
  status: "queued" | "running" | "passed" | "failed" | "error";
  requested_provider: string;
  provider: string | null;
  model: string | null;
  prompt_hashes: Record<PromptStage, string>;
  suite_hash: string;
  result_json: Record<string, unknown>;
  error: string | null;
  created_by_user_id: string;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
};

type PromptEvalGovernance = {
  active_suite: PromptEvalSuite | null;
  suites: PromptEvalSuite[];
  runs: PromptEvalRun[];
};

const DEFAULT_PROMPT_EVAL_CASES: PromptEvalCase[] = [
  {
    name: "plan-output-contract",
    stage: "plan",
    input_json: {
      brief: {
        product_name: "ContentFlow",
        city: "北京",
        goal: "帮助内容团队建立可审核的生产流程",
        audience: "需要稳定生产公众号内容的运营人员",
        platforms: ["wechat"],
        must_include: ["人工复核"],
        product_facts: ["整理内容工作流"],
        call_to_action: "查看完整方案",
      },
      knowledge: [],
    },
    required_paths: [
      "angle_candidates",
      "selected_angle",
      "content_thesis",
      "evidence_ledger",
      "platform_strategies.wechat",
      "image_search_query",
    ],
  },
  {
    name: "wechat-generation-contract",
    stage: "generate",
    input_json: {
      brief: {
        product_name: "ContentFlow",
        city: "北京",
        goal: "帮助内容团队建立可审核的生产流程",
        audience: "需要稳定生产公众号内容的运营人员",
        platforms: ["wechat"],
        must_include: ["人工复核"],
        product_facts: ["整理内容工作流"],
        call_to_action: "查看完整方案",
      },
      platform: "wechat",
      plan: {},
      knowledge: [],
    },
    required_paths: [
      "title",
      "alternate_titles",
      "body",
      "layout.sections",
      "evidence_usage",
      "media_brief.generation_prompt",
    ],
    required_substrings: ["ContentFlow"],
  },
  {
    name: "review-output-contract",
    stage: "review",
    input_json: {
      brief: {
        product_name: "ContentFlow",
        city: "北京",
        goal: "帮助内容团队建立可审核的生产流程",
        audience: "需要稳定生产公众号内容的运营人员",
        platforms: ["wechat"],
        must_include: ["人工复核"],
        product_facts: ["整理内容工作流"],
        call_to_action: "查看完整方案",
      },
      platform: "wechat",
      content: {
        title: "ContentFlow 内容工作流：发布前人工复核清单",
        body: (
          "ContentFlow 用于整理内容工作流。运营人员应先核对资料来源和产品事实，"
          + "再检查标题、正文、图片许可与平台要求；模型建议不能替代人工判断。"
          + "完成修订后由审核人员人工复核，通过后再查看完整方案。"
        ),
      },
      knowledge: [],
    },
    required_paths: [
      "risk_level",
      "quality_score",
      "scores.hook",
      "scores.specificity",
      "scores.evidence",
      "scores.platform_native",
      "scores.structure",
      "scores.usefulness",
      "scores.voice",
      "scores.originality",
      "scores.cta",
      "revision_instructions",
    ],
    expected_values: { passed: true },
  },
];

type DataState = {
  dashboard: DashboardSummary;
  campaigns: Campaign[];
  runs: WorkflowRun[];
  styleSkills: StyleSkill[];
  contents: Content[];
  assets: Asset[];
  mediaCapabilities: MediaCapabilities;
  channels: Channel[];
  publishes: PublishJob[];
  knowledge: KnowledgeDocument[];
  jobs: QueueJob[];
  metrics: MetricsSummary;
  workspaces: WorkspaceAccess[];
  members: Member[];
  auditLogs: AuditLog[];
  storageUsage: StorageUsage | null;
  storageAttention: StorageObjectAllocation[];
  promptGovernance: PromptGovernance | null;
  promptEval: PromptEvalGovernance | null;
};

const EMPTY_DATA: DataState = {
  dashboard: {
    campaigns: 0,
    runs_active: 0,
    contents_needing_review: 0,
    assets_processing: 0,
    publishes_scheduled: 0,
    jobs_manual_review: 0,
    jobs_failed: 0,
  },
  campaigns: [],
  runs: [],
  styleSkills: [],
  contents: [],
  assets: [],
  mediaCapabilities: {
    image_generation_available: false,
    image_search_available: false,
    video_generation_available: false,
  },
  channels: [],
  publishes: [],
  knowledge: [],
  jobs: [],
  workspaces: [],
  members: [],
  auditLogs: [],
  storageUsage: null,
  storageAttention: [],
  promptGovernance: null,
  promptEval: null,
  metrics: {
    sample_count: 0,
    impressions: 0,
    clicks: 0,
    engagements: 0,
    click_through_rate: 0,
    engagement_rate: 0,
    recommendations: [],
  },
};

function mergeUpdatedRows<T extends { id: string; updated_at: string }>(
  current: T[],
  updates: T[],
): T[] {
  const rows = new Map(current.map((item) => [item.id, item]));
  for (const item of updates) rows.set(item.id, item);
  return [...rows.values()].sort(
    (left, right) =>
      right.updated_at.localeCompare(left.updated_at)
      || right.id.localeCompare(left.id),
  );
}

function safeRefreshBoundary(syncTimes: Array<string | null>): string {
  const earliest = syncTimes
    .filter((value): value is string => Boolean(value))
    .sort()[0];
  const parsed = earliest ? Date.parse(earliest) : Number.NaN;
  const timestamp = Number.isFinite(parsed) ? parsed : Date.now();
  return new Date(timestamp - 2_000).toISOString();
}

const PLATFORM: Record<string, string> = {
  xiaohongshu: "小红书",
  douyin: "抖音",
  wechat: "公众号",
};

const STYLE_SKILL_EXAMPLE = {
  manifest_version: 1,
  slug: "warm-editor",
  name: "温暖生活方式编辑",
  version: "1.0.0",
  description: "用克制、具体、有生活感的语言讲清产品价值。",
  instructions: [
    "从具体生活场景进入，不先写产品口号",
    "每段保留一个能被读者带走的动作或判断",
  ],
  forbidden_patterns: ["夸张承诺", "虚构个人经历"],
  platform_instructions: {
    xiaohongshu: ["像有经验的朋友分享，保留可收藏清单"],
    wechat: ["导语有观点，正文充分展开并说明边界"],
  },
  examples: [],
};

const ROLE_LABEL: Record<string, string> = {
  viewer: "只读成员",
  editor: "内容编辑",
  reviewer: "审核人员",
  admin: "管理员",
};

const ROLE_RANK: Record<string, number> = {
  viewer: 0,
  editor: 1,
  reviewer: 2,
  admin: 3,
};

function roleAtLeast(role: string, minimum: keyof typeof ROLE_RANK) {
  return (ROLE_RANK[role] ?? -1) >= ROLE_RANK[minimum];
}

const STATUS: Record<string, string> = {
  active: "进行中",
  approved: "已通过",
  awaiting_review: "待审核",
  awaiting_upload: "待上传",
  awaiting_selection: "待选择",
  blocked: "规则拦截",
  cancelled: "已取消",
  connected: "已连接",
  draft: "草稿",
  draft_created: "已建草稿",
  exported: "已导出",
  export_only: "导出模式",
  script_only: "脚本模式",
  script_ready: "脚本包就绪",
  script_confirmation_pending: "等待第二人确认",
  script_published: "脚本确认发布",
  error: "执行错误",
  failed: "失败",
  manual_review: "待人工核对",
  outcome_unknown: "结果待核对",
  late_succeeded: "迟到成功回执",
  late_failed: "迟到失败回执",
  generating: "生成中",
  indexed: "已索引",
  indexing: "索引中",
  invalid: "连接异常",
  needs_review: "待审核",
  pending: "等待中",
  passed: "已通过评测",
  pending_test: "待测试",
  planned: "待生成",
  processing: "处理中",
  published: "已发布",
  queued: "排队中",
  ready: "已就绪",
  rejected: "已驳回",
  retired: "已退役",
  retry: "重试中",
  running: "运行中",
  scheduled: "已排期",
  stale: "旧版本",
  submitted: "已提交",
  succeeded: "成功",
  reserved: "写入预留",
  delete_pending: "等待删除",
  missing: "对象缺失",
  integrity_error: "完整性异常",
  deleted: "已删除",
  abandoned: "已释放预留",
};

const DELIVERY_MODE: Record<string, string> = {
  connector: "官方 API",
  script: "脚本辅助",
  manual_export: "人工导出",
};

const PUBLISH_FAILURE_STAGE: Record<string, string> = {
  authenticate: "渠道鉴权",
  validate_assets: "素材检查",
  read_assets: "素材读取",
};

const NAV: Array<{ id: View; label: string; icon: IconName }> = [
  { id: "dashboard", label: "工作台", icon: "grid" },
  { id: "campaigns", label: "1 创建内容", icon: "campaign" },
  { id: "review", label: "2 审核内容", icon: "review" },
  { id: "assets", label: "3 准备素材", icon: "image" },
  { id: "publishing", label: "4 发布", icon: "send" },
  { id: "knowledge", label: "知识库", icon: "book" },
  { id: "channels", label: "平台连接", icon: "link" },
  { id: "metrics", label: "数据复盘", icon: "chart" },
  { id: "jobs", label: "任务队列", icon: "queue" },
  { id: "admin", label: "团队与审计", icon: "settings" },
];

const PRIMARY_NAV_IDS: View[] = [
  "dashboard",
  "campaigns",
  "review",
  "assets",
  "publishing",
];

type IconName =
  | "grid"
  | "campaign"
  | "review"
  | "image"
  | "send"
  | "book"
  | "link"
  | "chart"
  | "queue"
  | "settings"
  | "refresh"
  | "logout"
  | "plus"
  | "download";

function Icon({ name }: { name: IconName }) {
  const paths: Record<IconName, ReactNode> = {
    grid: <path d="M4 4h6v6H4zm10 0h6v6h-6zM4 14h6v6H4zm10 0h6v6h-6z" />,
    campaign: <path d="M4 7h11l5-3v16l-5-3H4zM7 17v4" />,
    review: <path d="M5 3h14v18H5zM8 8h8m-8 4h8m-8 4h5" />,
    image: <path d="M3 5h18v14H3zM7 14l3-3 4 4 2-2 3 3M8 9h.01" />,
    send: <path d="M3 11.5 21 3l-8.5 18-2-7.5zM10.5 13.5 21 3" />,
    book: <path d="M4 4h7v16H4a2 2 0 0 1 0-4h7m2-12h7v16h-7z" />,
    link: <path d="M10 13a5 5 0 0 0 7.5.5l2-2a5 5 0 0 0-7-7l-1.2 1.2M14 11a5 5 0 0 0-7.5-.5l-2 2a5 5 0 0 0 7 7l1.2-1.2" />,
    chart: <path d="M4 20V10m6 10V4m6 16v-7m4 7H2" />,
    queue: <path d="M8 6h12M8 12h12M8 18h12M3 6h.01M3 12h.01M3 18h.01" />,
    settings: <path d="M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8Zm8 4 2 1-2 3-2-.5a8 8 0 0 1-2 1.5l-.2 2H12l-.5-2a8 8 0 0 1-2-1.2L7 17l-2-3 1.7-1.6a8 8 0 0 1 0-1.8L5 9l2-3 2.5 1.2a8 8 0 0 1 2-1.2L12 4h3.8l.2 2a8 8 0 0 1 2 1.5l2-.5 2 3-2 1v1Z" />,
    refresh: <path d="M20 6v5h-5M4 18v-5h5M6.1 8a7 7 0 0 1 11.7-2L20 11M4 13l2.2 5a7 7 0 0 0 11.7-2" />,
    logout: <path d="M10 4H4v16h6m4-4 4-4-4-4m4 4H8" />,
    plus: <path d="M12 5v14M5 12h14" />,
    download: <path d="M12 3v12m-5-5 5 5 5-5M4 20h16" />,
  };
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24">
      {paths[name]}
    </svg>
  );
}

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : "发生未知错误";
}

function StatusBadge({ value }: { value: string }) {
  const semantic =
    value === "failed" || value === "manual_review" || value === "blocked" || value === "rejected"
      || value === "missing" || value === "integrity_error"
      || value === "abandoned"
      ? "danger"
      : value === "approved" ||
          value === "ready" ||
          value === "published" ||
          value === "script_published" ||
          value === "script_ready" ||
          value === "exported" ||
          value === "succeeded" ||
          value === "indexed" ||
          value === "connected"
        ? "success"
        : value === "processing" ||
            value === "running" ||
            value === "queued" ||
            value === "scheduled" ||
            value === "delete_pending" ||
            value === "reserved"
          ? "info"
          : "neutral";
  const animated = ["processing", "running", "queued", "generating", "indexing", "retry"].includes(value);
  return (
    <span className={`status status-${semantic} ${animated ? "status-animated" : ""}`}>
      {animated ? <span className="status-spinner" aria-hidden="true" /> : null}
      {STATUS[value] || value}
    </span>
  );
}

const ACTIVE_RUN_STATUSES = new Set(["queued", "running"]);

function projectCode(id: string): string {
  return `CF-${id.replaceAll("-", "").slice(0, 6).toUpperCase()}`;
}

function runStageMeta(run: WorkflowRun) {
  if (run.status === "awaiting_review" || run.current_stage === "human_review") {
    return { label: "生成完成，等待人工审核", detail: "内容与质量报告已保存", progress: 100, step: "7 / 7" };
  }
  if (run.status === "failed" || run.current_stage === "failed") {
    return { label: "生成失败", detail: "查看错误后修复，再创建新的生成批次", progress: 100, step: "已停止" };
  }
  const stage = run.current_stage || "queued";
  const platformStage = stage.match(
    /^(final_review|drafting|reviewing|revising)_([a-z0-9-]+)__(\d+)_of_(\d+)$/,
  );
  if (platformStage) {
    const phaseMetaByName = {
      drafting: { label: "正在撰写平台初稿…", detail: "根据选定角度、知识与风格生成正文", fraction: 0.18, step: "3 / 7" },
      reviewing: { label: "正在编辑与安全评审…", detail: "检查事实边界、平台表达与 9 项质量指标", fraction: 0.48, step: "4 / 7" },
      revising: { label: "正在定向改写…", detail: "只修正评审指出的问题，最多 1 次", fraction: 0.72, step: "5 / 7" },
      final_review: { label: "正在复核改写稿…", detail: "确认安全与质量没有回退", fraction: 0.9, step: "6 / 7" },
    } as const;
    const phase = platformStage[1] as keyof typeof phaseMetaByName;
    const [, , platform, indexRaw, totalRaw] = platformStage;
    const index = Number(indexRaw);
    const total = Math.max(Number(totalRaw), 1);
    const phaseMeta = phaseMetaByName[phase];
    const platformName = PLATFORM[platform] || platform;
    const progress = Math.round(34 + ((index - 1 + phaseMeta.fraction) / total) * 60);
    return {
      label: `${platformName}：${phaseMeta.label}`,
      detail: phaseMeta.detail,
      progress,
      step: `平台 ${index} / ${total} · ${phaseMeta.step}`,
    };
  }
  if (stage.startsWith("final_review_")) {
    return { label: "正在复核改写稿…", detail: "确认安全与质量没有回退", progress: 88, step: "6 / 7" };
  }
  if (stage.startsWith("revising_")) {
    return { label: "正在定向改写…", detail: "只修正评审指出的问题，最多 1 次", progress: 76, step: "5 / 7" };
  }
  if (stage.startsWith("reviewing_")) {
    return { label: "正在编辑与安全评审…", detail: "检查事实边界、平台表达与 9 项质量指标", progress: 64, step: "4 / 7" };
  }
  if (stage.startsWith("drafting_") || stage === "content_generation") {
    return { label: "正在撰写平台初稿…", detail: "根据选定角度、知识与风格生成正文", progress: 50, step: "3 / 7" };
  }
  if (stage === "planning") {
    return { label: "正在比较选题角度…", detail: "建立证据账本、结构和素材方向", progress: 34, step: "2 / 7" };
  }
  if (stage === "knowledge_retrieval") {
    return { label: "正在检索项目知识…", detail: "只使用当前工作区可访问的资料", progress: 20, step: "1 / 7" };
  }
  return { label: "等待 Worker 接手…", detail: "任务已安全入队，可以离开当前页面", progress: 8, step: "排队" };
}

function ProjectIdentity({
  campaign,
  context,
  contentTitle,
  fallbackCampaignId,
  compact = false,
}: {
  campaign?: Campaign | null;
  context?: QueueJob["context"];
  contentTitle?: string | null;
  fallbackCampaignId?: string | null;
  compact?: boolean;
}) {
  const id = campaign?.id || context?.campaign_id || fallbackCampaignId || "";
  const name = campaign?.name || context?.campaign_name || "系统级任务";
  const product = campaign?.product_name || context?.product_name || "不属于单个内容项目";
  const detail = contentTitle || context?.content_title;
  return (
    <div className={`project-identity ${compact ? "project-identity-compact" : ""}`}>
      <span className="project-code" translate="no">{id ? projectCode(id) : "SYSTEM"}</span>
      <span className="project-identity-copy">
        <strong>{name}</strong>
        <small>{product}{detail ? ` · ${detail}` : ""}</small>
      </span>
    </div>
  );
}

function GenerationProgress({ run, compact = false }: { run: WorkflowRun; compact?: boolean }) {
  const stage = runStageMeta(run);
  const active = ACTIVE_RUN_STATUSES.has(run.status);
  return (
    <div className={`generation-progress ${compact ? "generation-progress-compact" : ""}`} aria-live="polite">
      <div className="generation-progress-heading">
        {active ? <span className="activity-spinner" aria-hidden="true" /> : null}
        <span>
          <strong>{stage.label}</strong>
          <small>{stage.step} · {stage.detail}</small>
        </span>
      </div>
      <div
        className="progress-track"
        role="progressbar"
        aria-label="内容生成阶段"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={stage.progress}
        aria-valuetext={`${stage.label}，${stage.step}`}
      >
        <span style={{ width: `${stage.progress}%` }} />
      </div>
    </div>
  );
}

function ActiveGenerationStrip({ runs, campaigns }: { runs: WorkflowRun[]; campaigns: Campaign[] }) {
  const activeRuns = runs.filter((run) => ACTIVE_RUN_STATUSES.has(run.status));
  if (!activeRuns.length) return null;
  const campaignMap = Object.fromEntries(campaigns.map((campaign) => [campaign.id, campaign]));
  return (
    <section className="active-generation-strip" aria-label="正在生成的内容" aria-live="polite">
      <div className="active-generation-title">
        <span className="activity-spinner" aria-hidden="true" />
        <div><strong>{activeRuns.length} 个内容任务正在进行</strong><small>页面会自动刷新，离开本页不会中断任务。</small></div>
      </div>
      <div className="active-generation-list">
        {activeRuns.slice(0, 3).map((run) => (
          <article key={run.id}>
            <ProjectIdentity campaign={campaignMap[run.campaign_id]} compact />
            <GenerationProgress run={run} compact />
          </article>
        ))}
        {activeRuns.length > 3 ? <p>另有 {activeRuns.length - 3} 个任务正在运行。</p> : null}
      </div>
    </section>
  );
}

function EmptyState({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  return (
    <div className="empty-state">
      <span className="empty-mark">—</span>
      <h3>{title}</h3>
      <p>{description}</p>
    </div>
  );
}

function Button({
  children,
  kind = "primary",
  busy = false,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  kind?: "primary" | "secondary" | "ghost" | "danger";
  busy?: boolean;
}) {
  return (
    <button
      {...props}
      disabled={props.disabled || busy}
      className={`button button-${kind} ${props.className || ""}`}
    >
      {busy ? <span className="button-spinner" /> : null}
      {children}
    </button>
  );
}

function AuthScreen({
  onAuthenticated,
}: {
  onAuthenticated: (session: Session) => void;
}) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [apiBase, setBase] = useState(getApiBase());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setBusy(true);
    const form = new FormData(event.currentTarget);
    try {
      setApiBase(apiBase);
      const payload =
        mode === "login"
          ? {
              email: String(form.get("email") || ""),
              password: String(form.get("password") || ""),
            }
          : {
              email: String(form.get("email") || ""),
              password: String(form.get("password") || ""),
              display_name: String(form.get("display_name") || ""),
              workspace_name: String(form.get("workspace_name") || ""),
            };
      await api<unknown>(
        mode === "login" ? "/auth/login" : "/auth/register",
        { method: "POST", body: payload },
      );
      const current = await api<Session>("/auth/session");
      onAuthenticated(current);
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="auth-layout">
      <section className="auth-intro">
        <div className="brand-lockup brand-lockup-large">
          <span className="brand-symbol">CF</span>
          <span>ContentFlow</span>
        </div>
        <div>
          <p className="eyebrow">AI 内容营销自动化系统</p>
          <h1>把内容生产变成一条可管理、可审核的工作流。</h1>
          <p className="auth-lead">
            知识检索、内容策划、多平台生成、素材生产、人工审核、分发与数据复盘，
            每一步都有状态、权限和审计记录。
          </p>
        </div>
        <ol className="auth-flow">
          <li><span>01</span>知识与营销 Brief</li>
          <li><span>02</span>模型生成与规则校验</li>
          <li><span>03</span>人工审核与素材生产</li>
          <li><span>04</span>平台分发与指标回收</li>
        </ol>
      </section>
      <section className="auth-panel">
        <div className="auth-card">
          <p className="eyebrow">{mode === "login" ? "登录工作台" : "创建工作区"}</p>
          <h2>{mode === "login" ? "继续处理内容任务" : "开始搭建内容流程"}</h2>
          <div className="auth-tabs" role="tablist" aria-label="账户操作">
            <button
              className={mode === "login" ? "active" : ""}
              onClick={() => setMode("login")}
              type="button"
            >
              登录
            </button>
            <button
              className={mode === "register" ? "active" : ""}
              onClick={() => setMode("register")}
              type="button"
            >
              注册
            </button>
          </div>
          <form onSubmit={submit} className="stack-form">
            {mode === "register" ? (
              <div className="form-grid">
                <label>
                  姓名
                  <input name="display_name" required placeholder="运营负责人" />
                </label>
                <label>
                  工作区名称
                  <input name="workspace_name" required placeholder="示例内容中心" />
                </label>
              </div>
            ) : null}
            <label>
              邮箱
              <input name="email" type="email" required placeholder="you@example.com" />
            </label>
            <label>
              密码
              <input
                name="password"
                type="password"
                minLength={mode === "register" ? 12 : 8}
                required
                placeholder={mode === "register" ? "至少 12 位" : "输入密码"}
              />
            </label>
            <label>
              API 地址
              <input
                value={apiBase}
                onChange={(event) => setBase(event.target.value)}
                required
                disabled={!runtimeApiBaseConfigurable}
              />
              <small>
                {runtimeApiBaseConfigurable
                  ? "本地默认连接 http://localhost:8000/api/v1"
                  : "生产环境 API 地址由构建配置固定"}
              </small>
            </label>
            {error ? <p className="form-error" role="alert">{error}</p> : null}
            <Button busy={busy} type="submit">
              {mode === "login" ? "登录 ContentFlow" : "创建账户与工作区"}
            </Button>
          </form>
        </div>
        <p className="auth-note">
          平台凭据由后端加密保存，不会返回到浏览器。小红书默认使用审核后导出模式。
        </p>
      </section>
    </main>
  );
}

export function ContentFlowApp() {
  const [session, setSession] = useState<Session | null>(null);
  const [view, setView] = useState<View>("dashboard");
  const [campaignFilter, setCampaignFilter] = useState("");
  const [advancedNavOpen, setAdvancedNavOpen] = useState(false);
  const [data, setData] = useState<DataState>(EMPTY_DATA);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [pageWarning, setPageWarning] = useState("");
  const pollInFlight = useRef(false);
  const lastOperationalRefresh = useRef<string | null>(null);

  const loadData = useCallback(async () => {
    setRefreshing(true);
    try {
      const [
        dashboard,
        campaignPage,
        runPage,
        stylePage,
        contentPage,
        assetPage,
        mediaCapabilities,
        channelPage,
        publishPage,
        knowledgePage,
        jobPage,
        metrics,
        workspacePage,
        memberPage,
        auditPage,
        promptGovernanceControl,
        promptReleasePage,
        promptEvalControl,
        promptEvalSuitePage,
        promptEvalRunPage,
        storageUsage,
        storageAttentionPage,
      ] = await Promise.all([
        api<DashboardSummary>("/dashboard/summary"),
        apiAllPages<Campaign>("/campaigns"),
        apiAllPages<WorkflowRun>("/runs"),
        apiAllPages<StyleSkill>("/style-skills"),
        apiAllPages<Content>("/contents"),
        apiAllPages<Asset>("/assets"),
        api<MediaCapabilities>("/assets/capabilities"),
        apiAllPages<Channel>("/channels"),
        apiAllPages<PublishJob>("/publishing/jobs"),
        apiAllPages<KnowledgeDocument>("/knowledge/documents"),
        apiAllPages<QueueJob>("/jobs"),
        api<MetricsSummary>(
          campaignFilter
            ? `/metrics/summary?campaign_id=${encodeURIComponent(campaignFilter)}`
            : "/metrics/summary",
        ),
        apiAllPages<WorkspaceAccess>("/auth/workspaces"),
        session?.role === "admin"
          ? apiAllPages<Member>("/admin/members")
          : Promise.resolve({ items: [], truncated: false, syncTime: null }),
        session?.role === "admin"
          ? apiAllPages<AuditLog>("/admin/audit-logs")
          : Promise.resolve({ items: [], truncated: false, syncTime: null }),
        session?.role === "admin"
          ? api<PromptGovernance>("/admin/prompt-releases")
          : Promise.resolve(null),
        session?.role === "admin"
          ? apiAllPages<PromptRelease>("/admin/prompt-releases/history")
          : Promise.resolve({ items: [], truncated: false, syncTime: null }),
        session?.role === "admin"
          ? api<PromptEvalGovernance>("/admin/prompt-eval")
          : Promise.resolve(null),
        session?.role === "admin"
          ? apiAllPages<PromptEvalSuite>("/admin/prompt-eval/suites")
          : Promise.resolve({ items: [], truncated: false, syncTime: null }),
        session?.role === "admin"
          ? apiAllPages<PromptEvalRun>("/admin/prompt-eval/runs")
          : Promise.resolve({ items: [], truncated: false, syncTime: null }),
        session?.role === "admin"
          ? api<StorageUsage>("/admin/storage/usage")
          : Promise.resolve(null),
        session?.role === "admin"
          ? apiAllPages<StorageObjectAllocation>(
              "/admin/storage/objects?attention_only=true",
            )
          : Promise.resolve({ items: [], truncated: false, syncTime: null }),
      ]);
      const limitedCollections = [
        ["活动", campaignPage.truncated],
        ["运行记录", runPage.truncated],
        ["风格 Skill", stylePage.truncated],
        ["内容", contentPage.truncated],
        ["素材", assetPage.truncated],
        ["发布任务", publishPage.truncated],
        ["知识文档", knowledgePage.truncated],
        ["队列任务", jobPage.truncated],
        ["渠道", channelPage.truncated],
        ["工作区", workspacePage.truncated],
        ["成员", memberPage.truncated],
        ["审计记录", auditPage.truncated],
        ["Prompt 版本", promptReleasePage.truncated],
        ["Prompt Eval 套件", promptEvalSuitePage.truncated],
        ["Prompt Eval 运行", promptEvalRunPage.truncated],
        ["存储异常对象", storageAttentionPage.truncated],
      ].filter(([, truncated]) => truncated).map(([label]) => label);
      setData({
        dashboard,
        campaigns: campaignPage.items,
        runs: runPage.items,
        styleSkills: stylePage.items,
        contents: contentPage.items,
        assets: assetPage.items,
        mediaCapabilities,
        channels: channelPage.items,
        publishes: publishPage.items,
        knowledge: knowledgePage.items,
        jobs: jobPage.items,
        metrics,
        workspaces: workspacePage.items,
        members: memberPage.items,
        auditLogs: auditPage.items,
        storageUsage,
        storageAttention: storageAttentionPage.items,
        promptGovernance: promptGovernanceControl
          ? { ...promptGovernanceControl, releases: promptReleasePage.items }
          : null,
        promptEval: promptEvalControl
          ? {
              ...promptEvalControl,
              suites: promptEvalSuitePage.items,
              runs: promptEvalRunPage.items,
            }
          : null,
      });
      lastOperationalRefresh.current = safeRefreshBoundary([
        campaignPage.syncTime,
        runPage.syncTime,
        contentPage.syncTime,
        assetPage.syncTime,
        publishPage.syncTime,
        jobPage.syncTime,
      ]);
      setPageWarning(
        limitedCollections.length
          ? `${limitedCollections.join("、")}已达到安全加载上限 2000 条，请使用筛选或历史视图继续定位。`
          : "",
      );
      setError("");
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        setSession(null);
      } else {
        setError(messageOf(caught));
      }
    } finally {
      setRefreshing(false);
    }
  }, [session, campaignFilter]);

  const pollOperationalData = useCallback(async () => {
    if (
      !session
      || pollInFlight.current
      || typeof document === "undefined"
      || document.visibilityState === "hidden"
    ) return;
    const updatedAfter = lastOperationalRefresh.current;
    if (!updatedAfter) return;
    const query = `updated_after=${encodeURIComponent(updatedAfter)}`;
    pollInFlight.current = true;
    try {
      const [
        dashboard,
        campaignPage,
        runPage,
        contentPage,
        assetPage,
        publishPage,
        jobPage,
        metrics,
      ] = await Promise.all([
        api<DashboardSummary>("/dashboard/summary"),
        apiAllPages<Campaign>(`/campaigns?${query}`, { maxPages: 10 }),
        apiAllPages<WorkflowRun>(`/runs?${query}`, { maxPages: 10 }),
        apiAllPages<Content>(`/contents?${query}`, { maxPages: 10 }),
        apiAllPages<Asset>(`/assets?${query}`, { maxPages: 10 }),
        apiAllPages<PublishJob>(`/publishing/jobs?${query}`, { maxPages: 10 }),
        apiAllPages<QueueJob>(`/jobs?${query}`, { maxPages: 10 }),
        api<MetricsSummary>(
          campaignFilter
            ? `/metrics/summary?campaign_id=${encodeURIComponent(campaignFilter)}`
            : "/metrics/summary",
        ),
      ]);
      setData((current) => ({
        ...current,
        dashboard,
        campaigns: mergeUpdatedRows(current.campaigns, campaignPage.items),
        runs: mergeUpdatedRows(current.runs, runPage.items),
        contents: mergeUpdatedRows(current.contents, contentPage.items),
        assets: mergeUpdatedRows(current.assets, assetPage.items),
        publishes: mergeUpdatedRows(current.publishes, publishPage.items),
        jobs: mergeUpdatedRows(current.jobs, jobPage.items),
        metrics,
      }));
      const truncated = [
        campaignPage,
        runPage,
        contentPage,
        assetPage,
        publishPage,
        jobPage,
      ].some((page) => page.truncated);
      if (truncated) {
        setPageWarning("短时间内更新的数据超过 1000 条，请手动刷新以重新同步。");
      } else {
        lastOperationalRefresh.current = safeRefreshBoundary([
          campaignPage.syncTime,
          runPage.syncTime,
          contentPage.syncTime,
          assetPage.syncTime,
          publishPage.syncTime,
          jobPage.syncTime,
        ]);
      }
      setError("");
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        setSession(null);
      } else {
        setError(messageOf(caught));
      }
    } finally {
      pollInFlight.current = false;
    }
  }, [session, campaignFilter]);

  useEffect(() => {
    async function restore() {
      try {
        const current = await api<Session>("/auth/session");
        setSession(current);
      } catch {
        setSession(null);
      } finally {
        setLoading(false);
      }
    }
    void restore();
  }, []);

  const hasActiveWork = data.runs.some((run) => ACTIVE_RUN_STATUSES.has(run.status))
    || data.assets.some((asset) => ["queued", "generating", "processing"].includes(asset.status));

  useEffect(() => {
    if (!session) return;
    const initial = window.setTimeout(() => void loadData(), 0);
    return () => window.clearTimeout(initial);
  }, [session, loadData]);

  useEffect(() => {
    if (!session) return;
    const timer = window.setInterval(
      () => void pollOperationalData(),
      hasActiveWork ? 2_500 : 15_000,
    );
    return () => {
      window.clearInterval(timer);
    };
  }, [session, pollOperationalData, hasActiveWork]);

  function flash(message: string) {
    setNotice(message);
    window.setTimeout(() => setNotice(""), 3200);
  }

  async function activateWorkspace(workspaceId: string) {
    if (workspaceId === session?.workspace.id) return;
    setRefreshing(true);
    try {
      await api<unknown>(
        `/auth/switch/${workspaceId}`,
        { method: "POST" },
      );
      const current = await api<Session>("/auth/session");
      setData(EMPTY_DATA);
      setCampaignFilter("");
      setView("dashboard");
      setSession(current);
      flash(`已切换到 ${current.workspace.name}`);
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setRefreshing(false);
    }
  }

  async function createAndActivateWorkspace(name: string) {
    await api<unknown>("/auth/workspaces", {
      method: "POST",
      body: { name },
    });
    const current = await api<Session>("/auth/session");
    setData(EMPTY_DATA);
    setCampaignFilter("");
    setView("dashboard");
    setSession(current);
    flash(`工作区 ${current.workspace.name} 已创建`);
  }

  async function signOut() {
    try {
      await api<void>("/auth/logout", { method: "POST" });
    } finally {
      setSession(null);
      setData(EMPTY_DATA);
      setCampaignFilter("");
      setView("dashboard");
    }
  }

  if (loading) {
    return (
      <main className="startup">
        <span className="startup-mark">CF</span>
        <p>正在连接工作台…</p>
      </main>
    );
  }
  if (!session) {
    return <AuthScreen onAuthenticated={setSession} />;
  }

  const effectiveCampaignFilter = data.campaigns.some((campaign) => campaign.id === campaignFilter)
    ? campaignFilter
    : "";
  const scopedCampaigns = effectiveCampaignFilter
    ? data.campaigns.filter((campaign) => campaign.id === effectiveCampaignFilter)
    : data.campaigns;
  const scopedRuns = effectiveCampaignFilter
    ? data.runs.filter((run) => run.campaign_id === effectiveCampaignFilter)
    : data.runs;
  const scopedContents = effectiveCampaignFilter
    ? data.contents.filter((content) => content.campaign_id === effectiveCampaignFilter)
    : data.contents;
  const scopedContentIds = new Set(scopedContents.map((content) => content.id));
  const scopedAssets = effectiveCampaignFilter
    ? data.assets.filter((asset) => asset.content_item_id && scopedContentIds.has(asset.content_item_id))
    : data.assets;
  const scopedPublishes = effectiveCampaignFilter
    ? data.publishes.filter((job) => scopedContentIds.has(job.content_item_id))
    : data.publishes;
  const scopedJobs = effectiveCampaignFilter
    ? data.jobs.filter((job) => job.context.campaign_id === effectiveCampaignFilter)
    : data.jobs;
  const scopedDashboard: DashboardSummary = effectiveCampaignFilter
    ? {
        campaigns: scopedCampaigns.filter((campaign) => campaign.status !== "archived").length,
        runs_active: scopedRuns.filter((run) => ACTIVE_RUN_STATUSES.has(run.status)).length,
        contents_needing_review: scopedContents.filter((content) => content.status === "needs_review").length,
        assets_processing: scopedAssets.filter((asset) => ["pending", "processing"].includes(asset.status)).length,
        publishes_scheduled: scopedPublishes.filter((job) => job.status === "scheduled").length,
        jobs_manual_review: scopedJobs.filter((job) => job.status === "manual_review").length,
        jobs_failed: scopedJobs.filter((job) => job.status === "failed").length,
      }
    : data.dashboard;
  const scopedData: DataState = effectiveCampaignFilter
    ? {
        ...data,
        dashboard: scopedDashboard,
        campaigns: scopedCampaigns,
        runs: scopedRuns,
        contents: scopedContents,
        assets: scopedAssets,
        publishes: scopedPublishes,
        jobs: scopedJobs,
      }
    : data;

  const visibleNav = NAV.filter(
    (item) => item.id !== "admin" || session.role === "admin",
  );
  const viewLabel = visibleNav.find((item) => item.id === view)?.label || "";
  const primaryNav = visibleNav.filter((item) =>
    PRIMARY_NAV_IDS.includes(item.id),
  );
  const advancedNav = visibleNav.filter(
    (item) => !PRIMARY_NAV_IDS.includes(item.id),
  );
  const advancedActive = advancedNav.some((item) => item.id === view);
  const renderSidebarItem = (item: (typeof NAV)[number]) => (
    <button
      key={item.id}
      className={view === item.id ? "active" : ""}
      onClick={() => setView(item.id)}
      aria-current={view === item.id ? "page" : undefined}
      title={item.label}
    >
      <Icon name={item.icon} />
      <span>{item.label}</span>
      {item.id === "review" && scopedDashboard.contents_needing_review ? (
        <b>{scopedDashboard.contents_needing_review}</b>
      ) : null}
    </button>
  );

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand-lockup">
          <span className="brand-symbol">CF</span>
          <span className="brand-name">ContentFlow</span>
        </div>
        <nav aria-label="工作台导航">
          <p className="nav-group-label">核心流程</p>
          {primaryNav.map(renderSidebarItem)}
          {advancedNav.length ? (
            <details
              className="nav-more"
              open={advancedNavOpen || advancedActive}
              onToggle={(event) =>
                setAdvancedNavOpen(event.currentTarget.open)
              }
            >
              <summary>
                <span>资源与系统</span>
                <span aria-hidden="true">＋</span>
              </summary>
              <div>{advancedNav.map(renderSidebarItem)}</div>
            </details>
          ) : null}
        </nav>
        <div className="sidebar-footer">
          <span className="avatar">{session.user.display_name.slice(0, 1)}</span>
          <div>
            <strong>{session.user.display_name}</strong>
            <small>{session.role}</small>
          </div>
          <button
            className="icon-button inverse"
            aria-label="退出登录"
            onClick={() => void signOut()}
          >
            <Icon name="logout" />
          </button>
        </div>
      </aside>
      <div className="app-main">
        <header className="utility-header">
          <div>
            <span className="mobile-product">ContentFlow</span>
            <strong>{viewLabel}</strong>
            <span className="header-divider" />
            <select
              className="workspace-switcher"
              aria-label="切换工作区"
              value={session.workspace.id}
              onChange={(event) => void activateWorkspace(event.target.value)}
              disabled={refreshing}
            >
              {data.workspaces.length ? (
                data.workspaces.map((workspace) => (
                  <option key={workspace.id} value={workspace.id}>
                    {workspace.name} · {ROLE_LABEL[workspace.role]}
                  </option>
                ))
              ) : (
                <option value={session.workspace.id}>{session.workspace.name}</option>
              )}
            </select>
            <span className="header-divider" />
            <select
              className="project-switcher"
              aria-label="按项目筛选当前工作台"
              value={effectiveCampaignFilter}
              onChange={(event) => setCampaignFilter(event.target.value)}
            >
              <option value="">全部项目</option>
              {data.campaigns.map((campaign) => (
                <option key={campaign.id} value={campaign.id}>
                  {projectCode(campaign.id)} · {campaign.name}
                </option>
              ))}
            </select>
          </div>
          <button
            className="icon-button"
            aria-label="刷新数据"
            onClick={() => void loadData()}
            disabled={refreshing}
          >
            <span className={refreshing ? "refresh-spin" : ""}><Icon name="refresh" /></span>
          </button>
        </header>
        <div className="mobile-nav">
          {primaryNav.map((item) => (
            <button
              key={item.id}
              className={view === item.id ? "active" : ""}
              onClick={() => setView(item.id)}
            >
              {item.label}
            </button>
          ))}
          {advancedNav.length ? (
            <select
              className="mobile-more-nav"
              aria-label="更多功能"
              value={advancedActive ? view : ""}
              onChange={(event) => {
                if (event.target.value) setView(event.target.value as View);
              }}
            >
              <option value="">更多</option>
              {advancedNav.map((item) => (
                <option key={item.id} value={item.id}>{item.label}</option>
              ))}
            </select>
          ) : null}
        </div>
        <main className="workspace">
          {notice ? (
            <div className="toast toast-success" role="status" aria-live="polite">
              {notice}
            </div>
          ) : null}
          {error ? (
            <div className="toast toast-error" role="alert">
              <span>{error}</span>
              <button onClick={() => setError("")}>关闭</button>
            </div>
          ) : null}
          {pageWarning ? (
            <div className="pagination-warning" role="status">
              <span>{pageWarning}</span>
              <button onClick={() => setPageWarning("")}>知道了</button>
            </div>
          ) : null}
          <ActiveGenerationStrip runs={scopedRuns} campaigns={data.campaigns} />
          {view === "dashboard" ? (
            <DashboardView data={scopedData} onNavigate={setView} />
          ) : null}
          {view === "campaigns" ? (
            <CampaignsView
              campaigns={scopedCampaigns}
              runs={scopedRuns}
              styleSkills={data.styleSkills}
              mediaCapabilities={data.mediaCapabilities}
              role={session.role}
              onChanged={() => loadData()}
              flash={flash}
            />
          ) : null}
          {view === "review" ? (
            <ReviewView
              campaigns={data.campaigns}
              contents={scopedContents}
              role={session.role}
              onChanged={() => loadData()}
              flash={flash}
            />
          ) : null}
          {view === "assets" ? (
            <AssetsView
              assets={scopedAssets}
              campaigns={data.campaigns}
              contents={scopedContents}
              mediaCapabilities={data.mediaCapabilities}
              role={session.role}
              onChanged={() => loadData()}
              flash={flash}
            />
          ) : null}
          {view === "publishing" ? (
            <PublishingView
              publishes={scopedPublishes}
              campaigns={data.campaigns}
              contents={scopedContents}
              channels={data.channels}
              role={session.role}
              onNavigate={setView}
              onChanged={() => loadData()}
              flash={flash}
            />
          ) : null}
          {view === "knowledge" ? (
            <KnowledgeView
              documents={data.knowledge}
              role={session.role}
              onChanged={() => loadData()}
              flash={flash}
            />
          ) : null}
          {view === "channels" ? (
            <ChannelsView
              channels={data.channels}
              role={session.role}
              onChanged={() => loadData()}
              flash={flash}
            />
          ) : null}
          {view === "metrics" ? (
            <MetricsView
              data={data.metrics}
              publishes={scopedPublishes}
              campaigns={data.campaigns}
              contents={scopedContents}
              channels={data.channels}
              role={session.role}
              onChanged={() => loadData()}
              flash={flash}
            />
          ) : null}
          {view === "jobs" ? (
            <JobsView
              jobs={scopedJobs}
              role={session.role}
              onNavigate={setView}
              onChanged={() => loadData()}
              flash={flash}
            />
          ) : null}
          {view === "admin" && session.role === "admin" ? (
            <AdministrationView
              currentSession={session}
              workspaces={data.workspaces}
              members={data.members}
              auditLogs={data.auditLogs}
              storageUsage={data.storageUsage}
              storageAttention={data.storageAttention}
              promptGovernance={data.promptGovernance}
              promptEval={data.promptEval}
              onWorkspaceCreated={createAndActivateWorkspace}
              onChanged={() => loadData()}
              flash={flash}
            />
          ) : null}
        </main>
      </div>
    </div>
  );
}

function PageHeading({
  eyebrow,
  title,
  description,
  action,
}: {
  eyebrow: string;
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <header className="page-heading">
      <div>
        <p className="eyebrow">{eyebrow}</p>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {action ? <div className="page-action">{action}</div> : null}
    </header>
  );
}

function DashboardView({
  data,
  onNavigate,
}: {
  data: DataState;
  onNavigate: (view: View) => void;
}) {
  const metrics = [
    ["进行中的活动", data.dashboard.campaigns, "campaigns" as View],
    ["待人工审核", data.dashboard.contents_needing_review, "review" as View],
    ["素材处理中", data.dashboard.assets_processing, "assets" as View],
    ["已安排发布", data.dashboard.publishes_scheduled, "publishing" as View],
  ] as const;
  const hasCampaign = data.campaigns.length > 0;
  const hasContent = data.contents.length > 0;
  const hasApproved = data.contents.some((item) => item.status === "approved");
  const hasReadyAsset = data.assets.some((item) => item.status === "ready");
  const needsAssetAttention = data.assets.some((item) =>
    ["planned", "processing", "awaiting_upload", "failed"].includes(item.status),
  );
  const hasDelivered = data.publishes.some((item) =>
    [
      "draft_created",
      "submitted",
      "published",
      "exported",
      "script_published",
    ].includes(item.status),
  );
  const nextAction = !hasCampaign
    ? { view: "campaigns" as View, label: "创建第一个内容活动", copy: "先说明产品、受众和平台，系统会据此生成内容。" }
    : data.dashboard.contents_needing_review > 0
      ? { view: "review" as View, label: "审核待处理内容", copy: `有 ${data.dashboard.contents_needing_review} 篇内容等待你确认事实、语气和风险。` }
      : hasApproved && (!hasReadyAsset || needsAssetAttention)
        ? { view: "assets" as View, label: "准备发布素材", copy: "内容已通过审核，补齐真实封面后才能进入发布。" }
        : hasApproved
          ? { view: "publishing" as View, label: "立即发布或设置时间", copy: "内容与素材已就绪，可以选择立即执行或定时发布。" }
          : { view: "campaigns" as View, label: "继续生成内容", copy: "活动已经建立，开始一次新的内容生成。" };
  const workflowSteps = [
    { label: "创建内容", detail: "活动与生成", done: hasCampaign && hasContent, view: "campaigns" as View },
    { label: "审核内容", detail: "人工确认", done: hasApproved, view: "review" as View },
    { label: "准备素材", detail: "封面与视频", done: hasReadyAsset, view: "assets" as View },
    { label: "发布", detail: "立即或定时", done: hasDelivered, view: "publishing" as View },
  ];
  const campaignMap = Object.fromEntries(
    data.campaigns.map((campaign) => [campaign.id, campaign]),
  );

  return (
    <>
      <PageHeading
        eyebrow="运营总览"
        title="今天从哪里继续？"
        description="按照创建、审核、素材、发布四步完成内容投放；高级配置已收纳到资源与系统。"
      />
      <section className="workflow-guide" aria-label="内容发布主流程">
        <div className="workflow-next">
          <p className="eyebrow">建议下一步</p>
          <h2>{nextAction.label}</h2>
          <p>{nextAction.copy}</p>
          <Button onClick={() => onNavigate(nextAction.view)}>
            继续处理 <span aria-hidden="true">→</span>
          </Button>
        </div>
        <ol>
          {workflowSteps.map((step, index) => (
            <li
              key={step.view}
              className={
                step.done
                  ? "complete"
                  : step.view === nextAction.view
                    ? "current"
                    : "upcoming"
              }
              aria-current={step.view === nextAction.view ? "step" : undefined}
            >
              <button onClick={() => onNavigate(step.view)}>
                <span>{step.done ? "✓" : String(index + 1).padStart(2, "0")}</span>
                <strong>{step.label}</strong>
                <small>{step.done ? "已完成" : step.detail}</small>
              </button>
            </li>
          ))}
        </ol>
      </section>
      <section className="metric-grid compact-metrics" aria-label="关键指标">
        {metrics.map(([label, value, target], index) => (
          <button key={label} onClick={() => onNavigate(target)}>
            <span>0{index + 1}</span>
            <strong>{value}</strong>
            <small>{label}</small>
          </button>
        ))}
      </section>
      <div className="dashboard-grid">
        <section className="panel span-2">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">需要处理</p>
              <h2>审核队列</h2>
            </div>
            <button className="text-link" onClick={() => onNavigate("review")}>
              查看全部 →
            </button>
          </div>
          {data.contents.filter((item) => item.status === "needs_review").length ? (
            <div className="record-list">
              {data.contents
                .filter((item) => item.status === "needs_review")
                .slice(0, 5)
                .map((item) => (
                  <div className="record-row" key={item.id}>
                    <div className="platform-mark">
                      {(PLATFORM[item.platform] || item.platform).slice(0, 1)}
                    </div>
                    <div className="record-main">
                      <ProjectIdentity
                        campaign={campaignMap[item.campaign_id]}
                        fallbackCampaignId={item.campaign_id}
                        compact
                      />
                      <strong>{item.title}</strong>
                      <small>
                        {PLATFORM[item.platform]} · 版本 {item.version}
                      </small>
                    </div>
                    <StatusBadge value={item.status} />
                  </div>
                ))}
            </div>
          ) : (
            <EmptyState title="没有待审核内容" description="新的生成结果会出现在这里。" />
          )}
        </section>
        <section className="panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">系统健康</p>
              <h2>后台任务</h2>
            </div>
          </div>
          <div className="health-list">
            <div><span>运行中</span><strong>{data.dashboard.runs_active}</strong></div>
            <div><span>待人工核对</span><strong className={data.dashboard.jobs_manual_review ? "danger-text" : ""}>{data.dashboard.jobs_manual_review}</strong></div>
            <div><span>失败任务</span><strong className={data.dashboard.jobs_failed ? "danger-text" : ""}>{data.dashboard.jobs_failed}</strong></div>
            <div><span>发布队列</span><strong>{data.dashboard.publishes_scheduled}</strong></div>
          </div>
          <button className="button button-secondary full" onClick={() => onNavigate("jobs")}>
            打开任务队列
          </button>
        </section>
        <section className="panel span-3">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">最近活动</p>
              <h2>营销活动进度</h2>
            </div>
          </div>
          <DataTable
            headers={["项目", "产品", "平台", "状态", "更新时间"]}
            rows={data.campaigns.slice(0, 6).map((campaign) => [
              <ProjectIdentity key="project" campaign={campaign} compact />,
              campaign.product_name,
              campaign.platforms.map((item) => PLATFORM[item]).join(" / "),
              <StatusBadge key="status" value={campaign.status} />,
              formatDate(campaign.updated_at),
            ])}
            empty="还没有营销活动"
          />
        </section>
      </div>
    </>
  );
}

function RunEvidence({ run }: { run: WorkflowRun }) {
  const provenance = run.result_json.ai_provenance;
  const source = provenance?.provider === "mock"
    ? "离线 Mock（非外部模型）"
    : provenance?.provider || run.provider || "等待执行";
  const usage = provenance?.token_usage;
  const usageLabel = usage?.source === "provider_reported"
    ? `${usage.total_tokens ?? "—"} Tokens（平台返回）`
    : usage?.source === "partial"
      ? `${usage.total_tokens ?? "部分"} Tokens（部分平台返回）`
      : "平台未返回，系统未估算";
  return (
    <article className="run-record">
      <div className="run-record-heading">
        <div>
          <strong>{formatDateTime(run.created_at)} 生成批次</strong>
          <span>追踪号 {run.trace_id.slice(0, 12)}</span>
        </div>
        <StatusBadge value={run.status} />
      </div>
      <GenerationProgress run={run} compact />
      <div className="run-evidence-grid">
        <span><small>生成来源</small><b>{source}</b></span>
        <span><small>模型</small><b>{provenance?.model || "等待执行"}</b></span>
        <span><small>提示词版本</small><b>{provenance?.prompt_set_version || "等待执行"}</b></span>
        <span><small>AI 调用</small><b>{provenance ? `${provenance.invocation_count} 次` : "等待执行"}</b></span>
        <span><small>Token 记录</small><b>{usageLabel}</b></span>
      </div>
      {run.error ? <p className="run-error" role="alert">最近错误：{run.error}</p> : null}
    </article>
  );
}

function CampaignsView({
  campaigns,
  runs,
  styleSkills,
  mediaCapabilities,
  role,
  onChanged,
  flash,
}: {
  campaigns: Campaign[];
  runs: WorkflowRun[];
  styleSkills: StyleSkill[];
  mediaCapabilities: MediaCapabilities;
  role: string;
  onChanged: () => Promise<void> | void;
  flash: (message: string) => void;
}) {
  const [editingCampaign, setEditingCampaign] = useState<Campaign | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [showSkillInstaller, setShowSkillInstaller] = useState(false);
  const [busyId, setBusyId] = useState("");
  const [error, setError] = useState("");
  const [expandedCampaignId, setExpandedCampaignId] = useState("");
  const [imageSource, setImageSource] = useState<MediaSource | "">("");

  const canEdit = roleAtLeast(role, "editor");

  function closeForm() {
    setShowForm(false);
    setEditingCampaign(null);
    setImageSource("");
  }

  function openCreate() {
    setEditingCampaign(null);
    setImageSource("");
    setShowForm(true);
  }

  function openEdit(campaign: Campaign) {
    setEditingCampaign(campaign);
    setImageSource(campaign.brief.image_source || "manual");
    setShowForm(true);
  }

  async function saveCampaign(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusyId("save");
    setError("");
    const form = new FormData(event.currentTarget);
    const payload = {
      name: form.get("name"),
      product_name: form.get("product_name"),
      objective: form.get("objective"),
      audience: form.get("audience"),
      platforms: form.getAll("platforms"),
      tone: form.get("tone"),
      city: form.get("city"),
      must_include: String(form.get("must_include") || "")
        .split(/[，,\n]/)
        .map((item) => item.trim())
        .filter(Boolean),
      forbidden_phrases: String(form.get("forbidden_phrases") || "")
        .split(/[，,\n]/)
        .map((item) => item.trim())
        .filter(Boolean),
      call_to_action: form.get("call_to_action"),
      product_facts: String(form.get("product_facts") || "")
        .split(/[，,\n]/)
        .map((item) => item.trim())
        .filter(Boolean),
      style_skill_id: form.get("style_skill_id"),
      style_notes: form.get("style_notes"),
      quality_profile: form.get("quality_profile"),
      image_source: form.get("image_source"),
      image_search_query: form.get("image_search_query"),
    };
    try {
      await api(
        editingCampaign ? `/campaigns/${editingCampaign.id}` : "/campaigns",
        {
          method: editingCampaign ? "PATCH" : "POST",
          body: payload,
        },
      );
      flash(editingCampaign ? "活动 Brief 已更新" : "营销活动已创建");
      closeForm();
      await onChanged();
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusyId("");
    }
  }

  async function installStyleSkill(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusyId("install-skill");
    setError("");
    const form = new FormData(event.currentTarget);
    try {
      const manifest = JSON.parse(String(form.get("manifest") || ""));
      await api("/style-skills", {
        method: "POST",
        body: { manifest },
      });
      flash("风格 Skill 已安装并完成完整性哈希记录");
      setShowSkillInstaller(false);
      await onChanged();
    } catch (caught) {
      setError(
        caught instanceof SyntaxError
          ? "Manifest 不是有效 JSON"
          : messageOf(caught),
      );
    } finally {
      setBusyId("");
    }
  }

  async function updateStatus(campaign: Campaign, status: "active" | "archived") {
    if (
      status === "archived" &&
      !window.confirm(`归档“${campaign.name}”？归档后不会再生成新内容。`)
    ) {
      return;
    }
    setBusyId(`status-${campaign.id}`);
    setError("");
    try {
      await api(`/campaigns/${campaign.id}`, {
        method: "PATCH",
        body: { status },
      });
      if (editingCampaign?.id === campaign.id) closeForm();
      flash(status === "archived" ? "活动已归档" : "活动已恢复");
      await onChanged();
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusyId("");
    }
  }

  function toggleRuns(campaign: Campaign) {
    setExpandedCampaignId((current) => current === campaign.id ? "" : campaign.id);
  }

  function campaignRuns(campaignId: string) {
    return runs.filter((runItem) => runItem.campaign_id === campaignId).slice(0, 5);
  }

  function activeCampaignRun(campaignId: string) {
    return campaignRuns(campaignId).find((runItem) => ACTIVE_RUN_STATUSES.has(runItem.status));
  }

  async function run(campaign: Campaign) {
    setBusyId(`run-${campaign.id}`);
    setError("");
    try {
      await api<WorkflowRun>(`/campaigns/${campaign.id}/runs`, {
        method: "POST",
        body: {},
      });
      flash("内容任务已入队；页面会持续显示真实生成阶段");
      setExpandedCampaignId(campaign.id);
      await onChanged();
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusyId("");
    }
  }


  return (
    <>
      <PageHeading
        eyebrow="Campaign"
        title="营销活动"
        description="用结构化 Brief 管理目标、人群、平台约束与生成批次。"
        action={canEdit ? (
          <div className="page-action-group">
            <Button
              kind="ghost"
              onClick={() => setShowSkillInstaller((value) => !value)}
            >
              {showSkillInstaller ? "收起 Skill" : "安装风格 Skill"}
            </Button>
            <Button onClick={showForm ? closeForm : openCreate}>
              <Icon name="plus" />
              {showForm ? "收起表单" : "新建活动"}
            </Button>
          </div>
        ) : undefined}
      />
      {error ? <p className="inline-error">{error}</p> : null}
      {!canEdit ? (
        <p className="permission-note">当前为只读权限，可查看活动与生成状态。</p>
      ) : null}
      {showSkillInstaller && canEdit ? (
        <section className="panel form-panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">Declarative Style Skill</p>
              <h2>安装纯声明式风格包</h2>
            </div>
          </div>
          <form className="stack-form" onSubmit={installStyleSkill}>
            <p className="form-note">
              风格包只保存写作规则、平台差异和示例，不执行代码或访问外部工具。
              slug 与版本组合不可覆盖；更新风格请安装新版本。
            </p>
            <label>Manifest JSON
              <textarea
                name="manifest"
                className="skill-manifest"
                required
                defaultValue={JSON.stringify(STYLE_SKILL_EXAMPLE, null, 2)}
              />
            </label>
            <div className="form-actions">
              <Button type="submit" busy={busyId === "install-skill"}>
                校验并安装
              </Button>
              <Button
                type="button"
                kind="ghost"
                onClick={() => setShowSkillInstaller(false)}
              >
                取消
              </Button>
            </div>
          </form>
        </section>
      ) : null}
      {showForm ? (
        <section className="panel form-panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">{editingCampaign ? "维护 Brief" : "新建 Brief"}</p>
              <h2>{editingCampaign ? `编辑 ${editingCampaign.name}` : "活动基础信息"}</h2>
            </div>
          </div>
          <form
            className="stack-form"
            onSubmit={saveCampaign}
            key={editingCampaign?.id || "create"}
          >
            <div className="form-grid">
              <label>活动名称<input name="name" required defaultValue={editingCampaign?.name} placeholder="北京周末出行内容计划" /></label>
              <label>产品名称<input name="product_name" required defaultValue={editingCampaign?.product_name} placeholder="星图地图" /></label>
            </div>
            <label>活动目标<textarea name="objective" required minLength={5} defaultValue={editingCampaign?.objective} placeholder="希望用户完成什么动作，解决什么问题" /></label>
            <label>目标人群<textarea name="audience" required minLength={3} defaultValue={editingCampaign?.audience} placeholder="核心人群、场景和需求" /></label>
            <fieldset>
              <legend>投放平台</legend>
              <div className="checkbox-row">
                {Object.entries(PLATFORM).map(([value, label]) => (
                  <label key={value}>
                    <input
                      type="checkbox"
                      name="platforms"
                      value={value}
                      defaultChecked={
                        editingCampaign
                          ? editingCampaign.platforms.includes(value)
                          : true
                      }
                    />
                    {label}
                  </label>
                ))}
              </div>
            </fieldset>
            <div className="form-grid">
              <label>内容语气<input name="tone" defaultValue={editingCampaign?.brief.tone || "清晰、可信、不夸大承诺"} /></label>
              <label>主要城市<input name="city" defaultValue={editingCampaign?.brief.city || "北京"} /></label>
            </div>
            <div className="form-grid">
              <label>写作风格 Skill
                <select
                  name="style_skill_id"
                  defaultValue={editingCampaign?.brief.style_skill_id || "builtin:editorial"}
                >
                  {styleSkills.map((skill) => (
                    <option
                      key={skill.id}
                      value={skill.id}
                      disabled={skill.status !== "enabled"}
                    >
                      {skill.manifest.name} · v{skill.manifest.version}
                      {skill.source === "workspace" ? " · 已安装" : ""}
                    </option>
                  ))}
                </select>
                <small>运行时会冻结版本与 SHA-256，不受之后修改影响</small>
              </label>
              <label>生成深度
                <select
                  name="quality_profile"
                  defaultValue={editingCampaign?.brief.quality_profile || "deep"}
                >
                  <option value="deep">深度创作 · 审核不达标自动改写一次</option>
                  <option value="standard">标准创作 · 生成后只评审不改写</option>
                </select>
              </label>
            </div>
            <label>本次风格补充
              <textarea
                name="style_notes"
                defaultValue={editingCampaign?.brief.style_notes || ""}
                placeholder="例如：像长期居住在北京的编辑，克制、有细节，不使用网络热梗"
              />
              <small>这是本活动补充规则，不会修改已安装 Skill</small>
            </label>
            <fieldset className="media-source-fieldset">
              <legend>封面怎么准备</legend>
              <p className="field-help">
                请选择本活动的默认方式。这不是强制上传：内容审核通过后，仍可在素材中心针对单条封面切换路线。
              </p>
              <div className="media-source-grid">
                {([
                  {
                    value: "manual",
                    title: "人工上传",
                    description: "使用你有权发布的品牌图、实拍图或设计稿。",
                    badge: "随时可用",
                  },
                  {
                    value: "generate",
                    title: "AI 生成",
                    description: "根据内容 Agent 产出的视觉提示词生成封面。",
                    badge: mediaCapabilities.image_generation_available ? "已配置" : "当前未配置",
                  },
                  {
                    value: "search",
                    title: "开放图库",
                    description: "检索候选图，人工核验作者、许可和署名后选用。",
                    badge: mediaCapabilities.image_search_available ? "已配置" : "当前未配置",
                  },
                  {
                    value: "hybrid",
                    title: "图库 + AI",
                    description: "同时准备两类候选，最后由你明确选定。",
                    badge: mediaCapabilities.image_generation_available && mediaCapabilities.image_search_available
                      ? "已配置"
                      : "部分未配置",
                  },
                ] as const).map((option) => (
                  <label
                    className={`media-source-option ${imageSource === option.value ? "selected" : ""}`}
                    key={option.value}
                  >
                    <input
                      type="radio"
                      name="image_source"
                      value={option.value}
                      checked={imageSource === option.value}
                      onChange={() => setImageSource(option.value)}
                      required
                    />
                    <span>
                      <strong>{option.title}</strong>
                      <small>{option.description}</small>
                    </span>
                    <b>{option.badge}</b>
                  </label>
                ))}
              </div>
              {!imageSource ? (
                <small className="field-prompt">请选择一种默认封面来源后再保存活动。</small>
              ) : null}
            </fieldset>
            <label>图片搜索词（选择开放图库时使用，可选）
              <input
                name="image_search_query"
                defaultValue={editingCampaign?.brief.image_search_query || ""}
                placeholder="留空则由内容 Agent 结合主题自动生成搜索词"
              />
            </label>
            <label>产品事实<input name="product_facts" defaultValue={editingCampaign?.brief.product_facts?.join("，")} placeholder="已确认的功能、服务范围或业务事实" /><small>使用逗号分隔，生成和审核只以这些事实为依据</small></label>
            <div className="form-grid">
              <label>必含信息<input name="must_include" defaultValue={editingCampaign?.brief.must_include?.join("，")} placeholder="路线确认，候选地点" /><small>使用逗号分隔</small></label>
              <label>禁用表达<input name="forbidden_phrases" defaultValue={editingCampaign?.brief.forbidden_phrases?.join("，")} placeholder="百分百准确，绝对优惠" /><small>使用逗号分隔</small></label>
            </div>
            <label>行动引导<input name="call_to_action" defaultValue={editingCampaign?.brief.call_to_action} placeholder="打开星图地图确认路线" /></label>
            <div className="form-actions">
              <Button type="submit" busy={busyId === "save"}>
                {editingCampaign ? "保存修改" : "保存活动"}
              </Button>
              <Button type="button" kind="ghost" onClick={closeForm}>取消</Button>
            </div>
          </form>
        </section>
      ) : null}
      <section className="panel">
        {campaigns.length ? (
          <div className="campaign-list">
            {campaigns.map((campaign) => (
              <article key={campaign.id} className="campaign-row">
                <div className="campaign-index"><span>{String(campaigns.indexOf(campaign) + 1).padStart(2, "0")}</span><strong translate="no">{projectCode(campaign.id)}</strong></div>
                <div className="campaign-copy">
                  <div className="row-title">
                    <h2>{campaign.name}</h2>
                    <StatusBadge value={campaign.status} />
                  </div>
                  <p>{campaign.objective}</p>
                  <div className="meta-row">
                    <span>{campaign.product_name}</span>
                    <span>{campaign.platforms.map((item) => PLATFORM[item]).join(" / ")}</span>
                    <span>
                      {styleSkills.find((skill) => (
                        skill.id === (campaign.brief.style_skill_id || "builtin:editorial")
                      ))?.manifest.name || "专业社媒编辑"}
                    </span>
                    <span>{campaign.brief.quality_profile === "standard" ? "标准创作" : "深度创作"}</span>
                    <span>{formatDate(campaign.updated_at)}</span>
                  </div>
                  {activeCampaignRun(campaign.id) ? (
                    <GenerationProgress run={activeCampaignRun(campaign.id)!} />
                  ) : null}
                  {expandedCampaignId === campaign.id ? (
                    <section className="run-history" aria-label={`${campaign.name} 的生成记录`}>
                      <div className="run-history-heading">
                        <strong>最近生成记录</strong>
                        <span>最多展示 5 个批次</span>
                      </div>
                      {campaignRuns(campaign.id).length ? (
                        campaignRuns(campaign.id).map((runItem) => (
                          <RunEvidence key={runItem.id} run={runItem} />
                        ))
                      ) : (
                        <p className="run-history-empty">还没有生成记录。</p>
                      )}
                    </section>
                  ) : null}
                </div>
                <div className="campaign-actions">
                  {canEdit ? (
                    <>
                      <Button
                        kind="secondary"
                        busy={busyId === `run-${campaign.id}` || Boolean(activeCampaignRun(campaign.id))}
                        onClick={() => void run(campaign)}
                        disabled={campaign.status === "archived" || Boolean(activeCampaignRun(campaign.id))}
                      >
                        {activeCampaignRun(campaign.id) ? "生成进行中…" : "生成内容"}
                      </Button>
                      <button type="button" onClick={() => openEdit(campaign)}>编辑 Brief</button>
                      <button
                        type="button"
                        disabled={busyId === `status-${campaign.id}`}
                        onClick={() =>
                          void updateStatus(
                            campaign,
                            campaign.status === "archived" ? "active" : "archived",
                          )
                        }
                      >
                        {campaign.status === "archived" ? "恢复活动" : "归档"}
                      </button>
                    </>
                  ) : null}
                  <button
                    type="button"
                    onClick={() => toggleRuns(campaign)}
                  >
                    {expandedCampaignId === campaign.id ? "收起记录" : "生成记录"}
                  </button>
                </div>
              </article>
            ))}
          </div>
        ) : (
          <EmptyState title="还没有活动" description="先创建一个结构化营销 Brief。" />
        )}
      </section>
    </>
  );
}

function ReviewView({
  campaigns,
  contents,
  role,
  onChanged,
  flash,
}: {
  campaigns: Campaign[];
  contents: Content[];
  role: string;
  onChanged: () => Promise<void> | void;
  flash: (message: string) => void;
}) {
  const reviewable = contents.filter((item) =>
    ["needs_review", "blocked"].includes(item.status),
  );
  const campaignMap = Object.fromEntries(
    campaigns.map((campaign) => [campaign.id, campaign]),
  );
  const [showAll, setShowAll] = useState(false);
  const visibleContents = showAll ? contents : reviewable;
  const [selectedId, setSelectedId] = useState(reviewable[0]?.id || "");
  const selected =
    visibleContents.find((item) => item.id === selectedId) ||
    visibleContents[0];
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [revisionState, setRevisionState] = useState<{
    key: string;
    items: ContentRevision[];
  }>({ key: "", items: [] });
  const canEdit = roleAtLeast(role, "editor");
  const canReview = roleAtLeast(role, "reviewer");
  const needsDecision = Boolean(
    selected &&
      ["needs_review", "blocked"].includes(selected.status),
  );
  const revisionKey = selected ? `${selected.id}:${selected.version}` : "";
  const revisions =
    revisionState.key === revisionKey ? revisionState.items : [];
  const revisionsLoading = Boolean(revisionKey && revisionState.key !== revisionKey);
  const modelReview = selected?.review_json.model_review
    && typeof selected.review_json.model_review === "object"
    ? selected.review_json.model_review as Record<string, unknown>
    : {};
  const qualityScores = modelReview.scores
    && typeof modelReview.scores === "object"
    ? Object.entries(modelReview.scores as Record<string, unknown>)
    : [];
  const styleEvidence = selected?.generation_json.style_skill
    && typeof selected.generation_json.style_skill === "object"
    ? selected.generation_json.style_skill as Record<string, unknown>
    : {};

  useEffect(() => {
    if (!selected?.id) return;
    let active = true;
    apiAllPages<ContentRevision>(`/contents/${selected.id}/revisions`)
      .then((page) => {
        if (active) {
          setRevisionState({
            key: `${selected.id}:${selected.version}`,
            items: page.items,
          });
          if (page.truncated) {
            setError("内容修订已达到安全加载上限 2000 条，请联系管理员导出完整历史。");
          }
        }
      })
      .catch((caught) => {
        if (active) setError(messageOf(caught));
      });
    return () => {
      active = false;
    };
  }, [selected?.id, selected?.version]);

  async function decide(decision: "approve" | "reject") {
    if (!selected) return;
    setBusy(decision);
    setError("");
    try {
      const reason =
        decision === "approve"
          ? "人工确认事实、表达与平台格式"
          : window.prompt("请输入驳回原因") || "";
      if (decision === "reject" && !reason) return;
      await api(`/contents/${selected.id}/review`, {
        method: "POST",
        body: { decision, reason, expected_version: selected.version },
      });
      flash(decision === "approve" ? "内容已通过，素材已进入准备流程" : "内容已驳回");
      setSelectedId("");
      await onChanged();
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy("");
    }
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected) return;
    setBusy("save");
    const form = new FormData(event.currentTarget);
    try {
      const layoutJson = JSON.parse(String(form.get("layout_json") || "{}"));
      if (
        !layoutJson ||
        typeof layoutJson !== "object" ||
        Array.isArray(layoutJson)
      ) {
        throw new Error("平台排版必须是 JSON 对象");
      }
      await api(`/contents/${selected.id}`, {
        method: "PATCH",
        body: {
          expected_version: selected.version,
          title: form.get("title"),
          body: form.get("body"),
          hashtags: String(form.get("hashtags") || "")
            .split(/[，,\s]/)
            .map((item) => item.replace(/^#/, "").trim())
            .filter(Boolean),
          call_to_action: form.get("call_to_action"),
          layout_json: layoutJson,
        },
      });
      flash("内容已保存为新版本，需要重新审核");
      await onChanged();
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy("");
    }
  }


  return (
    <>
      <PageHeading
        eyebrow="Human in the loop"
        title="内容审核"
        description="规则校验只处理底线；事实、语气和发布风险必须由人确认。"
        action={
          <Button
            kind="secondary"
            onClick={() => {
              setShowAll((value) => !value);
              setSelectedId("");
            }}
          >
            {showAll ? `只看待处理（${reviewable.length}）` : `查看全部内容（${contents.length}）`}
          </Button>
        }
      />
      {error ? <p className="inline-error">{error}</p> : null}
      {!canEdit ? (
        <p className="permission-note">当前为只读权限，可查看内容、校验结果与版本历史。</p>
      ) : canEdit && !canReview ? (
        <p className="permission-note">当前可编辑内容；最终通过或驳回需要审核人员处理。</p>
      ) : null}
      {selected ? (
        <div className="review-layout">
          <section className="panel review-queue">
            <div className="panel-heading">
              <div>
                <p className="eyebrow">{showAll ? "内容库" : "审核队列"}</p>
                <h2>{showAll ? `${visibleContents.length} 条内容` : `${reviewable.length} 条待处理`}</h2>
              </div>
            </div>
            {visibleContents.map((item) => (
              <button
                key={item.id}
                className={selected.id === item.id ? "active" : ""}
                onClick={() => setSelectedId(item.id)}
              >
                <span className="platform-mark">{(PLATFORM[item.platform] || item.platform).slice(0, 1)}</span>
                <span className="review-item-copy">
                  <ProjectIdentity campaign={campaignMap[item.campaign_id]} compact />
                  <strong className="review-content-title">{item.title}</strong>
                  <small>{PLATFORM[item.platform]} · v{item.version}</small>
                </span>
                <StatusBadge value={item.status} />
              </button>
            ))}
          </section>
          <section className="panel review-editor">
            <div className="panel-heading">
              <div>
                <ProjectIdentity campaign={campaignMap[selected.campaign_id]} contentTitle={selected.title} compact />
                <p className="eyebrow">{PLATFORM[selected.platform]} · v{selected.version}</p>
                <h2>编辑与确认</h2>
              </div>
              <StatusBadge value={selected.status} />
            </div>
            <form className="stack-form" onSubmit={save} key={`${selected.id}-${selected.version}`}>
              <label>标题<input name="title" defaultValue={selected.title} disabled={!canEdit} /></label>
              <label>正文<textarea className="content-textarea" name="body" defaultValue={selected.body} disabled={!canEdit} /></label>
              <div className="form-grid">
                <label>话题标签<input name="hashtags" defaultValue={selected.hashtags.join("，")} disabled={!canEdit} /></label>
                <label>行动引导<input name="call_to_action" defaultValue={selected.call_to_action} disabled={!canEdit} /></label>
              </div>
              <label>
                平台排版 / 镜头脚本
                <textarea
                  className="layout-textarea"
                  name="layout_json"
                  defaultValue={JSON.stringify(selected.layout_json, null, 2)}
                  disabled={!canEdit}
                />
                <small>结构会随内容版本保存，并用于短视频分镜或人工投放包。</small>
              </label>
              <div className="review-summary quality-summary">
                <div className="quality-heading">
                  <div>
                    <strong>内容 Agent 质量评审</strong>
                    <span>
                      风格 {String(styleEvidence.slug || "editorial")} ·
                      {Number(selected.generation_json.revision_count || 0)} 次定向改写
                    </span>
                  </div>
                  <b>{Number(selected.review_json.quality_score || 0).toFixed(1)} / 10</b>
                </div>
                <div className="quality-score-grid">
                  {qualityScores.map(([name, value]) => (
                    <span key={name}>
                      <small>{name}</small>
                      <strong>{Number(value || 0).toFixed(1)}</strong>
                    </span>
                  ))}
                </div>
                {Array.isArray(modelReview.issues) && modelReview.issues.length ? (
                  <ul>
                    {modelReview.issues.map((issue) => (
                      <li key={String(issue)}>{String(issue)}</li>
                    ))}
                  </ul>
                ) : null}
                <details>
                  <summary>查看完整自动校验 JSON</summary>
                  <pre>{JSON.stringify(selected.review_json, null, 2)}</pre>
                </details>
              </div>
              <section className="revision-history" aria-label="内容版本历史">
                <div className="revision-heading">
                  <strong>版本历史</strong>
                  <span>{revisionsLoading ? "加载中…" : `${revisions.length} 个版本`}</span>
                </div>
                {revisions.map((revision) => (
                  <details key={revision.id}>
                    <summary>
                      <span>v{revision.version} · {revision.change_reason === "human_edit" ? "人工修改" : "模型生成"}</span>
                      <time>{formatDate(revision.created_at)}</time>
                    </summary>
                    <div>
                      <strong>{revision.title}</strong>
                      <p>{revision.body}</p>
                      {Object.keys(revision.layout_json).length ? (
                        <pre>{JSON.stringify(revision.layout_json, null, 2)}</pre>
                      ) : null}
                      {revision.hashtags.length ? <small>#{revision.hashtags.join(" #")}</small> : null}
                    </div>
                  </details>
                ))}
                {!revisionsLoading && !revisions.length ? <p>暂无历史版本。</p> : null}
              </section>
              {canEdit || canReview ? (
                <div className="form-actions split-actions">
                  {canEdit ? <Button type="submit" kind="ghost" busy={busy === "save"}>保存修改</Button> : <span />}
                  {canReview && needsDecision ? (
                    <div>
                      <Button type="button" kind="danger" busy={busy === "reject"} onClick={() => void decide("reject")}>驳回</Button>
                      <Button type="button" busy={busy === "approve"} onClick={() => void decide("approve")}>确认通过</Button>
                    </div>
                  ) : null}
                </div>
              ) : null}
            </form>
          </section>
        </div>
      ) : (
        <section className="panel">
          <EmptyState
            title={showAll ? "还没有生成内容" : "审核队列已清空"}
            description={
              showAll
                ? "完成一次营销活动生成后，内容和版本会出现在这里。"
                : "新的工作流结果会自动进入这里；已通过内容可在“查看全部内容”中回看。"
            }
          />
        </section>
      )}
    </>
  );
}

function AssetsView({
  campaigns,
  assets,
  contents,
  mediaCapabilities,
  role,
  onChanged,
  flash,
}: {
  campaigns: Campaign[];
  assets: Asset[];
  contents: Content[];
  mediaCapabilities: MediaCapabilities;
  role: string;
  onChanged: () => Promise<void> | void;
  flash: (message: string) => void;
}) {
  const [error, setError] = useState("");
  const [uploading, setUploading] = useState(false);
  const [showUpload, setShowUpload] = useState(false);
  const [uploadTargetId, setUploadTargetId] = useState("");
  const [uploadKind, setUploadKind] = useState("image");
  const [sourceBusyId, setSourceBusyId] = useState("");
  const canEdit = roleAtLeast(role, "editor");
  const contentMap = useMemo(
    () => Object.fromEntries(contents.map((item) => [item.id, item.title])),
    [contents],
  );
  const contentById = useMemo(
    () => Object.fromEntries(contents.map((item) => [item.id, item])),
    [contents],
  );
  const campaignMap = useMemo(
    () => Object.fromEntries(campaigns.map((campaign) => [campaign.id, campaign])),
    [campaigns],
  );
  const contentVersionMap = useMemo(
    () => Object.fromEntries(contents.map((item) => [item.id, item.version])),
    [contents],
  );
  const uploadTargets = useMemo(
    () => assets.filter((asset) => (
      ["awaiting_upload", "planned", "failed"].includes(asset.status)
      && Number(asset.metadata_json.content_version || 1)
        === contentVersionMap[asset.content_item_id || ""]
    )),
    [assets, contentVersionMap],
  );
  const uploadTarget = uploadTargets.find((asset) => asset.id === uploadTargetId);
  const systemProcessing = assets.filter((asset) =>
    ["queued", "generating", "processing"].includes(asset.status),
  );
  const awaitingUpload = assets.filter((asset) => asset.status === "awaiting_upload");
  const awaitingSelection = assets.filter((asset) => asset.status === "awaiting_selection");
  const sourceChoiceAssets = assets.filter((asset) => (
    asset.kind === "image"
    && ["failed", "awaiting_upload", "awaiting_selection"].includes(asset.status)
    && contentById[asset.content_item_id || ""]?.status === "approved"
    && Number(asset.metadata_json.content_version || 1)
      === contentVersionMap[asset.content_item_id || ""]
    && !asset.metadata_json.candidate_group
  ));
  const sourceChoiceIds = new Set(sourceChoiceAssets.map((asset) => asset.id));
  const otherAwaitingUpload = awaitingUpload.filter((asset) => !sourceChoiceIds.has(asset.id));
  const otherAwaitingSelection = awaitingSelection.filter((asset) => !sourceChoiceIds.has(asset.id));
  const needsAction = sourceChoiceAssets.length
    + otherAwaitingUpload.length
    + otherAwaitingSelection.length;
  const readyAssets = assets.filter((asset) => asset.status === "ready");
  const campaignForAsset = (asset: Asset) => {
    const content = contentById[asset.content_item_id || ""];
    return content ? campaignMap[content.campaign_id] : undefined;
  };
  const selectedUploadKind = uploadTarget?.kind || uploadKind;
  const uploadAccept = selectedUploadKind === "image"
    ? "image/png,image/jpeg,image/webp"
    : selectedUploadKind === "video_storyboard"
      ? "application/json,.json"
      : "video/*";
  function openUpload(targetId = "") {
    setUploadTargetId(targetId);
    setUploadKind("image");
    setShowUpload(true);
    window.setTimeout(() => {
      const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      document.getElementById("asset-upload-form")?.scrollIntoView({
        behavior: reduceMotion ? "auto" : "smooth",
        block: "start",
      });
    }, 0);
  }
  function sourceOf(asset: Asset): Exclude<MediaSource, "hybrid"> {
    const recorded = asset.metadata_json.media_source;
    if (recorded === "manual" || recorded === "generate" || recorded === "search") {
      return recorded;
    }
    if (["manual", "manual-upload"].includes(asset.provider)) return "manual";
    if (asset.provider === "openverse") return "search";
    return "generate";
  }
  async function changeSource(
    asset: Asset,
    source: Exclude<MediaSource, "hybrid">,
  ) {
    if (sourceOf(asset) === source) return;
    if (
      asset.status === "awaiting_selection"
      && !window.confirm("切换路线会清除当前图库候选，确认继续？")
    ) {
      return;
    }
    setSourceBusyId(`${asset.id}-${source}`);
    setError("");
    try {
      await api(`/assets/${asset.id}/source`, {
        method: "POST",
        body: { source },
      });
      flash(
        source === "manual"
          ? "已改为人工上传，你可以继续选择本机文件"
          : source === "search"
            ? "已改用开放图库，正在检索候选图片"
            : "已改用 AI 生成，素材任务已经入队",
      );
      await onChanged();
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setSourceBusyId("");
    }
  }
  function sourceChooser(asset: Asset) {
    const current = sourceOf(asset);
    const options: Array<{
      source: Exclude<MediaSource, "hybrid">;
      label: string;
      available: boolean;
    }> = [
      { source: "manual", label: "人工上传", available: true },
      {
        source: "generate",
        label: mediaCapabilities.image_generation_available ? "AI 生成" : "AI 生成 · 未配置",
        available: mediaCapabilities.image_generation_available,
      },
      {
        source: "search",
        label: mediaCapabilities.image_search_available ? "开放图库" : "开放图库 · 未配置",
        available: mediaCapabilities.image_search_available,
      },
    ];
    return (
      <div className="asset-source-chooser">
        <span>这条封面怎么准备</span>
        <div role="group" aria-label="切换封面来源">
          {options.map((option) => (
            <Button
              type="button"
              kind={current === option.source ? "secondary" : "ghost"}
              key={option.source}
              disabled={current === option.source || !option.available}
              busy={sourceBusyId === `${asset.id}-${option.source}`}
              title={!option.available ? "需要管理员先配置对应素材服务" : undefined}
              onClick={() => void changeSource(asset, option.source)}
            >
              {option.label}
            </Button>
          ))}
        </div>
        {!mediaCapabilities.image_generation_available ? (
          <small>AI 生成入口已保留；配置真实图片生成 Provider 后即可选择。</small>
        ) : null}
      </div>
    );
  }
  async function retry(asset: Asset) {
    try {
      await api(`/assets/${asset.id}/retry`, { method: "POST" });
      flash("素材已重新进入生成队列");
      await onChanged();
    } catch (caught) {
      setError(messageOf(caught));
    }
  }
  async function selectCandidate(
    asset: Asset,
    candidate?: ImageSearchCandidate,
  ) {
    const needsLicenseCheck = asset.provider === "openverse";
    if (
      needsLicenseCheck
      && !window.confirm(
        "请先打开原始落地页核验作者、许可和署名要求。确认已核验并选用这张图片？",
      )
    ) {
      return;
    }
    try {
      await api(`/assets/${asset.id}/select`, {
        method: "POST",
        body: {
          candidate_id: candidate?.id || null,
          acknowledge_license_check: needsLicenseCheck,
        },
      });
      flash("图片已选用并绑定当前内容版本");
      await onChanged();
    } catch (caught) {
      setError(messageOf(caught));
    }
  }

  async function uploadAsset(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setUploading(true);
    try {
      await api("/assets/upload", {
        method: "POST",
        body: new FormData(event.currentTarget),
      });
      event.currentTarget.reset();
      setShowUpload(false);
      setUploadTargetId("");
      setUploadKind("image");
      flash("真实素材已上传并绑定当前内容版本");
      await onChanged();
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setUploading(false);
    }
  }

  return (
    <>
      <PageHeading
        eyebrow="Media"
        title="素材中心"
        description="先看系统是否仍在处理，再只完成“等你操作”中的上传或选图；已就绪素材会自动成为发布前置条件。"
        action={canEdit ? (
          <Button onClick={() => openUpload()}>
            <Icon name="plus" />上传所需素材
          </Button>
        ) : undefined}
      />
      {error ? <p className="inline-error">{error}</p> : null}
      {!canEdit ? <p className="permission-note">当前为只读权限，可查看和下载已就绪素材。</p> : null}
      <section className="asset-stage-grid" aria-label="素材准备阶段">
        <article className="asset-stage-lane">
          <span className="asset-stage-number">1</span>
          <div><strong>系统处理中</strong><small>生成或检索会自动刷新，无需重复点击</small></div>
          <b>{systemProcessing.length}</b>
        </article>
        <article className={needsAction ? "asset-stage-lane asset-stage-action" : "asset-stage-lane"}>
          <span className="asset-stage-number">2</span>
          <div><strong>等你操作</strong><small>先选素材路线，再上传文件或核验候选图</small></div>
          <b>{needsAction}</b>
        </article>
        <article className="asset-stage-lane">
          <span className="asset-stage-number">3</span>
          <div><strong>已就绪</strong><small>素材已绑定当前内容版本，可以进入发布</small></div>
          <b>{readyAssets.length}</b>
        </article>
      </section>
      {systemProcessing.length ? (
        <section className="panel asset-processing-panel" aria-live="polite">
          <div className="panel-heading">
            <div><p className="eyebrow">System activity</p><h2>系统正在准备素材</h2><p>页面会自动更新；离开此页不会中断任务。</p></div>
          </div>
          <div className="asset-processing-list">
            {systemProcessing.map((asset) => (
              <div className="asset-processing-row" key={asset.id}>
                <ProjectIdentity
                  campaign={campaignForAsset(asset)}
                  contentTitle={contentMap[asset.content_item_id || ""]}
                  compact
                />
                <div className="asset-processing-state">
                  <span className="activity-spinner" aria-hidden="true" />
                  <span>{asset.provider === "openverse" ? "正在检索候选图片" : "正在生成素材"}</span>
                </div>
                <div className="indeterminate-track" aria-label="处理中"><span /></div>
              </div>
            ))}
          </div>
        </section>
      ) : null}
      {needsAction ? (
        <section className="panel asset-action-panel">
          <div className="panel-heading">
            <div><p className="eyebrow">Action required</p><h2>选择素材准备方式</h2><p>人工上传不是必选项。你可以对每条封面单独选择人工上传、AI 生成或开放图库。</p></div>
          </div>
          <div className="asset-action-list">
            {sourceChoiceAssets.map((asset) => {
              const currentSource = sourceOf(asset);
              return (
              <article className="asset-action-card asset-source-card" key={asset.id}>
                <ProjectIdentity
                  campaign={campaignForAsset(asset)}
                  contentTitle={contentMap[asset.content_item_id || ""]}
                />
                <div className="asset-action-copy">
                  <strong>
                    {asset.status === "failed"
                      ? "当前封面路线不可用，请重新选择"
                      : currentSource === "search"
                        ? "图库候选已经准备好，等待你核验"
                        : "封面尚未就绪，请选择一种准备方式"}
                  </strong>
                  <p>
                    {asset.error
                      ? `最近错误：${asset.error}`
                      : currentSource === "manual"
                        ? "当前选择人工上传；你也可以直接改用 AI 生成或开放图库。"
                        : "当前选择开放图库；选用前必须核验作者、许可和署名要求。"}
                  </p>
                </div>
                <div className="asset-source-actions">
                  {canEdit ? sourceChooser(asset) : null}
                  {canEdit && currentSource === "manual" ? (
                    <Button onClick={() => openUpload(asset.id)}>选择文件并上传</Button>
                  ) : null}
                  {currentSource === "search" && asset.status === "awaiting_selection" ? (
                    <a className="button button-primary" href={`#asset-candidates-${asset.id}`}>查看并核验候选图</a>
                  ) : null}
                </div>
              </article>
              );
            })}
            {otherAwaitingUpload.map((asset) => (
              <article className="asset-action-card" key={asset.id}>
                <ProjectIdentity
                  campaign={campaignForAsset(asset)}
                  contentTitle={contentMap[asset.content_item_id || ""]}
                />
                <div className="asset-action-copy">
                  <strong>需要上传{asset.kind === "video" ? "真实视频" : "分镜 JSON"}</strong>
                  <p>这个任务不是封面图片，仍需按任务要求提供对应文件。</p>
                </div>
                {canEdit ? <Button onClick={() => openUpload(asset.id)}>查看要求并上传</Button> : null}
              </article>
            ))}
            {otherAwaitingSelection.map((asset) => (
              <article className="asset-action-card" key={asset.id}>
                <ProjectIdentity
                  campaign={campaignForAsset(asset)}
                  contentTitle={contentMap[asset.content_item_id || ""]}
                />
                <div className="asset-action-copy">
                  <strong>混合路线候选等待选择</strong>
                  <p>活动已同时准备图库与 AI 候选；核验来源后选定其中一张即可。</p>
                </div>
                <a className="button button-ghost" href={`#asset-candidates-${asset.id}`}>查看候选图</a>
              </article>
            ))}
          </div>
        </section>
      ) : null}
      {showUpload && canEdit ? (
        <section className="panel form-panel" id="asset-upload-form">
          <div className="panel-heading"><div><p className="eyebrow">Upload</p><h2>上传真实素材</h2><p>选择明确的待办后，文件会绑定到对应项目和当前内容版本。</p></div></div>
          <div className="upload-explainer">
            <div><strong>为什么需要你上传</strong><span>系统无法凭空获得品牌实拍、产品图或企业授权文件。</span></div>
            <div><strong>上传什么</strong><span>{selectedUploadKind === "image" ? "PNG、JPEG 或 WebP 的真实封面图" : selectedUploadKind === "video_storyboard" ? "合法 JSON 分镜文件" : "平台可接受的视频文件"}</span></div>
            <div><strong>完成后会怎样</strong><span>文件校验并绑定当前内容版本，随后才能创建发布任务。</span></div>
          </div>
          <form className="stack-form" onSubmit={uploadAsset}>
            <label>待上传任务
              <select name="asset_id" value={uploadTargetId} onChange={(event) => setUploadTargetId(event.target.value)}>
                <option value="">补充素材（仅在没有对应待办时使用）</option>
                {uploadTargets.map((asset) => (
                  <option key={asset.id} value={asset.id}>
                    {asset.kind === "image" ? "封面图片" : asset.kind === "video" ? "视频" : "视频分镜 JSON"} · {contentMap[asset.content_item_id || ""] || "未关联"} · {STATUS[asset.status] || asset.status}
                  </option>
                ))}
              </select>
            </label>
            {!uploadTarget ? (
              <>
                <label>关联内容
                  <select name="content_item_id" required defaultValue="">
                    <option value="" disabled>选择已审核内容版本</option>
                    {contents.filter((item) => item.status === "approved").map((item) => <option key={item.id} value={item.id}>{PLATFORM[item.platform]} · v{item.version} · {item.title}</option>)}
                  </select>
                </label>
                <label>素材类型
                  <select name="kind" value={uploadKind} onChange={(event) => setUploadKind(event.target.value)}>
                    <option value="image">封面图片</option>
                    <option value="video">视频</option>
                    <option value="video_storyboard">视频分镜 JSON</option>
                  </select>
                </label>
              </>
            ) : (
              <div className="upload-target-summary">
                <ProjectIdentity
                  campaign={campaignForAsset(uploadTarget)}
                  contentTitle={contentMap[uploadTarget.content_item_id || ""]}
                />
                <p className="form-note">只会填充这个项目的当前版本素材任务；上传成功并通过类型校验后才允许发布。</p>
              </div>
            )}
            <label>选择本机文件
              <input name="file" type="file" accept={uploadAccept} required />
              <small>接受：{uploadAccept.replaceAll(",", "、")}。请使用清晰、无水印且你有权发布的文件。</small>
            </label>
            <div className="form-actions"><Button type="submit" busy={uploading}>上传并绑定</Button><Button type="button" kind="ghost" onClick={() => { setShowUpload(false); setUploadTargetId(""); }}>取消</Button></div>
          </form>
        </section>
      ) : null}
      {assets
        .filter((asset) => asset.status === "awaiting_selection")
        .map((asset) => {
          const candidates = Array.isArray(asset.metadata_json.search_candidates)
            ? asset.metadata_json.search_candidates as ImageSearchCandidate[]
            : [];
          return (
            <section className="panel media-candidate-panel" id={`asset-candidates-${asset.id}`} key={asset.id}>
              <div className="panel-heading">
                <div>
                  <p className="eyebrow">Open-licensed candidates</p>
                  <ProjectIdentity
                    campaign={campaignForAsset(asset)}
                    contentTitle={contentMap[asset.content_item_id || ""] || "图片候选"}
                  />
                  <p>
                    搜索词：{String(asset.metadata_json.search_query || "未记录")}。
                    Openverse 许可元数据仅作线索，选用前必须打开原始页面核验。
                  </p>
                </div>
              </div>
              <div className="media-candidate-grid">
                {candidates.map((candidate) => (
                  <article className="media-candidate-card" key={candidate.id}>
                    {candidate.thumbnail_url ? (
                      <>
                        {/* The API restricts remote thumbnails to an exact host. */}
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img
                          src={candidate.thumbnail_url}
                          alt={candidate.title}
                          width={320}
                          height={180}
                          loading="lazy"
                          referrerPolicy="no-referrer"
                        />
                      </>
                    ) : (
                      <div className="media-candidate-placeholder">无缩略图</div>
                    )}
                    <div>
                      <strong>{candidate.title}</strong>
                      <p>{candidate.creator} · {candidate.license.toUpperCase()} {candidate.license_version}</p>
                      <div className="candidate-actions">
                        {candidate.landing_url ? (
                          <a href={candidate.landing_url} target="_blank" rel="noreferrer">
                            核验原始页面
                          </a>
                        ) : null}
                        {canEdit ? (
                          <button onClick={() => void selectCandidate(asset, candidate)}>
                            核验后选用
                          </button>
                        ) : null}
                      </div>
                    </div>
                  </article>
                ))}
              </div>
            </section>
          );
        })}
      <section className="panel">
        <DataTable
          headers={["项目 / 内容", "素材", "生成方式", "大小", "状态", "操作"]}
          rows={assets.map((asset) => [
            <ProjectIdentity
              key="project"
              campaign={campaignForAsset(asset)}
              contentTitle={contentMap[asset.content_item_id || ""]}
              compact
            />,
            asset.kind === "image" ? "营销图片" : asset.kind === "video" ? "视频" : "视频分镜 JSON",
            ["manual", "manual-upload"].includes(asset.provider)
              ? "人工上传"
              : asset.provider === "openverse"
                ? "开放授权图库"
                : "AI 生成",
            formatBytes(asset.size_bytes),
            <StatusBadge key="status" value={asset.status} />,
            <div className="table-actions" key="actions">
              {asset.status === "ready" ? (
                <button onClick={() => void download(`/assets/${asset.id}/download`, `asset-${asset.id}`)}>
                  <Icon name="download" />下载
                </button>
              ) : null}
              {canEdit
              && asset.status === "ready"
              && Boolean(asset.metadata_json.candidate_optional)
              && !Boolean(asset.metadata_json.selected) ? (
                <button onClick={() => void selectCandidate(asset)}>选用此素材</button>
              ) : null}
              {Boolean(asset.metadata_json.candidate_optional)
              && Boolean(asset.metadata_json.selected) ? (
                <span className="selected-candidate">已选用</span>
              ) : null}
              {canEdit && asset.status === "awaiting_upload" ? (
                <button onClick={() => openUpload(asset.id)}>上传真实素材</button>
              ) : null}
              {canEdit && !["manual", "manual-upload"].includes(asset.provider) && ["failed", "planned", "stale"].includes(asset.status) ? (
                <button onClick={() => void retry(asset)}>
                  {asset.provider === "openverse" ? "重新搜索" : "重新生成"}
                </button>
              ) : null}
            </div>,
          ])}
          empty="还没有素材任务"
        />
      </section>
    </>
  );
}

function PublishingView({
  campaigns,
  publishes,
  contents,
  channels,
  role,
  onNavigate,
  onChanged,
  flash,
}: {
  campaigns: Campaign[];
  publishes: PublishJob[];
  contents: Content[];
  channels: Channel[];
  role: string;
  onNavigate: (view: View) => void;
  onChanged: () => Promise<void> | void;
  flash: (message: string) => void;
}) {
  const approved = contents.filter((item) => item.status === "approved");
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);
  const [pulling, setPulling] = useState("");
  const [cancelling, setCancelling] = useState("");
  const [reconciling, setReconciling] = useState("");
  const [scriptBusy, setScriptBusy] = useState("");
  const [retrying, setRetrying] = useState("");
  const [publishTiming, setPublishTiming] = useState<"immediate" | "scheduled">(
    "immediate",
  );
  const [selectedContentId, setSelectedContentId] = useState("");
  const [selectedChannelId, setSelectedChannelId] = useState("");
  const [publishRequestId, setPublishRequestId] = useState(() =>
    crypto.randomUUID(),
  );
  const [error, setError] = useState("");
  const [evidenceJobId, setEvidenceJobId] = useState("");
  const [evidence, setEvidence] = useState<PublishEvidence[]>([]);
  const [confirmations, setConfirmations] = useState<PublishConfirmation[]>([]);
  const [evidenceBusy, setEvidenceBusy] = useState(false);
  const canSchedule = roleAtLeast(role, "reviewer");
  const [defaultSchedule] = useState(() =>
    toLocalInput(new Date(Date.now() + 10 * 60_000)),
  );
  const contentMap = useMemo(
    () => Object.fromEntries(contents.map((item) => [item.id, item])),
    [contents],
  );
  const campaignMap = useMemo(
    () => Object.fromEntries(campaigns.map((campaign) => [campaign.id, campaign])),
    [campaigns],
  );
  const channelMap = useMemo(
    () => Object.fromEntries(channels.map((item) => [item.id, item])),
    [channels],
  );
  const selectedContent = contentMap[selectedContentId];
  const matchingChannels = selectedContent
    ? channels.filter((item) => item.platform === selectedContent.platform)
    : [];
  const selectedChannel = channelMap[selectedChannelId];

  async function createPublish(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const form = new FormData(event.currentTarget);
    const publishNow = publishTiming === "immediate";
    try {
      await api("/publishing/jobs", {
        method: "POST",
        body: {
          content_item_id: form.get("content_item_id"),
          channel_id: form.get("channel_id"),
          delivery_mode: form.get("delivery_mode"),
          publish_now: publishNow,
          request_id: publishRequestId,
          ...(publishNow
            ? {}
            : {
                scheduled_at: new Date(
                  String(form.get("scheduled_at")),
                ).toISOString(),
              }),
        },
      });
      setCreating(false);
      setSelectedContentId("");
      setSelectedChannelId("");
      setPublishRequestId(crypto.randomUUID());
      flash(publishNow ? "发布任务已立即进入队列" : "发布任务已按时间排期");
      await onChanged();
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy(false);
    }
  }

  async function retrySafely(job: PublishJob) {
    setRetrying(job.id);
    setError("");
    try {
      await api(`/publishing/jobs/${job.id}/retry`, { method: "POST" });
      flash("已确认在平台写入前失败，任务正在安全重试");
      await onChanged();
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setRetrying("");
    }
  }

  async function pullMetrics(job: PublishJob) {
    setPulling(job.id);
    try {
      await api(`/metrics/pull/${job.id}`, { method: "POST" });
      flash("指标回收任务已进入队列");
      await onChanged();
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setPulling("");
    }
  }
  async function cancel(job: PublishJob) {
    const prompt = job.publish_timing === "immediate"
      ? "取消这条尚未执行的立即发布任务？取消后不会自动分发。"
      : "取消这条发布排期？取消后不会自动分发。";
    if (!window.confirm(prompt)) return;
    setCancelling(job.id);
    setError("");
    try {
      await api(`/publishing/jobs/${job.id}/cancel`, { method: "POST" });
      flash(job.publish_timing === "immediate" ? "立即发布任务已取消" : "发布排期已取消");
      await onChanged();
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setCancelling("");
    }
  }

  async function reconcile(
    job: PublishJob,
    decision: "confirmed_published" | "confirmed_not_published",
  ) {
    const confirmedPublished = decision === "confirmed_published";
    const reason = window.prompt(
      confirmedPublished
        ? "请填写在平台核对到已发布的依据"
        : "请填写在平台核对到未发布的依据",
    );
    if (!reason?.trim()) return;
    const externalId = confirmedPublished
      ? window.prompt("平台内容 ID（可选）")?.trim() || null
      : null;
    const externalUrl = confirmedPublished
      ? window.prompt("平台内容链接（可选）")?.trim() || null
      : null;
    setReconciling(job.id);
    setError("");
    try {
      await api(`/publishing/jobs/${job.id}/reconcile`, {
        method: "POST",
        body: {
          decision,
          reason: reason.trim(),
          external_id: externalId,
          external_url: externalUrl,
        },
      });
      flash(
        confirmedPublished
          ? "已登记为平台确认发布，系统不会重复分发"
          : "已登记为未发布；如需再次分发，请在任务中心重试",
      );
      await onChanged();
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setReconciling("");
    }
  }

  async function requestScriptPackage(job: PublishJob) {
    const prompt = job.script_confirmation_expired
      ? "当前脚本任务包已过期。重新生成会废止并清理旧包，是否继续？"
      : "改用本机脚本辅助？请先确认平台没有生成草稿或发布结果，以免重复发布。";
    if (!window.confirm(prompt)) return;
    setScriptBusy(job.id);
    setError("");
    try {
      await api(`/publishing/jobs/${job.id}/script-package`, { method: "POST" });
      flash("脚本发布包已进入生成队列");
      await onChanged();
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setScriptBusy("");
    }
  }

  async function openEvidence(job: PublishJob) {
    setEvidenceBusy(true);
    setError("");
    try {
      const [evidencePage, confirmationPage] = await Promise.all([
        apiAllPages<PublishEvidence>(`/publishing/jobs/${job.id}/evidence`),
        apiAllPages<PublishConfirmation>(`/publishing/jobs/${job.id}/confirmations`),
      ]);
      setEvidenceJobId(job.id);
      setEvidence(evidencePage.items);
      setConfirmations(confirmationPage.items);
      if (evidencePage.truncated || confirmationPage.truncated) {
        setError("发布证据历史已达到安全加载上限 2000 条，请联系管理员导出完整记录。");
      }
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setEvidenceBusy(false);
    }
  }

  async function uploadEvidence(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!evidenceJobId) return;
    const formElement = event.currentTarget;
    setEvidenceBusy(true);
    setError("");
    try {
      await api<PublishEvidence>(
        `/publishing/jobs/${evidenceJobId}/evidence`,
        { method: "POST", body: new FormData(formElement) },
      );
      formElement.reset();
      const job = publishes.find((item) => item.id === evidenceJobId);
      if (job) await openEvidence(job);
      await onChanged();
      flash("证据已校验并绑定到当前脚本任务包");
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setEvidenceBusy(false);
    }
  }

  async function recordScriptResult(
    job: PublishJob,
    decision: "confirmed_published" | "confirmed_not_published",
  ) {
    const confirmedPublished = decision === "confirmed_published";
    const reason = window.prompt(
      confirmedPublished
        ? "请填写在平台后台核对到已发布的依据"
        : "请填写在平台后台核对到未发布的依据",
    );
    if (!reason?.trim()) return;
    const externalId = confirmedPublished
      ? window.prompt("平台内容 ID（可选）")?.trim() || null
      : null;
    const externalUrl = confirmedPublished
      ? window.prompt("平台内容链接（可选）")?.trim() || null
      : null;
    setScriptBusy(job.id);
    setError("");
    try {
      const updated = await api<PublishJob>(`/publishing/jobs/${job.id}/script-result`, {
        method: "POST",
        body: {
          decision,
          reason: reason.trim(),
          external_id: externalId,
          external_url: externalUrl,
        },
      });
      flash(
        updated.status === "script_confirmation_pending"
          ? "首次确认已保存，证据已冻结，等待另一位审核人独立确认"
          : confirmedPublished
            ? "脚本发布结果已登记"
            : "已登记为未发布，可修正后重新生成脚本包",
      );
      await openEvidence(updated);
      await onChanged();
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setScriptBusy("");
    }
  }

  return (
    <>
      <PageHeading
        eyebrow="4 · 发布"
        title="把已审核内容交付到平台"
        description="默认立即执行；需要预约时再切换为定时发布。公众号连接关闭自动发布时，只会创建草稿。"
        action={
          canSchedule ? (
            <Button
              onClick={() => {
                setCreating((value) => !value);
                setPublishRequestId(crypto.randomUUID());
              }}
            >
              <Icon name="plus" />新建发布
            </Button>
          ) : undefined
        }
      />
      <section className="publish-quick-guide" aria-label="发布前检查">
        <span><b>1</b> 内容已审核</span>
        <span><b>2</b> 素材已就绪</span>
        <span><b>3</b> 渠道已连接</span>
        <span><b>4</b> 立即或定时执行</span>
      </section>
      {error ? <p className="inline-error" role="alert">{error}</p> : null}
      {!canSchedule ? <p className="permission-note">当前可查看发布状态与下载投放包；执行发布需要审核人员权限。</p> : null}
      {creating && canSchedule ? (
        <section className="panel form-panel publish-composer">
          <div className="panel-heading">
            <div><p className="eyebrow">创建发布任务</p><h2>什么时候执行？</h2></div>
            <Button kind="ghost" type="button" onClick={() => setCreating(false)}>关闭</Button>
          </div>
          <form className="stack-form" onSubmit={createPublish}>
            <div className="timing-switch" role="group" aria-label="发布时间选择">
              <button
                type="button"
                className={publishTiming === "immediate" ? "active" : ""}
                aria-pressed={publishTiming === "immediate"}
                onClick={() => setPublishTiming("immediate")}
              >
                <strong>立即执行</strong>
                <small>保存后马上进入可靠队列</small>
              </button>
              <button
                type="button"
                className={publishTiming === "scheduled" ? "active" : ""}
                aria-pressed={publishTiming === "scheduled"}
                onClick={() => setPublishTiming("scheduled")}
              >
                <strong>定时发布</strong>
                <small>到指定时间再进入分发</small>
              </button>
            </div>
            <div className="form-grid">
              <label>已审核内容
                <select
                  name="content_item_id"
                  required
                  value={selectedContentId}
                  onChange={(event) => {
                    setSelectedContentId(event.target.value);
                    setSelectedChannelId("");
                  }}
                >
                  <option value="" disabled>选择要发布的内容</option>
                  {approved.map((item) => (
                    <option key={item.id} value={item.id}>
                      {projectCode(item.campaign_id)} · {campaignMap[item.campaign_id]?.name || "未知项目"} · {PLATFORM[item.platform]} · {item.title}
                    </option>
                  ))}
                </select>
                {!approved.length ? <small>还没有已审核内容，请先完成第 2 步。</small> : null}
              </label>
              <label>平台连接
                <select
                  name="channel_id"
                  required
                  value={selectedChannelId}
                  disabled={!selectedContentId}
                  onChange={(event) => setSelectedChannelId(event.target.value)}
                >
                  <option value="" disabled>
                    {selectedContentId ? "选择匹配的平台连接" : "先选择内容"}
                  </option>
                  {matchingChannels.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.display_name} · {STATUS[item.status] || item.status}
                    </option>
                  ))}
                </select>
                {selectedContentId && !matchingChannels.length ? (
                  <small>没有匹配连接，请先到“平台连接”完成配置。</small>
                ) : null}
              </label>
            </div>
            {selectedChannel?.platform === "wechat"
              && selectedChannel.config_json.auto_publish !== true ? (
              <p className="safe-notice">
                当前公众号连接为安全模式：执行后只创建草稿，不会公开发布。
              </p>
            ) : null}
            {publishTiming === "scheduled" ? (
              <label>计划执行时间
                <input
                  name="scheduled_at"
                  type="datetime-local"
                  required
                  defaultValue={defaultSchedule}
                />
                <small>时间使用当前设备时区，保存后可在执行前取消。</small>
              </label>
            ) : null}
            <details className="advanced-options">
              <summary>高级发布方式</summary>
              <label>发布方式
                <select name="delivery_mode" required defaultValue="connector">
                  <option value="connector">官方 API（推荐）</option>
                  <option value="script">本机脚本辅助（人工最终点击）</option>
                  <option value="manual_export">人工导出（仅小红书）</option>
                </select>
                <small>API 结果不确定时必须先对账，系统不会静默切换发布方式。</small>
              </label>
            </details>
            <div className="form-actions">
              <Button type="submit" busy={busy}>
                {publishTiming === "immediate" ? "立即执行" : "确认定时发布"}
              </Button>
              <Button type="button" kind="ghost" onClick={() => setCreating(false)}>取消</Button>
            </div>
          </form>
        </section>
      ) : null}
      {evidenceJobId && publishes.find((item) => item.id === evidenceJobId)?.script_confirmation_expired ? (
        <p className="inline-error">
          该脚本尝试已过期，只保留证据和确认记录用于审计；请在任务列表重新生成任务包。
        </p>
      ) : null}
      {evidenceJobId ? (
        <section className="panel form-panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">发布证据</p>
              <h2>脚本发布证据与确认</h2>
            </div>
            <Button kind="ghost" onClick={() => setEvidenceJobId("")}>关闭</Button>
          </div>
          {canSchedule
            && publishes.find((item) => item.id === evidenceJobId)?.status === "script_ready"
            && !publishes.find((item) => item.id === evidenceJobId)?.script_confirmation_expired ? (
            <form className="stack-form" onSubmit={uploadEvidence}>
              <div className="form-grid">
                <label>证据类型
                  <select name="kind" defaultValue="screenshot">
                    <option value="screenshot">平台截图（PNG/JPEG/WebP）</option>
                    <option value="platform_export">平台导出（JSON）</option>
                  </select>
                </label>
                <label>证据文件
                  <input
                    name="file"
                    type="file"
                    accept=".png,.jpg,.jpeg,.webp,.json"
                    required
                  />
                </label>
              </div>
              <small>服务端会解码、规范化并计算哈希；首次确认后证据将被冻结。</small>
              <Button type="submit" busy={evidenceBusy}>上传并校验</Button>
            </form>
          ) : null}
          <div className="record-list">
            {evidence.map((item) => (
              <div className="record-row" key={item.id}>
                <div className="document-icon">{item.kind === "screenshot" ? "IMG" : "JSON"}</div>
                <div className="record-main">
                  <strong>{item.original_filename}</strong>
                  <small>{formatBytes(item.size_bytes)} · SHA-256 {item.object_sha256.slice(0, 16)}…</small>
                </div>
                <button onClick={() => void download(
                  `/publishing/jobs/${item.publish_job_id}/evidence/${item.id}/download`,
                  `evidence-${item.id}`,
                )}>
                  <Icon name="download" />下载
                </button>
              </div>
            ))}
            {!evidence.length ? <p className="form-note">确认结果前至少上传一份证据文件。</p> : null}
          </div>
          {confirmations.length ? (
            <div className="record-list">
              {confirmations.map((item, index) => (
                <div className="record-row" key={item.id}>
                  <div className="document-icon">{index + 1}</div>
                  <div className="record-main">
                    <strong>{item.decision === "confirmed_published" ? "确认已发布" : "确认未发布"}</strong>
                    <small>
                      审核人 {item.confirmed_by_user_id.slice(0, 8)} · 清单 {item.evidence_manifest_sha256.slice(0, 16)}…
                    </small>
                  </div>
                  <span>{formatDateTime(item.created_at)}</span>
                </div>
              ))}
            </div>
          ) : null}
        </section>
      ) : null}
      <section className="panel">
        <DataTable
          headers={["项目 / 内容", "平台", "方式", "执行时间", "尝试", "状态", "下一步"]}
          rows={publishes.map((job) => [
            <ProjectIdentity
              key="project"
              campaign={campaignMap[contentMap[job.content_item_id]?.campaign_id]}
              contentTitle={contentMap[job.content_item_id]?.title || job.content_item_id}
              compact
            />,
            channelMap[job.channel_id]?.display_name || job.channel_id,
            DELIVERY_MODE[job.delivery_mode] || job.delivery_mode,
            <div className="timing-cell" key="timing">
              <strong>{job.publish_timing === "immediate" ? "立即" : "定时"}</strong>
              <small>{formatDateTime(job.scheduled_at)}</small>
            </div>,
            job.attempts,
            <div className="status-stack" key="status">
              <StatusBadge value={job.status} />
              {job.retry_safe ? (
                <small>
                  可安全重试 · {PUBLISH_FAILURE_STAGE[job.failure_stage || ""] || job.failure_stage}
                </small>
              ) : null}
            </div>,
            <div className="table-actions" key="actions">
              {job.error ? <span className="publish-error">{job.error}</span> : null}
              {canSchedule && job.retry_safe ? (
                channelMap[job.channel_id]?.status === "connected" ? (
                  <button
                    disabled={retrying === job.id}
                    onClick={() => void retrySafely(job)}
                  >
                    安全重试
                  </button>
                ) : (
                  <button onClick={() => onNavigate("channels")}>
                    先复测渠道
                  </button>
                )
              ) : null}
              {job.status === "exported" || (job.script_package_available && !job.script_confirmation_expired) ? (
                <button onClick={() => void download(`/publishing/jobs/${job.id}/artifact`, `contentflow-${job.id}.zip`)}>
                  <Icon name="download" />{job.delivery_mode === "script" ? "下载脚本包" : "下载投放包"}
                </button>
              ) : null}
              {job.script_package_available ? (
                <>
                  <button
                    disabled={evidenceBusy}
                    onClick={() => void openEvidence(job)}
                  >
                    证据 {job.script_evidence_count} · 确认 {job.script_confirmation_count}/{job.script_confirmation_required}
                  </button>
                  <span>
                    {job.script_confirmation_expired
                      ? "任务包已过期"
                      : `任务包有效期至 ${formatDateTime(job.script_confirmation_expires_at || "")}`}
                  </span>
                </>
              ) : null}
              {canSchedule && !job.script_confirmation_expired && ["script_ready", "script_confirmation_pending"].includes(job.status) ? (
                <>
                  <button
                    disabled={scriptBusy === job.id}
                    onClick={() => void recordScriptResult(job, "confirmed_published")}
                  >
                    登记已发布
                  </button>
                  <button
                    className="danger-text"
                    disabled={scriptBusy === job.id}
                    onClick={() => void recordScriptResult(job, "confirmed_not_published")}
                  >
                    登记未发布
                  </button>
                </>
              ) : null}
              {canSchedule && job.status === "reconciliation_required" ? (
                <>
                  <button
                    disabled={reconciling === job.id}
                    onClick={() => void reconcile(job, "confirmed_published")}
                  >
                    确认已发布
                  </button>
                  <button
                    className="danger-text"
                    disabled={reconciling === job.id}
                    onClick={() => void reconcile(job, "confirmed_not_published")}
                  >
                    确认未发布
                  </button>
                </>
              ) : null}
              {canSchedule && job.delivery_mode === "connector" && job.external_id && channelMap[job.channel_id]?.platform !== "xiaohongshu" ? (
                <button
                  disabled={pulling === job.id}
                  onClick={() => void pullMetrics(job)}
                >
                  回收指标
                </button>
              ) : null}
              {canSchedule && (
                (["script_ready", "script_confirmation_pending"].includes(job.status) && job.script_confirmation_expired)
                || (job.status === "failed" && !job.retry_safe)
                || (job.delivery_mode !== "script" && ["scheduled", "queued", "exported"].includes(job.status))
              ) ? (
                <button
                  disabled={scriptBusy === job.id}
                  onClick={() => void requestScriptPackage(job)}
                >
                  {job.delivery_mode === "script" ? "重新生成脚本包" : "改用脚本辅助"}
                </button>
              ) : null}
              {canSchedule && ["scheduled", "queued"].includes(job.status) ? (
                <button
                  className="danger-text"
                  disabled={cancelling === job.id}
                  onClick={() => void cancel(job)}
                >
                  {job.publish_timing === "immediate" ? "取消执行" : "取消排期"}
                </button>
              ) : null}
              {!["scheduled", "queued", "exported", "script_ready"].includes(job.status) && job.external_id ? (
                <span>ID {job.external_id}</span>
              ) : null}
              {!job.external_id && !job.error && !["scheduled", "queued", "reconciliation_required", "script_ready"].includes(job.status) ? <span>—</span> : null}
            </div>,
          ])}
          empty="还没有发布任务"
        />
      </section>
    </>
  );
}

function KnowledgeView({
  documents,
  role,
  onChanged,
  flash,
}: {
  documents: KnowledgeDocument[];
  role: string;
  onChanged: () => Promise<void> | void;
  flash: (message: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const canEdit = roleAtLeast(role, "editor");
  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    try {
      await api("/knowledge/documents", { method: "POST", body: form });
      event.currentTarget.reset();
      flash("文档已上传，正在索引");
      await onChanged();
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeading
        eyebrow="RAG"
        title="营销知识库"
        description="上传品牌规范、产品事实与历史内容，生成结果会保留引用的知识块 ID。"
      />
      {!canEdit ? <p className="permission-note">当前为只读权限，可查看知识文档及索引状态。</p> : null}
      <div className="knowledge-grid">
        {canEdit ? <section className="panel upload-panel">
          <p className="eyebrow">添加资料</p>
          <h2>上传知识文档</h2>
          <p>支持 Markdown、TXT、CSV、JSON，单文件不超过 20MB。</p>
          <form onSubmit={upload}>
            <label className="file-drop">
              <input name="file" type="file" accept=".md,.txt,.csv,.json" required />
              <span><Icon name="book" />选择知识文件</span>
            </label>
            {error ? <p className="inline-error">{error}</p> : null}
            <Button type="submit" busy={busy}>上传并索引</Button>
          </form>
        </section> : null}
        <section className="panel document-panel">
          <div className="panel-heading"><div><p className="eyebrow">Documents</p><h2>{documents.length} 份资料</h2></div></div>
          {documents.length ? (
            <div className="record-list">
              {documents.map((document) => (
                <div className="record-row" key={document.id}>
                  <div className="document-icon">TXT</div>
                  <div className="record-main">
                    <strong>{document.name}</strong>
                    <small>{formatBytes(document.metadata_json.size_bytes || null)} · {document.metadata_json.chunk_count || 0} 个知识块</small>
                  </div>
                  <StatusBadge value={document.status} />
                </div>
              ))}
            </div>
          ) : <EmptyState title="知识库为空" description="上传品牌与产品资料后开始生成。" />}
        </section>
      </div>
    </>
  );
}

function ChannelsView({
  channels,
  role,
  onChanged,
  flash,
}: {
  channels: Channel[];
  role: string;
  onChanged: () => Promise<void> | void;
  flash: (message: string) => void;
}) {
  const [creating, setCreating] = useState(false);
  const [platform, setPlatform] = useState("xiaohongshu");
  const [connectionMode, setConnectionMode] = useState("manual_export");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const canAdmin = roleAtLeast(role, "admin");
  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const credentials: Record<string, string> = {};
    if (connectionMode === "connector") {
      ["access_token", "open_id", "app_id", "app_secret"].forEach((key) => {
        const value = String(form.get(key) || "");
        if (value) credentials[key] = value;
      });
    }
    setBusy("create");
    try {
      await api("/channels", {
        method: "POST",
        body: {
          platform,
          display_name: form.get("display_name"),
          connection_mode: connectionMode,
          credentials,
          script_confirmation_required: connectionMode === "script" ? Number(form.get("script_confirmation_required") || 1) : 1,
          config: connectionMode === "manual_export" ? { export_format: "zip" } : {},
        },
      });
      setCreating(false);
      flash("平台连接已保存");
      await onChanged();
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy("");
    }
  }
  async function test(channel: Channel) {
    setBusy(channel.id);
    try {
      await api(`/channels/${channel.id}/test`, { method: "POST" });
      flash("连接测试任务已进入队列");
      await onChanged();
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy("");
    }
  }

  return (
    <>
      <PageHeading
        eyebrow="Connectors"
        title="平台连接"
        description="每个平台账号可配置官方 API、无需平台密码的本机脚本辅助，或小红书人工导出。"
        action={canAdmin ? <Button onClick={() => setCreating((value) => !value)}><Icon name="plus" />添加连接</Button> : undefined}
      />
      {error ? <p className="inline-error">{error}</p> : null}
      {!canAdmin ? <p className="permission-note">当前可查看连接状态；凭据配置与连接测试仅由管理员执行。</p> : null}
      {creating && canAdmin ? (
        <section className="panel form-panel">
          <div className="panel-heading"><div><p className="eyebrow">New connector</p><h2>连接平台账号</h2></div></div>
          <form className="stack-form" onSubmit={create}>
            <div className="form-grid">
              <label>平台
                <select value={platform} onChange={(event) => {
                  const nextPlatform = event.target.value;
                  setPlatform(nextPlatform);
                  setConnectionMode(nextPlatform === "xiaohongshu" ? "manual_export" : "connector");
                }}>
                  {Object.entries(PLATFORM).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                </select>
              </label>
              <label>连接名称<input name="display_name" required placeholder="品牌官方账号" /></label>
              <label>连接方式
                <select value={connectionMode} onChange={(event) => setConnectionMode(event.target.value)}>
                  <option value="connector" disabled={platform === "xiaohongshu"}>官方 API</option>
                  <option value="script">本机脚本辅助</option>
                  <option value="manual_export" disabled={platform !== "xiaohongshu"}>人工导出</option>
                </select>
              </label>
            </div>
            {connectionMode === "connector" && platform === "douyin" ? (
              <div className="form-grid">
                <label>Access Token<input name="access_token" type="password" required /></label>
                <label>Open ID<input name="open_id" required /></label>
              </div>
            ) : null}
            {connectionMode === "connector" && platform === "wechat" ? (
              <div className="form-grid">
                <label>App ID<input name="app_id" required /></label>
                <label>App Secret<input name="app_secret" type="password" required /></label>
              </div>
            ) : null}
            {connectionMode === "script" ? (
              <label>Result confirmation policy
                <select name="script_confirmation_required" defaultValue="1">
                  <option value="1">One independent reviewer with evidence</option>
                  <option value="2">Two independent reviewers with evidence</option>
                </select>
                <small>发起人不能确认自己的脚本尝试；单审核人策略至少需要 2 名用户，双审核人策略至少需要 3 名用户。</small>
              </label>
            ) : null}
            {connectionMode === "script" ? <p className="form-note">脚本连接不接收或保存账号密码，使用每个平台账号独立的本机浏览器登录目录，且不会点击最终发布按钮。</p> : null}
            {connectionMode === "manual_export" ? <p className="form-note">系统生成审核后的 ZIP 投放包，由运营人员人工发布；该方式目前仅支持小红书。</p> : null}
            <div className="form-actions"><Button type="submit" busy={busy === "create"}>保存连接</Button><Button type="button" kind="ghost" onClick={() => setCreating(false)}>取消</Button></div>
          </form>
        </section>
      ) : null}
      <section className="connector-grid">
        {channels.map((channel) => (
          <article className="panel connector-card" key={channel.id}>
            <div className="connector-platform">{(PLATFORM[channel.platform] || channel.platform).slice(0, 1)}</div>
            <div>
              <p className="eyebrow">{PLATFORM[channel.platform]}</p>
              <h2>{channel.display_name}</h2>
              <StatusBadge value={channel.status} />
            </div>
            {canAdmin && !["script_only", "export_only"].includes(channel.status) ? <Button kind="ghost" busy={busy === channel.id} onClick={() => void test(channel)}>测试连接</Button> : null}
          </article>
        ))}
        {!channels.length ? <section className="panel span-3"><EmptyState title="还没有平台连接" description="先添加导出连接或经授权的平台账号。" /></section> : null}
      </section>
    </>
  );
}

function MetricsView({
  data,
  publishes,
  campaigns,
  contents,
  channels,
  role,
  onChanged,
  flash,
}: {
  data: MetricsSummary;
  publishes: PublishJob[];
  campaigns: Campaign[];
  contents: Content[];
  channels: Channel[];
  role: string;
  onChanged: () => Promise<void> | void;
  flash: (message: string) => void;
}) {
  const hasData = data.impressions > 0;
  const [showManual, setShowManual] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const canEdit = roleAtLeast(role, "editor");
  const contentMap = useMemo(
    () => Object.fromEntries(contents.map((item) => [item.id, item])),
    [contents],
  );
  const campaignMap = useMemo(
    () => Object.fromEntries(campaigns.map((campaign) => [campaign.id, campaign])),
    [campaigns],
  );
  const channelMap = useMemo(
    () => Object.fromEntries(channels.map((item) => [item.id, item])),
    [channels],
  );
  const completedPublishes = publishes.filter((job) =>
    ["exported", "published", "script_published", "submitted", "draft_created"].includes(job.status),
  );

  async function ingest(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setBusy(true);
    setError("");
    try {
      await api("/metrics/snapshots", {
        method: "POST",
        body: {
          publish_job_id: form.get("publish_job_id"),
          impressions: Number(form.get("impressions") || 0),
          clicks: Number(form.get("clicks") || 0),
          likes: Number(form.get("likes") || 0),
          comments: Number(form.get("comments") || 0),
          shares: Number(form.get("shares") || 0),
          raw: { source: "manual_console" },
        },
      });
      formElement.reset();
      setShowManual(false);
      flash("人工指标已录入并纳入统一复盘");
      await onChanged();
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy(false);
    }
  }


  return (
    <>
      <PageHeading
        eyebrow="Review"
        title="数据复盘"
        description="汇总平台回收或人工录入的曝光、点击与互动数据。"
        action={canEdit ? (
          <Button onClick={() => setShowManual((value) => !value)}>
            <Icon name="plus" />{showManual ? "收起录入" : "录入人工指标"}
          </Button>
        ) : undefined}
      />
      {error ? <p className="inline-error">{error}</p> : null}
      {!canEdit ? <p className="permission-note">当前为只读权限，可查看统一口径的指标与复盘建议。</p> : null}
      {showManual && canEdit ? (
        <section className="panel form-panel">
          <div className="panel-heading">
            <div><p className="eyebrow">Manual metrics</p><h2>录入平台后台已核对数据</h2></div>
          </div>
          <form className="stack-form" onSubmit={ingest}>
            <label>
              已完成的发布记录
              <select name="publish_job_id" required defaultValue="">
                <option value="" disabled>选择需要补录数据的内容</option>
                {completedPublishes.map((job) => {
                  const content = contentMap[job.content_item_id];
                  const channel = channelMap[job.channel_id];
                  return (
                    <option key={job.id} value={job.id}>
                      {content ? projectCode(content.campaign_id) : "SYSTEM"} · {content ? campaignMap[content.campaign_id]?.name || "未知项目" : "系统任务"} · {PLATFORM[channel?.platform || content?.platform] || channel?.platform} · {content?.title || job.content_item_id} · {STATUS[job.status] || job.status}
                    </option>
                  );
                })}
              </select>
              <small>小红书人工投放后，可按同一统计周期从平台后台抄录；请勿填估算值。</small>
            </label>
            <div className="metric-input-grid">
              {[
                ["impressions", "曝光"],
                ["clicks", "点击"],
                ["likes", "点赞"],
                ["comments", "评论"],
                ["shares", "分享"],
              ].map(([name, label]) => (
                <label key={name}>
                  {label}
                  <input name={name} type="number" min="0" step="1" defaultValue="0" required />
                </label>
              ))}
            </div>
            <div className="form-actions">
              <Button type="submit" busy={busy} disabled={!completedPublishes.length}>保存指标</Button>
              <Button type="button" kind="ghost" onClick={() => setShowManual(false)}>取消</Button>
            </div>
            {!completedPublishes.length ? <p className="form-note">当前没有可录入指标的已完成发布记录。</p> : null}
          </form>
        </section>
      ) : null}
      <section className="metric-grid metric-grid-five">
        {[
          ["曝光", data.impressions],
          ["点击", data.clicks],
          ["互动", data.engagements],
          ["点击率", `${(data.click_through_rate * 100).toFixed(2)}%`],
          ["互动率", `${(data.engagement_rate * 100).toFixed(2)}%`],
        ].map(([label, value], index) => (
          <div key={label}>
            <span>{String(index + 1).padStart(2, "0")}</span>
            <strong>{typeof value === "number" ? value.toLocaleString() : value}</strong>
            <small>{label}</small>
          </div>
        ))}
      </section>
      <section className="panel">
        {hasData ? (
          <div className="analysis-copy">
            <p className="eyebrow">当前结果</p>
            <h2>基于 {data.sample_count} 条指标快照的下一轮建议</h2>
            {data.recommendations.map((recommendation) => (
              <p key={recommendation}>— {recommendation}</p>
            ))}
            <p>建议基于统一口径的确定性规则生成；样本不足时只提示继续积累，不输出夸张结论。</p>
          </div>
        ) : (
          <EmptyState title="还没有可复盘数据" description="发布完成并回收平台指标后，这里会展示统一口径的结果。" />
        )}
      </section>
    </>
  );
}

function AdministrationView({
  currentSession,
  workspaces,
  members,
  auditLogs,
  storageUsage,
  storageAttention,
  promptGovernance,
  promptEval,
  onWorkspaceCreated,
  onChanged,
  flash,
}: {
  currentSession: Session;
  workspaces: WorkspaceAccess[];
  members: Member[];
  auditLogs: AuditLog[];
  storageUsage: StorageUsage | null;
  storageAttention: StorageObjectAllocation[];
  promptGovernance: PromptGovernance | null;
  promptEval: PromptEvalGovernance | null;
  onWorkspaceCreated: (name: string) => Promise<void>;
  onChanged: () => Promise<void> | void;
  flash: (message: string) => void;
}) {
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [auditIntegrity, setAuditIntegrity] = useState<AuditIntegrity | null>(null);
  const [auditChecking, setAuditChecking] = useState(true);
  const [promptDraftSource, setPromptDraftSource] = useState<
    "active" | "builtin"
  >("active");
  const promptDraftBase = promptDraftSource === "builtin"
    ? promptGovernance?.builtin
    : promptGovernance?.active;

  const checkAuditIntegrity = useCallback(async () => {
    setAuditChecking(true);
    try {
      setAuditIntegrity(await api<AuditIntegrity>("/admin/audit-integrity"));
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setAuditChecking(false);
    }
  }, []);

  useEffect(() => {
    let active = true;
    api<AuditIntegrity>("/admin/audit-integrity")
      .then((result) => {
        if (active) setAuditIntegrity(result);
      })
      .catch((caught) => {
        if (active) setError(messageOf(caught));
      })
      .finally(() => {
        if (active) setAuditChecking(false);
      });
    return () => {
      active = false;
    };
  }, []);

  async function createWorkspace(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setBusy("workspace");
    setError("");
    try {
      await onWorkspaceCreated(String(form.get("name") || ""));
      formElement.reset();
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy("");
    }
  }

  async function addMember(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setBusy("member");
    setError("");
    try {
      await api<Member>("/admin/members", {
        method: "POST",
        body: {
          email: String(form.get("email") || ""),
          role: String(form.get("role") || "editor"),
        },
      });
      formElement.reset();
      flash("成员已加入当前工作区");
      await onChanged();
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy("");
    }
  }

  async function reconcileStorage(deleteOrphans: boolean) {
    if (
      deleteOrphans
      && !window.confirm(
        "确认清理孤儿对象？系统只会删除超过安全宽限期、且不在账本中的对象；该操作无法撤销。",
      )
    ) return;
    const busyKey = deleteOrphans ? "storage-cleanup" : "storage-reconcile";
    setBusy(busyKey);
    setError("");
    try {
      await api<QueueJob>("/admin/storage/reconcile", {
        method: "POST",
        body: { delete_orphans: deleteOrphans },
      });
      flash(
        deleteOrphans
          ? "存储核对与孤儿对象清理已加入任务队列"
          : "存储核对已加入任务队列",
      );
      await onChanged();
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy("");
    }
  }

  async function updateRole(member: Member, role: string) {
    setBusy(member.id);
    setError("");
    try {
      await api<Member>(`/admin/members/${member.id}`, {
        method: "PATCH",
        body: { role },
      });
      flash(`${member.display_name} 的权限已更新`);
      await onChanged();
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy("");
    }
  }

  async function removeMember(member: Member) {
    if (!window.confirm(`确认从当前工作区移除 ${member.display_name}？`)) return;
    setBusy(member.id);
    setError("");
    try {
      await api<void>(`/admin/members/${member.id}`, { method: "DELETE" });
      flash(`${member.display_name} 已移出工作区`);
      await onChanged();
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy("");
    }
  }



  async function createPromptEvalSuite(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    let cases: unknown;
    try {
      cases = JSON.parse(String(form.get("cases") || "[]"));
      if (!Array.isArray(cases)) throw new Error("用例必须是 JSON 数组");
    } catch (caught) {
      setError(caught instanceof Error ? `Eval 用例 JSON 无效：${caught.message}` : "Eval 用例 JSON 无效");
      return;
    }
    setBusy("eval-suite-create");
    setError("");
    try {
      await api<PromptEvalSuite>("/admin/prompt-eval/suites", {
        method: "POST",
        body: {
          name: String(form.get("name") || ""),
          description: String(form.get("description") || ""),
          cases,
        },
      });
      formElement.reset();
      flash("Eval 套件草稿已创建，需由另一名管理员激活");
      await onChanged();
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy("");
    }
  }

  async function activatePromptEvalSuite(suite: PromptEvalSuite) {
    const verb = suite.status === "retired" ? "重新激活" : "激活";
    if (!window.confirm(
      `确认${verb} ${suite.version}？现有 Prompt 的旧评测证据将立即失效。`,
    )) return;
    setBusy(`eval-suite-${suite.id}`);
    setError("");
    try {
      await api<PromptEvalSuite>(
        `/admin/prompt-eval/suites/${suite.id}/activate`,
        { method: "POST" },
      );
      flash(`${suite.version} 已成为当前 Eval 门禁`);
      await onChanged();
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy("");
    }
  }

  async function evaluatePromptRelease(release: PromptRelease) {
    setBusy(`eval-release-${release.id}`);
    setError("");
    try {
      await api<PromptEvalRun>(
        `/admin/prompt-releases/${release.id}/evaluate`,
        { method: "POST", body: {} },
      );
      flash(`${release.version} 评测已进入队列，页面会自动刷新结果`);
      await onChanged();
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy("");
    }
  }

  function currentEvalRun(releaseId: string) {
    const activeSuiteId = promptEval?.active_suite?.id;
    if (!activeSuiteId) return undefined;
    return promptEval.runs.find(
      (run) => run.prompt_release_id === releaseId && run.suite_id === activeSuiteId,
    );
  }

  async function createPromptRelease(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setBusy("prompt-create");
    setError("");
    try {
      await api<PromptRelease>("/admin/prompt-releases", {
        method: "POST",
        body: {
          change_summary: String(form.get("change_summary") || ""),
          prompts: {
            plan: String(form.get("plan") || ""),
            generate: String(form.get("generate") || ""),
            review: String(form.get("review") || ""),
          },
        },
      });
      formElement.reset();
      flash("Prompt 草稿已创建，需由另一名管理员审批后才能发布");
      await onChanged();
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy("");
    }
  }

  async function reviewPromptRelease(
    release: PromptRelease,
    action: "approve" | "reject" | "activate",
  ) {
    let body: { note: string } | undefined;
    if (action === "reject") {
      const reason = window.prompt("请输入拒绝原因");
      if (!reason?.trim()) return;
      body = { note: reason.trim() };
    } else if (action === "approve") {
      body = { note: "" };
    } else {
      const verb = release.status === "retired" ? "回滚到" : "激活";
      if (!window.confirm(
        `确认${verb} ${release.version}？新的工作流将使用这一版本。`,
      )) return;
    }

    setBusy(`prompt-${release.id}`);
    setError("");
    try {
      await api<PromptRelease>(
        `/admin/prompt-releases/${release.id}/${action}`,
        { method: "POST", ...(body ? { body } : {}) },
      );
      flash(
        action === "approve"
          ? "Prompt 版本已审批"
          : action === "reject"
            ? "Prompt 版本已拒绝"
            : release.status === "retired"
              ? "Prompt 版本已回滚并生效"
              : "Prompt 版本已激活",
      );
      await onChanged();
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy("");
    }
  }

  function memberName(userId: string | null) {
    if (!userId) return "—";
    return members.find((member) => member.user_id === userId)?.display_name
      || userId.slice(0, 8);
  }
  return (
    <>
      <PageHeading
        eyebrow="Administration"
        title="团队、Prompt 治理与审计"
        description="管理协作边界、Prompt 双人审批与回滚，以及关键操作记录。只有管理员可以访问本页。"
      />
      {error ? <p className="inline-error" role="alert">{error}</p> : null}
      <section className="admin-form-grid">
        <article className="panel">
          <div className="panel-heading">
            <div><p className="eyebrow">Workspace</p><h2>创建独立工作区</h2></div>
          </div>
          <form className="stack-form panel-form" onSubmit={createWorkspace}>
            <label>
              工作区名称
              <input name="name" required minLength={1} maxLength={120} placeholder="新品牌内容中心" />
              <small>新工作区的数据、成员、连接器和审计记录相互隔离。</small>
            </label>
            <Button busy={busy === "workspace"} type="submit">创建并切换</Button>
          </form>
        </article>
        <article className="panel">
          <div className="panel-heading">
            <div><p className="eyebrow">Member</p><h2>添加已注册成员</h2></div>
          </div>
          <form className="stack-form panel-form" onSubmit={addMember}>
            <div className="form-grid">
              <label>
                成员邮箱
                <input name="email" type="email" required placeholder="member@example.com" />
              </label>
              <label>
                初始角色
                <select name="role" defaultValue="editor">
                  <option value="viewer">只读成员</option>
                  <option value="editor">内容编辑</option>
                  <option value="reviewer">审核人员</option>
                  <option value="admin">管理员</option>
                </select>
              </label>
            </div>
            <Button busy={busy === "member"} type="submit">添加成员</Button>
          </form>
        </article>
      </section>

      <section className="panel admin-section">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">Storage governance</p>
            <h2>对象存储配额与一致性</h2>
          </div>
          <div className="storage-actions">
            <Button
              type="button"
              kind="ghost"
              busy={busy === "storage-reconcile"}
              onClick={() => void reconcileStorage(false)}
            >
              核对账本
            </Button>
            <Button
              type="button"
              kind="danger"
              busy={busy === "storage-cleanup"}
              onClick={() => void reconcileStorage(true)}
            >
              清理孤儿对象
            </Button>
          </div>
        </div>
        {storageUsage ? (
          <>
            <div className="metric-grid storage-metrics" aria-label="工作区存储统计">
              <div>
                <span>已计费容量</span>
                <strong>{formatBytes(storageUsage.used_bytes)}</strong>
                <small>上限 {formatBytes(storageUsage.max_bytes)}</small>
              </div>
              <div>
                <span>已计费对象</span>
                <strong>{storageUsage.used_objects.toLocaleString()}</strong>
                <small>上限 {storageUsage.max_objects.toLocaleString()} 个</small>
              </div>
              <div>
                <span>写入预留</span>
                <strong>{storageUsage.reserved_objects.toLocaleString()}</strong>
                <small>{formatBytes(storageUsage.reserved_bytes)} 尚未转为正式对象</small>
              </div>
              <div>
                <span>需要关注</span>
                <strong>{storageAttention.length.toLocaleString()}</strong>
                <small>
                  缺失 {storageUsage.missing_objects} · 待删 {storageUsage.delete_pending_objects}
                  {storageUsage.integrity_error_objects
                    ? ` · 完整性异常 ${storageUsage.integrity_error_objects}`
                    : ""}
                  {storageUsage.abandoned_reservations
                    ? ` · 已释放 ${storageUsage.abandoned_reservations}`
                    : ""}
                </small>
              </div>
            </div>
            <p className="form-note storage-note" role="status">
              {storageUsage.unverified_objects
                ? `${storageUsage.unverified_objects} 个历史对象尚未验证大小；完成核对前会阻止新增上传。`
                : "账本中的对象大小均已验证。"}
              {storageUsage.last_reconciled_at
                ? ` 最近一次完成核对：${formatDateTime(storageUsage.last_reconciled_at)}。`
                : " 尚未完成过全量核对。"}
            </p>
          </>
        ) : (
          <p className="form-note" role="status">正在读取当前工作区的存储账本…</p>
        )}
        <DataTable
          headers={["状态", "文件", "归属", "大小", "重试 / 原因", "更新时间"]}
          rows={storageAttention.map((item) => [
            <StatusBadge key="status" value={item.status} />,
            <span key="file"><strong>{item.filename}</strong><br /><small>{item.category}</small></span>,
            <code key="owner">{item.owner_type} · {item.owner_id.slice(0, 8)}</code>,
            item.size_verified ? formatBytes(item.size_bytes) : "待验证",
            item.last_error
              ? `${item.delete_attempts} 次 · ${item.last_error}`
              : item.delete_attempts
                ? `${item.delete_attempts} 次`
                : "—",
            formatDateTime(item.updated_at),
          ])}
          empty="当前没有缺失、完整性异常、待删除或已释放的对象"
        />
      </section>


      <section className="panel admin-section">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">AI governance</p>
            <h2>Prompt 审批、发布与回滚</h2>
          </div>
          {promptGovernance ? (
            <StatusBadge
              value={promptGovernance.ready_for_generation ? "active" : "blocked"}
            />
          ) : null}
        </div>
        {promptGovernance ? (
          <>
            {!promptGovernance.ready_for_generation ? (
              <p className="permission-note" role="status">
                生成已被治理策略阻断：{promptGovernance.generation_block_reason}
              </p>
            ) : null}
            {promptGovernance.governance_required
              && promptGovernance.active.source === "builtin" ? (
                <p className="form-note">
                  生产初始化顺序：添加第二名管理员；创建 Eval 套件并由对方激活；
                  创建 Prompt 草稿；使用当前目标模型运行评测；最后由另一名管理员审批并激活。
                </p>
              ) : null}
            <div className="prompt-active-summary">
              <div>
                <small>当前来源</small>
                <strong>
                  {promptGovernance.active.source === "builtin"
                    ? "内置安全基线"
                    : "工作区已审批版本"}
                </strong>
              </div>
              <div>
                <small>生效版本</small>
                <strong>{promptGovernance.active.version}</strong>
              </div>
              <div>
                <small>发布标识</small>
                <strong>
                  {promptGovernance.active.release_id?.slice(0, 8) || "builtin"}
                </strong>
              </div>
              <div>
                <small>Plan 哈希</small>
                <code>
                  {promptGovernance.active.prompt_hashes.plan.slice(0, 12)}
                </code>
              </div>
            </div>
            <form
              key={`${promptGovernance.active.version}-${promptDraftSource}`}
              className="stack-form prompt-release-form"
              onSubmit={createPromptRelease}
            >
              <div className="prompt-draft-toolbar">
                <div>
                  <p className="eyebrow">Immutable draft</p>
                  <h3>
                    {promptDraftSource === "builtin"
                      ? "基于最新内容 Agent 基线创建草稿"
                      : "基于当前生效版本创建新草稿"}
                  </h3>
                  <p className="form-note">
                    草稿创建后不可修改；创建者不能自行审批，必须由另一名管理员复核。
                    审批与激活前还必须通过当前 Eval 套件。审计日志只保存版本与哈希，不保存 Prompt 正文。
                  </p>
                </div>
                <Button
                  type="button"
                  kind="ghost"
                  onClick={() => setPromptDraftSource((source) => (
                    source === "active" ? "builtin" : "active"
                  ))}
                >
                  {promptDraftSource === "active"
                    ? `载入最新 Agent 基线 ${promptGovernance.builtin.version}`
                    : "改回当前生效版本"}
                </Button>
              </div>
              <label>
                变更摘要
                <input
                  name="change_summary"
                  required
                  minLength={3}
                  maxLength={500}
                  placeholder="说明目标、评测结论和风险边界"
                />
              </label>
              <div className="prompt-editor-grid">
                {(["plan", "generate", "review"] as PromptStage[]).map((stage) => (
                  <label key={stage}>
                    {stage} Prompt
                    <textarea
                      name={stage}
                      required
                      minLength={20}
                      maxLength={20000}
                      defaultValue={promptDraftBase?.prompts[stage] || ""}
                    />
                    <small>
                      SHA-256 {promptDraftBase?.prompt_hashes[stage].slice(0, 12)}…
                    </small>
                  </label>
                ))}
              </div>
              <Button busy={busy === "prompt-create"} type="submit">
                创建不可变草稿
              </Button>
            </form>
          </>
        ) : (
          <EmptyState
            title="正在读取 Prompt 治理状态"
            description="数据加载完成后可查看生效版本并创建新草稿。"
          />
        )}
      </section>

      <section className="panel admin-section">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">Evaluation gate</p>
            <h2>版本化 Prompt Eval 套件</h2>
          </div>
          {promptEval?.active_suite
            ? <StatusBadge value="active" />
            : <StatusBadge value="blocked" />}
        </div>
        {promptEval?.active_suite ? (
          <>
            <div className="prompt-active-summary">
              <div><small>当前套件</small><strong>{promptEval.active_suite.version}</strong></div>
              <div><small>名称</small><strong>{promptEval.active_suite.name}</strong></div>
              <div><small>确定性用例</small><strong>{promptEval.active_suite.cases.length}</strong></div>
              <div><small>Suite 哈希</small><code>{promptEval.active_suite.suite_hash.slice(0, 12)}</code></div>
            </div>
            <details className="eval-suite-details">
              <summary>查看当前门禁的完整用例快照</summary>
              <pre>{JSON.stringify(promptEval.active_suite.cases, null, 2)}</pre>
            </details>
          </>
        ) : (
          <p className="permission-note">
            当前没有生效的 Eval 套件，所有 Prompt 审批与激活都会 fail closed。
          </p>
        )}
        <form className="stack-form prompt-release-form" onSubmit={createPromptEvalSuite}>
          <div>
            <p className="eyebrow">Immutable suite</p>
            <h3>创建不可变 Eval 套件草稿</h3>
            <p className="form-note">
              套件必须覆盖 plan、generate、review，并为每个用例提供确定性断言。
              创建者不能自行激活；运行结果不保存模型正文，只保存哈希、字节数与失败项。
            </p>
          </div>
          <div className="form-grid">
            <label>
              套件名称
              <input name="name" required minLength={3} maxLength={160} placeholder="公众号内容质量基线 2026-Q3" />
            </label>
            <label>
              说明
              <input name="description" maxLength={2000} placeholder="覆盖输出契约、必要事实与风险边界" />
            </label>
          </div>
          <label>
            用例 JSON
            <textarea
              className="eval-case-editor"
              name="cases"
              required
              defaultValue={JSON.stringify(DEFAULT_PROMPT_EVAL_CASES, null, 2)}
              spellCheck={false}
            />
            <small>支持 required_paths、expected_values、required_substrings、forbidden_substrings 和 max_output_bytes。</small>
          </label>
          <Button busy={busy === "eval-suite-create"} type="submit">创建 Eval 套件草稿</Button>
        </form>
        <DataTable
          headers={["套件", "名称 / 哈希", "用例", "创建时间", "状态", "操作"]}
          rows={(promptEval?.suites || []).map((suite) => [
            <code key="version">{suite.version}</code>,
            <div className="prompt-release-details" key="name">
              <strong>{suite.name}</strong>
              <small>SHA-256 {suite.suite_hash}</small>
            </div>,
            String(suite.cases.length),
            formatDateTime(suite.created_at),
            <StatusBadge key="status" value={suite.status} />,
            <div className="table-actions" key="actions">
              {suite.status === "active" ? <span>当前门禁</span> : null}
              {suite.status !== "active" && suite.created_by_user_id === currentSession.user.id
                ? <span>等待其他管理员</span>
                : null}
              {suite.status !== "active" && suite.created_by_user_id !== currentSession.user.id ? (
                <button
                  className="table-link"
                  disabled={busy === `eval-suite-${suite.id}`}
                  onClick={() => void activatePromptEvalSuite(suite)}
                >
                  {suite.status === "retired" ? "重新激活" : "激活"}
                </button>
              ) : null}
            </div>,
          ])}
          empty="还没有 Eval 套件；创建并由另一名管理员激活后才能审批 Prompt"
        />
      </section>

      <section className="panel admin-section">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">Evaluation history</p>
            <h2>最近 {promptEval?.runs.length || 0} 次 Prompt 评测</h2>
          </div>
        </div>
        <DataTable
          headers={["时间", "Prompt", "套件", "Provider / 模型", "结果", "证据"]}
          rows={(promptEval?.runs || []).map((run) => [
            formatDateTime(run.created_at),
            <code key="release">{run.prompt_release_id.slice(0, 8)}</code>,
            <code key="suite">{run.suite_id.slice(0, 8)}</code>,
            `${run.provider || run.requested_provider} / ${run.model || "等待执行"}`,
            <StatusBadge key="status" value={run.status} />,
            <details className="eval-run-details" key="evidence">
              <summary>哈希化结果</summary>
              <pre>{JSON.stringify(run.result_json, null, 2)}</pre>
              {run.error ? <p className="inline-error">{run.error}</p> : null}
            </details>,
          ])}
          empty="还没有 Prompt 评测记录"
        />
      </section>

      <section className="panel admin-section">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">Release history</p>
            <h2>{promptGovernance?.releases.length || 0} 个工作区 Prompt 版本</h2>
          </div>
        </div>
        <DataTable
          headers={["版本", "变更摘要", "创建 / 复核", "时间", "当前 Eval", "状态", "操作"]}
          rows={(promptGovernance?.releases || []).map((release) => [
            <code key="version">{release.version}</code>,
            <div className="prompt-release-details" key="summary">
              <strong>{release.change_summary}</strong>
              <details>
                <summary>查看正文与哈希</summary>
                {(["plan", "generate", "review"] as PromptStage[]).map((stage) => (
                  <section key={stage}>
                    <small>
                      {stage} · SHA-256 {release.prompt_hashes[stage]}
                    </small>
                    <pre>{release.prompts[stage]}</pre>
                  </section>
                ))}
              </details>
            </div>,
            `${memberName(release.created_by_user_id)} / ${memberName(release.reviewed_by_user_id)}`,
            formatDateTime(release.created_at),
            <div className="table-actions" key="eval">
              {currentEvalRun(release.id) ? (
                <StatusBadge value={currentEvalRun(release.id)?.status || "queued"} />
              ) : <span>无当前证据</span>}
              {release.status !== "rejected" && promptEval?.active_suite ? (
                <button
                  className="table-link"
                  disabled={busy === `eval-release-${release.id}`}
                  onClick={() => void evaluatePromptRelease(release)}
                >
                  {currentEvalRun(release.id) ? "重新评测" : "运行评测"}
                </button>
              ) : null}
            </div>,
            <StatusBadge key="status" value={release.status} />,
            <div className="table-actions" key="actions">
              {release.status === "draft"
                && release.created_by_user_id !== currentSession.user.id ? (
                  <>
                    <button
                      className="table-link"
                      disabled={
                        busy === `prompt-${release.id}`
                        || currentEvalRun(release.id)?.status !== "passed"
                      }
                      title={currentEvalRun(release.id)?.status === "passed" ? "审批" : "需先通过当前 Eval 套件"}
                      onClick={() => void reviewPromptRelease(release, "approve")}
                    >
                      审批
                    </button>
                    <button
                      className="table-link danger-text"
                      disabled={busy === `prompt-${release.id}`}
                      onClick={() => void reviewPromptRelease(release, "reject")}
                    >
                      拒绝
                    </button>
                  </>
                ) : null}
              {release.status === "draft"
                && release.created_by_user_id === currentSession.user.id ? (
                  <span>等待其他管理员</span>
                ) : null}
              {release.status === "approved" ? (
                <button
                  className="table-link"
                  disabled={
                    busy === `prompt-${release.id}`
                    || currentEvalRun(release.id)?.status !== "passed"
                  }
                  title={currentEvalRun(release.id)?.status === "passed" ? "激活" : "需先通过当前 Eval 套件"}
                  onClick={() => void reviewPromptRelease(release, "activate")}
                >
                  激活
                </button>
              ) : null}
              {release.status === "retired" ? (
                <button
                  className="table-link"
                  disabled={
                    busy === `prompt-${release.id}`
                    || currentEvalRun(release.id)?.status !== "passed"
                  }
                  title={currentEvalRun(release.id)?.status === "passed" ? "回滚" : "需先通过当前 Eval 套件"}
                  onClick={() => void reviewPromptRelease(release, "activate")}
                >
                  回滚
                </button>
              ) : null}
              {release.status === "active" ? <span>当前生效</span> : null}
              {release.status === "rejected" ? <span>已拒绝</span> : null}
            </div>,
          ])}
          empty="当前工作区还没有自定义 Prompt 版本，继续使用内置安全基线"
        />
      </section>
      <section className="panel admin-section">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">Access control</p>
            <h2>{members.length} 名工作区成员</h2>
          </div>
        </div>
        <DataTable
          headers={["成员", "邮箱", "加入时间", "角色", "操作"]}
          rows={members.map((member) => [
            member.display_name,
            member.email,
            formatDateTime(member.created_at),
            <select
              className="compact-select"
              key="role"
              value={member.role}
              disabled={busy === member.id}
              aria-label={`修改 ${member.display_name} 的角色`}
              onChange={(event) => void updateRole(member, event.target.value)}
            >
              <option value="viewer">只读成员</option>
              <option value="editor">内容编辑</option>
              <option value="reviewer">审核人员</option>
              <option value="admin">管理员</option>
            </select>,
            member.user_id === currentSession.user.id ? (
              <span key="self">当前用户</span>
            ) : (
              <button
                className="table-link danger-text"
                key="remove"
                disabled={busy === member.id}
                onClick={() => void removeMember(member)}
              >
                移除
              </button>
            ),
          ])}
          empty="当前工作区还没有成员"
        />
      </section>

      <section className="panel admin-section">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">Workspaces</p>
            <h2>{workspaces.length} 个可访问工作区</h2>
          </div>
        </div>
        <DataTable
          headers={["工作区", "标识", "我的角色", "当前状态"]}
          rows={workspaces.map((workspace) => [
            workspace.name,
            workspace.slug,
            ROLE_LABEL[workspace.role] || workspace.role,
            workspace.id === currentSession.workspace.id ? "当前工作区" : "可切换",
          ])}
          empty="没有可访问的工作区"
        />
      </section>

      <section className="panel admin-section">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">Audit trail</p>
            <h2>最近 {auditLogs.length} 条审计记录</h2>
          </div>
          <div className="table-actions">
            {auditIntegrity ? (
              <StatusBadge value={auditIntegrity.valid ? "passed" : "blocked"} />
            ) : null}
            <Button
              type="button"
              kind="ghost"
              busy={auditChecking}
              onClick={() => void checkAuditIntegrity()}
            >
              重新核验
            </Button>
          </div>
        </div>
        {auditIntegrity?.valid ? (
          <p className="form-note" role="status">
            已验证 {auditIntegrity.checked_entries} 条连续记录，链头序号 {auditIntegrity.head_sequence}
            {auditIntegrity.head_hash ? ` · SHA-256 ${auditIntegrity.head_hash.slice(0, 16)}…` : ""}。
          </p>
        ) : auditIntegrity ? (
          <p className="permission-note" role="alert">
            审计链完整性异常：{auditIntegrity.reason || "unknown"}
            {auditIntegrity.first_invalid_sequence
              ? `，首个异常序号 ${auditIntegrity.first_invalid_sequence}`
              : ""}。请暂停高风险操作并保留数据库与对象存储快照。
          </p>
        ) : null}
        <DataTable
          headers={["序号 / 哈希", "时间", "操作者", "动作", "对象", "详情"]}
          rows={auditLogs.map((log) => [
            <code key="integrity">
              #{log.chain_sequence} · {log.entry_hash.slice(0, 12)}…
            </code>,
            formatDateTime(log.created_at),
            log.actor_display_name || "系统任务",
            log.action,
            `${log.entity_type}${log.entity_id ? ` · ${log.entity_id.slice(0, 8)}` : ""}`,
            Object.keys(log.metadata_json).length ? (
              <code className="audit-metadata" key="metadata">
                {JSON.stringify(log.metadata_json)}
              </code>
            ) : "—",
          ])}
          empty="还没有审计记录"
        />
      </section>
    </>
  );
}

function JobsView({
  jobs,
  role,
  onNavigate,
  onChanged,
  flash,
}: {
  jobs: QueueJob[];
  role: string;
  onNavigate: (view: View) => void;
  onChanged: () => Promise<void> | void;
  flash: (message: string) => void;
}) {
  const [error, setError] = useState("");
  const [reviewJobId, setReviewJobId] = useState("");
  const [providerChecked, setProviderChecked] = useState(false);
  const [reviewNote, setReviewNote] = useState("");
  const [reviewBusy, setReviewBusy] = useState<"retry" | "abandon" | "">("");
  const [providerInvocations, setProviderInvocations] = useState<ProviderInvocationAttempt[]>([]);
  const [providerInvocationsLoading, setProviderInvocationsLoading] = useState(false);
  const [providerInvocationsError, setProviderInvocationsError] = useState("");
  const [providerInvocationsTruncated, setProviderInvocationsTruncated] = useState(false);
  const canRetry = roleAtLeast(role, "editor");
  const canReview = roleAtLeast(role, "reviewer");
  const reviewJob = jobs.find((job) => job.id === reviewJobId && job.status === "manual_review");

  async function retry(job: QueueJob) {
    setError("");
    try {
      await api(`/jobs/${job.id}/retry`, { method: "POST" });
      flash("失败任务已重置并进入重试队列");
      await onChanged();
    } catch (caught) {
      setError(messageOf(caught));
    }
  }

  async function openReview(job: QueueJob) {
    setReviewJobId(job.id);
    setProviderChecked(false);
    setReviewNote("");
    setError("");
    setProviderInvocations([]);
    setProviderInvocationsError("");
    setProviderInvocationsTruncated(false);
    setProviderInvocationsLoading(true);
    try {
      const result = await apiAllPages<ProviderInvocationAttempt>(
        `/jobs/${job.id}/provider-invocations`,
        { pageLimit: 100, maxPages: 10 },
      );
      setProviderInvocations(result.items);
      setProviderInvocationsTruncated(result.truncated);
    } catch (caught) {
      setProviderInvocationsError(messageOf(caught));
    } finally {
      setProviderInvocationsLoading(false);
    }
  }

  async function resolveReview(decision: "retry" | "abandon") {
    if (!reviewJob || !providerChecked || reviewNote.trim().length < 8) return;
    if (decision === "abandon" && !window.confirm("确认放弃此任务？任务会保留为失败记录，不会再次调用供应商。")) return;
    setReviewBusy(decision);
    setError("");
    try {
      await api(`/jobs/${reviewJob.id}/manual-review`, {
        method: "POST",
        body: {
          decision,
          provider_checked: true,
          note: reviewNote.trim(),
        },
      });
      flash(decision === "retry" ? "核对记录已保存，任务进入重试队列" : "核对记录已保存，任务已放弃");
      setReviewJobId("");
      setProviderChecked(false);
      setReviewNote("");
      await onChanged();
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setReviewBusy("");
    }
  }

  return (
    <>
      <PageHeading
        eyebrow="Operations"
        title="任务队列"
        description="生成、索引、素材和发布任务使用租约、幂等键与退避重试。"
      />
      {error ? <p className="inline-error">{error}</p> : null}
      {!canRetry ? <p className="permission-note">当前为只读权限，可查看任务状态与错误信息。</p> : null}
      {canRetry && !canReview ? <p className="permission-note">你可以重试普通失败任务；供应商结果不确定的任务需由审核者核对后处置。</p> : null}
      {reviewJob ? (
        <section className="panel manual-review-panel" aria-labelledby="manual-review-title">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">Provider safety checkpoint</p>
              <h2 id="manual-review-title">核对供应商结果后再决定</h2>
              <p>{reviewJob.context.campaign_name || reviewJob.context.content_title || reviewJob.job_type} · {reviewJob.id.slice(0, 8)}</p>
            </div>
            <StatusBadge value="manual_review" />
          </div>
          <div className="manual-review-body">
            <div className="manual-review-warning">
              <strong>为什么被拦截</strong>
              <p>{reviewJob.manual_review?.context_json.possible_side_effect || "供应商可能已接收或计费，但系统没有保存最终结果。"}</p>
              <code>{reviewJob.manual_review?.reason_code || "provider_outcome_unknown"}</code>
            </div>
            <div className="provider-ledger" aria-live="polite">
              <div className="provider-ledger-heading">
                <div>
                  <strong>ContentFlow 已保存的调用证据</strong>
                  <p>这里只保存请求/响应摘要、供应商请求号和用量，不保存提示词、正文或密钥。</p>
                </div>
                {providerInvocationsLoading ? <span className="button-spinner" aria-hidden="true" /> : null}
              </div>
              {providerInvocationsError ? (
                <p className="inline-error">调用证据读取失败：{providerInvocationsError}</p>
              ) : providerInvocations.length ? (
                <div className="provider-ledger-list">
                  {providerInvocationsTruncated ? (
                    <p className="pagination-warning">仅显示最近 1000 条调用证据，请使用 API 分页继续取证。</p>
                  ) : null}
                  {providerInvocations.map((attempt) => (
                    <article className="provider-ledger-row" key={attempt.id}>
                      <div>
                        <strong>{attempt.operation}</strong>
                        <span>{attempt.provider_name} · {attempt.model_name} · 第 {attempt.attempt_number} 次</span>
                      </div>
                      <StatusBadge value={attempt.status} />
                      <dl>
                        <div><dt>请求时间</dt><dd>{formatDateTime(attempt.started_at)}</dd></div>
                        <div><dt>供应商请求号</dt><dd><code>{attempt.provider_request_id || "未返回"}</code></dd></div>
                        <div><dt>请求摘要</dt><dd><code>{attempt.request_sha256.slice(0, 16)}…</code></dd></div>
                        <div><dt>Token</dt><dd>{attempt.total_tokens ?? "未报告"}</dd></div>
                      </dl>
                      <p className="provider-ledger-note">
                        {attempt.idempotency_key_sent
                          ? "已发送 Idempotency-Key；这只证明请求头已发送，不代表供应商确认支持幂等。"
                          : "供应商适配器未发送 Idempotency-Key，必须以供应商控制台记录为准。"}
                      </p>
                    </article>
                  ))}
                </div>
              ) : providerInvocationsLoading ? null : (
                <p className="provider-ledger-empty">没有可用调用证据。旧任务或账本提交前中断的任务仍需按下方步骤到供应商控制台核对。</p>
              )}
            </div>
            <ol>
              {(reviewJob.manual_review?.context_json.required_checks || [
                "打开当前供应商控制台，查看该时间窗口内的调用记录。",
                "确认是否已有对应请求、计费或结果。",
                "仅在确认没有结果时重试；已有结果或无法确认时应放弃并人工对账。",
              ]).map((step) => <li key={step}>{step}</li>)}
            </ol>
            <div className="stack-form">
              <label className="manual-review-confirmation">
                <input
                  type="checkbox"
                  checked={providerChecked}
                  onChange={(event) => setProviderChecked(event.target.checked)}
                />
                <span>我已在供应商控制台核对请求、计费和结果，不是仅凭本页错误文字判断。</span>
              </label>
              <label>
                核对记录
                <textarea
                  value={reviewNote}
                  onChange={(event) => setReviewNote(event.target.value)}
                  maxLength={2000}
                  placeholder="至少 8 个字符：核对了哪个时间窗口、看到什么结果、为什么选择重试或放弃。"
                />
                <small>{reviewNote.trim().length} / 2000；该记录会与处置人、时间和结论一起保留。</small>
              </label>
              <div className="form-actions">
                <button
                  className="button button-primary"
                  type="button"
                  disabled={!providerChecked || reviewNote.trim().length < 8 || Boolean(reviewBusy)}
                  onClick={() => void resolveReview("retry")}
                >
                  {reviewBusy === "retry" ? <span className="button-spinner" aria-hidden="true" /> : null}
                  确认没有结果，允许重试
                </button>
                <button
                  className="button button-danger"
                  type="button"
                  disabled={!providerChecked || reviewNote.trim().length < 8 || Boolean(reviewBusy)}
                  onClick={() => void resolveReview("abandon")}
                >
                  {reviewBusy === "abandon" ? <span className="button-spinner" aria-hidden="true" /> : null}
                  已有或无法确认，放弃任务
                </button>
                <button
                  className="button button-ghost"
                  type="button"
                  disabled={Boolean(reviewBusy)}
                  onClick={() => {
                    setReviewJobId("");
                    setProviderInvocations([]);
                    setProviderInvocationsError("");
                    setProviderInvocationsTruncated(false);
                  }}
                >
                  暂不处理
                </button>
              </div>
            </div>
          </div>
        </section>
      ) : null}
      <section className="panel">
        <DataTable
          headers={["项目 / 内容", "任务类型", "执行时间", "尝试次数", "状态", "最近错误", "操作"]}
          rows={jobs.map((job) => [
            <ProjectIdentity key="project" context={job.context} compact />,
            job.job_type,
            formatDateTime(job.run_at),
            `${job.attempts} / ${job.max_attempts}`,
            <StatusBadge key="status" value={job.status} />,
            job.last_error || "—",
            job.status === "manual_review" ? (
              canReview ? (
                <button className="table-link" key="review" onClick={() => void openReview(job)}>核对处理</button>
              ) : <span key="review-required">需审核者处理</span>
            ) : canRetry && job.status === "failed" ? (
              job.job_type === "publish.dispatch" ? (
                <button className="table-link" key="publish" onClick={() => onNavigate("publishing")}>
                  到发布页处理
                </button>
              ) : (
                <button className="table-link" key="retry" onClick={() => void retry(job)}>重试</button>
              )
            ) : "—",
          ])}
          empty="任务队列为空"
        />
      </section>
    </>
  );
}

function DataTable({
  headers,
  rows,
  empty,
}: {
  headers: string[];
  rows: ReactNode[][];
  empty: string;
}) {
  if (!rows.length) return <EmptyState title={empty} description="相关记录生成后会显示在这里。" />;
  return (
    <div className="table-wrap">
      <table>
        <thead><tr>{headers.map((header) => <th key={header}>{header}</th>)}</tr></thead>
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr key={rowIndex}>
              {row.map((cell, cellIndex) => (
                <td key={`${rowIndex}-${cellIndex}`} data-label={headers[cellIndex]}>{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
  }).format(new Date(value));
}

function formatDateTime(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function formatBytes(value: number | null): string {
  if (value === null) return "—";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  if (value < 1024 * 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`;
  if (value < 1024 ** 4) return `${(value / 1024 ** 3).toFixed(1)} GB`;
  return `${(value / 1024 ** 4).toFixed(1)} TB`;
}

function toLocalInput(date: Date): string {
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}
