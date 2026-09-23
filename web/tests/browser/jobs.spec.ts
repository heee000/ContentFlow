import { expect, test } from "@playwright/test";

test("superseded media work is not displayed as a successful generated asset or offered for replay", async ({ page, context }) => {
  await context.route("**/*", (route) => ["http://127.0.0.1:18765", "http://127.0.0.1:18766"].includes(new URL(route.request().url()).origin)
    ? route.continue() : route.abort("blockedbyclient"));
  await page.goto("/");
  await page.getByLabel("邮箱", { exact: true }).fill("worker@example.com");
  await page.getByLabel("密码", { exact: true }).fill("a-secure-password");
  await page.getByRole("button", { name: "登录 ContentFlow", exact: true }).click();
  await expect(page.getByRole("button", { name: "刷新数据", exact: true })).toBeEnabled();
  const navigation = page.getByRole("navigation", { name: "工作台导航" });
  await navigation.locator("summary").click();
  await navigation.getByRole("button", { name: "任务队列", exact: true }).click();
  const row = page.getByRole("row").filter({ hasText: "旧结果未采用" });
  await expect(row).toHaveCount(1);
  await expect(row).toContainText("asset.generate");
  await expect(row).toContainText("如有暂存对象，请在存储管理核对");
  await expect(row.getByText("成功", { exact: true })).toHaveCount(0);
  await expect(row.getByRole("button")).toHaveCount(0);
});
