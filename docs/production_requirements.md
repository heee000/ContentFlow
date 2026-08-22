# ContentFlow 生产化需求与验收清单

这份清单用于验证 ContentFlow 是否具备可部署、可配置、可审计的完整内容营销工作流。

## 1. 核心业务闭环

- [x] 营销活动：创建、编辑、归档 brief，支持产品、目标、人群、平台、语气、必含信息和禁用词。
- [x] RAG 知识库：上传文档、切块、向量化、检索、引用追踪、增量索引和工作区隔离。
- [x] 内容策划：根据 brief 与知识引用生成结构化主题、平台策略、素材方向和发布时间建议。
- [x] 多平台内容：小红书、抖音、公众号分别生成，不允许只改平台名称。
- [x] AI 排版与脚本：小红书卡片、抖音逐镜头脚本、公众号章节结构由模型生成，随版本持久化并进入素材/投放包。
- [x] 审核：规则审核、模型审核、人工审核、版本历史、驳回原因和重新生成。
- [x] 素材：图片生成、视频异步生成、文件上传、状态轮询、对象存储和素材元数据。
- [x] 分发：平台授权、人工确认、定时发布、幂等、重试和失败处理；平台可用的状态与指标接口按连接器能力接入。
- [x] 复盘：在工作台人工录入或通过连接器拉取曝光、点击、互动数据，计算指标并形成保守的下一轮建议。

## 2. 工程与安全

- [x] 用户注册/登录、工作区成员关系和 `viewer/editor/reviewer/admin` 角色权限。
- [x] 数据库会话、15 分钟 Access Token、旋转 Refresh Token、复用检测、单会话/全会话撤销、HttpOnly/SameSite Cookie、可信 Origin 校验与 CLI Bearer 兼容。
- [x] 登录账号/IP、注册 IP、刷新会话/IP 使用 PostgreSQL 共享限流；HMAC 键无明文标识，返回 429/Retry-After，生产环境不可关闭且默认不信任转发头。
- [x] PostgreSQL 默认数据库、仅用于显式隔离测试的 SQLite，以及 Alembic 迁移。
- [x] 数据库任务队列、Worker、租约、重试、幂等键和失败记录。
- [x] 外部平台凭据加密保存，日志中不得出现 API Key、access token 或 refresh token。
- [x] REST API、OpenAPI 文档、输入校验、统一错误返回和请求追踪 ID。
- [x] 审计日志覆盖注册/登录、内容修改、审批、生成、发布和连接器配置。
- [x] 健康检查、就绪检查、结构化日志和工作台运行摘要。
- [x] 受保护的 Prometheus 指标：默认关闭、生产强制开启并使用独立长 Token；HTTP/数据库指标使用固定低基数标签，不暴露租户或对象 ID。
- [x] 可选 observability profile：固定摘要 Prometheus/Grafana、Compose secrets/preflight、版本化抓取与 recording/alert rules、promtool 故障行为测试、只读 provisioned Dashboard；Prometheus 不映射宿主端口。
- [x] Docker Compose 一键启动 API、Worker、Web、PostgreSQL、pgvector 和 MinIO。
- [x] 生产启动 fail-fast：拒绝 SQLite、local 存储、通配 CORS、弱/复用密钥、未知或缺凭据 Provider，以及未显式许可的 Mock/Hash 模式。
- [x] 生产强制显式启用受治理 Prompt：内置基线未晋级时管理页显示阻断原因，生成请求在入队前返回 409，Worker 在首次模型调用前二次校验证据。
- [x] 凭据使用独立主密钥加密，支持历史密钥回退；审计递归脱敏 token/secret/password/API Key 变体。
- [x] local/S3 上传都有 100 MiB 默认上限；S3 使用有界临时文件流式上传，失败会清理本地临时文件；外部媒体内联/下载结果同样有界，并校验 HTTP(S)、URL 凭据、下载域名允许列表和重定向目标。
- [x] API/Web 安全响应头、API `no-store`、不泄露内部细节的统一 500；ready 同时检查数据库和对象存储。
- [x] Next 与 Sites 共用 CSP；生产 API Origin 固定，HTTPS 构建启用 HSTS/请求升级且禁止 `unsafe-eval`，本地 HTTP 构建保持可用。
- [x] Python/Node 已知漏洞审计、PostgreSQL+MinIO 备份与随机临时数据库恢复校验具有可执行命令。

## 3. 连接器

本节 `[x]` 表示适配器代码和 Mock/HTTP 契约已实现，不表示真实外部账号、付费模型或平台审核已验收。

- [x] 文本模型：Mock 与显式配置的 OpenAI-compatible 接口，不预设云厂商或模型。
- [x] AI 生成追溯：工作流保存 Provider/模型、Prompt 来源与发布版本、模板哈希、分阶段调用、摘要、时延和 Provider 返回的 Token 用量；失败证据可追踪且不复制原始 Prompt/正文，不虚构成本。
- [x] Prompt Registry：工作区不可变版本、创建者与审批者分离、拒绝、激活、回滚、租户隔离、激活/运行前哈希校验和脱敏审计。
- [x] Prompt Eval：版本化不可变确定性用例、创建/激活职责分离、异步运行、Prompt/Suite/目标 Provider/模型证据绑定、切换失效、审批/激活/回滚/运行时失败关闭与不保存模型正文。
- [x] 生产 Prompt 可用性：禁止以 builtin 来源长期生成，提供可见 readiness/block reason 和双管理员安全初始化顺序；API 入队前与 Worker 运行时均失败关闭。
- [x] Embedding：本地哈希向量、OpenAI 兼容 embedding 与 pgvector 检索。
- [x] 图片：中立 HTTP 生成契约，支持受限 base64 或下载 URL 并转存对象存储。
- [x] 视频：中立 HTTP 异步创建、轮询、超时、失败与结果转存。
- [x] Media Contract v1 live conformance runner：显式计费确认、HTTPS/目标校验、同键重放/冲突、版本/鉴权拒绝、视频轮询、响应上限、下载域名校验与脱敏独占报告；异步任务保存非敏感目标配置指纹并在配置漂移时失败关闭。
- [x] 抖音：OAuth 凭据校验、视频上传/创建和数据拉取；图片发布与审核回调不冒充已实现。
- [x] 公众号：封面素材、草稿和可选发布提交；可用能力由账号接口权限决定。
- [x] 脚本辅助发布：小红书/抖音/公众号可显式选择无平台凭据的本机 Playwright 辅助；任务包带 SHA-256、固定依赖、官方入口 allowlist 和人工最终提交门禁，结果人工回填。
- [x] 安全发布降级：仅无外部副作用的 scheduled/queued/failed/exported 可切脚本；publishing/submitted/reconciliation_required 必须先对账，自动指标拉取拒绝非 API 任务。
- [x] 小红书：在缺少公开发布资质时提供审核后的导出包，不伪造自动发布成功。

## 4. 运营工作台

- [x] 登录、注册、工作区创建/切换、成员增删改角色与最后管理员保护；PostgreSQL 管理操作锁定 Workspace 行，避免并发降级/移除留下零管理员。
- [x] 总览、活动创建/编辑/归档、知识库、可回看全部内容及版本的审核台、素材库、发布排期/取消、连接器、人工指标录入与数据复盘、任务队列，以及包含 Prompt Eval 套件/运行证据、Prompt 审批/发布/回滚的团队管理与审计。
- [x] Web 操作与 `viewer/editor/reviewer/admin` 权限保持一致，只读成员不会看到上传、重试或审批入口。
- [x] 清晰展示每一步状态、来源引用、失败原因、发布方式、脚本包下载/结果回填、重试与人工确认。
- [x] 响应式布局、键盘可操作、空状态、加载状态和错误状态。

## 5. 当前验收结论（2026-08-03）

### 仓库与本地交付证据

- [x] Docker Compose 已在 PostgreSQL、pgvector、MinIO、API、Worker、Web 真实容器栈中启动并通过健康检查；就绪探针同时验证数据库与对象存储。
- [x] `scripts/validate_stack.py` 已跑通“注册—多工作区/RBAC/审计—活动维护/归档—知识入库—RAG 生成/结构化排版—编辑版本—人工审核—素材—定时导出—投放包—指标—看板”。
- [x] 后端 104 项测试通过、7 项 PostgreSQL/MinIO 集成测试在本机跳过；其中 PostgreSQL/pgvector 集成测试 5 项、MinIO 集成测试 2 项待 CI 执行，分支覆盖率 81.11%，Ruff、锁文件一致性和 `pip-audit` 均通过。
- [x] 前端 lint、Next.js 生产构建、vinext/Sites 测试通过，`npm audit` 为 0 项漏洞。
- [x] PostgreSQL 并发验收通过：8 个并发连接器测试请求只创建 1 个 Job；两个 Worker 竞争过期租约时只有 1 个执行最终失败处理。
- [x] 迁移前回滚包已按历史门槛恢复验证：Alembic `8b6c1f3a9d21`、17 张表和 39 个对象；临时资源均已清理。
- [x] 2026-08-09 已完成当前 head `c9e7b4a2d610` 的 manifest v2 联合备份与隔离恢复：18 张表、39 个对象、165208 字节逐项哈希复验通过；临时数据库、bucket 和目录残留均为 0。
- [x] README、架构、系统使用手册、部署运维、连接器、能力说明与验收文档齐全。

### 2026-08-08 可靠性增量验收

- [x] Worker 运行期间周期性续租，续租与最终落库同时校验 worker_id 和 attempt；真实后台心跳、错误所有者续租失败、旧 Worker 拒绝完成和最终租约失联均有回归测试；发布中最终租约耗尽会进入待人工对账并阻断通用重试。
- [x] Worker 响应 SIGTERM/SIGINT 后停止领取新任务、完成在途任务并退出；Compose 默认提供 10 分钟停机宽限期，真实 Docker SIGTERM 以 ExitCode=0、signal=15 验证。
- [x] Worker 节点按独立数据库会话周期上报心跳并在退出时标记 stopped；管理员健康接口汇总活跃/失联容量、按工作区隔离队列并识别最长就绪等待，真实 PostgreSQL 验证 18 张表、4 个 Worker 索引和最终 stopped 状态。
- [x] 内容编辑与审核强制 expected_version，PostgreSQL 行锁串行化竞争请求，旧版本返回 409；已用随机临时 PostgreSQL schema 验证等待与冲突结果。
- [x] 发布调用前持久化 dispatch_token 和 publishing；确定提交结果与后续对账 Job 先于队列完成落库；不确定结果禁止自动重复分发，并提供 reviewer 人工处置和审计。
- [x] PostgreSQL 最后管理员并发竞态验证通过：两个降级事务一个提交、一个 409，最终保留一名管理员；随机临时 schema 已删除。
- [x] 公众号发布提交后基于 `publish_id` 自动排队查询；只有返回 `article_id` 才进入 published，pending 自动退避重试，耗尽后转人工；人工可接管 submitted，对账期间不持有远程调用行锁，迟到结果不会覆盖人工决策。
- [ ] 抖音无 `item_id` 的不确定结果仍只能人工核对；平台级幂等键、Outbox、回调验签/去重和真实租户签收仍属于最终生产发布门禁。
- [x] 渠道创建入口与连接器运行时要求一致：抖音必须提供非空 `access_token/open_id`（`open_id` 可位于凭据或配置），公众号拒绝空白 App ID/Secret，避免保存后才异步失败。
- [x] 后端 73 项测试、全量 Ruff、78.42% 分支覆盖率、前端双构建和 TypeScript 检查通过；真实 PostgreSQL 已验证对账扫描 `SKIP LOCKED`、单任务幂等入队和人工竞态。
- [x] S3 新对象保存完整 SHA-256 元数据并在读取时验证长度与内容；真实 MinIO 已覆盖上传/读取、bucket 隔离、大小限制、篡改检测、旧对象兼容和随机 bucket 清理。
- [x] 已加入 `uv.lock`、GitHub Actions 后端/前端门禁和 Dependabot；Action 使用完整提交 SHA，CI pgvector 使用镜像 digest，Python 与 npm 严格审计均为 0 个已知漏洞。
- [x] 当前 Prompt 治理 head 已由 [ContentFlow CI #31359992207](https://github.com/heee000/ContentFlow/actions/runs/31359992207) 完成后端 PostgreSQL/MinIO/安全与前端构建/安全签收。
- [x] Prompt Eval head c95f1e4a8d73 已由 [ContentFlow CI #31362922394](https://github.com/heee000/ContentFlow/actions/runs/31362922394) 完成真实 PostgreSQL/pgvector、MinIO、覆盖率、双端构建与依赖安全签收。
- [x] 生产受治理 Prompt 提交 `47fe3444d9a4a2f7c2c8a284c4e6b0b95fcad4c2` 已由 [ContentFlow CI #31364881430](https://github.com/heee000/ContentFlow/actions/runs/31364881430) 完成真实 PostgreSQL/pgvector、MinIO、覆盖率、双端构建与依赖安全签收。
- [x] 受保护 Prometheus 指标提交 `fe3ee101799e36dc05e644f51efbca8204cc7b02` 已由 [ContentFlow CI #31367481260](https://github.com/heee000/ContentFlow/actions/runs/31367481260) 签收鉴权、低基数标签、异常安全返回、生产 fail-fast、真实 PostgreSQL Collector、MinIO、覆盖率和双端构建/安全门禁。
- [x] Prometheus/Grafana 交付资产已通过本地 YAML/JSON/Compose 契约与同版本 promtool 验证；提交 `c9d73101e7318da5fed5e496ad9a78eb7fb09832` 的 [ContentFlow CI #31374854714](https://github.com/heee000/ContentFlow/actions/runs/31374854714) 已完成固定 promtool 配置/规则/故障行为、真实 PostgreSQL/MinIO 和双端安全签收。
- [x] 2026-08-13 已实现 Python/前端 CycloneDX、可复现源码归档、SHA-256 清单、离线失败关闭校验，以及只在非 PR 运行的 SLSA/CycloneDX GitHub Artifact Attestations；本地真实材料为 144 个跟踪文件、76 个 Python 组件、620 个前端组件，两次源码归档 SHA-256 一致。提交 `38ad07c64d60f19330b4f4b42aebcdd328a4cd63` 已由 [ContentFlow CI #31691997756](https://github.com/heee000/ContentFlow/actions/runs/31691997756) 签收四个 Job；Artifact `9177772957` 摘要为 `sha256:5dad8fa59cab27e89b7a127dd718270f68faab19bea27b9a988d26ac8fbd481b`，SLSA/Python/前端三份证明已在同一证明 Job 中发布并验证。
- [ ] 受保护分支必需检查、浏览器 E2E、双 Worker/SIGKILL/数据库闪断、PITR/异地恢复、OCI 镜像扫描/签名、独立迁移与灰度回滚仍待补齐。
- [ ] 2026-08-08 未重新跑完整 Compose 栈：Docker Hub 拉取 python:3.12-slim 时网络失败；PostgreSQL 16/pgvector 单服务的 SKIP LOCKED、内容冲突和取消/分发竞态验证已通过，但不能替代完整栈验收。

### 2026-08-09 认证会话增量验收

- [x] Alembic 迁移 `f4c2d8e7a190` 新增认证会话与刷新历史；SQLite 从空库、未版本化旧 head 与未版本化 `c9e7b4a2d610` 均可安全升级。
- [x] 浏览器不再把 Bearer Token 写入 localStorage；旧版令牌会被清除，401 只触发一次共享刷新请求并重放原请求。
- [x] 认证/安全/迁移专项 34 passed；最新全量回归 73 passed、6 skipped；Ruff、前端 lint、Sites/vinext 测试和 Next.js 生产构建通过。
- [ ] 持久 PostgreSQL 仍停留在上一 head `c9e7b4a2d610`；Docker 引擎恢复后必须先使用现有回滚点，经 `f4c2d8e7a190 -> a73f9c2e4b61` 迁移到 `c95f1e4a8d73`，并重跑 24 表 PostgreSQL+MinIO 联合恢复。
- [ ] OIDC/SAML、MFA、设备会话管理、nonce/strict-dynamic CSP、网关级全业务限流与生产 TLS/WAF 仍是最终企业签收门禁。

### 最终生产发布门禁

- [x] 仓库已发布供应商中立的 Media Contract v1 OpenAPI、版本头、稳定生成幂等键、请求参数白名单、永久/暂时错误分类、有界 `Retry-After` 和 Worker 终态回归测试；实现提交 `58238f3fc694da4ab884ed3d0c158b9e49bc593e` 已由 [ContentFlow CI #31390831127](https://github.com/heee000/ContentFlow/actions/runs/31390831127) 完成真实 PostgreSQL/pgvector、MinIO、Linux、覆盖率、双构建和双端安全审计签收。
- [x] 2026-08-12 正式媒体运行时已与 v1/runner 对齐：响应有界、封闭信封、状态/来源/标识互斥、跨平台文件名、精确下载 allowlist、逐跳重定向、配置漂移和脱敏错误均有本地回归；live 报告升级为运行级 HMAC 指纹与完整 JSON 转义秘密扫描，幂等键拒绝首尾空白。全量 177 passed、7 skipped、130 subtests，分支覆盖率 82.13%，Ruff、锁文件、pip/npm 审计、前端双构建、Compose 与 15 个 YAML/JSON 解析通过。实现提交 `8a79658` 与证据提交 `285de6a` 已普通快进同步到 `main`；[ContentFlow CI #31560723174](https://github.com/heee000/ContentFlow/actions/runs/31560723174) 为 success，后端真实 PostgreSQL/pgvector/MinIO 与前端双 Job 已远程签收。
- [ ] 使用目标环境的真实模型与媒体 Provider 完成调用和 v1 conformance matrix，记录请求模式、错误、时延、令牌/成本、同键不重复计费，以及视频任务轮询、下载和过期行为。
- [x] 受控脚本发布要求至少一项规范化截图或平台 JSON 证据；任务包、脚本尝试和证据 manifest 以 SHA-256 绑定，下载重新验真。脚本渠道可选不同 reviewer 的双人一致确认，首次确认后冻结证据，冲突失败关闭。
- [x] `c290f64` 为脚本尝试增加发起/确认分离、15 分钟至 30 天 TTL、过期重建和对象事务补偿；[CI #32568712614](https://github.com/heee000/ContentFlow/actions/runs/32568712614) 的 PostgreSQL/pgvector、MinIO、Linux 前端、安全审计和供应链四个 Job 全部成功。
- [x] 脚本发起人与确认人分离；任务包默认 24 小时有效，过期后运行器、下载、证据上传和确认失败关闭，并可显式重建新尝试。对象写入后数据库失败会执行同步补偿删除。
- [ ] 历史脚本尝试的证据行会保留，但当前运营 API/UI 只聚焦当前尝试；仍需历史尝试归档视图、不可变证据存储、可信时间戳和 legal hold。
- [ ] 当前发布证据仍需补平台签名/官方查询交叉核验、可信时间戳与 WORM/Object Lock、恶意扫描/DLP、保留与 legal hold；双人策略仍需企业职责分离、step-up MFA 和确认 SLA。
- [ ] live runner 当前尚未取得目标媒体 Base、API Key、模型名和下载域名，因此没有执行真实媒体生成；自动探针也不能替代限流/超时/审核/下载过期测试钩子、账单核对和人工质量签收。
- [x] 在用户授权和 `auto_publish=false` 前提下完成微信公众号真实鉴权、素材/草稿计数与一份“不发布”草稿验收；未调用公开发布提交接口。
- [ ] 完成真实抖音 OAuth 发布/指标回收，以及微信公众号公开发布、最终 `article_id` 对账和异常矩阵验收。
- [ ] 部署真实 TLS 网关/WAF、全业务/租户配额限流，以及集中式密钥管理与轮换流程。
- [ ] 完成多节点压力/耐久测试、数据库连接池容量、任务积压、告警与故障演练，并确认 SLO。
- [ ] 当前已有版本化确定性输出契约 Eval 与自动晋级门禁；仍需建立 RAG/生成语义金标集，把召回、事实性、安全、提示注入、PII/版权、统计稳定性和真实 Token/成本阈值纳入门禁。

当前代码已达到可部署、可演示、可持续开发、可备份恢复的仓库级交付基线；在上述外部生产证据补齐前，不应将其表述为已完成最终生产签收。
