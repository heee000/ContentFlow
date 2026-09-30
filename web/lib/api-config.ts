import { DEFAULT_API_BASE, RUNTIME_API_BASE_CONFIGURABLE } from "@/security";

export const runtimeApiBaseConfigurable = RUNTIME_API_BASE_CONFIGURABLE;
let selectedBase: string | undefined;

function normalizeApiBase(value: string): string {
  let url: URL;
  try { url = new URL(value.trim()); }
  catch { throw new Error("API 地址必须是完整的 HTTP(S) URL"); }
  if (!["http:", "https:"].includes(url.protocol)) throw new Error("API 地址只允许使用 HTTP 或 HTTPS");
  if (url.username || url.password || url.search || url.hash) throw new Error("API 地址不能包含账号、密码、查询参数或片段");
  return `${url.origin}${url.pathname.replace(/\/+$/, "")}`;
}

export function getApiBase(): string {
  if (typeof window === "undefined") return DEFAULT_API_BASE;
  if (selectedBase) return selectedBase;
  try {
    localStorage.removeItem("contentflow_token");
    if (RUNTIME_API_BASE_CONFIGURABLE) {
      const stored = localStorage.getItem("contentflow_api_base");
      if (stored) return selectedBase = normalizeApiBase(stored);
    } else localStorage.removeItem("contentflow_api_base");
  } catch { /* Storage denial must not prevent a cookie-authenticated session. */ }
  return selectedBase = DEFAULT_API_BASE;
}

export function setApiBase(value: string): void {
  selectedBase = RUNTIME_API_BASE_CONFIGURABLE ? normalizeApiBase(value) : DEFAULT_API_BASE;
  try {
    if (RUNTIME_API_BASE_CONFIGURABLE) localStorage.setItem("contentflow_api_base", selectedBase);
    else localStorage.removeItem("contentflow_api_base");
  } catch { /* Use this explicit in-memory selection; never reread another tab's target mid-request. */ }
}
