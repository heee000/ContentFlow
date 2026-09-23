import { getApiBase } from "./api-config";
import { ApiError, StaleResponseError, apiError } from "./api-errors";

export type BrowserSession = {
  user: { id: string; email: string; display_name: string };
  workspace: { id: string; name: string };
  role: string;
  context: string;
};
export type RequestContext = Readonly<{ base: string; context: string; epoch: number }>;

export const SESSION_CONTEXT_EVENT = "contentflow-session-context-lost";
const CONTEXT_HEADER = "X-ContentFlow-Context";
const MODE_HEADER = "X-ContentFlow-Session-Mode";
let active: { base: string; context: string } | null = null;
let epoch = 0;
let blocked = false;
let transitioning = false;
let channel: BroadcastChannel | null = null;
let channelBase = "";
let refreshing: { epoch: number; promise: Promise<void> } | null = null;

function notifyBlocked(): void {
  if (!active || blocked) return;
  blocked = true;
  epoch += 1;
  window.dispatchEvent(new Event(SESSION_CONTEXT_EVENT));
}

function watchContext(base: string): void {
  if (channelBase === base) return;
  channel?.close();
  channel = null;
  channelBase = base;
  try {
    channel = new BroadcastChannel(`contentflow-session:${base}`);
    channel.onmessage = (event) => {
      // Notification can only lock this page, never authenticate/adopt a context.
      if (event.data?.kind === "changed" && active?.base === base
        && event.data.context !== active.context && !transitioning) notifyBlocked();
    };
  } catch { /* Server preconditions remain mandatory without notifications. */ }
}

export function activateBrowserSession(session: BrowserSession): void {
  if (!/^[a-f0-9]{64}$/.test(session.context)) throw new Error("服务端会话协议不兼容，请协调升级客户端与 API");
  const base = getApiBase();
  active = { base, context: session.context };
  epoch += 1;
  blocked = transitioning = false;
  watchContext(base);
  channel?.postMessage({ kind: "changed", context: session.context });
}

export function captureContext(): RequestContext {
  if (!active || blocked || transitioning || active.base !== getApiBase()) {
    throw new ApiError(409, "session_context_changed", "会话上下文已变化，请保留输入并同步当前会话");
  }
  return { ...active, epoch };
}

export function assertContext(context: RequestContext): void {
  if (blocked || transitioning || epoch !== context.epoch || active?.context !== context.context
    || active.base !== context.base || getApiBase() !== context.base) throw new StaleResponseError();
}

function discardResponse(response: Response): void {
  // A no-store response that nobody consumes can retain its stream/connection.
  // Cancel only this unread response, never replay or undo the server operation.
  // Cleanup failure must not replace a context error or delay blocking the page.
  try { void response.body?.cancel().catch(() => {}); }
  catch { /* A disposed/locked stream still must not make stale data usable. */ }
}

function assertResponseContext(response: Response, context: RequestContext): void {
  try { assertContext(context); }
  catch (error) {
    discardResponse(response);
    throw error;
  }
}

function headers(context?: string, supplied?: HeadersInit): Headers {
  const result = new Headers(supplied);
  result.set(MODE_HEADER, "cookie");
  if (context) result.set(CONTEXT_HEADER, context);
  else result.delete(CONTEXT_HEADER);
  return result;
}

async function cookieLock<T>(base: string, work: () => Promise<T>, requireLock = false): Promise<T> {
  if (typeof navigator === "undefined" || !navigator.locks) {
    if (requireLock) throw new ApiError(401, "session_reauthentication_required", "浏览器无法安全协调跨页续期，请保留输入并重新登录（建议使用 HTTPS）");
    return work();
  }
  const abort = new AbortController();
  const timeout = setTimeout(() => abort.abort(), 15_000);
  try {
    return await navigator.locks.request(`contentflow-cookie:${base}`, { signal: abort.signal }, async () => {
      clearTimeout(timeout);
      return work();
    });
  } finally { clearTimeout(timeout); }
}

async function authFetch(base: string, path: string, init: RequestInit = {}, context?: string): Promise<Response> {
  return fetch(`${base}${path}`, { ...init, headers: headers(context, init.headers),
    credentials: "include", cache: "no-store", signal: AbortSignal.timeout(20_000) });
}

async function readSession(response: Response, expected?: string): Promise<BrowserSession> {
  if (!response.ok) throw await apiError(response, "无法读取当前会话");
  const session = await response.json() as BrowserSession;
  if (!/^[a-f0-9]{64}$/.test(session.context) || (expected && expected !== session.context)) {
    throw new ApiError(409, "session_context_changed", "会话已变化，请确认后重新载入");
  }
  return session;
}

// Must run inside the shared cookie lock. A successful probe means another tab
// already renewed; never send its now-obsolete refresh cookie a second time.
async function renewUnderLock(base: string, expected?: string): Promise<BrowserSession> {
  const probe = await authFetch(base, "/auth/session", {}, expected);
  if (probe.ok) return readSession(probe, expected);
  if (probe.status !== 401) throw await apiError(probe, "无法核对会话");
  discardResponse(probe);
  if (typeof navigator === "undefined" || !navigator.locks) {
    throw new ApiError(401, "session_reauthentication_required", "当前浏览器无法安全自动续期，请重新登录");
  }
  const context = expected || (await readSession(await authFetch(base, "/auth/bootstrap", { method: "POST" }))).context;
  const refresh = await authFetch(base, "/auth/refresh", { method: "POST" }, context);
  if (!refresh.ok) throw await apiError(refresh, "会话续期失败，请重新登录");
  // Read the body before releasing the lock. A lost receipt is not retried.
  await refresh.json();
  return readSession(await authFetch(base, "/auth/session", {}, context), context);
}

export async function restoreBrowserSession(): Promise<BrowserSession> {
  const base = getApiBase();
  return cookieLock(base, () => renewUnderLock(base));
}

function beginTransition(): number {
  if (transitioning) throw new ApiError(409, "session_transition_busy", "已有会话操作正在执行");
  transitioning = true;
  return ++epoch;
}

function assertTransition(version: number): void {
  if (epoch !== version || !transitioning) throw new StaleResponseError();
}

async function mutateSession(path: string, body?: unknown, context?: RequestContext): Promise<BrowserSession> {
  const base = context?.base || getApiBase();
  const version = beginTransition();
  try {
    return await cookieLock(base, async () => {
      assertTransition(version);
      if (context) await renewUnderLock(base, context.context);
      const response = await authFetch(base, path, {
        method: "POST", headers: body === undefined ? undefined : { "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
      }, context?.context);
      if (!response.ok) throw await apiError(response, "会话操作失败");
      const result = await response.json() as { context: string };
      const session = await readSession(await authFetch(base, "/auth/session", {}, result.context), result.context);
      assertTransition(version);
      return session;
    });
  } catch (error) {
    transitioning = false;
    notifyBlocked();
    throw error;
  }
}

export function authenticateBrowserSession(mode: "login" | "register", body: unknown): Promise<BrowserSession> {
  return mutateSession(`/auth/${mode}`, body);
}

export function switchBrowserWorkspace(workspaceId: string): Promise<BrowserSession> {
  return mutateSession(`/auth/switch/${encodeURIComponent(workspaceId)}`, undefined, captureContext());
}

export function createBrowserWorkspace(name: string): Promise<BrowserSession> {
  return mutateSession("/auth/workspaces", { name }, captureContext());
}

export async function logoutBrowserSession(): Promise<void> {
  const context = captureContext();
  const version = beginTransition();
  try {
    await cookieLock(context.base, async () => {
      assertTransition(version);
      const response = await authFetch(context.base, "/auth/logout", { method: "POST" }, context.context);
      if (!response.ok) throw await apiError(response, "退出失败");
      assertTransition(version);
      active = null;
      blocked = transitioning = false;
      epoch += 1;
      channel?.postMessage({ kind: "changed", context: null });
    });
  } catch (error) {
    transitioning = false;
    notifyBlocked();
    throw error;
  }
}

async function refreshFor(context: RequestContext): Promise<void> {
  assertContext(context);
  if (!refreshing || refreshing.epoch !== context.epoch) {
    const pending = { epoch: context.epoch, promise: Promise.resolve() };
    pending.promise = cookieLock(context.base, async () => {
      assertContext(context);
      await renewUnderLock(context.base, context.context);
      assertContext(context);
    }, true).finally(() => { if (refreshing === pending) refreshing = null; });
    refreshing = pending;
  }
  return refreshing.promise;
}

export async function fetchWithSession(path: string, init: RequestInit, context: RequestContext): Promise<Response> {
  assertContext(context);
  const send = () => fetch(`${context.base}${path}`, { ...init, headers: headers(context.context, init.headers),
    credentials: "include", cache: "no-store" });
  let response = await send();
  assertResponseContext(response, context);
  try {
    if (response.status === 401) {
      discardResponse(response);
      await refreshFor(context);
      assertContext(context);
      response = await send();
      assertResponseContext(response, context);
    }
    if (!response.ok) {
      const error = await apiError(response, `请求失败（${response.status}）`);
      assertContext(context);
      throw error;
    }
    return response;
  } catch (error) {
    if (error instanceof StaleResponseError) throw error;
    assertContext(context);
    if (error instanceof ApiError && (error.status === 401 || error.code.startsWith("session_context_"))) notifyBlocked();
    // A refresh transport failure could follow a committed rotation. Never
    // retry it blindly, and keep the page draft until an explicit resync.
    else if (!(error instanceof ApiError)) notifyBlocked();
    throw error;
  }
}
