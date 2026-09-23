import { expect, test, type Page } from "@playwright/test";
import { API, cookieHeaders, currentCookieHeaders } from "./session-helpers";

const navigation = (page: Page) => page.getByRole("navigation", { name: "工作台导航" });
const blocked = (page: Page) => page.getByRole("alert", { name: "会话上下文已变化", exact: true });
const refresh = (page: Page) => page.getByRole("button", { name: "刷新数据", exact: true });

test.beforeEach(async ({ context }) => {
  await context.route("**/*", (route) => ["http://127.0.0.1:18765", "http://127.0.0.1:18766"].includes(new URL(route.request().url()).origin)
    ? route.continue() : route.abort("blockedbyclient"));
});

async function ready(page: Page) {
  await expect(page.locator(".project-switcher option").filter({ hasText: "TEST-ONLY review-context-draft project" })).toHaveCount(1);
  await expect(refresh(page)).toBeEnabled();
}

async function login(page: Page) {
  await page.goto("/");
  await page.getByLabel("邮箱", { exact: true }).fill("worker@example.com");
  await page.getByLabel("密码", { exact: true }).fill("a-secure-password");
  await page.getByRole("button", { name: "登录 ContentFlow", exact: true }).click();
  await ready(page);
}

async function secondPage(page: Page) {
  const other = await page.context().newPage();
  await other.goto("/");
  await ready(other);
  return other;
}

async function switchWorkspace(page: Page) {
  const select = page.getByRole("combobox", { name: "切换工作区", exact: true });
  const target = await select.getByRole("option", { name: /TEST-ONLY empty review workspace/ }).getAttribute("value");
  if (!target) throw new Error("Missing isolated workspace");
  await select.selectOption(target);
  await expect(select).toHaveValue(target);
  await expect(refresh(page)).toBeEnabled();
  return target;
}

async function editDraft(page: Page, name: string) {
  await navigation(page).getByRole("button", { name: /^2 审核内容(?: \d+)?$/ }).click();
  await page.locator(".review-queue").getByRole("button").filter({ hasText: `TEST-ONLY review-${name} project` }).click();
  const body = page.locator(".review-editor").getByRole("textbox", { name: "正文", exact: true });
  await body.fill(`TEST-ONLY ${name} 本地草稿不能被自动丢弃`);
  return body;
}

test("another tab switches workspace: preserve draft and block old-page writes", async ({ page }) => {
  await login(page);
  const body = await editDraft(page, "context-draft");
  const staleHeaders = await currentCookieHeaders(page);
  const other = await secondPage(page);
  await switchWorkspace(other);
  await expect(blocked(page)).toBeVisible();
  await expect(body).toHaveValue("TEST-ONLY context-draft 本地草稿不能被自动丢弃");
  let writes = 0;
  page.on("request", (request) => { if (request.method() === "PATCH") writes += 1; });
  await page.locator(".review-editor").getByRole("button", { name: "保存修改", exact: true }).click();
  expect(writes).toBe(0);
  const rejected = await page.request.post(`${API}/campaigns`, { headers: staleHeaders,
    data: { name: "TEST-ONLY wrong workspace", product_name: "TEST", objective: "Must not be created", audience: "test users", platforms: ["wechat"] } });
  expect(rejected.status()).toBe(409);
  const freshHeaders = await currentCookieHeaders(other);
  expect(await (await other.request.get(`${API}/campaigns`, { headers: freshHeaders })).json()).toEqual([]);
  page.once("dialog", (dialog) => dialog.dismiss());
  await blocked(page).getByRole("button", { name: "同步当前会话" }).click();
  await expect(body).toHaveValue("TEST-ONLY context-draft 本地草稿不能被自动丢弃");
});

test("server rejects actual old-page create when broadcast notifications are unavailable", async ({ context, page }) => {
  await context.addInitScript(() => { Object.defineProperty(window, "BroadcastChannel", { value: undefined }); });
  await login(page);
  await navigation(page).getByRole("button", { name: "1 创建内容", exact: true }).click();
  await page.getByRole("button", { name: "新建活动", exact: true }).click();
  await page.getByLabel("活动名称", { exact: true }).fill("TEST-ONLY must stay local");
  await page.getByLabel("产品名称", { exact: true }).fill("Test product");
  await page.getByLabel("活动目标", { exact: true }).fill("Only test context safety");
  await page.getByLabel("目标人群", { exact: true }).fill("test users");
  await page.locator('input[type="radio"][name="image_source"][value="manual"]').check();
  const other = await secondPage(page);
  await switchWorkspace(other);
  const rejected = page.waitForResponse((response) => response.url() === `${API}/campaigns` && response.request().method() === "POST");
  await page.getByRole("button", { name: "保存活动", exact: true }).click();
  expect((await rejected).status()).toBe(409);
  await expect(blocked(page)).toBeVisible();
  await expect(page.getByLabel("活动名称", { exact: true })).toHaveValue("TEST-ONLY must stay local");
  expect(await (await other.request.get(`${API}/campaigns`, { headers: await currentCookieHeaders(other) })).json()).toEqual([]);
});

test("late first page is discarded and cannot continue pagination after context invalidation", async ({ page }) => {
  await login(page);
  const other = await secondPage(page);
  let release = () => {};
  const wait = new Promise<void>((resolve) => { release = resolve; });
  let arrived = () => {};
  const held = new Promise<void>((resolve) => { arrived = resolve; });
  let nextPages = 0;
  const firstPage = (request: import("@playwright/test").Request) => request.method() === "GET"
    && request.url().startsWith(`${API}/campaigns?`)
    && !new URL(request.url()).searchParams.has("cursor");
  // Cancellation is a valid terminal state for an unread stale response.
  // response.finished() alone does not settle a cancelled request in this client.
  const settled = Promise.race([
    page.waitForEvent("requestfinished", firstPage).then(() => null),
    page.waitForEvent("requestfailed", firstPage).then((request) => request.failure()?.errorText),
  ]);
  await page.route(`${API}/campaigns?*`, async (route) => {
    if (new URL(route.request().url()).searchParams.get("cursor")) { nextPages += 1; return route.abort(); }
    const response = await route.fetch();
    const rows = await response.json();
    rows[0].name = "TEST-ONLY late response must be discarded";
    arrived();
    await wait;
    const modifiedHeaders: Record<string, string> = { ...response.headers(), "x-contentflow-next-cursor": "TEST-ONLY-next-page" };
    // The injected JSON has a different byte length. Retaining the original
    // Content-Length would leave the browser waiting for nonexistent bytes.
    delete modifiedHeaders["content-length"];
    await route.fulfill({ response, headers: modifiedHeaders, json: rows });
  });
  await refresh(page).click();
  const delivered = page.waitForResponse((response) => response.url().startsWith(`${API}/campaigns?`));
  try {
    await held;
    await switchWorkspace(other);
    await expect(blocked(page)).toBeVisible();
  } finally { release(); }
  // Wait for the held request to finish, not just for release() to be called.
  // Module tests additionally await the entire apiAllPages rejection, covering
  // the asynchronous no-next-page invariant without a timing-based assertion.
  expect((await delivered).status()).toBe(200);
  const failure = await settled;
  if (failure !== null) expect(failure).toBe("net::ERR_ABORTED");
  await page.unrouteAll({ behavior: "wait" });
  await expect(page.locator(".project-switcher option").filter({ hasText: "late response must be discarded" })).toHaveCount(0);
  expect(nextPages).toBe(0);
});

test("two tabs with expired access cookies coordinate one refresh without revocation", async ({ context, page }) => {
  await login(page);
  const original = await (await page.request.get(`${API}/auth/session`, { headers: cookieHeaders })).json();
  const other = await secondPage(page);
  let refreshes = 0;
  context.on("request", (request) => { if (request.url() === `${API}/auth/refresh`) refreshes += 1; });
  await context.clearCookies({ name: "contentflow_access" });
  await Promise.all([refresh(page).click(), refresh(other).click()]);
  await expect(refresh(page)).toBeEnabled();
  await expect(refresh(other)).toBeEnabled();
  expect(refreshes).toBe(1);
  await expect(blocked(page)).toHaveCount(0);
  await expect(blocked(other)).toHaveCount(0);
  const current = await page.request.get(`${API}/auth/session`, { headers: cookieHeaders });
  expect(current.status()).toBe(200);
  expect((await current.json()).context).toBe(original.context);
  await ready(page);
  await ready(other);
});

test("committed refresh with lost receipt does not blindly retry or discard a draft", async ({ context, page }) => {
  await login(page);
  const body = await editDraft(page, "refresh-lost");
  let refreshes = 0;
  await page.route(`${API}/auth/refresh`, async (route) => {
    refreshes += 1;
    const response = await route.fetch();
    expect(response.status()).toBe(200);
    await route.abort("connectionreset");
  });
  await context.clearCookies({ name: "contentflow_access" });
  await page.locator(".review-editor").getByRole("button", { name: "保存修改", exact: true }).click();
  await expect(blocked(page)).toBeVisible();
  await expect(body).toHaveValue("TEST-ONLY refresh-lost 本地草稿不能被自动丢弃");
  expect(refreshes).toBe(1);
});

test("missing Web Locks fails closed instead of racing refresh, preserving draft", async ({ page, context }) => {
  await page.addInitScript(() => { Object.defineProperty(navigator, "locks", { value: undefined }); });
  await login(page);
  const body = await editDraft(page, "refresh-unsupported");
  let refreshes = 0;
  page.on("request", (request) => { if (request.url() === `${API}/auth/refresh`) refreshes += 1; });
  await context.clearCookies({ name: "contentflow_access" });
  await page.locator(".review-editor").getByRole("button", { name: "保存修改", exact: true }).click();
  await expect(blocked(page)).toBeVisible();
  await expect(body).toHaveValue("TEST-ONLY refresh-unsupported 本地草稿不能被自动丢弃");
  expect(refreshes).toBe(0);
});

test("workspace switch waits for the other tab's committed refresh receipt", async ({ page, context }) => {
  await login(page);
  const other = await secondPage(page);
  let release = () => {};
  const held = new Promise<void>((resolve) => { release = resolve; });
  let arrived = () => {};
  const committed = new Promise<void>((resolve) => { arrived = resolve; });
  let refreshes = 0;
  let switches = 0;
  context.on("request", (request) => {
    if (request.url() === `${API}/auth/refresh`) refreshes += 1;
    if (request.url().startsWith(`${API}/auth/switch/`)) switches += 1;
  });
  await page.route(`${API}/auth/refresh`, async (route) => {
    const response = await route.fetch();
    expect(response.status()).toBe(200);
    arrived();
    await held;
    await route.fulfill({ response });
  });
  await context.clearCookies({ name: "contentflow_access" });
  await refresh(page).click();
  const select = other.getByRole("combobox", { name: "切换工作区", exact: true });
  const target = await select.getByRole("option", { name: /TEST-ONLY empty review workspace/ }).getAttribute("value");
  if (!target) throw new Error("Missing isolated target");
  try {
    await committed;
    await select.selectOption(target);
    await expect.poll(() => other.evaluate(async () => (await navigator.locks.query()).pending?.length ?? 0)).toBeGreaterThan(0);
    expect(switches).toBe(0);
  } finally { release(); }
  await expect(select).toHaveValue(target);
  await expect(refresh(other)).toBeEnabled();
  await expect(blocked(page)).toBeVisible();
  expect(refreshes).toBe(1);
  expect(switches).toBe(1);
  const current = await other.request.get(`${API}/auth/session`, { headers: cookieHeaders });
  expect(current.status()).toBe(200);
  expect((await current.json()).workspace.id).toBe(target);
  expect(await (await other.request.get(`${API}/campaigns`, { headers: await currentCookieHeaders(other) })).json()).toEqual([]);
});

test("denied local storage does not prevent explicit login and a safe session restore", async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(window, "localStorage", { get() { throw new DOMException("TEST-ONLY storage denied", "SecurityError"); } });
  });
  await login(page);
  await page.reload();
  await ready(page);
  await expect(blocked(page)).toHaveCount(0);
});
