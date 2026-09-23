# 发布确认浏览器验收

目的：验证真实 Next.js 生产构建页面 → Cookie 登录 → FastAPI → 临时 SQLite 的最终预览/确认/回执恢复，而不是用模拟接口返回值代替服务端入库。前端自动 build 并运行 standalone server（复制必需静态文件）；API 仍是专用测试环境，不等于实机生产配置验收。

## 隔离

- `tests/browser_server.py` 复用合成集成测试夹具，创建临时数据库和有效 PNG；所有内容/渠道标为 `TEST-ONLY`，没有真实平台凭据，不启动 Worker、模型或发布调用。
- `web/scripts/e2e-service.mjs` 移除继承的 ContentFlow/API 配置，API/前端只绑定 `127.0.0.1:18765/18766`。测试不复用已有服务；端口被占用时应失败，不杀未知进程。
- 浏览器为 Playwright 新建的临时配置，不读取用户浏览器账户。Next 输出到 `.next-e2e`；失败截图/trace 位于忽略的 `web/test-results`，不提交合成 DB 或浏览器缓存。
- 故障注入只用于延迟素材读取、拒绝浏览器存储或中断响应；失回执用例先让真实 POST 返回 202/入库，再中断浏览器回执，重载后核对原始请求与同一个任务 ID。

## 运行

先按仓库锁文件准备根目录 `.venv`（含 test extra）和 `web/node_modules`。Node 需满足 `web/package.json`，不要降低版本要求或改全局 Node。

在 `web` 目录：

```sh
npx playwright install chromium --only-shell
npm run test:e2e
```

Linux CI 先执行 `npx playwright install --with-deps chromium --only-shell`。Windows 本机已有 Edge 时可只对当前测试进程设置 `CONTENTFLOW_E2E_BROWSER_CHANNEL=msedge`，仍为独立无头临时浏览器，不需要接管正在使用的窗口。

测试不自动重试，首次失败即停止。先检查失败的 `error-context.md`/trace，区分夹具选择器、测试环境和产品缺陷，不靠重复执行制造绿色。正常结束由 Playwright 回收专用服务；异常中止时只处理已核对 PID/命令行的本任务子进程，不删除项目数据或修改 ACL。

## 六条正式旅程

1. 实际封面加载完成且用户勾选前禁止确认；页面正文与已保存正文一致，确认只产生一份任务。
2. 服务端已经入库但浏览器丢失回执：表单锁住，刷新恢复，原样重放返回同一任务，不增加分发。
3. 另一页面保存改稿后，旧预览确认返回 409，不创建任务。
4. sessionStorage 无法持久化时不发送确认。
5. 预览素材读取失败时不可勾选/提交。
6. 未解决回执不跨账号/工作区出现。

这些用例不覆盖真实微信排版、公开发布、视频播放兼容、所有浏览器或跨设备恢复；PostgreSQL 锁竞争和对象存储字节完整性由专项测试另行验证。最新执行结果在 `IMPLEMENTATION_PROGRESS.md`，不是以本说明存在作为通过证据。
