import {
  DEFAULT_API_BASE,
  RUNTIME_API_BASE_CONFIGURABLE,
} from "@/security";

export type ApiOptions = Omit<RequestInit, "body"> & {
  body?: unknown;
};

export class ApiError extends Error {
  status: number;
  code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

const COOKIE_SESSION_HEADER = "X-ContentFlow-Session-Mode";
const NO_REFRESH_PATHS = new Set([
  "/auth/login",
  "/auth/register",
  "/auth/refresh",
  "/auth/logout",
]);
let refreshPromise: Promise<boolean> | null = null;

export const runtimeApiBaseConfigurable =
  RUNTIME_API_BASE_CONFIGURABLE;

function normalizeApiBase(value: string): string {
  let url: URL;
  try {
    url = new URL(value.trim());
  } catch {
    throw new Error("API 地址必须是完整的 HTTP(S) URL");
  }
  if (!["http:", "https:"].includes(url.protocol)) {
    throw new Error("API 地址只允许使用 HTTP 或 HTTPS");
  }
  if (url.username || url.password || url.search || url.hash) {
    throw new Error("API 地址不能包含账号、密码、查询参数或片段");
  }
  return `${url.origin}${url.pathname.replace(/\/+$/, "")}`;
}

export function getApiBase(): string {
  if (typeof window === "undefined") return DEFAULT_API_BASE;
  localStorage.removeItem("contentflow_token");
  if (!RUNTIME_API_BASE_CONFIGURABLE) {
    localStorage.removeItem("contentflow_api_base");
    return DEFAULT_API_BASE;
  }
  const stored = localStorage.getItem("contentflow_api_base");
  if (!stored) return DEFAULT_API_BASE;
  try {
    return normalizeApiBase(stored);
  } catch {
    localStorage.removeItem("contentflow_api_base");
    return DEFAULT_API_BASE;
  }
}

export function setApiBase(value: string): void {
  if (!RUNTIME_API_BASE_CONFIGURABLE) {
    localStorage.removeItem("contentflow_api_base");
    return;
  }
  localStorage.setItem("contentflow_api_base", normalizeApiBase(value));
}

async function refreshBrowserSession(): Promise<boolean> {
  if (!refreshPromise) {
    refreshPromise = fetch(`${getApiBase()}/auth/refresh`, {
      method: "POST",
      credentials: "include",
      headers: { [COOKIE_SESSION_HEADER]: "cookie" },
      cache: "no-store",
    })
      .then((response) => response.ok)
      .catch(() => false)
      .finally(() => {
        refreshPromise = null;
      });
  }
  return refreshPromise;
}

async function fetchWithSession(
  path: string,
  init: RequestInit,
  allowRefresh = true,
): Promise<Response> {
  const response = await fetch(`${getApiBase()}${path}`, {
    ...init,
    credentials: "include",
    cache: "no-store",
  });
  if (
    response.status === 401 &&
    allowRefresh &&
    !NO_REFRESH_PATHS.has(path) &&
    (await refreshBrowserSession())
  ) {
    return fetchWithSession(path, init, false);
  }
  return response;
}

function requestBody(options: ApiOptions, headers: Headers): BodyInit | undefined {
  if (options.body instanceof FormData) return options.body;
  if (options.body !== undefined) {
    headers.set("Content-Type", "application/json");
    return JSON.stringify(options.body);
  }
  return undefined;
}

async function apiError(response: Response, fallback: string): Promise<ApiError> {
  const payload = await response.json().catch(() => null);
  const error = payload?.error;
  return new ApiError(
    response.status,
    error?.code || `http_${response.status}`,
    error?.message || fallback,
  );
}

export async function api<T>(
  path: string,
  options: ApiOptions = {},
): Promise<T> {
  const headers = new Headers(options.headers);
  headers.set(COOKIE_SESSION_HEADER, "cookie");
  const response = await fetchWithSession(path, {
    ...options,
    headers,
    body: requestBody(options, headers),
  });
  if (!response.ok) {
    throw await apiError(response, `请求失败（${response.status}）`);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export async function download(
  path: string,
  fallbackName: string,
): Promise<void> {
  const headers = new Headers({ [COOKIE_SESSION_HEADER]: "cookie" });
  const response = await fetchWithSession(path, { headers });
  if (!response.ok) {
    throw await apiError(response, "下载失败");
  }
  const blob = await response.blob();
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
