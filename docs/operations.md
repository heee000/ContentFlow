# 生产部署与运维

## 配置检查

生产环境至少确认：

- `CONTENTFLOW_ENVIRONMENT=production`
- 两个不同的 32 位以上随机密钥：`CONTENTFLOW_SECRET_KEY` 和 `CONTENTFLOW_CREDENTIAL_ENCRYPTION_KEY`
- PostgreSQL 数据库地址；生产启动会拒绝 SQLite
- S3/MinIO Endpoint、Bucket 和凭据；生产启动会拒绝 local 存储
- 明确的 `CONTENTFLOW_CORS_ORIGINS`，不得包含 `*`
- Web 映射端口与跨域来源一致；例如 `CONTENTFLOW_WEB_PORT=3300` 时，CORS 列表需包含 `http://localhost:3300`
- 真实生产设置 `CONTENTFLOW_ALLOW_MOCK_PROVIDERS=false`，并配置文本、Embedding、图片、视频 Provider 所需的 Base URL、API Key 和 Workspace ID
- 初始管理员创建完成后设置 `CONTENTFLOW_ALLOW_REGISTRATION=false`
- 根据入口网关限制设置 `CONTENTFLOW_MAX_UPLOAD_BYTES`；应用默认上限为 100 MiB


密钥轮换时先把新值设为 `CONTENTFLOW_CREDENTIAL_ENCRYPTION_KEY`，把旧凭据密钥加入 JSON 数组 `CONTENTFLOW_CREDENTIAL_ENCRYPTION_PREVIOUS_KEYS`，所有 API/Worker 实例同时部署后重新创建平台连接，确认新密钥可解密，再移除旧密钥。应用签名密钥与凭据加密密钥不得复用，也不得只保存在同一台主机的 `.env` 中。

仓库不会在生产环境自动 `create_all`。API 容器启动前执行 `alembic upgrade head`；迁移失败时服务不会启动。

## Docker Compose

```powershell
Copy-Item .env.example .env
# 编辑 .env，替换所有 replace-me
docker compose config
docker compose up --build -d
docker compose ps
```

验收：

```powershell
Invoke-RestMethod http://localhost:8000/health/live
Invoke-RestMethod http://localhost:8000/health/ready
$contentFlowWebPort = if ($env:CONTENTFLOW_WEB_PORT) { $env:CONTENTFLOW_WEB_PORT } else { "3000" }
Invoke-WebRequest "http://localhost:$contentFlowWebPort"
docker compose logs api worker --tail 200
```

## 互联网入口边界

Compose 是单机参考拓扑，不是直接暴露公网的边缘层。上线前必须在 API/Web 前部署受控的 HTTPS 网关或负载均衡器，并至少配置：

- TLS 终止、HTTP 到 HTTPS 重定向和受信任代理头
- `/api/v1/auth/login`、注册与刷新已使用 PostgreSQL 共享限流；边缘层仍需 WAF/DDoS 与全业务 API 配额
- 请求体上限不高于 `CONTENTFLOW_MAX_UPLOAD_BYTES`，并设置连接、读取和上游超时
- WAF/机器人防护、访问日志脱敏和告警；不得记录 Authorization、Cookie、API Key 或平台凭据

应用内安全响应头、统一错误和上传上限属于纵深防御，不能替代网关限流、企业 SSO 或集中密钥管理。

## 浏览器会话与撤销

- Access Token 默认 15 分钟，Refresh Token 默认 14 天；生产环境可分别通过 `CONTENTFLOW_ACCESS_TOKEN_MINUTES`、`CONTENTFLOW_REFRESH_TOKEN_DAYS` 调整。
- Access/Refresh Cookie 都是 `HttpOnly` 和 `SameSite=Lax`，生产环境自动启用 `Secure`。Web 与 API 必须位于同一站点的 HTTPS 域名，并将 Web Origin 精确加入 `CONTENTFLOW_CORS_ORIGINS`。
- `CONTENTFLOW_AUTH_COOKIE_DOMAIN` 建议留空，保持 host-only Cookie；只有明确需要共享父域且完成威胁评审时才设置。
- `POST /api/v1/auth/logout` 撤销当前会话，`POST /api/v1/auth/logout-all` 撤销该用户全部会话。发现 `auth.refresh_reuse_detected` 审计事件时，应视为令牌疑似泄漏并要求重新登录。
- 轮换 `CONTENTFLOW_SECRET_KEY` 会使现有访问/刷新会话全部失效；应安排维护窗口并提前通知用户。平台凭据加密密钥使用独立轮换流程。
- 生产 Web 会固定 `NEXT_PUBLIC_CONTENTFLOW_API_BASE` 并生成 CSP；HTTPS API 构建还启用 HSTS 和请求升级。部署后应检查响应头，并确保 CSP 的 `connect-src` 只包含真实 API Origin。当前仍使用 `unsafe-inline` 兼容静态 Next 输出，后续应迁移到 nonce/hash 策略。
- 认证限流默认窗口/阻断均为 900 秒；登录账号/IP、注册 IP、刷新会话/IP 门槛分别由 `CONTENTFLOW_AUTH_*_ATTEMPTS` 调整。达到门槛返回 429/Retry-After。只有确认网关会覆盖并清洗转发头时才设置 `CONTENTFLOW_TRUSTED_PROXY_HOPS`，值应等于可信代理层数。

## 水平扩展

API 无本地会话状态，可增加副本。Worker 通过 PostgreSQL `SKIP LOCKED` 并发领取任务，也可横向扩展。使用多副本时：

- 所有实例使用同一数据库、对象存储、`CONTENTFLOW_SECRET_KEY` 和相同的凭据解密密钥列表
- 不要把 `local` 存储作为多副本共享存储
- 根据外部模型和平台限流设置 Worker 数量
- 数据库时间应统一为 UTC
- Worker 租约必须保持在 3 到 86400 秒；心跳周期为“30 秒与租约三分之一中的较小值”
- 监控 job lease heartbeat failed、job lease lost 和 stale worker stopped 日志；这些事件意味着当前执行结果已被安全拒绝，但需要排查数据库或 Worker 健康

## Worker 优雅停机与滚动升级

- contentflow-worker 会捕获 SIGTERM/SIGINT，停止领取新 Job；正在执行的 Job 保持租约心跳并在完成落库后退出。
- 空闲 Worker 使用可中断等待，不会因为较长的 poll interval 延迟容器停止。
- Compose 默认 CONTENTFLOW_WORKER_STOP_GRACE_PERIOD=10m；该值必须大于真实 Provider P99 调用时间，并与平台/负载均衡器的终止窗口一致。
- 滚动升级时先停止 Worker，再等待日志出现 worker stopped；不要先停止 PostgreSQL 或对象存储。
- 如果宽限期耗尽后被 SIGKILL，当前 Job 会依靠租约过期恢复；publish.dispatch 若已进入 publishing，则必须走 reconciliation_required 对账，不能直接重发。
- CONTENTFLOW_RESTART_POLICY 默认 unless-stopped，可在开发环境覆盖；API 的迁移 shell 在成功后使用 exec 把信号转交给 Uvicorn。
- 数据库会记录 Worker 节点心跳，工作区管理员可查询 `/api/v1/admin/worker-health` 判断全局消费容量及本工作区队列是否停滞；Compose/编排层仍未自动消费该信号，也缺滚动升级故障注入。

## 数据库迁移

```powershell
python -m alembic current
python -m alembic upgrade head
python -m alembic history
```

初始 PostgreSQL 迁移会安装 `vector` 扩展并创建 1024 维 HNSW 索引。托管数据库需要允许 `CREATE EXTENSION vector`，否则由管理员预先安装。

## 备份与恢复演练

```powershell
# PostgreSQL custom dump + MinIO 对象镜像 + 逐对象 SHA-256 manifest v2
.\scripts\backup_stack.ps1

# 恢复到随机临时数据库和随机临时 bucket 验证
.\scripts\verify_backup.ps1 -BackupPath .\.contentflow\backups\<timestamp>

# 历史回滚包必须显式声明它对应的版本门槛
.\scripts\verify_backup.ps1 -BackupPath <path> -ExpectedAlembicRevision <revision> -MinimumPublicTableCount <count>
```

正式备份前先停止 API/Worker。脚本默认检查 Compose 写入服务与 Alembic `c95f1e4a8d73`；写入服务仍运行或数据库版本不符时会在创建目录前拒绝。`-AllowLiveWrites` 只用于明确接受不一致风险的临时取证，不得作为正式恢复点。

`verify_backup.ps1` 默认校验 dump 哈希、至少 24 张 public 表、迁移版本、对象数量/总字节数和每个对象的大小/SHA-256；随后把数据库恢复到随机临时库、对象恢复到随机临时 bucket 并下载复验，最后清理它创建的库、bucket 和目录。历史备份需显式传入其旧 revision 和表数。

真正灾难恢复时仍必须恢复到新的 PostgreSQL 数据库和空 bucket，完成应用验收后再切换流量。不要未经演练直接对当前 `contentflow` 库执行 `pg_restore --clean`。

- 2026-08-09 已完成上一 head `c9e7b4a2d610` 的本地静默联合恢复演练：18 张表、39 个对象、165208 字节，临时库/bucket/目录均为 0 残留。新 head `c95f1e4a8d73`（经 `f4c2d8e7a190 -> a73f9c2e4b61 -> b84e0d3f7c92`）的持久 PostgreSQL 迁移与 24 表联合恢复需在 Docker 引擎可用后重新签收；旧证据不等于 PITR 或异地灾备。
- MinIO/S3 生产 bucket 应启用版本控制、生命周期、服务端加密和不可变保留策略。
- `CONTENTFLOW_SECRET_KEY`、当前/历史凭据加密密钥必须单独备份到集中密钥管理系统；丢失后访问令牌和已加密平台凭据无法恢复。
- 审计日志按合规周期归档，不要和普通应用日志一起随意清理。

## 监控建议

当前 `/health/ready` 同时检查 PostgreSQL 与对象存储；`/api/v1/admin/worker-health` 汇总 Worker 心跳并按当前工作空间统计队列，出现 `no_active_workers`、`stale_worker_nodes`、`ready_jobs_without_active_workers` 或 `queue_ready_age_exceeded` 时应告警。生产环境仍应采集：

- HTTP 状态、P95/P99 延迟、请求 ID
- Job queued/running/retry/failed 数量与最长排队时间
- 外部模型耗时、错误率和费用
- 各平台限流、授权过期与发布失败率
- 对象存储容量和下载错误
- PostgreSQL 连接池、慢查询和向量查询耗时
- Worker 心跳失败、租约过期、reconciliation_required 数量与最长停留时间

## 故障处理

- 内容未生成：查看 `workflow.execute` Job 和关联 `WorkflowRun.error`
- 文档未索引：确认对象可读、编码与 `knowledge.index` 错误
- 素材长期 processing：查看 `asset.poll` 重试次数和外部 task ID
- 发布失败：确认内容版本、素材状态、渠道 scope 与外部响应
- 取消发布返回 409：Worker 已先锁定任务并进入 publishing，不能再保证取消；等待发布结果，不要把 409 当成取消成功
- 渠道 invalid：重新授权或更新凭据后执行连接测试
- Job 最终 failed：修复根因后在任务队列点击“重试”

## 发布自动对账与人工处置

公众号 `freepublish/submit` 返回 `publish_id` 只表示平台接收，不代表最终发布。ContentFlow 会把 PublishJob 保持为 submitted，并创建幂等的 `publish.reconcile:{publish_job_id}` 队列任务。

### 自动对账

1. 首次查询默认延迟 15 秒，最多尝试 20 次，可通过 `CONTENTFLOW_PUBLISH_RECONCILIATION_INITIAL_DELAY_SECONDS` 和 `CONTENTFLOW_PUBLISH_RECONCILIATION_MAX_ATTEMPTS` 调整。
2. 微信查询未返回 `article_id` 时一律视为 pending，保留 `publish_id` 并按队列退避重试；不能根据不稳定的数字状态猜测已发布。
3. 只有取得 `article_id` 时，PublishJob 才进入 published，并保存文章 ID、URL、查询原始响应和 `publish.reconcile_auto` 审计。
4. 查询连续失败或尝试耗尽后进入 reconciliation_required，保留证据并转人工，不会再次调用发布接口。
5. 远程查询期间不持有 PublishJob 行锁；查询返回后重新锁定并比较状态和查询键。人工处置或其他新状态已经提交时，迟到结果写 `publish.reconciliation_stale_ignored` 后被丢弃。

### 人工处置

1. reviewer 可在 submitted 或 reconciliation_required 时直接接管；接管会在同一事务中终结对应自动对账 Job 并释放 Worker 租约。
2. 若平台已经存在作品，在“发布管理”选择“确认已发布”，填写核对依据，可选补充平台内容 ID 和链接；PublishJob 与原 publish.dispatch Job 分别进入 published 和 succeeded。
3. 若确认平台没有作品，选择“确认未发布”并填写依据；PublishJob 和原 publish.dispatch Job 保持 failed，自动对账 Job 记为人工完成。之后重试原分发任务并获得新 `publish_id` 时，同一个幂等对账 Job 会清空旧结果/租约/尝试次数并重新 queued。
4. 抖音在不确定响应中没有可靠 `item_id` 时不能按标题或时间模糊匹配，仍必须人工核对；不要先重试队列 Job。
5. 检查 `publish.dispatch_started`、`publish.reconciliation_queued`、`publish.reconciliation_requeued`、`publish.reconciliation_checked`、`publish.reconcile_auto`、`publish.reconcile` 和 `publish.reconciliation_stale_ignored` 审计，保留处置证据。

API 等价操作是 `POST /api/v1/publishing/jobs/{publish_job_id}/reconcile`，decision 只能是 `confirmed_published` 或 `confirmed_not_published`，reason 必填。

应分别为 submitted、reconciliation_required、对账 Job 失败率和最长停留时间建立告警与处置 SLA。当前尚无统一平台回调验签/去重、平台级幂等键和真实租户生产签收。

## 自动化质量与依赖门禁

仓库的 `.github/workflows/ci.yml` 在 main 推送、Pull Request 和人工触发时定义两条只读门禁：

1. 后端使用固定 digest 的 PostgreSQL/pgvector 和临时 MinIO 服务，从 `uv.lock` 重建全部依赖，执行 Ruff、56 项测试、真实 PostgreSQL/MinIO 集成场景、75% 分支覆盖率和严格 `pip-audit`。
2. 前端使用 Node 22.13.0 和 `npm ci`，执行 ESLint、Sites/vinext 渲染测试、Next.js 生产构建和高危级别 npm 审计。
3. checkout、setup-uv 和 setup-node 均固定到完整提交 SHA；`.github/dependabot.yml` 每周检查 uv、npm、GitHub Actions 和 Docker 依赖。

本地等价验证：

```powershell
uv sync --all-extras --locked --python 3.12
uv lock --check
uv run --locked ruff check .
uv run --locked pytest -q --cov=contentflow --cov-branch --cov-fail-under=75
$env:PYTHONUTF8="1"
uv run --locked pip-audit --strict

Set-Location web
npm ci
npm run lint
npm test
npm run build
npm audit --audit-level=moderate
```

升级 Python 依赖后必须重新运行 `uv lock` 和漏洞审计；升级前端依赖后必须更新 `package-lock.json` 并从空 `node_modules` 执行 `npm ci`。不要未经依赖链审查直接运行 `npm audit fix --force`。工作流文件存在不等于远程门禁已经启用：仓库管理员仍需在 GitHub 受保护分支上要求后端和前端检查通过后才能合并。

### 租约耗尽与权限竞态补充

- publish.dispatch 已进入 publishing 后若最终租约耗尽，系统会把 PublishJob 置为 reconciliation_required。按“发布结果不确定的处置”核对，禁止直接重试。
- 管理员降级和移除在 PostgreSQL 中锁定 Workspace 行，保证最后管理员判断串行执行；如果管理操作长期等待，应检查该工作区上的未提交管理事务和数据库锁等待。
