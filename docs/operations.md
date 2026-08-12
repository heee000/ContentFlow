# 生产部署与运维

## 配置检查

生产环境至少确认：

- `CONTENTFLOW_ENVIRONMENT=production`
- 两个不同的 32 位以上随机密钥：`CONTENTFLOW_SECRET_KEY` 和 `CONTENTFLOW_CREDENTIAL_ENCRYPTION_KEY`
- PostgreSQL 数据库地址；生产启动会拒绝 SQLite
- S3/MinIO Endpoint、Bucket 和凭据；生产启动会拒绝 local 存储
- 明确的 `CONTENTFLOW_CORS_ORIGINS`，不得包含 `*`
- Web 映射端口与跨域来源一致；例如 `CONTENTFLOW_WEB_PORT=3300` 时，CORS 列表需包含 `http://localhost:3300`
- 真实生产设置 `CONTENTFLOW_ALLOW_MOCK_PROVIDERS=false`；文本/Embedding 显式配置 `MODEL_API_BASE`、`MODEL_API_KEY` 和模型名，图片/视频显式配置中立 HTTP 媒体契约的 `MEDIA_API_BASE`、`MEDIA_API_KEY`、模型名和精确的 `MEDIA_DOWNLOAD_ALLOWED_HOSTS`
- 生产模型与媒体 API Base 必须是 HTTPS，且不得包含 URL 凭据、query 或 fragment；媒体下载 allowlist 只填写不带 scheme、路径、端口或凭据的精确主机名
- 显式设置 `CONTENTFLOW_REQUIRE_GOVERNED_PROMPTS=true`；生产启动会拒绝关闭，Compose 的 API/Worker 默认启用
- 显式设置 `CONTENTFLOW_METRICS_ENABLED=true`，并从密钥管理系统注入独立的 32 位以上 `CONTENTFLOW_METRICS_BEARER_TOKEN`；不得与应用签名或凭据加密密钥复用
- 初始管理员创建完成后设置 `CONTENTFLOW_ALLOW_REGISTRATION=false`
- 根据入口网关限制设置 `CONTENTFLOW_MAX_UPLOAD_BYTES`；应用默认上限为 100 MiB。媒体 Provider JSON 响应另受 `CONTENTFLOW_MEDIA_PROVIDER_MAX_RESPONSE_BYTES` 限制，默认 32 MiB；超过该值的素材必须走精确 allowlist 的下载 URL，避免在 Worker 内存中缓冲超大 base64 JSON


密钥轮换时先把新值设为 `CONTENTFLOW_CREDENTIAL_ENCRYPTION_KEY`，把旧凭据密钥加入 JSON 数组 `CONTENTFLOW_CREDENTIAL_ENCRYPTION_PREVIOUS_KEYS`，所有 API/Worker 实例同时部署后重新创建平台连接，确认新密钥可解密，再移除旧密钥。应用签名密钥与凭据加密密钥不得复用，也不得只保存在同一台主机的 `.env` 中。

仓库不会在生产环境自动 `create_all`。API 容器启动前执行 `alembic upgrade head`；迁移失败时服务不会启动。

## 媒体 Provider v1 上线检查

真实媒体服务必须先按 [`contentflow-media-v1.openapi.yml`](contracts/contentflow-media-v1.openapi.yml) 完成一致性验收：

1. 所有请求识别 `ContentFlow-Media-Version: 1`，所有成功和错误响应都回显该版本。
2. 相同 `Idempotency-Key` 与相同请求至少 24 小时内返回原任务/结果且不重复计费；同键不同请求返回 409。
3. 覆盖同步图片、同步/异步视频、轮询、400、401/403、408、425、429、5xx、无效 JSON、超大响应和下载 URL 过期；确认错误体不含密钥或堆栈。
4. `Retry-After` 使用整数秒；ContentFlow 最多采纳 300 秒。永久 4xx/协议错误应一次失败，可重试错误进入有界退避。
5. 只允许 OpenAPI 声明的 `parameters`；确认 ContentFlow 的 workspace、asset、内容版本和审计字段不会出现在外部请求。
6. 下载域名和所有重定向域名逐一加入非空精确 allowlist；生产仅允许默认 HTTPS 端口。执行空 allowlist、越权域名、非默认端口、URL 凭据和超大文件的拒绝测试。

把上述目标配置注入当前 shell 后，先执行供应商中立的受控 live runner。它会真实创建所选类型素材，可能产生费用；输出路径必须是尚不存在的新文件：

```powershell
$contentFlowEvidenceStamp = Get-Date -Format "yyyyMMdd-HHmmss"
uv run --locked contentflow-media-conformance --kind both --output ".contentflow/evidence/media-conformance-$contentFlowEvidenceStamp.json" --confirm-live-generation
```

退出码 `0` 表示所有自动探针通过，`1` 表示已生成脱敏失败报告，`2` 表示配置/输出路径不安全或不完整。每种所选素材在目标服务遵约时最多产生一次逻辑生成；工具还会发送同键重放、409 冲突、旧版本和无鉴权探针。`--allow-insecure-http` 只允许用于完全隔离的本地适配服务，生产禁止使用。不要把报告与账单、人工质量抽检和故障注入矩阵互相替代。

修改 `MEDIA_API_BASE`、图片/视频模型或 Provider 类型前，先停止新任务入队并排空 `queued/generating/processing` 媒体任务。异步任务会保存不含端点、模型或密钥明文的目标配置指纹；轮询时配置不一致或历史任务缺少指纹会永久失败并要求人工核对，不会把旧任务 ID 发往新服务。排空后再部署全部 API/Worker，并用新报告文件重新执行 conformance。

v1 尚未交付取消操作、能力发现和签名 Webhook；部署验收必须使用轮询并为超时结果保留人工收敛流程。自动 runner 也不能主动制造 408/425/429/5xx、审核拒绝或下载过期，这些仍需目标服务测试钩子和账单侧证据。

## 受治理 Prompt 的生产初始化

生产环境不存在“先用内置 Prompt 顶着运行”的旁路。`CONTENTFLOW_REQUIRE_GOVERNED_PROMPTS=true` 时，管理页会显示生成就绪状态；没有活动的受治理 Prompt，或活动 Prompt 缺少与当前 Eval 套件、Provider 和模型完全匹配的通过证据时，创建生成任务直接返回 409，Worker 在真正调用模型前还会再次复核。

首次部署按以下顺序初始化：

1. 保持 `CONTENTFLOW_ENVIRONMENT=production` 与 `CONTENTFLOW_REQUIRE_GOVERNED_PROMPTS=true`，临时设置 `CONTENTFLOW_ALLOW_REGISTRATION=true`；初始化入口必须限制在 VPN、堡垒机或 IP 白名单内。
2. 注册两名独立管理员账户；第一名创建目标工作区，并在“团队管理”中把第二名加入该工作区且设为管理员。
3. 第一名创建覆盖 plan/generate/review 的 Eval 套件，第二名激活；套件创建者不得自行激活。
4. 第一名基于内置安全基线创建 Prompt 草稿，用生产目标 Provider/模型运行评测；通过后由第二名审批，再由有权限的管理员激活。
5. 确认管理页 Prompt 状态为可生成，执行一次受控生成；随后设置 `CONTENTFLOW_ALLOW_REGISTRATION=false` 并重新部署全部 API 实例。

不要为初始化临时关闭治理门禁，也不要把注册入口直接暴露到公网。目标 Provider/模型、活动 Eval 套件或 Prompt 哈希变化会使旧证据失效，必须重新评测。

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
$contentFlowMetricsHeaders = @{ Authorization = "Bearer $env:CONTENTFLOW_METRICS_BEARER_TOKEN" }
Invoke-WebRequest http://localhost:8000/metrics -Headers $contentFlowMetricsHeaders
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
- `/metrics` 只允许 Prometheus 所在内部网络、VPN 或专用监控入口访问；不得经公网路由暴露，抓取凭据应由密钥系统注入并按流程轮换

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

## 监控基线与告警建议

当前 `/health/ready` 同时检查 PostgreSQL 与对象存储；`/api/v1/admin/worker-health` 提供当前工作区的可操作诊断。启用受保护的 `/metrics` 后，Prometheus 可抓取 HTTP 请求数/延迟/并发和全局数据库运行 Gauge。

仓库提供可选 `observability` Compose profile。启动前必须把 `CONTENTFLOW_METRICS_ENABLED` 设为 `true`，设置不同的 32 位以上 `CONTENTFLOW_METRICS_BEARER_TOKEN` 与 `CONTENTFLOW_GRAFANA_ADMIN_PASSWORD`；HTTPS 环境还需设置真实 `CONTENTFLOW_GRAFANA_ROOT_URL` 和 `CONTENTFLOW_GRAFANA_COOKIE_SECURE=true`。默认 Grafana 只绑定 `127.0.0.1:3301`，Prometheus 不发布宿主端口：

```powershell
docker compose --profile observability up --build -d
docker compose --profile observability ps
docker compose exec prometheus /bin/promtool check config /etc/prometheus/prometheus.yml
docker compose exec prometheus /bin/promtool check rules /etc/prometheus/contentflow.rules.yml
docker compose exec prometheus /bin/promtool test rules /etc/prometheus/contentflow.rules.test.yml
```

Grafana secret preflight 会检查管理员密码长度且不允许与指标 Token 相同。抓取配置、5 条 recording rules、8 条 alerting rules、promtool 行为场景和 11 面板 Dashboard 均位于 `deploy/observability/`，由只读 provisioning 加载。以下是规则采用的核心 PromQL，阈值仍需在目标环境压测后校准：

```promql
# 5 分钟 5xx 比例
sum(rate(contentflow_http_requests_total{status_class="5xx"}[5m]))
  / clamp_min(sum(rate(contentflow_http_requests_total[5m])), 0.001)

# 按模板路由统计 P95；标签不会包含实际资源 ID
histogram_quantile(
  0.95,
  sum by (le, route) (rate(contentflow_http_request_duration_seconds_bucket[5m]))
)

# Worker/积压/人工对账基线
max(contentflow_worker_nodes{state="active"}) < 1
max(contentflow_queue_oldest_ready_age_seconds) > 300
max(contentflow_publish_reconciliation_required) > 0
up{job="contentflow-api"} == 0
```

HTTP Counter/Histogram 来自各 API 进程，应按实例聚合。队列、Worker、Workflow/Eval 与发布对账 Gauge 都读取同一 PostgreSQL 全局视图，多 API 副本会重复暴露相同值，因此告警和看板使用 `max`，不能跨副本求和。

可选 profile 已提供单机 Prometheus、规则和 Grafana 看板，但没有配置占位 Alertmanager receiver，避免把不存在的通知人伪装成闭环。生产仍需接入企业 Alertmanager/托管告警平台，完成 HA/remote-write/长期保留、SLO/错误预算、通知升级、静默权限、值班日历与故障演练；还需用 OpenTelemetry 和专用 Exporter 补齐 Provider/平台耗时与费用、数据库池/慢查询、对象存储错误、Trace 与集中日志关联。

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
