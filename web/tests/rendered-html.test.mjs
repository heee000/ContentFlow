import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);

  return worker.fetch(
    new Request("http://localhost/", {
      headers: { accept: "text/html" },
    }),
    {
      ASSETS: {
        fetch: async () => new Response("Not found", { status: 404 }),
      },
      IMAGES: {
        input() {
          throw new Error("Image binding is not used by this page");
        },
      },
    },
    {
      waitUntil() {},
      passThroughOnException() {},
    },
  );
}

test("server-renders the ContentFlow application shell", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);
  const csp = response.headers.get("content-security-policy") ?? "";
  assert.match(csp, /default-src 'self'/);
  assert.match(csp, /connect-src 'self'/);
  assert.match(csp, /frame-ancestors 'none'/);
  assert.match(csp, /object-src 'none'/);
  assert.doesNotMatch(csp, /unsafe-eval/);
  const configuredApiBase =
    process.env.NEXT_PUBLIC_CONTENTFLOW_API_BASE ||
    "http://localhost:8000/api/v1";
  const apiUrl = new URL(configuredApiBase);
  assert.match(csp, new RegExp(`connect-src[^;]*${apiUrl.origin}`));
  if (apiUrl.protocol === "https:") {
    assert.match(csp, /upgrade-insecure-requests/);
    assert.equal(
      response.headers.get("strict-transport-security"),
      "max-age=31536000",
    );
  } else {
    assert.doesNotMatch(csp, /upgrade-insecure-requests/);
    assert.equal(response.headers.get("strict-transport-security"), null);
  }
  assert.equal(response.headers.get("x-frame-options"), "DENY");
  assert.equal(response.headers.get("x-content-type-options"), "nosniff");

  const html = await response.text();
  assert.match(html, /<title>ContentFlow 内容运营工作台(?: · ContentFlow)?<\/title>/i);
  assert.match(html, /ContentFlow/);
  assert.match(html, /正在连接工作台/);
  assert.doesNotMatch(html, /Your site is taking shape|SkeletonPreview/);
});

test("keeps production copy and design tokens in source", async () => {
  const [page, app, apiClient, css, design, packageJson, security] =
    await Promise.all([
      readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
      readFile(new URL("../app/contentflow-app.tsx", import.meta.url), "utf8"),
      readFile(new URL("../lib/contentflow-api.ts", import.meta.url), "utf8"),
      readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
      readFile(new URL("../DESIGN.md", import.meta.url), "utf8"),
      readFile(new URL("../package.json", import.meta.url), "utf8"),
      readFile(new URL("../security.ts", import.meta.url), "utf8"),
    ]);

  assert.match(page, /<ContentFlowApp \/>/);
  assert.match(app, /内容审核/);
  assert.match(app, /发布管理/);
  assert.match(app, /任务队列/);
  assert.match(app, /团队与审计/);
  assert.match(app, /切换工作区/);
  assert.match(app, /编辑 Brief/);
  assert.match(app, /归档后不会再生成新内容/);
  assert.match(app, /版本历史/);
  assert.match(app, /平台排版 \/ 镜头脚本/);
  assert.match(app, /查看全部内容/);
  assert.match(app, /录入人工指标/);
  assert.match(app, /取消排期/);
  assert.match(app, /最近 .* 条审计记录/);
  assert.match(app, /Prompt 审批、发布与回滚/);
  assert.match(app, /创建者不能自行审批/);
  assert.match(app, /查看正文与哈希/);
  assert.match(app, /prompt-releases/);
  assert.match(app, /人工发布/);
  assert.match(app, /生成记录/);
  assert.match(app, /提示词版本/);
  assert.match(app, /runs\?limit=5/);
  assert.match(css, /--blue:\s*#0f62fe/);
  assert.match(css, /prefers-reduced-motion/);
  assert.match(design, /No fake analytics or fake platform publish success/);
  assert.match(app, /生产环境 API 地址由构建配置固定/);
  assert.match(apiClient, /RUNTIME_API_BASE_CONFIGURABLE/);
  assert.doesNotMatch(apiClient, /localStorage\.setItem\("contentflow_token"/);
  assert.match(security, /Content-Security-Policy/);
  assert.match(security, /Strict-Transport-Security/);
  assert.doesNotMatch(packageJson, /react-loading-skeleton/);
});
