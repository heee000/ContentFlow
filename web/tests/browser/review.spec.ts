import { expect, test, type Page } from "@playwright/test";
import { currentCookieHeaders } from "./session-helpers";

const API = "http://127.0.0.1:18765/api/v1";
const headers = { "X-ContentFlow-Session-Mode": "cookie", Origin: "http://127.0.0.1:18766" };
const editor = (page: Page) => page.locator(".review-editor");
const approval = (page: Page) => editor(page).getByRole("button", { name: "确认通过", exact: true });

test.beforeEach(async ({ context }) => {
  await context.route("**/*", (route) => ["http://127.0.0.1:18765", "http://127.0.0.1:18766"].includes(new URL(route.request().url()).origin)
    ? route.continue() : route.abort("blockedbyclient"));
});

async function openReview(page: Page, name: string) {
  await page.goto("/");
  await page.getByLabel("邮箱", { exact: true }).fill("worker@example.com");
  await page.getByLabel("密码", { exact: true }).fill("a-secure-password");
  await page.getByRole("button", { name: "登录 ContentFlow", exact: true }).click();
  // The accessible name includes the asynchronously loaded pending count.
  await page.getByRole("navigation", { name: "工作台导航" }).getByRole("button", { name: /^2 审核内容(?: \d+)?$/ }).click();
  await page.locator(".review-queue").getByRole("button").filter({ hasText: `TEST-ONLY review-${name} project` }).click();
  await expect(editor(page).getByLabel("标题", { exact: true })).toHaveValue(`TEST-ONLY review-${name}`);
  const response = await page.request.get(`${API}/contents`, { headers: await currentCookieHeaders(page) });
  const contents: { id: string; title: string; campaign_id: string }[] = await response.json();
  const item = contents.find((item) => item.title === `TEST-ONLY review-${name}`);
  if (!item) throw new Error("Missing isolated review fixture");
  return item;
}

test("unsaved text cannot approve the previously saved version", async ({ page }) => {
  const item = await openReview(page, "dirty");
  await expect(approval(page)).toBeEnabled();
  await editor(page).getByRole("textbox", { name: "正文", exact: true }).fill("本地尚未保存的替换稿");
  await expect(approval(page)).toBeDisabled();
  await expect(editor(page).getByText("有未保存修改，请先保存后再审核。", { exact: true })).toBeVisible();
  const saved = await (await page.request.get(`${API}/contents/${item.id}`, { headers: await currentCookieHeaders(page) })).json();
  expect(saved.version).toBe(1);
  expect(saved.status).toBe("needs_review");
});

test("save failure keeps the draft and blocks approval", async ({ page }) => {
  const item = await openReview(page, "failed-save");
  await editor(page).getByRole("textbox", { name: "正文", exact: true }).fill("失败后仍应保留的本地草稿");
  await page.route(`${API}/contents/${item.id}`, (route) => route.request().method() === "PATCH"
    ? route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: { message: "TEST-ONLY 保存失败" } }) }) : route.continue());
  await editor(page).getByRole("button", { name: "保存修改", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "TEST-ONLY 保存失败" })).toBeVisible();
  await expect(editor(page).getByRole("textbox", { name: "正文", exact: true })).toHaveValue("失败后仍应保留的本地草稿");
  await expect(approval(page)).toBeDisabled();
});

test("saved new version requires explicit warning acknowledgement and preserves evidence", async ({ page }) => {
  const item = await openReview(page, "save");
  const original = await editor(page).getByRole("textbox", { name: "正文", exact: true }).inputValue();
  await editor(page).getByRole("textbox", { name: "正文", exact: true }).fill(`${original}\n已补充本轮验证说明。`);
  await editor(page).getByRole("button", { name: "保存修改", exact: true }).click();
  await expect(editor(page).getByText("当前版本尚无有效 AI 评分", { exact: true })).toBeVisible();
  await expect(approval(page)).toBeDisabled();
  await editor(page).getByLabel("人工核验理由", { exact: true }).fill("已对照原始资料核验本版本的事实和规则例外");
  await editor(page).getByRole("checkbox", { name: "我已核验当前版本并明确接受上述审核提示" }).check();
  const reviewResponse = page.waitForResponse((response) => response.url() === `${API}/contents/${item.id}/review`);
  await approval(page).click();
  const response = await reviewResponse;
  expect(response.status()).toBe(200);
  expect(response.request().postDataJSON().expected_version).toBe(2);
  const saved = await (await page.request.get(`${API}/contents/${item.id}`, { headers: await currentCookieHeaders(page) })).json();
  expect(saved.version).toBe(2);
  expect(saved.status).toBe("approved");
  expect(saved.review_json.human_content_version).toBe(2);
  const history = await (await page.request.get(`${API}/contents/${item.id}/review-evidence`, { headers: await currentCookieHeaders(page) })).json();
  expect(history.some((row: { event: string; content_version: number }) => row.event === "superseded" && row.content_version === 1)).toBeTruthy();
});

test("remote edit never silently overwrites a dirty draft", async ({ page }) => {
  const item = await openReview(page, "conflict");
  await editor(page).getByRole("textbox", { name: "正文", exact: true }).fill("尚未保存的本地版本，需要保留");
  const edited = await page.request.patch(`${API}/contents/${item.id}`, { headers: await currentCookieHeaders(page), data: { expected_version: 1, body: "另一页面保存的新版本" } });
  expect(edited.status()).toBe(200);
  await page.getByRole("button", { name: "刷新数据", exact: true }).click();
  await expect(editor(page).getByText("服务器上的版本或审核状态已变化。当前输入已保留，请核对后重新载入。", { exact: true })).toBeVisible();
  await expect(editor(page).getByRole("textbox", { name: "正文", exact: true })).toHaveValue("尚未保存的本地版本，需要保留");
  await expect(approval(page)).toBeDisabled();
  await expect(editor(page).getByRole("button", { name: "保存修改", exact: true })).toBeDisabled();
});

test("navigation and project changes require confirmation before discarding edits", async ({ page }) => {
  await openReview(page, "navigation");
  await editor(page).getByRole("textbox", { name: "正文", exact: true }).fill("不能静默丢失的草稿");
  page.once("dialog", (dialog) => dialog.dismiss());
  await page.getByRole("navigation", { name: "工作台导航" }).getByRole("button", { name: "4 发布", exact: true }).click();
  await expect(editor(page).getByRole("textbox", { name: "正文", exact: true })).toHaveValue("不能静默丢失的草稿");
  const other = await page.locator('.project-switcher option').filter({ hasText: "review-legacy project" }).getAttribute("value");
  if (!other) throw new Error("Missing project fixture");
  page.once("dialog", (dialog) => dialog.dismiss());
  await page.getByRole("combobox", { name: "按项目筛选当前工作台" }).selectOption(other);
  await expect(editor(page).getByRole("textbox", { name: "正文", exact: true })).toHaveValue("不能静默丢失的草稿");
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("navigation", { name: "工作台导航" }).getByRole("button", { name: "4 发布", exact: true }).click();
  await expect(page.getByRole("heading", { name: "把已审核内容交付到平台" })).toBeVisible();
});

test("legacy score is labelled unavailable rather than current or zero", async ({ page }) => {
  await openReview(page, "legacy");
  await expect(editor(page).getByText("当前版本尚无有效 AI 评分", { exact: true })).toBeVisible();
  await expect(editor(page).locator(".quality-heading b")).not.toHaveText("9.0 / 10");
  await expect(approval(page)).toBeDisabled();
});

test("in-flight save disables other mutations and prevents leaving before receipt", async ({ page }) => {
  const item = await openReview(page, "busy");
  let release = () => {};
  const pending = new Promise<void>((resolve) => { release = resolve; });
  let patchCount = 0;
  await page.route(`${API}/contents/${item.id}`, async (route) => {
    if (route.request().method() === "PATCH") {
      patchCount += 1;
      await pending;
    }
    await route.continue();
  });
  const body = editor(page).getByRole("textbox", { name: "正文", exact: true });
  await body.fill("测试产品，仅供内部测试。查看详情。等待保存回执。");
  const response = page.waitForResponse((response) => response.url() === `${API}/contents/${item.id}` && response.request().method() === "PATCH");
  await editor(page).getByRole("button", { name: "保存修改", exact: true }).click();
  try {
    await expect(body).toBeDisabled();
    await expect(approval(page)).toBeDisabled();
    await expect(editor(page).getByRole("button", { name: "重新载入当前版本", exact: true })).toBeDisabled();
    await page.getByRole("navigation", { name: "工作台导航" }).getByRole("button", { name: "4 发布", exact: true }).click();
    await expect(page.getByRole("alert").filter({ hasText: "正在保存或审核，请等待回执后再切换。" })).toBeVisible();
    await expect(editor(page)).toBeVisible();
    expect(patchCount).toBe(1);
  } finally {
    release();
  }
  expect((await response).status()).toBe(200);
  await expect(body).toBeEnabled();
  await expect(editor(page).getByText("有未保存修改，请先保存后再审核。", { exact: true })).toHaveCount(0);
});

test("lost committed save receipt retains draft until explicit reload of the saved version", async ({ page }) => {
  const item = await openReview(page, "lost-save-receipt");
  const savedText = "测试产品，仅供内部测试。查看详情。回执丢失但服务端已保存。";
  let patches = 0;
  await page.route(`${API}/contents/${item.id}`, async (route) => {
    if (route.request().method() !== "PATCH") return route.continue();
    patches += 1;
    const committed = await route.fetch();
    expect(committed.status()).toBe(200);
    await route.abort("connectionreset");
  });
  await editor(page).getByRole("textbox", { name: "正文", exact: true }).fill(savedText);
  await editor(page).getByRole("button", { name: "保存修改", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: /\S/ })).toBeVisible();
  await expect(editor(page).getByRole("textbox", { name: "正文", exact: true })).toHaveValue(savedText);
  await expect(approval(page)).toBeDisabled();
  const saved = await (await page.request.get(`${API}/contents/${item.id}`, { headers: await currentCookieHeaders(page) })).json();
  expect(saved.version).toBe(2);
  expect(saved.status).toBe("needs_review");
  page.once("dialog", (dialog) => dialog.dismiss());
  await editor(page).getByRole("button", { name: "重新载入当前版本", exact: true }).click();
  await expect(editor(page).getByText("有未保存修改，请先保存后再审核。", { exact: true })).toBeVisible();
  page.once("dialog", (dialog) => dialog.accept());
  await editor(page).getByRole("button", { name: "重新载入当前版本", exact: true }).click();
  await expect(editor(page).getByText("有未保存修改，请先保存后再审核。", { exact: true })).toHaveCount(0);
  await expect(editor(page).getByRole("textbox", { name: "正文", exact: true })).toHaveValue(savedText);
  await expect(approval(page)).toBeDisabled();
  await editor(page).getByLabel("人工核验理由", { exact: true }).fill("已核验重新载入的服务端第二版稿件");
  await editor(page).getByRole("checkbox", { name: "我已核验当前版本并明确接受上述审核提示" }).check();
  const reviewed = page.waitForResponse((response) => response.url() === `${API}/contents/${item.id}/review`);
  await approval(page).click();
  expect((await reviewed).request().postDataJSON().expected_version).toBe(2);
  expect(patches).toBe(1);
});

test("workspace switch and logout do not discard a draft without confirmation", async ({ page }) => {
  await openReview(page, "workspace");
  const original = await (await page.request.get(`${API}/auth/session`, { headers })).json();
  const target = await page.getByRole("combobox", { name: "切换工作区", exact: true })
    .getByRole("option", { name: "TEST-ONLY empty review workspace" }).getAttribute("value");
  if (!target) throw new Error("Missing isolated second workspace");
  let identityWrites = 0;
  page.on("request", (request) => {
    if (request.method() === "POST" && (request.url().includes("/auth/switch/") || request.url().endsWith("/auth/logout"))) identityWrites += 1;
  });
  await editor(page).getByRole("textbox", { name: "正文", exact: true }).fill("保留工作区中的未保存草稿");
  page.once("dialog", (dialog) => dialog.dismiss());
  await page.getByRole("combobox", { name: "切换工作区", exact: true }).selectOption(target);
  page.once("dialog", (dialog) => dialog.dismiss());
  await page.getByRole("button", { name: "退出登录", exact: true }).click();
  await expect(editor(page).getByRole("textbox", { name: "正文", exact: true })).toHaveValue("保留工作区中的未保存草稿");
  expect(identityWrites).toBe(0);
  const current = await (await page.request.get(`${API}/auth/session`, { headers })).json();
  expect(current.workspace.id).toBe(original.workspace.id);
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("combobox", { name: "切换工作区", exact: true }).selectOption(target);
  await expect(editor(page)).toHaveCount(0);
  expect((await (await page.request.get(`${API}/auth/session`, { headers })).json()).workspace.id).toBe(target);
});

test("unchanged current model evidence can be approved without an exception", async ({ page }) => {
  const item = await openReview(page, "clean-approval");
  await expect(approval(page)).toBeEnabled();
  await expect(editor(page).getByRole("checkbox", { name: "我已核验当前版本并明确接受上述审核提示" })).toHaveCount(0);
  const reviewed = page.waitForResponse((response) => response.url() === `${API}/contents/${item.id}/review`);
  await approval(page).click();
  const response = await reviewed;
  expect(response.status()).toBe(200);
  expect(response.request().postDataJSON()).toMatchObject({ expected_version: 1, acknowledge_review_warnings: false });
  const saved = await (await page.request.get(`${API}/contents/${item.id}`, { headers: await currentCookieHeaders(page) })).json();
  expect(saved.status).toBe("approved");
  expect(saved.review_json.human_warnings).toEqual([]);
});

test("invalid layout stays editable and cannot approve saved content", async ({ page }) => {
  const item = await openReview(page, "invalid-layout");
  let writes = 0;
  page.on("request", (request) => { if (request.method() === "PATCH" && request.url() === `${API}/contents/${item.id}`) writes += 1; });
  const layout = editor(page).getByRole("textbox", { name: /^平台排版 \/ 镜头脚本/ });
  await layout.fill("{invalid-json");
  await expect(approval(page)).toBeDisabled();
  await editor(page).getByRole("button", { name: "保存修改", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: /\S/ })).toBeVisible();
  await expect(layout).toHaveValue("{invalid-json");
  await expect(layout).toBeEnabled();
  await expect(approval(page)).toBeDisabled();
  expect(writes).toBe(0);
});
