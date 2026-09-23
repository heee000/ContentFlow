import { expect, test, type Page } from "@playwright/test";
import { API, currentCookieHeaders } from "./session-helpers";

const pending = (page: Page) => page.getByRole("region", { name: "待核对的生成请求", exact: true });
const navigate = (page: Page) => page.getByRole("navigation", { name: "工作台导航" }).getByRole("button", { name: "1 创建内容", exact: true }).click();

test.beforeEach(async ({ context }) => {
  await context.route("**/*", (route) => ["http://127.0.0.1:18765", "http://127.0.0.1:18766"].includes(new URL(route.request().url()).origin)
    ? route.continue() : route.abort("blockedbyclient"));
});

async function setup(page: Page, label: string) {
  await page.goto("/");
  await page.getByLabel("邮箱", { exact: true }).fill("worker@example.com");
  await page.getByLabel("密码", { exact: true }).fill("a-secure-password");
  await page.getByRole("button", { name: "登录 ContentFlow", exact: true }).click();
  await expect(page.getByRole("button", { name: "刷新数据", exact: true })).toBeEnabled();
  const response = await page.request.post(`${API}/campaigns`, { headers: await currentCookieHeaders(page),
    data: { name: `TEST-ONLY generation ${label}`, product_name: "TEST", objective: "Verify recovery", audience: "Tests", platforms: ["wechat"] } });
  expect(response.status()).toBe(201);
  const campaign = await response.json();
  await page.getByRole("button", { name: "刷新数据", exact: true }).click();
  await navigate(page);
  const row = page.locator(".campaign-row").filter({ has: page.getByRole("heading", { name: campaign.name, exact: true }) });
  await expect(row).toBeVisible();
  return { campaign, row, path: `${API}/campaigns/${campaign.id}/runs` };
}

test("lost accepted generation receipt restores and replays the identical operation once", async ({ page }) => {
  const { campaign, row, path } = await setup(page, "lost-receipt");
  let accepted = "";
  const requests: { key: string | null; body: string | null }[] = [];
  page.on("request", (request) => {
    if (request.url() === path && request.method() === "POST") requests.push({ key: request.headers()["idempotency-key"], body: request.postData() });
  });
  await page.route(path, async (route) => {
    const response = await route.fetch();
    expect(response.status()).toBe(202);
    accepted = (await response.json()).id;
    await route.abort("connectionreset");
  });
  await row.getByRole("button", { name: "生成内容", exact: true }).click();
  await expect(pending(page).getByRole("button")).toBeEnabled();
  await expect(pending(page)).toContainText(campaign.name);
  expect(requests).toHaveLength(1);
  await page.unroute(path);
  await page.reload();
  await navigate(page);
  await expect(pending(page)).toBeVisible();
  expect(requests).toHaveLength(1); // no automatic replay on restore
  await pending(page).getByRole("button").click();
  await expect(pending(page)).toHaveCount(0);
  expect(requests).toHaveLength(2);
  expect(requests[1]).toEqual(requests[0]);
  await row.getByText("任务与操作编号", { exact: true }).click();
  await expect(row.locator("details")).toContainText(requests[0].key!);
  const runs = await page.request.get(path, { headers: await currentCookieHeaders(page) });
  expect((await runs.json()).map((run: { id: string }) => run.id)).toEqual([accepted]);
});

test("denied receipt storage prevents any generation POST", async ({ page }) => {
  await page.addInitScript(() => {
    const original = Storage.prototype.setItem;
    Storage.prototype.setItem = function (key, value) {
      if (key.startsWith("contentflow-generation:")) throw new DOMException("TEST-ONLY denied", "QuotaExceededError");
      return original.call(this, key, value);
    };
  });
  const { row, path } = await setup(page, "storage-denied");
  let posts = 0;
  page.on("request", (request) => { if (request.url() === path && request.method() === "POST") posts += 1; });
  await row.getByRole("button", { name: "生成内容", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "TEST-ONLY denied" })).toBeVisible();
  expect(posts).toBe(0);
});

test("a never-accepted request cannot execute a subsequently edited Brief on recovery", async ({ page }) => {
  const { campaign, row, path } = await setup(page, "changed-brief");
  await page.route(path, (route) => route.abort("connectionreset"));
  await row.getByRole("button", { name: "生成内容", exact: true }).click();
  await expect(pending(page).getByRole("button")).toBeEnabled();
  await page.unroute(path);
  const edit = await page.request.patch(`${API}/campaigns/${campaign.id}`, { headers: await currentCookieHeaders(page),
    data: { objective: "TEST-ONLY changed after the original intent" } });
  expect(edit.status()).toBe(200);
  const rejected = page.waitForResponse((response) => response.url() === path && response.request().method() === "POST");
  await pending(page).getByRole("button").click();
  expect((await rejected).status()).toBe(409);
  await expect(pending(page)).toHaveCount(0);
  await expect(page.getByRole("alert").filter({ hasText: "Brief 已改变" })).toBeVisible();
  expect(await (await page.request.get(path, { headers: await currentCookieHeaders(page) })).json()).toEqual([]);
});

test("unresolved generation does not follow another workspace and returns intact on switching back", async ({ page }) => {
  const { campaign, row, path } = await setup(page, "workspace");
  await page.route(path, (route) => route.abort("connectionreset"));
  await row.getByRole("button", { name: "生成内容", exact: true }).click();
  await expect(pending(page).getByRole("button")).toBeEnabled();
  const operation = await pending(page).locator("code").innerText();
  const select = page.getByRole("combobox", { name: "切换工作区", exact: true });
  const original = await select.inputValue();
  const other = await select.getByRole("option", { name: /TEST-ONLY empty review workspace/ }).getAttribute("value");
  if (!other) throw new Error("Missing isolated workspace");
  await select.selectOption(other);
  await expect(select).toHaveValue(other);
  await navigate(page);
  await expect(pending(page)).toHaveCount(0);
  await select.selectOption(original);
  await expect(select).toHaveValue(original);
  await navigate(page);
  await expect(pending(page)).toContainText(campaign.name);
  await expect(pending(page).locator("code")).toHaveText(operation);
});

test("leaving and returning during an accepted request can recover without duplicate work or stuck cleanup", async ({ page }) => {
  const { row, path } = await setup(page, "navigation-race");
  let release = () => {};
  const held = new Promise<void>((resolve) => { release = resolve; });
  let arrived = () => {};
  const committed = new Promise<void>((resolve) => { arrived = resolve; });
  let releaseSecond = () => {};
  const secondHeld = new Promise<void>((resolve) => { releaseSecond = resolve; });
  let secondArrived = () => {};
  const secondCommitted = new Promise<void>((resolve) => { secondArrived = resolve; });
  let posts = 0;
  let accepted = "";
  await page.route(path, async (route) => {
    const index = ++posts;
    const response = await route.fetch();
    expect(response.status()).toBe(202);
    if (index === 1) { accepted = (await response.json()).id; arrived(); await held; }
    else { secondArrived(); await secondHeld; }
    await route.fulfill({ response });
  });
  await row.getByRole("button", { name: "生成内容", exact: true }).click();
  try {
    await committed;
    await page.getByRole("navigation", { name: "工作台导航" }).getByRole("button", { name: "工作台", exact: true }).click();
    await navigate(page);
    await expect(pending(page).getByRole("button")).toBeEnabled();
    await pending(page).getByRole("button").click();
    await secondCommitted;
    release();
    // The old mounted lifetime clears the same receipt first. The currently
    // visible lifetime must still finish when its identical response arrives.
    await expect.poll(() => page.evaluate(() => Object.keys(sessionStorage)
      .some((key) => key.startsWith("contentflow-generation:")))).toBe(false);
    releaseSecond();
    await expect(pending(page)).toHaveCount(0);
  } finally { release(); releaseSecond(); }
  await page.unrouteAll({ behavior: "wait" });
  expect(posts).toBe(2);
  expect((await (await page.request.get(path, { headers: await currentCookieHeaders(page) })).json())
    .map((run: { id: string }) => run.id)).toEqual([accepted]);
});
