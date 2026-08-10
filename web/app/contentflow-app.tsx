"use client";

import {
  FormEvent,
  ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";
import {
  ApiError,
  api,
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
  error: string | null;
  created_at: string;
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
  attempts: number;
  error: string | null;
  external_id: string | null;
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
};

type DashboardSummary = {
  campaigns: number;
  runs_active: number;
  contents_needing_review: number;
  assets_processing: number;
  publishes_scheduled: number;
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
  created_at: string;
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
        must_include: ["人工复核"],
        product_facts: ["整理内容工作流"],
        call_to_action: "查看完整方案",
      },
      knowledge: [],
    },
    required_paths: ["content_angle", "key_message", "posting_window"],
  },
  {
    name: "wechat-generation-contract",
    stage: "generate",
    input_json: {
      brief: {
        product_name: "ContentFlow",
        city: "北京",
        must_include: ["人工复核"],
        product_facts: ["整理内容工作流"],
        call_to_action: "查看完整方案",
      },
      platform: "wechat",
      plan: {},
      knowledge: [],
    },
    required_paths: ["title", "body", "layout"],
    required_substrings: ["ContentFlow"],
  },
  {
    name: "review-output-contract",
    stage: "review",
    input_json: {
      brief: {
        product_name: "ContentFlow",
        city: "北京",
        must_include: ["人工复核"],
        product_facts: ["整理内容工作流"],
        call_to_action: "查看完整方案",
      },
      platform: "wechat",
      content: { title: "测试标题", body: "测试正文" },
      knowledge: [],
    },
    required_paths: ["risk_level"],
    expected_values: { passed: true },
  },
];

type DataState = {
  dashboard: DashboardSummary;
  campaigns: Campaign[];
  contents: Content[];
  assets: Asset[];
  channels: Channel[];
  publishes: PublishJob[];
  knowledge: KnowledgeDocument[];
  jobs: QueueJob[];
  metrics: MetricsSummary;
  workspaces: WorkspaceAccess[];
  members: Member[];
  auditLogs: AuditLog[];
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
    jobs_failed: 0,
  },
  campaigns: [],
  contents: [],
  assets: [],
  channels: [],
  publishes: [],
  knowledge: [],
  jobs: [],
  workspaces: [],
  members: [],
  auditLogs: [],
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

const PLATFORM: Record<string, string> = {
  xiaohongshu: "小红书",
  douyin: "抖音",
  wechat: "公众号",
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
  blocked: "规则拦截",
  cancelled: "已取消",
  connected: "已连接",
  draft: "草稿",
  draft_created: "已建草稿",
  exported: "已导出",
  export_only: "导出模式",
  error: "执行错误",
  failed: "失败",
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
};

const NAV: Array<{ id: View; label: string; icon: IconName }> = [
  { id: "dashboard", label: "总览", icon: "grid" },
  { id: "campaigns", label: "营销活动", icon: "campaign" },
  { id: "review", label: "内容审核", icon: "review" },
  { id: "assets", label: "素材中心", icon: "image" },
  { id: "publishing", label: "发布管理", icon: "send" },
  { id: "knowledge", label: "知识库", icon: "book" },
  { id: "channels", label: "平台连接", icon: "link" },
  { id: "metrics", label: "数据复盘", icon: "chart" },
  { id: "jobs", label: "任务队列", icon: "queue" },
  { id: "admin", label: "团队与审计", icon: "settings" },
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
    value === "failed" || value === "blocked" || value === "rejected"
      ? "danger"
      : value === "approved" ||
          value === "ready" ||
          value === "published" ||
          value === "exported" ||
          value === "succeeded" ||
          value === "indexed" ||
          value === "connected"
        ? "success"
        : value === "processing" ||
            value === "running" ||
            value === "queued" ||
            value === "scheduled"
          ? "info"
          : "neutral";
  return (
    <span className={`status status-${semantic}`}>{STATUS[value] || value}</span>
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
  const [data, setData] = useState<DataState>(EMPTY_DATA);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  const loadData = useCallback(async (silent = false) => {
    if (!silent) setRefreshing(true);
    try {
      const [
        dashboard,
        campaigns,
        contents,
        assets,
        channels,
        publishes,
        knowledge,
        jobs,
        metrics,
        workspaces,
        members,
        auditLogs,
        promptGovernance,
        promptEval,
      ] = await Promise.all([
        api<DashboardSummary>("/dashboard/summary"),
        api<Campaign[]>("/campaigns"),
        api<Content[]>("/contents"),
        api<Asset[]>("/assets"),
        api<Channel[]>("/channels"),
        api<PublishJob[]>("/publishing/jobs"),
        api<KnowledgeDocument[]>("/knowledge/documents"),
        api<QueueJob[]>("/jobs"),
        api<MetricsSummary>("/metrics/summary"),
        api<WorkspaceAccess[]>("/auth/workspaces"),
        session?.role === "admin"
          ? api<Member[]>("/admin/members")
          : Promise.resolve([]),
        session?.role === "admin"
          ? api<AuditLog[]>("/admin/audit-logs")
          : Promise.resolve([]),
        session?.role === "admin"
          ? api<PromptGovernance>("/admin/prompt-releases")
          : Promise.resolve(null),
        session?.role === "admin"
          ? api<PromptEvalGovernance>("/admin/prompt-eval")
          : Promise.resolve(null),
      ]);
      setData({
        dashboard,
        campaigns,
        contents,
        assets,
        channels,
        publishes,
        knowledge,
        jobs,
        metrics,
        workspaces,
        members,
        auditLogs,
        promptGovernance,
        promptEval,
      });
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
  }, [session]);

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

  useEffect(() => {
    if (!session) return;
    const initial = window.setTimeout(() => void loadData(), 0);
    const timer = window.setInterval(() => void loadData(true), 15_000);
    return () => {
      window.clearTimeout(initial);
      window.clearInterval(timer);
    };
  }, [session, loadData]);

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

  const visibleNav = NAV.filter(
    (item) => item.id !== "admin" || session.role === "admin",
  );
  const viewLabel = visibleNav.find((item) => item.id === view)?.label || "";

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand-lockup">
          <span className="brand-symbol">CF</span>
          <span className="brand-name">ContentFlow</span>
        </div>
        <nav aria-label="工作台导航">
          {visibleNav.map((item) => (
            <button
              key={item.id}
              className={view === item.id ? "active" : ""}
              onClick={() => setView(item.id)}
              aria-current={view === item.id ? "page" : undefined}
              title={item.label}
            >
              <Icon name={item.icon} />
              <span>{item.label}</span>
              {item.id === "review" && data.dashboard.contents_needing_review ? (
                <b>{data.dashboard.contents_needing_review}</b>
              ) : null}
            </button>
          ))}
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
          </div>
          <button
            className="icon-button"
            aria-label="刷新数据"
            onClick={() => void loadData()}
            disabled={refreshing}
          >
            <Icon name="refresh" />
          </button>
        </header>
        <div className="mobile-nav">
          {visibleNav.map((item) => (
            <button
              key={item.id}
              className={view === item.id ? "active" : ""}
              onClick={() => setView(item.id)}
            >
              {item.label}
            </button>
          ))}
        </div>
        <main className="workspace">
          {notice ? <div className="toast toast-success">{notice}</div> : null}
          {error ? (
            <div className="toast toast-error">
              <span>{error}</span>
              <button onClick={() => setError("")}>关闭</button>
            </div>
          ) : null}
          {view === "dashboard" ? (
            <DashboardView data={data} onNavigate={setView} />
          ) : null}
          {view === "campaigns" ? (
            <CampaignsView
              campaigns={data.campaigns}
              role={session.role}
              onChanged={() => loadData()}
              flash={flash}
            />
          ) : null}
          {view === "review" ? (
            <ReviewView
              contents={data.contents}
              role={session.role}
              onChanged={() => loadData()}
              flash={flash}
            />
          ) : null}
          {view === "assets" ? (
            <AssetsView
              assets={data.assets}
              contents={data.contents}
              role={session.role}
              onChanged={() => loadData()}
              flash={flash}
            />
          ) : null}
          {view === "publishing" ? (
            <PublishingView
              publishes={data.publishes}
              contents={data.contents}
              channels={data.channels}
              role={session.role}
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
              publishes={data.publishes}
              contents={data.contents}
              channels={data.channels}
              role={session.role}
              onChanged={() => loadData()}
              flash={flash}
            />
          ) : null}
          {view === "jobs" ? (
            <JobsView
              jobs={data.jobs}
              role={session.role}
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

  return (
    <>
      <PageHeading
        eyebrow="运营总览"
        title="内容工作流状态"
        description="查看从生成、审核到分发的关键阻塞点。数据每 15 秒自动更新。"
      />
      <section className="metric-grid" aria-label="关键指标">
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
            headers={["活动", "产品", "平台", "状态", "更新时间"]}
            rows={data.campaigns.slice(0, 6).map((campaign) => [
              campaign.name,
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
  role,
  onChanged,
  flash,
}: {
  campaigns: Campaign[];
  role: string;
  onChanged: () => Promise<void> | void;
  flash: (message: string) => void;
}) {
  const [editingCampaign, setEditingCampaign] = useState<Campaign | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [busyId, setBusyId] = useState("");
  const [error, setError] = useState("");
  const [expandedCampaignId, setExpandedCampaignId] = useState("");
  const [runsByCampaign, setRunsByCampaign] = useState<Record<string, WorkflowRun[]>>({});
  const canEdit = roleAtLeast(role, "editor");

  function closeForm() {
    setShowForm(false);
    setEditingCampaign(null);
  }

  function openCreate() {
    setEditingCampaign(null);
    setShowForm(true);
  }

  function openEdit(campaign: Campaign) {
    setEditingCampaign(campaign);
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

  async function toggleRuns(campaign: Campaign) {
    if (expandedCampaignId === campaign.id) {
      setExpandedCampaignId("");
      return;
    }
    setExpandedCampaignId(campaign.id);
    if (Object.prototype.hasOwnProperty.call(runsByCampaign, campaign.id)) return;
    setBusyId(`runs-${campaign.id}`);
    setError("");
    try {
      const runs = await api<WorkflowRun[]>(
        `/campaigns/${campaign.id}/runs?limit=5`,
      );
      setRunsByCampaign((current) => ({ ...current, [campaign.id]: runs }));
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusyId("");
    }
  }

  async function run(campaign: Campaign) {
    setBusyId(`run-${campaign.id}`);
    setError("");
    try {
      await api(`/campaigns/${campaign.id}/runs`, {
        method: "POST",
        body: {},
      });
      flash("内容生成任务已进入队列");
      setRunsByCampaign((current) => {
        const next = { ...current };
        delete next[campaign.id];
        return next;
      });
      setExpandedCampaignId("");
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
          <Button onClick={showForm ? closeForm : openCreate}>
            <Icon name="plus" />
            {showForm ? "收起表单" : "新建活动"}
          </Button>
        ) : undefined}
      />
      {error ? <p className="inline-error">{error}</p> : null}
      {!canEdit ? (
        <p className="permission-note">当前为只读权限，可查看活动与生成状态。</p>
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
                <div className="campaign-index">{String(campaigns.indexOf(campaign) + 1).padStart(2, "0")}</div>
                <div className="campaign-copy">
                  <div className="row-title">
                    <h2>{campaign.name}</h2>
                    <StatusBadge value={campaign.status} />
                  </div>
                  <p>{campaign.objective}</p>
                  <div className="meta-row">
                    <span>{campaign.product_name}</span>
                    <span>{campaign.platforms.map((item) => PLATFORM[item]).join(" / ")}</span>
                    <span>{formatDate(campaign.updated_at)}</span>
                  </div>
                  {expandedCampaignId === campaign.id ? (
                    <section className="run-history" aria-label={`${campaign.name} 的生成记录`}>
                      <div className="run-history-heading">
                        <strong>最近生成记录</strong>
                        <span>最多展示 5 个批次</span>
                      </div>
                      {busyId === `runs-${campaign.id}` ? (
                        <p className="run-history-empty">正在读取生成证据…</p>
                      ) : (runsByCampaign[campaign.id] || []).length ? (
                        (runsByCampaign[campaign.id] || []).map((runItem) => (
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
                        busy={busyId === `run-${campaign.id}`}
                        onClick={() => void run(campaign)}
                        disabled={campaign.status === "archived"}
                      >
                        生成内容
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
                    disabled={busyId === `runs-${campaign.id}`}
                    onClick={() => void toggleRuns(campaign)}
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
  contents,
  role,
  onChanged,
  flash,
}: {
  contents: Content[];
  role: string;
  onChanged: () => Promise<void> | void;
  flash: (message: string) => void;
}) {
  const reviewable = contents.filter((item) =>
    ["needs_review", "blocked"].includes(item.status),
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

  useEffect(() => {
    if (!selected?.id) return;
    let active = true;
    api<ContentRevision[]>(`/contents/${selected.id}/revisions`)
      .then((items) => {
        if (active) {
          setRevisionState({
            key: `${selected.id}:${selected.version}`,
            items,
          });
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
      flash(decision === "approve" ? "内容已通过，素材任务已创建" : "内容已驳回");
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
                <span><strong>{item.title}</strong><small>{PLATFORM[item.platform]} · v{item.version}</small></span>
                <StatusBadge value={item.status} />
              </button>
            ))}
          </section>
          <section className="panel review-editor">
            <div className="panel-heading">
              <div><p className="eyebrow">{PLATFORM[selected.platform]} · v{selected.version}</p><h2>编辑与确认</h2></div>
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
              <div className="review-summary">
                <strong>自动校验记录</strong>
                <pre>{JSON.stringify(selected.review_json, null, 2)}</pre>
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
  assets,
  contents,
  role,
  onChanged,
  flash,
}: {
  assets: Asset[];
  contents: Content[];
  role: string;
  onChanged: () => Promise<void> | void;
  flash: (message: string) => void;
}) {
  const [error, setError] = useState("");
  const [uploading, setUploading] = useState(false);
  const [showUpload, setShowUpload] = useState(false);
  const canEdit = roleAtLeast(role, "editor");
  const contentMap = useMemo(
    () => Object.fromEntries(contents.map((item) => [item.id, item.title])),
    [contents],
  );
  async function retry(asset: Asset) {
    try {
      await api(`/assets/${asset.id}/retry`, { method: "POST" });
      flash("素材已重新进入生成队列");
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
      flash("人工素材已上传");
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
        description="审核通过后才会生成素材；旧内容版本的素材会保留但不可发布。"
        action={canEdit ? (
          <Button onClick={() => setShowUpload((value) => !value)}>
            <Icon name="plus" />上传素材
          </Button>
        ) : undefined}
      />
      {error ? <p className="inline-error">{error}</p> : null}
      {!canEdit ? <p className="permission-note">当前为只读权限，可查看和下载已就绪素材。</p> : null}
      {showUpload && canEdit ? (
        <section className="panel form-panel">
          <div className="panel-heading"><div><p className="eyebrow">Upload</p><h2>添加人工或外部 AIGC 素材</h2></div></div>
          <form className="stack-form" onSubmit={uploadAsset}>
            <label>关联内容
              <select name="content_item_id" required defaultValue="">
                <option value="" disabled>选择内容版本</option>
                {contents.map((item) => <option key={item.id} value={item.id}>{PLATFORM[item.platform]} · v{item.version} · {item.title}</option>)}
              </select>
            </label>
            <label>素材类型
              <select name="kind" defaultValue="image">
                <option value="image">图片</option>
                <option value="video_storyboard">视频 / 分镜</option>
              </select>
            </label>
            <label>文件<input name="file" type="file" accept="image/*,video/*,.json" required /></label>
            <div className="form-actions"><Button type="submit" busy={uploading}>上传素材</Button><Button type="button" kind="ghost" onClick={() => setShowUpload(false)}>取消</Button></div>
          </form>
        </section>
      ) : null}
      <section className="panel">
        <DataTable
          headers={["素材", "关联内容", "生成方式", "大小", "状态", "操作"]}
          rows={assets.map((asset) => [
            asset.kind === "image" ? "营销图片" : "视频 / 分镜",
            contentMap[asset.content_item_id || ""] || "未关联",
            asset.provider,
            formatBytes(asset.size_bytes),
            <StatusBadge key="status" value={asset.status} />,
            <div className="table-actions" key="actions">
              {asset.status === "ready" ? (
                <button onClick={() => void download(`/assets/${asset.id}/download`, `asset-${asset.id}`)}>
                  <Icon name="download" />下载
                </button>
              ) : null}
              {canEdit && ["failed", "planned", "stale"].includes(asset.status) ? (
                <button onClick={() => void retry(asset)}>重新生成</button>
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
  publishes,
  contents,
  channels,
  role,
  onChanged,
  flash,
}: {
  publishes: PublishJob[];
  contents: Content[];
  channels: Channel[];
  role: string;
  onChanged: () => Promise<void> | void;
  flash: (message: string) => void;
}) {
  const approved = contents.filter((item) => item.status === "approved");
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);
  const [pulling, setPulling] = useState("");
  const [cancelling, setCancelling] = useState("");
  const [reconciling, setReconciling] = useState("");
  const [error, setError] = useState("");
  const canSchedule = roleAtLeast(role, "reviewer");
  const [defaultSchedule] = useState(() =>
    toLocalInput(new Date(Date.now() + 10 * 60_000)),
  );
  const contentMap = useMemo(
    () => Object.fromEntries(contents.map((item) => [item.id, item])),
    [contents],
  );
  const channelMap = useMemo(
    () => Object.fromEntries(channels.map((item) => [item.id, item])),
    [channels],
  );
  async function schedule(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    const form = new FormData(event.currentTarget);
    try {
      await api("/publishing/jobs", {
        method: "POST",
        body: {
          content_item_id: form.get("content_item_id"),
          channel_id: form.get("channel_id"),
          scheduled_at: new Date(String(form.get("scheduled_at"))).toISOString(),
        },
      });
      setCreating(false);
      flash("发布任务已排期");
      await onChanged();
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy(false);
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
    if (!window.confirm("取消这条发布排期？取消后不会自动分发。")) return;
    setCancelling(job.id);
    setError("");
    try {
      await api(`/publishing/jobs/${job.id}/cancel`, { method: "POST" });
      flash("发布排期已取消");
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


  return (
    <>
      <PageHeading
        eyebrow="Distribution"
        title="发布管理"
        description="内容版本、平台连接和人工审核三项同时满足后，任务才会进入分发队列。"
        action={canSchedule ? <Button onClick={() => setCreating((value) => !value)}><Icon name="plus" />安排发布</Button> : undefined}
      />
      {error ? <p className="inline-error">{error}</p> : null}
      {!canSchedule ? <p className="permission-note">当前可查看发布状态与下载投放包；安排分发需要审核人员权限。</p> : null}
      {creating && canSchedule ? (
        <section className="panel form-panel">
          <div className="panel-heading"><div><p className="eyebrow">Schedule</p><h2>创建发布任务</h2></div></div>
          <form className="stack-form" onSubmit={schedule}>
            <label>已审核内容
              <select name="content_item_id" required defaultValue="">
                <option value="" disabled>选择内容</option>
                {approved.map((item) => <option key={item.id} value={item.id}>{PLATFORM[item.platform]} · {item.title}</option>)}
              </select>
            </label>
            <label>平台连接
              <select name="channel_id" required defaultValue="">
                <option value="" disabled>选择连接器</option>
                {channels.map((item) => <option key={item.id} value={item.id}>{PLATFORM[item.platform]} · {item.display_name}</option>)}
              </select>
            </label>
            <label>发布时间
              <input
                name="scheduled_at"
                type="datetime-local"
                required
                defaultValue={defaultSchedule}
              />
            </label>
            <div className="form-actions"><Button type="submit" busy={busy}>确认排期</Button><Button type="button" kind="ghost" onClick={() => setCreating(false)}>取消</Button></div>
          </form>
        </section>
      ) : null}
      <section className="panel">
        <DataTable
          headers={["内容", "平台连接", "计划时间", "尝试", "状态", "结果"]}
          rows={publishes.map((job) => [
            contentMap[job.content_item_id]?.title || job.content_item_id,
            channelMap[job.channel_id]?.display_name || job.channel_id,
            formatDateTime(job.scheduled_at),
            job.attempts,
            <StatusBadge key="status" value={job.status} />,
            <div className="table-actions" key="actions">
              {job.status === "exported" ? (
                <button onClick={() => void download(`/publishing/jobs/${job.id}/artifact`, `contentflow-${job.id}.zip`)}>
                  <Icon name="download" />下载投放包
                </button>
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
              {canSchedule && job.external_id && channelMap[job.channel_id]?.platform !== "xiaohongshu" ? (
                <button
                  disabled={pulling === job.id}
                  onClick={() => void pullMetrics(job)}
                >
                  回收指标
                </button>
              ) : null}
              {canSchedule && ["scheduled", "queued"].includes(job.status) ? (
                <button
                  className="danger-text"
                  disabled={cancelling === job.id}
                  onClick={() => void cancel(job)}
                >
                  取消排期
                </button>
              ) : null}
              {!["scheduled", "queued", "exported"].includes(job.status) && job.external_id ? (
                <span>ID {job.external_id}</span>
              ) : null}
              {!job.external_id && !["scheduled", "queued", "reconciliation_required"].includes(job.status) ? <span>—</span> : null}
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
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const canAdmin = roleAtLeast(role, "admin");
  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const credentials: Record<string, string> = {};
    ["access_token", "open_id", "app_id", "app_secret"].forEach((key) => {
      const value = String(form.get(key) || "");
      if (value) credentials[key] = value;
    });
    setBusy("create");
    try {
      await api("/channels", {
        method: "POST",
        body: {
          platform,
          display_name: form.get("display_name"),
          credentials,
          config: platform === "xiaohongshu" ? { export_format: "zip" } : {},
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
        description="平台凭据由后端加密保存；公开能力不足的平台使用清晰标注的导出模式。"
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
                <select value={platform} onChange={(event) => setPlatform(event.target.value)}>
                  {Object.entries(PLATFORM).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                </select>
              </label>
              <label>连接名称<input name="display_name" required placeholder="品牌官方账号" /></label>
            </div>
            {platform === "douyin" ? (
              <div className="form-grid">
                <label>Access Token<input name="access_token" type="password" required /></label>
                <label>Open ID<input name="open_id" required /></label>
              </div>
            ) : null}
            {platform === "wechat" ? (
              <div className="form-grid">
                <label>App ID<input name="app_id" required /></label>
                <label>App Secret<input name="app_secret" type="password" required /></label>
              </div>
            ) : null}
            {platform === "xiaohongshu" ? <p className="form-note">小红书连接不收集账号密码。系统生成审核后的 ZIP 投放包，由运营人员人工发布。</p> : null}
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
            {canAdmin ? <Button kind="ghost" busy={busy === channel.id} onClick={() => void test(channel)}>测试连接</Button> : null}
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
  contents,
  channels,
  role,
  onChanged,
  flash,
}: {
  data: MetricsSummary;
  publishes: PublishJob[];
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
  const channelMap = useMemo(
    () => Object.fromEntries(channels.map((item) => [item.id, item])),
    [channels],
  );
  const completedPublishes = publishes.filter((job) =>
    ["exported", "published", "submitted", "draft_created"].includes(job.status),
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
                      {PLATFORM[channel?.platform || content?.platform] || channel?.platform} · {content?.title || job.content_item_id} · {STATUS[job.status] || job.status}
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
  promptGovernance: PromptGovernance | null;
  promptEval: PromptEvalGovernance | null;
  onWorkspaceCreated: (name: string) => Promise<void>;
  onChanged: () => Promise<void> | void;
  flash: (message: string) => void;
}) {
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

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
            <p className="eyebrow">AI governance</p>
            <h2>Prompt 审批、发布与回滚</h2>
          </div>
          {promptGovernance ? <StatusBadge value="active" /> : null}
        </div>
        {promptGovernance ? (
          <>
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
              key={promptGovernance.active.version}
              className="stack-form prompt-release-form"
              onSubmit={createPromptRelease}
            >
              <div>
                <p className="eyebrow">Immutable draft</p>
                <h3>基于当前生效版本创建新草稿</h3>
                <p className="form-note">
                  草稿创建后不可修改；创建者不能自行审批，必须由另一名管理员复核。
                  审批与激活前还必须通过当前 Eval 套件。审计日志只保存版本与哈希，不保存 Prompt 正文。
                </p>
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
                      defaultValue={promptGovernance.active.prompts[stage]}
                    />
                    <small>
                      SHA-256 {promptGovernance.active.prompt_hashes[stage].slice(0, 12)}…
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
        </div>
        <DataTable
          headers={["时间", "操作者", "动作", "对象", "详情"]}
          rows={auditLogs.map((log) => [
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
  onChanged,
  flash,
}: {
  jobs: QueueJob[];
  role: string;
  onChanged: () => Promise<void> | void;
  flash: (message: string) => void;
}) {
  const [error, setError] = useState("");
  const canRetry = roleAtLeast(role, "editor");
  async function retry(job: QueueJob) {
    try {
      await api(`/jobs/${job.id}/retry`, { method: "POST" });
      flash("失败任务已重置并进入重试队列");
      await onChanged();
    } catch (caught) {
      setError(messageOf(caught));
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
      <section className="panel">
        <DataTable
          headers={["任务类型", "执行时间", "尝试次数", "状态", "最近错误", "操作"]}
          rows={jobs.map((job) => [
            job.job_type,
            formatDateTime(job.run_at),
            `${job.attempts} / ${job.max_attempts}`,
            <StatusBadge key="status" value={job.status} />,
            job.last_error || "—",
            canRetry && job.status === "failed" ? <button className="table-link" key="retry" onClick={() => void retry(job)}>重试</button> : "—",
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
  if (!value) return "—";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function toLocalInput(date: Date): string {
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}
