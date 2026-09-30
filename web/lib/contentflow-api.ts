import { apiError } from "./api-errors";
import { assertContext, captureContext, fetchWithSession, type RequestContext } from "./browser-session";
export { ApiError, StaleResponseError } from "./api-errors";
export { getApiBase, setApiBase, runtimeApiBaseConfigurable } from "./api-config";

export type ApiOptions = Omit<RequestInit, "body"> & {
  body?: unknown;
};

export type PaginatedResult<T> = {
  items: T[];
  truncated: boolean;
  syncTime: string | null;
};

function requestBody(options: ApiOptions, headers: Headers): BodyInit | undefined {
  if (options.body instanceof FormData) return options.body;
  if (options.body !== undefined) {
    headers.set("Content-Type", "application/json");
    return JSON.stringify(options.body);
  }
  return undefined;
}

export async function api<T>(
  path: string,
  options: ApiOptions = {},
): Promise<T> {
  if (path.startsWith("/auth/") && options.method && options.method !== "GET") {
    throw new Error("会话变更必须使用跨页协调入口");
  }
  const context = captureContext();
  const headers = new Headers(options.headers);
  const response = await fetchWithSession(path, {
    ...options,
    headers,
    body: requestBody(options, headers),
  }, context);
  if (!response.ok) {
    throw await apiError(response, `请求失败（${response.status}）`);
  }
  if (response.status === 204) return undefined as T;
  const result = (await response.json()) as T;
  assertContext(context);
  return result;
}

const NEXT_CURSOR_HEADER = "X-ContentFlow-Next-Cursor";
const SYNC_TIME_HEADER = "X-ContentFlow-Sync-Time";
const DEFAULT_PAGE_LIMIT = 100;
const DEFAULT_MAX_PAGES = 20;

function appendQuery(
  path: string,
  values: Record<string, string | number | undefined>,
): string {
  const parameters = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined) parameters.set(key, String(value));
  }
  const query = parameters.toString();
  if (!query) return path;
  return `${path}${path.includes("?") ? "&" : "?"}${query}`;
}

async function apiPage<T>(path: string, context: RequestContext): Promise<{
  items: T[];
  nextCursor: string | null;
  syncTime: string | null;
}> {
  const response = await fetchWithSession(path, {}, context);
  if (!response.ok) {
    throw await apiError(response, `请求失败（${response.status}）`);
  }
  const items = (await response.json()) as T[];
  assertContext(context);
  return {
    items,
    nextCursor: response.headers.get(NEXT_CURSOR_HEADER),
    syncTime: response.headers.get(SYNC_TIME_HEADER),
  };
}

export async function apiAllPages<T>(
  path: string,
  options: { maxPages?: number; pageLimit?: number } = {},
): Promise<PaginatedResult<T>> {
  const context = captureContext();
  const maxPages = options.maxPages ?? DEFAULT_MAX_PAGES;
  const pageLimit = options.pageLimit ?? DEFAULT_PAGE_LIMIT;
  const items: T[] = [];
  const seenCursors = new Set<string>();
  let syncTime: string | null = null;
  let cursor: string | undefined;

  for (let pageNumber = 0; pageNumber < maxPages; pageNumber += 1) {
    const page = await apiPage<T>(appendQuery(path, { limit: pageLimit, cursor }), context);
    items.push(...page.items);
    if (page.syncTime && (!syncTime || page.syncTime < syncTime)) {
      syncTime = page.syncTime;
    }
    if (!page.nextCursor) return { items, truncated: false, syncTime };
    if (seenCursors.has(page.nextCursor)) {
      throw new Error("服务端返回了重复分页游标，请刷新后重试");
    }
    seenCursors.add(page.nextCursor);
    cursor = page.nextCursor;
  }
  return { items, truncated: true, syncTime };
}

export async function download(
  path: string,
  fallbackName: string,
): Promise<void> {
  const context = captureContext();
  const response = await fetchWithSession(path, {}, context);
  if (!response.ok) {
    throw await apiError(response, "下载失败");
  }
  const blob = await response.blob();
  assertContext(context);
  const disposition = response.headers.get("content-disposition") || "";
  const match = disposition.match(/filename="?([^";]+)"?/i);
  const name = match?.[1] || fallbackName;
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = name;
  anchor.click();
  URL.revokeObjectURL(url);
}

export async function apiBlob(path: string, signal?: AbortSignal): Promise<Blob> {
  const context = captureContext();
  const response = await fetchWithSession(path, { signal }, context);
  if (!response.ok) throw await apiError(response, "预览素材加载失败");
  const result = await response.blob();
  assertContext(context);
  return result;
}
