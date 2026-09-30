import { expect, test, type Page } from "@playwright/test";

const API = "http://127.0.0.1:18765/api/v1";

test.beforeEach(async ({ context }) => {
  await context.route("**/*", (route) => ["http://127.0.0.1:18765", "http://127.0.0.1:18766"].includes(new URL(route.request().url()).origin)
    ? route.continue() : route.abort("blockedbyclient"));
});

async function login(page: Page) {
  await page.goto("/");
  await page.getByLabel("邮箱", { exact: true }).fill("worker@example.com");
  await page.getByLabel("密码", { exact: true }).fill("a-secure-password");
  await page.getByRole("button", { name: "登录 ContentFlow", exact: true }).click();
  await expect(page.locator(".project-switcher option").filter({ hasText: "TEST-ONLY review-dirty project" })).toHaveCount(1);
}

async function openMetrics(page: Page) {
  const navigation = page.getByRole("navigation", { name: "工作台导航" });
  await navigation.getByText("资源与系统", { exact: true }).click();
  await navigation.getByRole("button", { name: "数据复盘", exact: true }).click();
  await expect(page.getByRole("heading", { name: "数据复盘", exact: true })).toBeVisible();
}

test("metrics outage does not blank project data or masquerade as zero", async ({ page }) => {
  await page.route(`${API}/metrics/summary*`, (route) => route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: { message: "TEST-ONLY metrics unavailable" } }) }));
  await login(page);
  await page.getByRole("navigation", { name: "工作台导航" }).getByRole("button", { name: /^2 审核内容(?: \d+)?$/ }).click();
  await expect(page.locator(".review-queue").getByRole("button").filter({ hasText: "TEST-ONLY review-dirty project" })).toBeVisible();
  await openMetrics(page);
  await expect(page.getByRole("alert").filter({ hasText: "指标暂时无法加载" })).toContainText("未加载的数据不代表零");
  await expect(page.locator(".metric-grid-five")).toHaveCount(0);
  await page.unroute(`${API}/metrics/summary*`);
  await page.getByRole("button", { name: "重新加载指标" }).click();
  await expect(page.locator(".metric-grid-five")).toBeVisible();
  await expect(page.getByText("未加载的数据不代表零", { exact: false })).toHaveCount(0);
});

test("quarantined observations are visibly incomplete, not silently zeroed", async ({ page }) => {
  await page.route(`${API}/metrics/summary*`, (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ sample_count: 1, impressions: 100, clicks: 5, engagements: 4, click_through_rate: 0.05, engagement_rate: 0.04, recommendations: [], excluded_snapshot_count: 2, data_complete: false }) }));
  await login(page);
  await openMetrics(page);
  await expect(page.getByRole("alert").filter({ hasText: "历史指标异常" })).toContainText("2 条历史指标异常");
  await expect(page.getByRole("alert").filter({ hasText: "历史指标异常" })).toContainText("数据不完整");
  await expect(page.locator(".metric-grid-five")).toContainText("100");
});
