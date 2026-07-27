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

const DEFAULT_API =
  process.env.NEXT_PUBLIC_CONTENTFLOW_API_BASE ||
  "http://localhost:8000/api/v1";

export function getApiBase(): string {
  if (typeof window === "undefined") return DEFAULT_API;
  return localStorage.getItem("contentflow_api_base") || DEFAULT_API;
}

export function setApiBase(value: string): void {
  const normalized = value.trim().replace(/\/+$/, "");
  localStorage.setItem("contentflow_api_base", normalized);
}

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("contentflow_token");
}

export function setToken(token: string | null): void {
  if (token) localStorage.setItem("contentflow_token", token);
  else localStorage.removeItem("contentflow_token");
}

export async function api<T>(
  path: string,
  options: ApiOptions = {},
): Promise<T> {
  const headers = new Headers(options.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  let body: BodyInit | undefined;
  if (options.body instanceof FormData) {
    body = options.body;
  } else if (options.body !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(options.body);
  }
  const response = await fetch(`${getApiBase()}${path}`, {
    ...options,
    headers,
    body,
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    const error = payload?.error;
    throw new ApiError(
      response.status,
      error?.code || `http_${response.status}`,
      error?.message || `请求失败（${response.status}）`,
    );
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export async function download(
  path: string,
  fallbackName: string,
): Promise<void> {
  const headers = new Headers();
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(`${getApiBase()}${path}`, { headers });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new ApiError(
      response.status,
      payload?.error?.code || `http_${response.status}`,
      payload?.error?.message || "下载失败",
    );
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
