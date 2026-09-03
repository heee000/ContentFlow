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

## 文本 Agent 请求预算

`CONTENTFLOW_MODEL_REQUEST_TIMEOUT_SECONDS` 默认 120 秒，只允许 10–300 秒。它约束每次模型 HTTP 请求，不改变标准档位 0 次、深度档位最多 1 次修订的预算。Prompt Eval 或工作流出现 `TimeoutError` 时，应先查看脱敏 AI provenance 的阶段与时延；只有确认没有平台副作用且已经修正原因后才重试。可选修订或最终复评的 RuntimeError/TimeoutError 会保留已评审原稿并记录失败类型，不采用未复评修订稿。

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

Worker 服务模式会按 PostgreSQL SQLSTATE 区分数据库异常，不再把所有 SQLAlchemy `OperationalError` 都当作断库。分类与处置如下：

| 类别 | SQLSTATE/信号 | 处置 |
| --- | --- | --- |
| `availability` | `08xxx`、`53300`、`57P01`-`57P04`、`58030`、连接失效、连接池超时 | 不进入 `fail_job`；保留已领取任务租约并由服务级有界退避恢复 |
| `transaction_retryable` | `40001`、`40P01` | 事务已回滚时按 Job 退避重试；若 `publish.dispatch` 已进入 `publishing`，立即终结队列尝试并转 `reconciliation_required` |
| `lock_contention` | `55P03` | 与事务冲突相同，禁止绕过发布副作用保护 |
| `query_interrupted` | `57014` | 允许有界恢复；持续超时最终由 Job 或 Worker 重试预算终结，不能无限循环 |
| `permanent` | 驱动提供的其他有效 SQLSTATE，或 SQLAlchemy `DataError`/`IntegrityError`/`ProgrammingError` | Job 内立即失败、不消耗后续尝试；领取/维护阶段直接退出交给编排器和人工修复 |

没有 SQLSTATE 的旧驱动 `OperationalError` 继续保守归为 `availability`，避免一次分类缺失把外部副作用误写成可安全重发。所有数据库 Job 错误、心跳与 Worker 恢复日志只记录类别、SQLSTATE 和异常类型，不持久化 SQL、参数、DSN 或驱动正文。非数据库约束、数据和编程错误继续走原业务失败路径并保留调试堆栈。数据库重试参数如下：

```dotenv
CONTENTFLOW_WORKER_DATABASE_RETRY_INITIAL_SECONDS=1
CONTENTFLOW_WORKER_DATABASE_RETRY_MAX_SECONDS=30
CONTENTFLOW_WORKER_DATABASE_RETRY_MAX_ATTEMPTS=8
CONTENTFLOW_WORKER_DATABASE_RETRY_JITTER_RATIO=0.2
```

服务默认按 1、2、4、8、16、30、30、30 秒名义间隔重试，每次加入最多 20% 抖动；第 8 次等待后的下一次可恢复数据库错误会以脱敏终止错误退出，再由 `CONTENTFLOW_RESTART_POLICY` 对应的编排器重启。退避等待可被 SIGTERM/SIGINT 立即打断；恢复后存储与发布维护扫描会立即重新取得资格。`contentflow-worker --once` 用于单次管理执行，不做进程内重试。

连接完全中断时，数据库心跳本身无法写入；应联合观察 `ContentFlowNoActiveWorkers`、`ContentFlowStaleWorkerDetected`、`ContentFlowAPIDown`、队列最老等待时间和编排器重启次数。PostgreSQL 集成门禁会由真实驱动产生 `40001`、`40P01`、`57014`、`55P03` 和 `42P01` 来验证解析：两个 `SERIALIZABLE` 事务并发更新同一快照会稳定产生一个序列化失败，两个事务以相反顺序锁定两行会稳定产生一个死锁牺牲者。

CI 还会把 GitHub Actions 创建的一次性 PostgreSQL service container ID 只交给集成测试。测试先让一个 Worker 完成探针任务，再优雅停止数据库；必须观察到脱敏 availability 重试且 Worker 仍存活。容器重新启动并通过就绪查询后，同一 Worker 必须在 15 秒测试上限内只执行一次新探针并正常登记停止。容器 ID 有严格格式校验，控制动作只允许 start/stop，无该 ID 的本地测试会跳过，不得手工把持久数据库容器 ID 传给此用例。

该门禁使用专用的 0.1–0.5 秒、100 次加速预算；15 秒只是单次 CI 防悬挂上限，不是生产默认预算的 RTO/SLO。它没有覆盖在途 Handler/完成提交、Worker SIGKILL、数据库 crash、DNS、连接池耗尽、网络分区、主从切换或多 Worker 惊群。上线前仍必须在目标环境按真实默认参数重复演练并测量 P50/P95，不能仅凭一次 stop/start 调低租约或宣称高可用。

另一个 PostgreSQL 集成门禁会让独立 Worker 领取无副作用探针并进入阻塞 Handler，然后在 Linux 上以 SIGKILL 强制终止。第二 Worker 在 6 秒测试租约到期前必须拒绝抢占，过期后才以新 attempt 接管并完成；崩溃节点应保持 `online` 但心跳变 stale，恢复节点应正常写入 `stopped`。这证明不要手工清空 `locked_by/locked_at`：应等待租约与 fencing 协议决定接管，并用 heartbeat 而不是 status 字段单独判断 Worker 是否存活。

6 秒是 CI 专用租约，不是生产恢复承诺。该探针没有调用 AI、对象存储或平台，也没有覆盖 Handler 返回后至 `complete_job` 提交之间的故障。真实环境遇到 Worker SIGKILL 时，先确认旧进程确已退出并观察租约/心跳；发布任务若可能已经产生外部写入，继续按 `reconciliation_required` 对账，绝不能因新 Worker 可接管就直接重发。

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

正式备份前先停止 API/Worker。脚本默认检查 Compose 写入服务与 Alembic `d2e3f4a5b6c7`；写入服务仍运行或数据库版本不符时会在创建目录前拒绝。`-AllowLiveWrites` 只用于明确接受不一致风险的临时取证，不得作为正式恢复点。

`verify_backup.ps1` 默认校验 dump 哈希、至少 30 张 public 表、迁移版本、对象数量/总字节数和每个对象的大小/SHA-256；随后把数据库恢复到随机临时库、对象恢复到随机临时 bucket 并下载复验，最后清理它创建的库、bucket 和目录。历史备份需显式传入其旧 revision 和表数。

真正灾难恢复时仍必须恢复到新的 PostgreSQL 数据库和空 bucket，完成应用验收后再切换流量。不要未经演练直接对当前 `contentflow` 库执行 `pg_restore --clean`。

- 2026-08-09 已完成上一 head `c9e7b4a2d610` 的本地静默联合恢复演练：18 张表、39 个对象、165208 字节，临时库/bucket/目录均为 0 残留。当前 head `d2e3f4a5b6c7` 已增加工作区存储用量、对象分配两张表、存储到期调度索引和发布对账恢复扫描索引；持久 PostgreSQL 迁移与 30 表联合恢复需由当前 CI/恢复演练重新签收，旧证据不等于 PITR 或异地灾备。
- 从大数据量旧版本升级到 `d2e3f4a5b6c7` 会先回填 `assets.content_version` 和分页索引，再分批扫描知识、素材、发布证据与发布包 URI 建立统一账本，最后创建存储和发布恢复调度索引。旧对象大小无法确认时标为未验证并阻止新增写入；同一 URI 被多条旧记录引用时标为 `integrity_error` 且禁止自动删除。当前自动迁移适合个人测试/低数据量环境；企业生产必须先在副本测量扫描、索引空间、WAL 和锁等待，并在维护窗口由独立迁移任务执行，不能依赖多副本 API 同时启动迁移。
- 运营列表调用方应保存 `X-ContentFlow-Next-Cursor` 原样继续读取，不得解析或构造游标。增量同步使用上次响应的 `X-ContentFlow-Sync-Time` 并保留短重叠窗口；收到 422 游标错误应丢弃游标并从第一页重载。`updated_after` 必须包含时区。
- MinIO/S3 生产 bucket 应启用版本控制、生命周期、服务端加密和不可变保留策略。
- `CONTENTFLOW_SECRET_KEY`、当前/历史凭据加密密钥必须单独备份到集中密钥管理系统；丢失后访问令牌和已加密平台凭据无法恢复。
- 审计日志按合规周期归档，不要和普通应用日志一起随意清理。管理员应定期调用 `GET /api/v1/admin/audit-integrity`；返回 `valid=false` 时暂停高风险发布/治理操作，保留数据库和对象存储快照，并依据 `reason` 与 `first_invalid_sequence` 调查。链头哈希可作为外部归档锚点，但当前数据库内哈希链只提供篡改检测，不等于 WORM、可信时间戳或管理员不可伪造。

脚本发布证据默认单文件 10 MiB、单次尝试 20 个对象/累计 50 MiB，图片解码像素上限 4000 万；通过 `CONTENTFLOW_PUBLISH_EVIDENCE_MAX_BYTES`、`CONTENTFLOW_PUBLISH_EVIDENCE_MAX_ITEMS`、`CONTENTFLOW_PUBLISH_EVIDENCE_MAX_TOTAL_BYTES` 和 `CONTENTFLOW_PUBLISH_EVIDENCE_MAX_PIXELS` 调整。累计上限不得小于单文件上限，单文件上限不得超过通用上传上限；PostgreSQL 会锁定发布任务后检查数量和累计字节，超限请求不会写对象。每个内容版本默认最多 20 个素材记录，可用 `CONTENTFLOW_ASSET_MAX_ITEMS_PER_CONTENT_VERSION` 在 1 至 100 间调整；旧版本任务会在媒体 Provider 调用前停止。任务包确认窗口默认 1440 分钟，可用 `CONTENTFLOW_SCRIPT_CONFIRMATION_TTL_MINUTES` 在 15 至 43200 分钟之间调整；修改只影响之后生成的新尝试。

证据对象应纳入与数据库一致的备份、保留和访问审计。任务包或证据对象写入成功、数据库 flush/commit 失败时，API/Worker 会同步回滚数据库并尽力删除刚写入的对象；过期重建和素材替换会创建可重试的 `storage.delete` 任务，只有物理删除成功后才释放配额。删除失败保持 `delete_pending` 和计费状态，避免把仍存在的对象伪装成已清理。

## 工作区存储账本与对账

所有持久对象写入先在 PostgreSQL 中原子预留工作区字节数和对象数，再使用带 allocation UUID 的唯一物理键写入，最后把预留转为正式用量。默认单工作区上限为 5 GiB / 10000 个对象，可通过以下配置调整：

```dotenv
CONTENTFLOW_WORKSPACE_STORAGE_MAX_BYTES=5368709120
CONTENTFLOW_WORKSPACE_STORAGE_MAX_OBJECTS=10000
CONTENTFLOW_STORAGE_RESERVATION_TTL_MINUTES=60
CONTENTFLOW_STORAGE_CLEANUP_BATCH_SIZE=100
CONTENTFLOW_STORAGE_DELETE_MAX_ATTEMPTS=20
CONTENTFLOW_STORAGE_ORPHAN_GRACE_SECONDS=86400
CONTENTFLOW_STORAGE_RECONCILE_SCHEDULE_ENABLED=true
CONTENTFLOW_STORAGE_RECONCILE_INTERVAL_HOURS=24
CONTENTFLOW_STORAGE_RECONCILE_SCHEDULE_BATCH_SIZE=25
CONTENTFLOW_STORAGE_RECONCILE_SCHEDULE_POLL_SECONDS=60
```

- 管理员在“团队与审计 → 对象存储配额与一致性”查看计费容量、对象数、预留、未验证旧对象、待删除、缺失和完整性异常；API 分别为 `GET /api/v1/admin/storage/usage` 与有界分页的 `GET /api/v1/admin/storage/objects`。
- “核对账本”调用 `POST /api/v1/admin/storage/reconcile`，由 Worker 分页扫描当前配置的存储后端，释放过期预留、补全旧对象大小、发现已验证对象缺失或大小变化，并报告超过宽限期的孤儿候选。巡检跨页携带固定开始水位，扫描期间的新写入不会被误判缺失。
- 任一 Worker 会在领取普通任务前检查是否需要为到期工作区安排自动核对，但检查本身使用独立的进程内节流，默认每个 Worker 最多每 60 秒查询一次；这与每个工作区默认 24 小时的核对周期是两个不同参数。每轮最多选择 25 个工作区。PostgreSQL 使用工作区行锁和 `SKIP LOCKED`，同一工作区使用固定入口幂等键，多 Worker 只会创建一个任务；终态失败在一个周期内冷却，不会空转重入。进程重启会立即检查一次，数据库锁与幂等键继续负责跨进程正确性；新工作区从创建时间开始计算首个周期，升级后 `last_reconciled_at` 为空的旧工作区会逐批完成首次核对。
- 自动任务始终写入 `delete_orphans=false`，只报告和修复账本计量，绝不会自动删除孤儿。禁用自动计划只需设置 `CONTENTFLOW_STORAGE_RECONCILE_SCHEDULE_ENABLED=false`；这不会终止已经入队的任务，也不应作为长期规避异常的手段。
- “清理孤儿对象”必须再次确认并发送 `delete_orphans=true`；只删除超过宽限期且不在账本中的对象。若已有仅核对任务运行，请等待完成后再发起清理，接口会返回 409 而不会悄悄降级请求。
- 迁移发现多个旧数据库记录共享同一 URI 时，会保留一个 `shared_legacy` 隔离项并禁止自动删除。应先定位所有引用、复制为独立对象并更新引用，再由维护流程清理；不要直接改账本状态或手工删原对象。
- 删除登记再次校验 URI 位于当前工作区前缀，不能用同 Bucket 或同本地根目录中的其他工作区对象创建删除任务。账本 API 不返回真实 `storage_uri`，降低路径与 Bucket key 暴露。

当前对账只扫描“当前配置的”存储后端；从 local/旧 Bucket 迁移到新 Bucket 时，旧后端必须在独立维护窗口单独清点。自动计划只解决定期存在性/大小核对，不等于周期性全对象内容哈希巡检。读取路径仍执行 SHA-256 校验。启用 S3/MinIO 版本控制后，逻辑删除可能只创建 delete marker，历史版本容量与云账单不计入 ContentFlow 工作区配额，必须由 Bucket 生命周期、Object Lock/保留策略和云成本告警治理。

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

Grafana secret preflight 会检查管理员密码长度且不允许与指标 Token 相同。抓取配置、5 条 recording rules、11 条 alerting rules、promtool 行为场景和 14 面板 Dashboard 均位于 `deploy/observability/`，由只读 provisioning 加载。以下是规则采用的核心 PromQL，阈值仍需在目标环境压测后校准：

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

# 存储账本异常、到期核对和删除积压
sum(contentflow_storage_allocations{status=~"missing|integrity_error"}) > 0
max(contentflow_storage_reconciliation_overdue_workspaces) > 0
max(contentflow_storage_reconciliation_failed_jobs) > 0
max(contentflow_storage_delete_pending_oldest_age_seconds) > 86400
```

HTTP Counter/Histogram 来自各 API 进程，应按实例聚合。队列、Worker、Workflow/Eval、发布对账和存储 Gauge 都读取同一 PostgreSQL 全局视图，多 API 副本会重复暴露相同值，因此告警和看板使用 `max`，不能跨副本求和。所有存储标签都是固定 `status/state` 集合，不得加入 workspace、对象 URI 或任务 ID。

存储告警处置顺序：先在 Grafana 判断是 `missing/integrity_error`、核对超期/失败，还是 `delete_pending` 超过一天；再到管理页核对对象状态和最近审计。完整性异常必须确认物理对象与业务引用，不能直接改数据库状态；核对失败可在根因修复后由管理员重新点“核对账本”，终态入口任务会原位重置。孤儿删除必须先完成只读核对和备份，再由管理员显式确认；自动调度永远不会替你执行删除。告警恢复只能说明数据库当前聚合恢复，不等于旧 Bucket、历史版本或备份副本已清理。

可选 profile 已提供单机 Prometheus、规则和 Grafana 看板，但没有配置占位 Alertmanager receiver，避免把不存在的通知人伪装成闭环。生产仍需接入企业 Alertmanager/托管告警平台，完成 HA/remote-write/长期保留、SLO/错误预算、通知升级、静默权限、值班日历与故障演练；还需用 OpenTelemetry 和专用 Exporter 补齐 Provider/平台耗时与费用、数据库池/慢查询、对象存储错误、Trace 与集中日志关联。

## 故障处理

- 内容未生成：查看 `workflow.execute` Job 和关联 `WorkflowRun.error`
- 文档未索引：确认对象可读、编码与 `knowledge.index` 错误
- 素材长期 processing：查看 `asset.poll` 重试次数和外部 task ID
- 发布失败：先看发布页是否标记“可安全重试”及失败阶段，再确认内容版本、素材状态、渠道 scope 与外部响应
- 取消发布返回 409：Worker 已先锁定任务并开始分发，不能再保证取消；等待发布结果，不要把 409 当成取消成功
- 渠道 invalid：重新授权、修复白名单或更新凭据后执行连接测试
- 普通非发布 Job 最终 failed：修复根因后在任务队列点击“重试”；`manual_review` 必须按下述供应商核对流程处置，发布 Job 必须回到发布页按副作用边界处理

### Job 自动恢复与人工核对

Worker 不会把所有失败或过期租约机械地视为可安全重放。当前生产 Handler 的恢复边界如下：

| 类型 | Job | 自动恢复前提 |
| --- | --- | --- |
| 只读/可重放 | `connector.test`、`asset.search`、`asset.poll`、`storage.reconcile`、`metrics.pull` | 没有外部写副作用；仍可能消耗查询配额或受速率限制 |
| Provider 幂等 | `asset.generate` | 只在 Media Contract 已验证稳定 `Idempotency-Key` 时自动恢复 |
| 领域状态保护 | `publish.dispatch`、`publish.reconcile`、`storage.delete` | 由发布状态机、对账或对象账本阻止盲目重复写入 |
| 配置决定 | `knowledge.index` | `hash`/`bge-m3-local` 可恢复；`openai-compatible` 必须人工核对 |
| 必须人工核对 | `workflow.execute`、`prompt_eval.execute` | OpenAI-compatible 适配器会发送稳定请求键并预写调用账本，但目标供应商是否兑现幂等尚未通过 conformance 证明；异常或租约过期均不自动重跑 |

需要人工核对的 Job 会进入独立 `manual_review` 状态并创建 `job_manual_reviews` 历史记录，不会进入自动退避或被过期租约重新领取。普通 `POST /jobs/{id}/retry` 同时拒绝当前及旧版 failed 形式的高风险 Provider Job，不能绕过核对流程。

处置步骤：

1. 由 reviewer 或 admin 打开“任务队列 → 核对处理”，按任务时间窗口进入当前模型/Embedding 供应商控制台。
2. 先查看页面中的“ContentFlow 已保存的调用证据”，再同时核对供应商控制台中的请求、费用和结果；不要仅凭 ContentFlow 没有保存结果就认定供应商没有执行。队列 idempotency key 只去重 Job 记录，调用账本中的 `Idempotency-Key` 也只证明请求头已发送，二者都不等于外部请求 exactly-once。
3. 勾选已经完成供应商侧核对，并写入至少 8 个字符的核对记录，说明检查范围、结果和决策依据。
4. 只有确认供应商未处理时选择“允许重试”；已有结果或仍无法确认时选择“放弃任务”，再按领域记录人工对账。API 等价入口为 `POST /api/v1/jobs/{job_id}/manual-review`，请求体必须包含 `decision=retry|abandon`、`provider_checked=true` 和 `note`。
5. 每次请求人工核对与最终处置均进入防篡改审计链；核对表保留每轮原因码、结构化检查步骤、确认位、处置人、时间、结论和备注。数据库部分唯一索引保证同一 Job 最多只有一个未关闭核对。

Prometheus 暴露 `contentflow_queue_jobs{status="manual_review"}`、`contentflow_job_manual_review_oldest_age_seconds` 和 Provider 调用账本指标。默认规则在最老未处理核对超过 1 小时并持续 15 分钟时触发 `ContentFlowJobManualReviewOverdue`；这是一组全局低基数告警，不包含工作区或任务 ID，值班人员应回到受权限保护的任务队列定位。

当前已有请求/响应摘要和供应商请求 ID 的基础账本，但仍没有价格表与金额核算、供应商请求 ID 自动查询、核对证据附件、负责人认领或双人确认。`manual_review` 关闭的是机械误重试和无留痕处置，不是 Provider exactly-once；企业生产仍需把这些缺口纳入外部工单和审批控制。

### Provider 调用账本与人工核对

OpenAI-compatible 文本与远程 Embedding 调用会写入两层账本：`provider_invocations` 表示由工作区、Job、领域实体、操作序号、Provider/模型和请求摘要确定的逻辑请求，`provider_invocation_attempts` 表示每次真实尝试。Worker 使用进程内上下文把调用绑定到当前已领取 Job；工作流、Prompt Eval 和知识索引在任何网络调用前先通过独立事务提交 `started` 尝试与审计记录。若账本不能提交，调用失败关闭且不会发出 Provider 请求。

账本只保存稳定请求键、请求/响应 SHA-256 与字节数、受控状态、适配器报告的供应商请求 ID/来源、响应模型、Token 用量、异常类型和时间。它不保存提示词、正文、Embedding 输入、模型原始响应、API Key、Authorization、端点或异常正文。`GET /api/v1/jobs/{job_id}/provider-invocations` 只允许 reviewer/admin 访问并使用有界游标分页；任务页最多自动取 1000 条并明确提示截断。

状态解释：

- `started`：调用意图已经持久化；不证明请求已经到达供应商。Worker 异常或租约过期进入人工核对时，仍为 `started` 的尝试会转为 `outcome_unknown`。
- `succeeded`：当前进程收到并解析了可用响应，响应内容本身只以摘要留存。
- `outcome_unknown`：本地无法证明供应商是否处理，必须到供应商控制台核对。它作为历史证据保留，即使人工决定重试或放弃也不会被伪装成成功/失败。
- `late_succeeded`：人工核对已把尝试标记为不确定后，原调用又返回了可用响应；领域 Job 仍保持人工核对，不能因迟到回执自动提交旧执行者结果。

OpenAI-compatible 适配器会把 64 位稳定请求键作为 `Idempotency-Key` 发出，但不同供应商可能忽略、拒绝或只在有限窗口内支持该头。只有目标供应商的正式契约、受控 conformance、重复请求结果查询和计费核对共同通过后，才可以考虑把某类 Job 从 `manual_review` 升级为自动恢复。

Prometheus 的 `contentflow_provider_invocation_attempts{status=...}` 是累计历史状态快照；`contentflow_provider_invocation_unresolved_outcome_unknown` 与最老时长只统计 Job 仍处于 `manual_review` 的未解决尝试。`ContentFlowProviderInvocationOutcomeUnknown` 告警要求持续 5 分钟。告警恢复只表示核对队列已经处置，不表示供应商侧费用或结果已经自动对平。

## 发布安全重试

1. 只有 `PublishJob.response_json.dispatch_failure.retry_safe=true` 的失败才允许调用 `POST /api/v1/publishing/jobs/{publish_job_id}/retry`。当前安全阶段包括公众号鉴权、素材前置检查和本地素材读取；这些失败发生在任何平台写入前。
2. 鉴权失败会把渠道置为 `invalid`。先修复出口 IP 白名单、凭据或网络，并通过渠道测试恢复 `connected`；随后由 reviewer 在发布页点击“安全重试”。
3. 专用重试会锁定发布任务，重新验证内容仍为已审核的相同版本，拒绝正在运行的队列任务，归档旧失败证据，清除旧分发令牌并立即重新入队，同时记录 `publish.retry_safe` 审计。
4. 通用 `POST /jobs/{id}/retry` 明确拒绝安全发布失败与待对账发布，防止绕过渠道复测或副作用判断。
5. 旧版本已经记录为 `reconciliation_required` 的任务不会被追溯重分类。即使后来确认错误发生在 token 阶段，也应先按现有人工对账流程确认平台没有作品，再创建新任务。

## 发布自动对账与人工处置

公众号 `freepublish/submit` 返回 `publish_id` 只表示平台接收，不代表最终发布。ContentFlow 会把 PublishJob 保持为 submitted，并创建幂等的 `publish.reconcile:{publish_job_id}` 队列任务。

### 自动对账

```dotenv
CONTENTFLOW_PUBLISH_RECONCILIATION_INITIAL_DELAY_SECONDS=15
CONTENTFLOW_PUBLISH_RECONCILIATION_MAX_ATTEMPTS=20
CONTENTFLOW_PUBLISH_RECONCILIATION_SWEEP_POLL_SECONDS=60
CONTENTFLOW_PUBLISH_RECONCILIATION_SWEEP_BATCH_SIZE=100
```

1. 正常发布在落库 submitted 结果时即时创建对账 Job；首次查询默认延迟 15 秒，最多尝试 20 次。Worker 另以默认 60 秒/每批 100 条的恢复扫描补建历史或异常缺失任务。可分别通过 `CONTENTFLOW_PUBLISH_RECONCILIATION_INITIAL_DELAY_SECONDS`、`CONTENTFLOW_PUBLISH_RECONCILIATION_MAX_ATTEMPTS`、`CONTENTFLOW_PUBLISH_RECONCILIATION_SWEEP_POLL_SECONDS` 和 `CONTENTFLOW_PUBLISH_RECONCILIATION_SWEEP_BATCH_SIZE` 调整；恢复扫描只选择没有活动对账 Job 的记录，不会让旧活动任务占满批次。
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

1. 后端使用固定 digest 的 PostgreSQL/pgvector 和临时 MinIO 服务，从 `uv.lock` 重建全部依赖，执行 Ruff、全量 pytest、真实 PostgreSQL/MinIO 集成场景、75% 分支覆盖率和严格 `pip-audit`。
2. 前端使用 Node 22.13.0 和 `npm ci`，执行 ESLint、Sites/vinext 渲染测试、Next.js 生产构建和高危级别 npm 审计。
3. checkout、setup-uv 和 setup-node 均固定到完整提交 SHA；`.github/dependabot.yml` 每周检查 uv、npm、GitHub Actions 和 Docker 依赖。

本地等价验证：

```powershell
uv sync --all-extras --locked --python 3.12
uv lock --check
uv run --locked ruff check .
uv run --locked pytest -q --cov=contentflow --cov-branch --cov-fail-under=75
$env:PYTHONUTF8="1"
uv run --locked python scripts/supply_chain.py audit-python

Set-Location web
npm ci
npm run lint
npm test
npm run build
npm audit --audit-level=moderate
```

升级 Python 依赖后必须重新运行 `uv lock` 和漏洞审计；升级前端依赖后必须更新 `package-lock.json` 并从空 `node_modules` 执行 `npm ci`。不要未经依赖链审查直接运行 `npm audit fix --force`。工作流文件存在不等于远程门禁已经启用：仓库管理员仍需在 GitHub 受保护分支上要求后端和前端检查通过后才能合并。

CI 还会生成 CycloneDX、可复现源码归档和 SHA-256 清单；非 PR 运行在三条低权限门禁通过后发布 SLSA/CycloneDX 签名证明。下载后的离线验证与 `gh attestation verify` 命令见 [软件供应链证据](supply_chain.md)。分支保护除后端/前端外还应要求 `SBOM and reproducible source evidence`；证明 Job 只在非 PR 运行，不能设为 PR 必需检查。公网镜像工作流另生成 BuildKit OCI provenance/SBOM、Critical 漏洞报告和 digest Artifact，但目前仍没有独立镜像签名与部署时密码学验签，不能用源码证明代替。

## 受控公网测试运维

公网测试使用 `deploy/public-test/compose.yml`，不得直接把本地 `docker-compose.yml` 暴露到互联网。提交前运行：

```powershell
uv run --locked python scripts/validate_public_test_deployment.py
```

校验器会渲染 maintenance profile 并拒绝未固定 digest、服务器现场 build、非 Caddy 端口、MinIO、开放注册、Mock/Hash、HTTP 外部端点、通配 CORS、API/Worker 镜像不一致和缺少 release SHA。

公网首次管理员不通过临时开放注册创建。目标数据库迁移完成后，在服务器 TTY 使用 `contentflow-bootstrap-admin bootstrap-workspace` 和 `add-admin` 交互输入两个不同账户的密码；第一条只接受空数据库，第二条拒绝已有邮箱，两条都要求注册关闭并写审计。

R2 上线前使用 `contentflow-s3-conformance` 跑完整 256 KiB、9 MiB multipart 和 100 MiB 边界矩阵。探针只删除自己记录的随机前缀对象，不执行 bucket-wide list/delete。BGE 缓存通过 `contentflow-prepare-embedding-cache prepare` 下载固定 revision，再以 `verify` 强制 offline 载入和归一化探测。

PostgreSQL 公网备份使用 restic 客户端加密和独立 R2 Token；`backup.sh` 保留 7 日/4 周，`verify-backup.sh` 只恢复到随机 `contentflow_verify_*` 数据库。详细初始化、GitHub Environment/SSH、恢复和真实验收步骤见[公网测试部署手册](../deploy/public-test/README.md)与[备份策略](../deploy/public-test/backup-policy.md)。

### 租约耗尽与权限竞态补充

- publish.dispatch 已进入 publishing 后若最终租约耗尽，系统会把 PublishJob 置为 reconciliation_required。按“发布结果不确定的处置”核对，禁止直接重试。
- 管理员降级和移除在 PostgreSQL 中锁定 Workspace 行，保证最后管理员判断串行执行；如果管理操作长期等待，应检查该工作区上的未提交管理事务和数据库锁等待。
