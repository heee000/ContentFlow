import { expect, test, type Page } from "@playwright/test";
import { currentCookieHeaders } from "./session-helpers";

const API = "http://127.0.0.1:18765/api/v1";
const cookieHeaders = { "X-ContentFlow-Session-Mode": "cookie", Origin: "http://127.0.0.1:18766" };
const previewRegion = (page: Page) => page.getByRole("region", { name: "最终发布确认" });
const pendingRegion = (page: Page) => page.getByRole("region", { name: "待核对的发布回执" });

test.beforeEach(async ({ context }) => {
  await context.route("**/*", (route) => {
    const origin = new URL(route.request().url()).origin;
    return ["http://127.0.0.1:18765", "http://127.0.0.1:18766"].includes(origin)
      ? route.continue() : route.abort("blockedbyclient");
  });
});

async function navigateToPublishing(page: Page) {
  await page.getByRole("navigation", { name: "工作台导航" }).getByRole("button", { name: "4 发布", exact: true }).click();
  await expect(page.getByRole("heading", { name: "把已审核内容交付到平台" })).toBeVisible();
}

async function startPreview(page: Page, name: string) {
  await page.goto("/");
  await page.getByLabel("邮箱", { exact: true }).fill("worker@example.com");
  await page.getByLabel("密码", { exact: true }).fill("a-secure-password");
  await page.getByRole("button", { name: "登录 ContentFlow", exact: true }).click();
  await navigateToPublishing(page);
  await page.getByRole("button", { name: "新建发布" }).click();
  const content = await page.locator('select[name="content_item_id"] option').filter({ hasText: `TEST-ONLY ${name}` }).getAttribute("value");
  if (!content) throw new Error("Missing disposable content fixture");
  await page.getByRole("combobox", { name: "已审核内容", exact: true }).selectOption(content);
  const channel = await page.locator('select[name="channel_id"] option').filter({ hasText: `TEST-ONLY ${name}` }).getAttribute("value");
  if (!channel) throw new Error("Missing disposable channel fixture");
  await page.getByRole("combobox", { name: "平台连接", exact: true }).selectOption(channel);
  await page.getByRole("button", { name: "预览发布内容", exact: true }).click();
  await expect(previewRegion(page)).toBeVisible();
  return content;
}

async function acknowledge(page: Page) {
  const checkbox = previewRegion(page).getByRole("checkbox");
  await expect(checkbox).toBeEnabled();
  await checkbox.check();
  await expect(page.getByRole("button", { name: "确认这份发布物", exact: true })).toBeEnabled();
}

async function publicationsFor(page: Page, contentId: string) {
  const response = await page.request.get(`${API}/publishing/jobs`, { headers: await currentCookieHeaders(page) });
  expect(response.ok()).toBeTruthy();
  const jobs: { id: string; content_item_id: string; status: string }[] = await response.json();
  return jobs.filter((job) => job.content_item_id === contentId && job.status !== "cancelled");
}

test("verified cover and acknowledgement gate the actual confirmation", async ({ page }) => {
  let releaseAsset!: () => void;
  const assetGate = new Promise<void>((resolve) => { releaseAsset = resolve; });
  await page.route("**/publishing/preview-assets/**", async (route) => {
    const response = await route.fetch();
    await assetGate;
    await route.fulfill({ response });
  });
  const contentId = await startPreview(page, "gating");
  await expect(previewRegion(page).getByRole("checkbox")).toBeDisabled();
  await expect(page.getByRole("button", { name: "确认这份发布物", exact: true })).toBeDisabled();
  expect(await publicationsFor(page, contentId)).toHaveLength(0);
  releaseAsset();
  await expect(previewRegion(page).getByRole("img", { name: "此次发布使用的封面" })).toBeVisible();
  await expect(previewRegion(page).locator(".publication-preview-text")).toHaveText(
    "TEST-ONLY gating: exact saved text.\nA & B <not executable>.");
  await acknowledge(page);
  const receipt = page.waitForResponse((response) => response.url() === `${API}/publishing/jobs` && response.request().method() === "POST");
  await page.getByRole("button", { name: "确认这份发布物", exact: true }).click();
  expect((await receipt).status()).toBe(202);
  await expect(previewRegion(page)).toHaveCount(0);
  expect(await publicationsFor(page, contentId)).toHaveLength(1);
});

test("lost receipt after commit survives reload and replays the identical request", async ({ page }) => {
  const contentId = await startPreview(page, "lost-receipt");
  await acknowledge(page);
  let originalBody = "";
  let originalId = "";
  await page.route(`${API}/publishing/jobs`, async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    originalBody = route.request().postData()!;
    const response = await route.fetch();
    expect(response.status()).toBe(202);
    originalId = (await response.json()).id;
    await route.abort("connectionreset");
  });
  await page.getByRole("button", { name: "确认这份发布物", exact: true }).click();
  await expect(pendingRegion(page).getByRole("button", { name: "重试获取原任务" })).toBeEnabled();
  await expect(page.getByRole("combobox", { name: "已审核内容", exact: true })).toBeDisabled();
  expect((await publicationsFor(page, contentId)).map((job) => job.id)).toEqual([originalId]);
  await page.unroute(`${API}/publishing/jobs`);
  await page.reload();
  await navigateToPublishing(page);
  await expect(pendingRegion(page)).toBeVisible();
  const receipt = page.waitForResponse((response) => response.url() === `${API}/publishing/jobs` && response.request().method() === "POST");
  await pendingRegion(page).getByRole("button", { name: "重试获取原任务" }).click();
  const response = await receipt;
  expect(response.request().postData()).toBe(originalBody);
  expect((await response.json()).id).toBe(originalId);
  await expect(pendingRegion(page)).toHaveCount(0);
  expect((await publicationsFor(page, contentId)).map((job) => job.id)).toEqual([originalId]);
});

test("a saved edit invalidates the old preview without creating work", async ({ page }) => {
  const contentId = await startPreview(page, "stale");
  await acknowledge(page);
  const edited = await page.request.patch(`${API}/contents/${contentId}`, {
    headers: await currentCookieHeaders(page), data: { body: "Edited in another tab after preview.", expected_version: 1 },
  });
  expect(edited.status()).toBe(200);
  const receipt = page.waitForResponse((response) => response.url() === `${API}/publishing/jobs` && response.request().method() === "POST");
  await page.getByRole("button", { name: "确认这份发布物", exact: true }).click();
  expect((await receipt).status()).toBe(409);
  await expect(previewRegion(page)).toHaveCount(0);
  await expect(pendingRegion(page)).toHaveCount(0);
  expect(await publicationsFor(page, contentId)).toHaveLength(0);
});

test("unavailable session storage prevents sending confirmation", async ({ page }) => {
  const contentId = await startPreview(page, "storage-denied");
  await acknowledge(page);
  await page.evaluate(() => {
    const original = Storage.prototype.setItem;
    Storage.prototype.setItem = function (key, value) {
      if (key.startsWith("contentflow-publication:")) throw new DOMException("Isolated storage denial", "QuotaExceededError");
      return original.call(this, key, value);
    };
  });
  let sent = 0;
  page.on("request", (request) => { if (request.url() === `${API}/publishing/jobs` && request.method() === "POST") sent += 1; });
  await page.getByRole("button", { name: "确认这份发布物", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Isolated storage denial" })).toBeVisible();
  expect(sent).toBe(0);
  expect(await publicationsFor(page, contentId)).toHaveLength(0);
});

test("asset fetch failure keeps confirmation disabled", async ({ page }) => {
  await page.route("**/publishing/preview-assets/**", (route) => route.abort("connectionreset"));
  const contentId = await startPreview(page, "asset-failure");
  await expect(previewRegion(page).getByRole("alert")).toBeVisible();
  await expect(previewRegion(page).getByRole("checkbox")).toBeDisabled();
  await expect(page.getByRole("button", { name: "确认这份发布物", exact: true })).toBeDisabled();
  expect(await publicationsFor(page, contentId)).toHaveLength(0);
});

test("pending confirmation does not follow a different account or workspace", async ({ page }) => {
  await startPreview(page, "scope");
  await acknowledge(page);
  await page.route(`${API}/publishing/jobs`, (route) => route.request().method() === "POST" ? route.abort("connectionreset") : route.continue());
  await page.getByRole("button", { name: "确认这份发布物", exact: true }).click();
  await expect(pendingRegion(page)).toBeVisible();
  await page.getByRole("button", { name: "退出登录" }).click();
  await expect(page.getByRole("button", { name: "登录 ContentFlow", exact: true })).toBeVisible();
  const registered = await page.request.post(`${API}/auth/register`, { headers: cookieHeaders, data: {
    email: "browser-other@example.com", password: "isolated-browser-password", display_name: "Browser Other", workspace_name: "Browser Other Workspace",
  } });
  expect(registered.status()).toBe(201);
  await page.reload();
  await navigateToPublishing(page);
  await expect(pendingRegion(page)).toHaveCount(0);
});
