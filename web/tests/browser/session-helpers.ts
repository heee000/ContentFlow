import { type Page } from "@playwright/test";

export const API = "http://127.0.0.1:18765/api/v1";
export const cookieHeaders = { "X-ContentFlow-Session-Mode": "cookie", Origin: "http://127.0.0.1:18766" };

// Direct test setup/assertion requests opt into the actual current context.
// Stale-page regressions instead retain the earlier value explicitly.
export async function currentCookieHeaders(page: Page) {
  const response = await page.request.get(`${API}/auth/session`, { headers: cookieHeaders });
  if (!response.ok()) throw new Error("No current synthetic browser session");
  const session = await response.json();
  if (!session.context) throw new Error("Missing browser context protocol");
  return { ...cookieHeaders, "X-ContentFlow-Context": session.context as string };
}
