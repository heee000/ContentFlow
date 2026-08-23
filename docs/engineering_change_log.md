# ContentFlow 工程变更台账

本台账记录每个阶段解决了什么问题、为什么要改、如何解决、怎样验证，以及还没有关闭的边界。它不替代 Git 历史、交接文档或企业成熟度复审，而是三者之间的可追溯索引。

## 1. 记录规则

每次阶段性改动必须至少记录：变更编号、状态、问题与影响、根因、解决方案、涉及文件、验证证据、剩余边界、提交与 CI。仓库测试通过只能证明仓库侧实现；真实平台、真实 Provider、账单、质量、SLO 和组织流程必须有目标环境证据才能标记为已签收。

状态只使用：“进行中”“本地已验证”“已提交”“CI 已签收”“外部待签收”“已关闭”。未知或用户私有文件不得因为记录工作被读取、修改或暂存。

## 2. 历史阶段索引

| 阶段 | 主要问题 | 解决结果 | 提交与证据 | 详细记录 |
| --- | --- | --- | --- | --- |
| 初始产品化 | 原型缺少可部署业务主链、持久任务和运营工作台 | 建立 FastAPI、SQLAlchemy、Worker、RAG、审核、素材和发布主链 | 9411bed | CONTENTFLOW_HANDOFF.md 1-20 |
| 运行时与数据库加固 | 本地运行、迁移兼容和数据库升级边界不足 | 加固运行时、迁移与 PostgreSQL 路径 | edc523a | CONTENTFLOW_HANDOFF.md 21.1-21.7 |
| 队列、并发、发布、灾备和会话产品化 | Worker 租约、并发编辑、外部发布不确定性、恢复与浏览器会话存在生产风险 | 增加租约心跳、乐观并发、发布对账、真实 PostgreSQL/MinIO 门禁、联合恢复、安全 Cookie/CSP/共享限流及平台验收入口 | b24e47f、29563c4 | CONTENTFLOW_HANDOFF.md 21.8-21.16；企业复审 11-22 |
| Prompt 治理 | Prompt 可被单人或运行时绕过，缺少版本、审批和质量晋级证据 | 增加不可变版本、双人审批、Eval 套件、生产强制门禁和 CI 证据 | beaeaf1…27ee098 | CONTENTFLOW_HANDOFF.md 21.17-21.19；企业复审 23-25 |
| 可观测性 | 缺少受保护指标、版本化规则、看板和可重复验证 | 增加 Prometheus 指标、固定制品监控栈、规则、Grafana 看板和 CI 行为测试 | fe3ee10…67e3206 | CONTENTFLOW_HANDOFF.md 21.20-21.21；企业复审 26-27 |
| 供应商中立化 | 设计和配置隐含单一云厂商或模型假设 | 改为显式 OpenAI-compatible 文本/Embedding 与 ContentFlow 中立媒体契约，移除定向分支 | c9d7310、1fcc371 | CONTENTFLOW_HANDOFF.md 21.22；企业复审 28 |
| Media Contract v1 | 媒体副作用缺少版本、幂等、错误分类和机器契约 | 发布 OpenAPI v1，增加稳定幂等键、数据最小化、永久/暂时错误分类和 Worker 回归 | 58238f3、bceff28；CI 31390831127、31391101343 | CONTENTFLOW_HANDOFF.md 21.23；企业复审 29 |

## 3. 2026-08-12：Live Media Conformance 与运行时信任边界

本阶段状态：“CI 已签收，外部目标服务待签收”。实现提交 `8a79658952ebac63ed866c24b57940e3286c023b` 与证据提交 `285de6a32de15124d1f7a59b771b6972b086bce9` 已普通快进同步到 `main`；[ContentFlow CI #31560723174](https://github.com/heee000/ContentFlow/actions/runs/31560723174) 对后者完成签收，Backend 与 Frontend 两个 Job 均为 success。未知文件 knowledge/北京周末 CityWalk 路线助手产品资料.txt 不在本阶段范围，未读取、未修改、未暂存。

### CF-20260812-01：缺少可受控的真实媒体契约验收入口

- 状态：外部待签收；仓库实现已由提交 `8a79658` 推送并由 [ContentFlow CI #31560723174](https://github.com/heee000/ContentFlow/actions/runs/31560723174) 签收，真实目标服务仍未验收。
- 问题与影响：仓库虽有 Media Contract v1，但无法自动证明目标服务对重放、冲突、版本、鉴权和异步轮询的行为；直接人工调用还可能误触发计费。
- 根因：只有运行时适配器和单元测试，没有把外部契约探针、计费确认、证据生成和秘密保护组合成正式命令。
- 解决方案：新增供应商中立的 contentflow-media-conformance。没有 --confirm-live-generation 时在读取环境和联网前拒绝；按素材类型执行 create、同键重放、同键异请求冲突、旧版本、无鉴权和视频轮询；响应流式有界读取并验证版本、Content-Type 与封闭信封。
- 涉及文件：contentflow/media_conformance.py、tests/test_media_conformance.py、pyproject.toml、README.md、docs/media_provider_contract.md、docs/operations.md、docs/external_acceptance.md、docs/production_requirements.md。
- 验证：专项测试覆盖正常、冲突、版本、鉴权、轮询、超限、非有限超时和错误状态载荷；真实目标服务配置尚未提供，因此状态为“外部待签收”。
- 剩余边界：不能主动制造全部 408/425/429/5xx、审核拒绝、下载过期或重复计费；需要目标服务故障控制面、账单和人工质量证据。

### CF-20260812-02：验收报告可能泄露请求材料或覆盖既有证据

- 状态：CI 已签收；实现提交 `8a79658`，签收运行 [ContentFlow CI #31560723174](https://github.com/heee000/ContentFlow/actions/runs/31560723174)。
- 问题与影响：若报告包含请求 ID、Prompt、幂等键、URL、模型或远端响应，可能泄露业务和凭据；若运行后才创建文件，可能覆盖或混淆既有证据。
- 根因：外部验收缺少独占预留、最小化证据 Schema、序列化前秘密扫描和公私运行标识隔离。
- 解决方案：初版在联网前以独占模式预留新报告，只保存状态、耗时、次数和截断 SHA-256 指纹；序列化前扫描 API Key、端点、模型、Prompt、幂等键、任务 ID、URL 和媒体内容；用独立的公开 run_id 与仅存内存的 request_nonce，防止从报告重建请求材料；落盘后 flush/fsync，并尽力限制文件权限。普通截断哈希与扫描覆盖不足随后由 CF-20260812-21 继续加固。
- 涉及文件：contentflow/media_conformance.py、tests/test_media_conformance.py。
- 验证：测试固定公私随机值，证明请求只含私有 nonce、报告只含公开 ID，且报告不含秘密、Prompt、模型、URL、任务 ID 或幂等键；已有文件拒绝覆盖。
- 剩余边界：本地 JSON 不是企业不可篡改证据，仍需签名、集中 WORM 或对象锁、保留和访问审计。

### CF-20260812-03：外部端点配置可扩大 SSRF 和凭据暴露面

- 状态：CI 已签收；实现提交 `8a79658`，签收运行 [ContentFlow CI #31560723174](https://github.com/heee000/ContentFlow/actions/runs/31560723174)。
- 问题与影响：带凭据、query、fragment、生产 HTTP 或含路径/端口的“域名”配置可能把秘密发送到错误目标，或允许不受控下载。
- 根因：此前生产启动只检查配置是否存在，没有统一解析和约束外部模型/媒体 URL 与精确下载主机名。
- 解决方案：所有环境拒绝 URL 凭据、query、fragment；生产只允许 HTTPS；媒体下载 allowlist 仅接受无 scheme、路径、端口或凭据的精确主机名；开发仍可显式使用隔离的本地 HTTP 服务。
- 涉及文件：contentflow/settings.py、tests/test_security.py、docs/operations.md。
- 验证：覆盖生产 HTTP、URL 凭据/query/fragment、畸形 URL、畸形 allowlist 主机和开发本地 HTTP 例外。
- 剩余边界：应用校验不能替代 DNS 固定、出口防火墙、私网策略、mTLS 和工作负载身份。

### CF-20260812-04：下载重定向在发出下一跳请求后才校验目标

- 状态：CI 已签收；实现提交 `8a79658`，签收运行 [ContentFlow CI #31560723174](https://github.com/heee000/ContentFlow/actions/runs/31560723174)。
- 问题与影响：自动跟随重定向后再检查最终 URL，恶意首跳可能先让客户端访问内网或未授权主机，形成 SSRF。
- 根因：下载器依赖 HTTP 客户端自动重定向，校验发生得太晚。
- 解决方案：关闭自动重定向；每一跳先解析相对 Location，再验证 scheme、凭据、目标主机和生产 HTTPS，验证通过后才发出下一请求；重定向次数有界，缺少 Location 或超过上限失败关闭。
- 涉及文件：contentflow/media_providers.py、contentflow/worker.py、tests/test_media_providers.py。
- 验证：测试证明未授权的重定向目标从未收到请求；允许的相对重定向可下载；生产 HTTP 在请求前被拒绝。
- 剩余边界：仍需基础设施级 DNS/出口策略抵御解析重绑定与网络层绕行。

### CF-20260812-05：异步媒体任务可能在配置切换后轮询错误服务

- 状态：CI 已签收；实现提交 `8a79658`，签收运行 [ContentFlow CI #31560723174](https://github.com/heee000/ContentFlow/actions/runs/31560723174)。
- 问题与影响：视频创建后若 Provider/Base/模型发生切换，旧任务 ID 可能被发送到新服务，导致数据泄露、错误结果或永久悬挂。
- 根因：任务只保存远端 ID，没有保存创建目标的非敏感身份并在轮询前比较。
- 解决方案：创建时保存由契约版本、素材类型、Provider、Base 和模型计算的 SHA-256 配置指纹；轮询前与当前配置比较，缺失或不一致时永久失败并转人工处理，不发出网络请求。
- 涉及文件：contentflow/media_providers.py、contentflow/worker.py、tests/test_media_contract_v1.py、docs/operations.md。
- 验证：测试证明指纹稳定、不泄露配置明文、配置变化会改变指纹，且漂移在调用 Provider 前被拒绝。
- 剩余边界：配置指纹不是配置签名或审批；仍需 Secret Manager、版本化配置、变更审批和排空在途任务的组织流程。

### CF-20260812-06：视频状态载荷和错误码定义不够封闭

- 状态：CI 已签收；实现提交 `8a79658`，签收运行 [ContentFlow CI #31560723174](https://github.com/heee000/ContentFlow/actions/runs/31560723174)。
- 问题与影响：活动状态携带 URL 或错误、成功同时携带错误、失败没有稳定错误详情等歧义会让不同适配器产生不一致状态，错误码不明确也妨碍自动验收。
- 根因：OpenAPI 对状态分支和错误响应的互斥约束不完整。
- 解决方案：OpenAPI 明确常见 400/401/403/404/409/429/500 响应、至少 24 小时幂等保留、保留错误码、failed/cancelled/expired 终态和共享 ErrorDetail；活动、成功、失败三类载荷互斥，成功恰有一个下载地址，失败必须有稳定错误详情。
- 涉及文件：docs/contracts/contentflow-media-v1.openapi.yml、tests/test_media_contract_v1.py、tests/test_media_conformance.py、docs/media_provider_contract.md。
- 验证：OpenAPI 严格解析与状态分支断言通过；conformance runner 对互斥违规失败关闭。
- 剩余边界：v1 尚无 capability discovery、取消、续期、签名 Webhook/Inbox、重放窗口和弃用政策。

### CF-20260812-07：非有限超时和畸形 URL 可绕过正常边界或产生不稳定错误

- 状态：CI 已签收；实现提交 `8a79658`，签收运行 [ContentFlow CI #31560723174](https://github.com/heee000/ContentFlow/actions/runs/31560723174)。
- 问题与影响：NaN 或无穷大等浮点值可能通过简单大小比较，导致轮询永不结束或 HTTP 超时行为异常；畸形 URL 可能冒出底层异常而不是稳定配置错误。
- 根因：只做上下界比较，没有显式 isfinite；URL 解析异常没有统一归一化。
- 解决方案：轮询超时、轮询间隔和请求超时均要求有限值并满足范围；捕获解析异常并转换为不含敏感输入的稳定失败。
- 涉及文件：contentflow/media_conformance.py、contentflow/settings.py、tests/test_media_conformance.py、tests/test_security.py。
- 验证：覆盖 NaN、正负无穷、畸形 IPv6 或主机和在创建报告或联网前拒绝的 CLI 路径。
- 剩余边界：目标服务自身超时、队列悬挂和超时后成功仍需故障注入及幂等查询证据。

### CF-20260812-08：正式媒体适配器无界读取且未执行完整响应契约

- 状态：CI 已签收；实现提交 `8a79658`，签收运行 [ContentFlow CI #31560723174](https://github.com/heee000/ContentFlow/actions/runs/31560723174)。
- 问题与影响：正式 HTTP 适配器把响应整体载入内存后才检查内联素材大小，错误状态先按 HTTP 分类而不验证版本和标准错误信封；超大或恶意响应可造成内存压力，非遵约 429/5xx 仍会被反复重试。
- 根因：运行时实现早于 OpenAPI v1 和 conformance runner，仍使用便捷的 post/get/response.json 路径，只在成功响应上做部分协议校验。
- 解决方案：请求改为关闭自动重定向的流式 send；错误体限制 64 KiB，成功 JSON 默认硬限制 32 MiB 且不超过内联素材派生上限；所有状态先验证版本、JSON Content-Type 和顶层对象。错误响应必须是封闭 ErrorResponse，retryable 必须与 HTTP 状态一致；成功图片/视频按 OpenAPI 封闭字段、来源互斥和状态载荷解析。
- 涉及文件：contentflow/media_providers.py、contentflow/settings.py、tests/test_media_contract_v1.py、tests/test_media_providers.py、tests/test_security.py、.env.example、docker-compose.yml、docs/media_provider_contract.md、docs/operations.md、README.md。
- 验证：覆盖标准 400/429、retryable 冲突、缺版本、非 JSON、非对象、超限 Content-Length、多来源图片、失败视频详情、大小写错误状态和 32 MiB 独立硬上限；媒体/安全联合专项当前 69 项通过。
- 剩余边界：运行时 Schema 校验为手写关键约束，不替代服务端 OpenAPI Schema fuzz；32 MiB 以上素材必须走下载 URL，并依赖目标服务遵守下载生命周期和内容安全要求。

### CF-20260812-09：HTTP 客户端生命周期与网络异常可能泄露目标信息

- 状态：CI 已签收；实现提交 `8a79658`，签收运行 [ContentFlow CI #31560723174](https://github.com/heee000/ContentFlow/actions/runs/31560723174)。
- 问题与影响：每个正式 Provider/下载任务创建的 httpx Client 未显式关闭，会积累连接和文件描述符；默认网络异常和 raise_for_status 可能把完整签名 URL、查询参数或远端响应带入 Worker 持久错误。
- 根因：客户端所有权没有区分自建与注入，网络/流式读取/下载错误没有统一转换为脱敏 MediaProviderError。
- 解决方案：自建 Provider 客户端按请求使用上下文关闭，自建下载客户端在 finally 关闭，注入客户端仍由调用方管理；连接、读取和下载异常使用固定脱敏消息，超时/网络/408/425/429/5xx 标为可重试，其他 HTTP 错误永久失败，Retry-After 仍限制为 300 秒，原始 URL/响应体不进入异常。
- 涉及文件：contentflow/media_providers.py、tests/test_media_contract_v1.py、tests/test_media_providers.py。
- 验证：测试证明自建客户端关闭、注入客户端保持可用，连接超时/下载超时不泄露签名 query，429 不复制响应体且保留有界重试元数据。
- 剩余边界：连接复用目前只发生在显式注入场景；如未来改成长生命周期池，必须同时设计进程退出关闭、连接上限、DNS 更新和 mTLS 轮换。

### CF-20260812-10：关键下载与 Provider 约束只依赖启动校验

- 状态：CI 已签收；实现提交 `8a79658`，签收运行 [ContentFlow CI #31560723174](https://github.com/heee000/ContentFlow/actions/runs/31560723174)。
- 问题与影响：直接构造 HTTPMediaProvider 可绕过生产 Base 安全校验；下载器在 allowlist 为空时默认放行，生产非默认 HTTPS 端口也未限制；宽松接受大小写状态会掩盖服务端协议漂移。
- 根因：安全约束集中在 Settings.validate_runtime，底层高风险函数没有纵深自校验；OpenAPI 枚举在运行时被 lower 归一化。
- 解决方案：HTTPMediaProvider 构造时再次拒绝凭据、query、fragment 和生产 HTTP；URL 下载要求非空精确 allowlist，生产只允许默认 HTTPS 端口，且每次重定向仍先校验后请求；状态值按 OpenAPI 大小写精确匹配。
- 涉及文件：contentflow/media_providers.py、tests/test_media_providers.py、README.md、docs/operations.md。
- 验证：直接构造不安全 Base、空 allowlist、生产 8443 下载和 Processing 状态均在网络前失败；允许的开发 HTTP、相对重定向和注入客户端行为保持通过。
- 剩余边界：应用纵深校验仍不能替代 DNS pinning、出口 ACL、代理隔离和工作负载身份；目标服务如需非默认生产端口，应先扩展为显式受控端口 allowlist，而不是恢复任意端口。

### CF-20260812-11：请求参数与响应标签边界不完整

- 状态：CI 已签收；实现提交 `8a79658`，签收运行 [ContentFlow CI #31560723174](https://github.com/heee000/ContentFlow/actions/runs/31560723174)。
- 问题与影响：shots 原先允许任意 JSON，模型名、API Key、request/task ID、MIME 和 filename 只做部分非空/长度检查；超大嵌套分镜、控制字符、请求头异常或路径型 filename 可能进入外部请求、持久化元数据或存储路径。
- 根因：OpenAPI 的 shots.items 留空，正式适配器与 conformance runner 没有复用同一文本/文件名规则，底层 httpx/Path 被动承担了输入校验。
- 解决方案：Media Contract v1 新增封闭 Shot：每项只能是 1–5000 字符文本，或只含 time/visual/voiceover/subtitle 的非空对象；最多 100 项且规范化 JSON 不超过 256 KiB。API Key 限 1–4096 可打印 ASCII，模型名限 1–200 无控制字符；request/task ID、错误码/消息、MIME、filename 均增加控制字符、格式与长度校验。正式适配器和 live runner 同步执行。
- 涉及文件：docs/contracts/contentflow-media-v1.openapi.yml、contentflow/media_providers.py、contentflow/media_conformance.py、tests/test_media_contract_v1.py、tests/test_media_conformance.py。
- 验证：两种真实业务 Shot 形态通过；未知字段、空/超长/超总量 Shot、异常 Key/模型、控制字符 ID/MIME/filename、双任务 ID 冲突和 null 禁止字段均在网络或持久化前失败。
- 剩余边界：Shot v1 只覆盖当前产品已有字段；未来扩展镜头语言必须版本化向后兼容，不应重新开放任意对象。

### CF-20260812-12：对象存储文件名缺少跨平台可移植安全规则

- 状态：CI 已签收；实现提交 `8a79658`，签收运行 [ContentFlow CI #31560723174](https://github.com/heee000/ContentFlow/actions/runs/31560723174)。
- 问题与影响：公共 safe_filename 只取 basename 并删除 NUL，在 Windows 仍允许备用数据流冒号、设备名、尾随点/空格和其他非法字符；同一对象名在本地 Windows、Linux 与 S3 行为不一致。
- 根因：路径穿越防护只关注目录边界，没有把文件系统保留名、控制字符和跨平台对象键可移植性纳入统一入口。
- 解决方案：新增无依赖 filenames.py；保留 ../name 到 basename 的历史兼容，但拒绝非字符串、空/点名、超过 255 字符、控制字符、Windows 非法字符、尾随点/空格以及 CON/PRN/AUX/NUL/CLOCK$/COM1-9/LPT1-9。Local/S3 存储、正式 Provider 和 live runner 共用同一规则。
- 涉及文件：contentflow/filenames.py、contentflow/object_storage.py、contentflow/media_providers.py、contentflow/media_conformance.py、tests/test_object_storage.py、tests/test_media_contract_v1.py、tests/test_media_conformance.py。
- 验证：中文、空格和 ../evidence.txt basename 兼容通过；ADS、设备名、控制字符、尾随点空格、路径型/保留 Provider filename 均拒绝。
- 剩余边界：这是刻意的跨平台兼容性收紧；若历史外部对象已有不合法名称，读取路径不受影响，但重新写入前需要迁移为安全名。

### CF-20260812-13：下载本地安全错误会被 Worker 当作可重试异常

- 状态：CI 已签收；实现提交 `8a79658`，签收运行 [ContentFlow CI #31560723174](https://github.com/heee000/ContentFlow/actions/runs/31560723174)。
- 问题与影响：空/越权 allowlist、非法 URL、重定向越界、大小超限等 ValueError 会走 Worker 通用重试，重复执行确定失败或危险下载，并可能持久化底层错误详情。
- 根因：下载函数同时承担本地策略和网络调用，但 Worker 只识别 MediaProviderError 的 retryable 语义。
- 解决方案：store_generation 捕获本地 ValueError 并转换为固定脱敏、不可重试 MediaProviderError；网络超时/408/425/429/5xx 仍保留可重试分类和有界 Retry-After。
- 涉及文件：contentflow/worker.py、tests/test_media_contract_v1.py。
- 验证：空 allowlist 的签名 URL 在请求前失败，异常不包含 URL/query，retryable=false；既有 Worker 永久错误首尝试终止和暂时错误退避测试继续通过。
- 剩余边界：目标服务返回成功但恶意媒体内容仍需 MIME sniff、解码沙箱、病毒扫描和内容审核，不能只依靠 URL/大小策略。

### CF-20260812-14：精确下载主机名、Retry-After 与响应标识存在边缘歧义

- 状态：CI 已签收；实现提交 `8a79658`，签收运行 [ContentFlow CI #31560723174](https://github.com/heee000/ContentFlow/actions/runs/31560723174)。
- 问题与影响：原 exact host 检查可能接受通配/下划线/空标签/尾点等非规范主机；超长数字 Retry-After 可能触发 Python 大整数限制；成功响应同时带 request_id/requestId 会造成审计身份歧义。
- 根因：Settings、下载器和 runner 各自实现宽松 hostname 逻辑；Retry-After 直接 int；OpenAPI 未声明双命名互斥。
- 解决方案：新增无依赖 network_validation.py，共享规范 DNS/IPv4/IPv6 exact host 规则，拒绝 URL 片段、通配和畸形标签；allowlist 任一无效项整体失败、合法重复项去重。Retry-After 只接受 ASCII 数字，超过 10 位直接按 300 秒封顶。两个成功响应 Schema、正式适配器和 runner 都拒绝同时存在两种 request ID 命名。
- 涉及文件：contentflow/network_validation.py、contentflow/settings.py、contentflow/media_providers.py、contentflow/media_conformance.py、docs/contracts/contentflow-media-v1.openapi.yml、tests/test_security.py、tests/test_media_providers.py、tests/test_media_contract_v1.py、tests/test_media_conformance.py。
- 验证：合法 DNS/IPv6 通过；通配、下划线、首尾连字符、空标签、尾点、混合无效 allowlist 均在网络前拒绝；5000 位 Retry-After 稳定封顶 300；双 request ID 永久失败。
- 剩余边界：exact host 不等于固定解析结果，仍需出口代理/DNS 策略防重绑定；Retry-After 仅支持整数秒，不支持 HTTP-date，和当前 v1 契约一致。

### CF-20260812-15：非典型输入可能冒出底层异常，协议失败不稳定

- 状态：CI 已签收；实现提交 `8a79658`，签收运行 [ContentFlow CI #31560723174](https://github.com/heee000/ContentFlow/actions/runs/31560723174)。
- 问题与影响：非字符串 Prompt/metadata/比例/幂等键、畸形端口、600 状态、字段存在但值为 null 等输入可能触发 TypeError/ValueError，或被 truthy 判断误放行，造成 Worker 重试分类不稳定。
- 根因：代码假设调用方和 Provider 总是发送正确 Python/JSON 类型，并以值真假代替字段存在性；5xx 判断使用 status>=500。
- 解决方案：对所有请求类型显式失败关闭；Settings/runner 主动解析端口；视频互斥按字段存在性判断，id/task_id 必须一致；只有 500–599 为可重试服务端状态，600 按永久非标准状态处理。
- 涉及文件：contentflow/media_providers.py、contentflow/media_conformance.py、contentflow/settings.py、tests/test_media_contract_v1.py、tests/test_security.py。
- 验证：非字符串请求材料、不可哈希比例、双 ID、null URL、畸形端口和 600 状态均返回稳定 MediaProviderError/配置错误，不产生网络请求或底层异常。
- 剩余边界：静态类型与单元测试不能替代目标端 schema fuzz；后续应对 OpenAPI 自动生成有效/无效向量并在 CI 运行。

### CF-20260812-16：下载 URL 安全校验发生在响应接收之后

- 状态：CI 已签收；实现提交 `8a79658`，签收运行 [ContentFlow CI #31560723174](https://github.com/heee000/ContentFlow/actions/runs/31560723174)。
- 问题与影响：正式适配器原先只检查下载地址的类型和长度，域名允许列表、凭据、fragment、生产 HTTPS/端口要等 Worker 开始下载时才拒绝；违规响应因此能先被当作成功结果传播和持久化中间状态。
- 根因：协议响应解析与下载器安全策略分层实现，但响应边界没有复用下载校验函数。
- 解决方案：HTTPMediaProvider 构造时要求非空且全部有效的精确主机 allowlist；图片和视频响应一旦携带 URL，立即复用完整下载策略校验，违规地址统一转成脱敏、永久 MediaProviderError；实际下载时仍逐跳重复校验，形成纵深防御。
- 涉及文件：contentflow/media_providers.py、tests/test_media_providers.py。
- 验证：未授权主机、fragment、空 allowlist、混合无效 allowlist 均在正式响应返回或 Provider 构造阶段失败，测试客户端未访问下载目标；Worker 下载前的原有校验仍保留。
- 剩余边界：精确字符串主机校验不能固定 DNS 解析结果，生产仍需出站代理、网络策略和 DNS 重绑定防护。

### CF-20260812-17：正式媒体凭据和模型错误未在启动阶段全部失败

- 状态：CI 已签收；实现提交 `8a79658`，签收运行 [ContentFlow CI #31560723174](https://github.com/heee000/ContentFlow/actions/runs/31560723174)。
- 问题与影响：API Key 含空格/控制字符、模型名带首尾空格/超长/非法 Unicode 时，Settings 只验证“存在”，直到某个 Worker 执行任务才报错，导致错误部署表面健康、任务随后永久失败。
- 根因：严格边界只实现在 HTTPMediaProvider 和 live runner，生产启动校验与真实运行时规则发生漂移。
- 解决方案：Settings.validate_runtime 对启用的 HTTP 媒体 API Key 执行 1–4096 位无空格可打印 ASCII 校验，对启用模型执行 1–200 字符、无首尾空白、无控制字符且可编码为 UTF-8 的校验；Base URL 同步拒绝控制字符、非法 Unicode 和非规范主机。
- 涉及文件：contentflow/settings.py、contentflow/media_providers.py、tests/test_security.py。
- 验证：空格/换行 Key，带首尾空格、超长和孤立代理项模型均在 validate_runtime 阶段稳定失败；合法供应商中立配置与开发本地 HTTP 例外继续通过。
- 剩余边界：启动校验只能证明静态配置格式，不能证明凭据权限、额度、轮换状态或远端模型存在；这些仍由受控 live conformance 和运维检查签收。

### CF-20260812-18：机器契约弱于跨平台文件名和内联媒体运行时规则

- 状态：CI 已签收；实现提交 `8a79658`，签收运行 [ContentFlow CI #31560723174](https://github.com/heee000/ContentFlow/actions/runs/31560723174)。
- 问题与影响：运行时已拒绝 Windows 设备名、非法字符和空 base64，但 OpenAPI 仍可能把这些载荷判为合法，服务端按文档实现后会被 ContentFlow 拒绝，破坏可交付契约的一致性。
- 根因：文件名安全收紧和空内容修复发生在共享 Python 校验器中，机器可读 Schema 未同步复用等价定义。
- 解决方案：OpenAPI 新增 PortableFilename 共享 Schema，约束 basename、非法字符、尾随点/空格和大小写不敏感的 Windows 设备 stem；图片/视频共同引用；b64_json 最小长度设为 4；模型规则同步禁止首尾空白。safe_filename 进一步拒绝扩展名前带空格的设备 stem，例如 COM1 .png。
- 涉及文件：contentflow/filenames.py、docs/contracts/contentflow-media-v1.openapi.yml、tests/test_object_storage.py、tests/test_media_contract_v1.py、tests/test_media_conformance.py。
- 验证：OpenAPI 3.1 YAML 严格解析通过；测试断言两个响应 Schema 共用 PortableFilename，空 base64、CON.png、COM1 .png 及非法 MIME 均失败关闭。
- 剩余边界：OpenAPI regex 和 Python 路径语义仍是两套实现；后续应增加由契约自动生成的边界向量或 property-based 差分测试，持续防止漂移。

### CF-20260812-19：非法 Unicode 和错误数值类型可逃逸为底层异常

- 状态：CI 已签收；实现提交 `8a79658`，签收运行 [ContentFlow CI #31560723174](https://github.com/heee000/ContentFlow/actions/runs/31560723174)。
- 问题与影响：孤立 UTF-16 代理项可能在 JSON 指纹或 UTF-8 编码时触发 UnicodeEncodeError；布尔值在 Python 中是整数子类，可能被 max_bytes、max_redirects 或 runner 数值配置误接收；非字符串 poll ID 可能触发 AttributeError。
- 根因：原校验偏重长度与范围，未明确检查 UTF-8 可编码性、bool 排除和 Python 运行时具体类型。
- 解决方案：正式适配器和 runner 的受限文本统一验证 UTF-8 可编码性；Prompt、Shot、Base URL、模型名和下载 URL 在进入序列化/HTTP 前拒绝非法 Unicode；poll ID 复用受限文本规则；下载大小/重定向上限及 runner 数值配置显式拒绝 bool 和错误类型。
- 涉及文件：contentflow/media_providers.py、contentflow/media_conformance.py、contentflow/settings.py、tests/test_media_providers.py、tests/test_media_conformance.py。
- 验证：孤立代理项 Prompt/Shot/模型、非字符串 poll ID、bool/字符串大小上限与 bool/浮点重定向上限均产生稳定、脱敏的本地失败且不访问网络；媒体/安全/存储/Worker 契约联合门禁为 103 passed、118 subtests passed。
- 剩余边界：手写边界用例无法穷尽所有 JSON/Unicode 组合；下一阶段应引入基于 OpenAPI 的 schema fuzz 和 Python/Schema 差分门禁。

### CF-20260812-20：标识字段存在性和失败终态重试语义不一致

- 状态：CI 已签收；实现提交 `8a79658`，签收运行 [ContentFlow CI #31560723174](https://github.com/heee000/ContentFlow/actions/runs/31560723174)。
- 问题与影响：正式视频任务 ID 仍可接受控制字符；成功/错误响应中的 request_id 在值为 null、空串或非字符串时可能因可选值/truthy 判断被忽略；live runner 对活动/成功态的 error:null 也会当作字段不存在；失败终态 ErrorDetail 可声明 retryable=true，但 Worker 实际立即永久终止。
- 根因：部分校验判断的是“是否有真值”而非“字段是否出现”，并且 ErrorDetail 的通用 retryable 字段没有结合视频终态状态机进一步约束。
- 解决方案：正式适配器和 runner 都按键存在性处理 request_id/requestId 与禁止字段，所有出现的请求/任务 ID 必须是 1–255 字符、无控制字符的 UTF-8 文本；活动/成功状态出现 error 字段即拒绝，包括 null；失败/取消/过期终态的 error.retryable 必须为 false；OpenAPI 同步用 minLength、非空 pattern 和终态 const:false 表达。
- 涉及文件：contentflow/media_providers.py、contentflow/media_conformance.py、docs/contracts/contentflow-media-v1.openapi.yml、tests/test_media_providers.py、tests/test_media_conformance.py、tests/test_media_contract_v1.py。
- 验证：专项回归 72 passed、90 subtests passed；覆盖空/数字/null request ID、控制字符 task ID、活动/成功态 error:null 和失败终态 retryable=true；OpenAPI 3.1 解析、Ruff 与 git diff --check 通过。
- 剩余边界：当前 v1 把失败终态定义为不可重试的远端任务结果；若未来需要可恢复终态，应新增独立状态或版本化恢复动作，不应重新放宽同一字段造成 Worker 语义歧义。

### CF-20260812-21：验收报告普通哈希可枚举且秘密扫描覆盖不完整

- 状态：CI 已签收；实现提交 `8a79658`，签收运行 [ContentFlow CI #31560723174](https://github.com/heee000/ContentFlow/actions/runs/31560723174)。
- 问题与影响：报告用无密钥截断 SHA-256 处理 Base URL、错误码、请求/任务标识和媒体结果；端点与错误码等低熵值可被字典枚举。序列化秘密扫描只匹配原始字符串，换行等值进入 JSON 后会被转义而绕过；图片 base64、远端请求 ID、任务 ID、URL 与错误消息也未全部进入扫描集合。错误信封中的显式 `request_id:null` 仍会因值判断被漏放。
- 根因：把“报告不含明文”等同于“普通哈希不可逆”，并且秘密收集散落在部分响应分支；扫描器没有按 JSON 实际序列化形式检查。报告指纹算法变化也没有独立 Schema 版本表达。
- 解决方案：报告 Schema 升级为 v2，显式声明 `hmac-sha256-96-run-scoped`；每轮使用系统随机生成且不落盘的 256 位 HMAC key，对目标、错误码、请求 ID 和结果指纹进行 96 位截断。扫描集合覆盖凭据、端点、模型、nonce、幂等键、Prompt、请求/任务 ID、远端错误消息、媒体 URL 与 base64，并同时检查原值和 JSON 转义形式；错误信封按字段存在性拒绝 null ID。
- 涉及文件：contentflow/media_conformance.py、tests/test_media_conformance.py、docs/media_provider_contract.md。
- 验证：固定测试 HMAC key 后精确断言算法结果且证明不等于普通 SHA-256；正常图片/视频完整运行收集并拒绝全部已知秘密材料；带换行的秘密以 JSON 转义形式泄漏时拒绝创建报告；错误响应 `request_id:null` 在冲突探针中稳定失败。专项测试通过。
- 剩余边界：运行级 key 刻意不持久化，因而不同报告的指纹不可关联；本地 JSON 仍无签名、时间戳证明、集中不可变存储与访问审计。精确字符串扫描也不能替代对所有未知语义变换的 DLP，安全性仍依赖封闭的最小报告 Schema。

### CF-20260812-22：幂等键首尾空白被静默修剪

- 状态：CI 已签收；实现提交 `8a79658`，签收运行 [ContentFlow CI #31560723174](https://github.com/heee000/ContentFlow/actions/runs/31560723174)。
- 问题与影响：正式适配器对 `Idempotency-Key` 先执行 `strip()` 再发送，导致非法键被静默接受，也可能把调用方认为不同的字节序列归一为同一远端键，破坏不透明键身份和计费审计。
- 根因：输入清理被误用于身份标识；OpenAPI 仅要求全部字符可打印，未禁止首尾空格，机器契约与客户端实际意图不一致。
- 解决方案：适配器仍用 trim 结果做边界检查，但只要结果与原值不同就永久失败且不发网络请求；OpenAPI regex 与说明同步禁止首尾空白，保留中间可打印空格并明确服务端不得修剪不透明键。
- 涉及文件：contentflow/media_providers.py、docs/contracts/contentflow-media-v1.openapi.yml、docs/media_provider_contract.md、tests/test_media_contract_v1.py。
- 验证：前导空格、尾随空格和换行键均在网络前产生不可重试失败；机器契约测试精确断言新 pattern，内部空格键按原始字节发送，合法既有键继续通过。
- 剩余边界：v1 仍允许键内部出现普通空格；这是机器契约明确允许的可打印 ASCII，而不是归一化授权。目标服务仍需用真实 conformance 与账单证明按原始字节执行幂等。

### CF-20260812-23：GitHub 仓库简介与贡献者身份缺失

- 状态：已处理；本条所在提交使用 GitHub 账号 `heee000` 的 noreply 身份创建，仓库 About 与 README 同步补充。
- 问题与影响：GitHub About 的 Description 与 Topics 为空，访客无法从仓库首页快速判断项目定位；历史 21 个提交统一使用未关联 GitHub 账号的 `ContentFlow Builder <contentflow-builder@users.noreply.local>`，因此 GitHub 原生 Contributors 统计只显示匿名身份，仓库所有者 `@heee000` 没有被识别为贡献者。
- 根因：仓库初始化和后续自动化提交未配置 GitHub 可验证的作者邮箱，也没有维护首页元数据与 README 维护者入口。
- 解决方案：About Description 使用厂商中立的一句话说明，并补充 AI 内容自动化、FastAPI、PostgreSQL、pgvector、Next.js、RAG 与工作流等 Topics；README 明确链接维护者与贡献者 `John Wang (@heee000)`；当前仓库后续提交改用 `182348029+heee000@users.noreply.github.com`，由 GitHub 自动完成账号归属。
- 涉及文件：README.md、docs/engineering_change_log.md；GitHub 仓库 About 元数据。
- 验证：提交推送后检查 GitHub Commit API 的 author.login、Contributors API、Description 与 Topics；不使用强制推送，不修改或重写历史提交。
- 剩余边界：既有提交仍保留原始作者元数据和 SHA，这是保护公开历史的刻意选择；GitHub Contributors 统计可能存在短暂缓存，但新提交的账号归属不依赖历史改写。

### CF-20260812-24：历史提交未关联 GitHub 账号

- 状态：用户已明确授权历史重写和 `force-with-lease`；隔离镜像已完成身份迁移与逐提交树校验，本条提交记录迁移映射，远程分支与 CI 在推送后复核。
- 问题与影响：默认分支前 21 个提交使用不可关联 GitHub 账号的 `ContentFlow Builder <contentflow-builder@users.noreply.local>`，GitHub Contributors 与个人贡献记录将其显示为匿名，只有最近 2 个提交归属 `@heee000`。
- 根因：仓库早期自动化提交使用本地 `.local` 占位邮箱；GitHub 按提交 author email 关联账号，无法把该通用地址添加为账号验证邮箱。
- 解决方案：在隔离裸仓库中重建 9 个分支的 30 个可达提交；只把完全匹配旧身份的 21 个提交改为 `John Wang <182348029+heee000@users.noreply.github.com>`，保留原始树、消息、作者时间和提交时间；父 SHA 随历史重写映射，Dependabot 作者身份保留。
- 涉及范围：`main`、`codex/enterprise-media-runtime` 与 7 个 Dependabot 分支；完整旧→新 SHA 映射见 [Git 历史身份重写映射](git_history_rewrite_20260812.md)。
- 安全措施：重写前 9 个分支已生成完整 Git bundle 并验证，bundle SHA-256 为 `196FB9C6EF87E1B8A964E22E6A16CE7CCF5DAEB5ED9711A40A3AE873E36B476F`；推送必须逐分支声明旧 tip 的 `force-with-lease` 并使用 atomic，任一远端分支变化则整组拒绝。
- 验证：30 个新旧提交逐项验证 tree SHA 完全一致；重写基础 `main` 含 23 个提交且全部归属 `John Wang (@heee000)`；可达历史中旧 `.local` 邮箱为 0。
- 副作用：7 个 Dependabot 提交因父 SHA 改变，原 GitHub Bot GPG 签名不再适用于新对象并已移除；作者仍为 `dependabot[bot]`。既有文档保留旧 SHA 与历史 CI 链接作为原始证据，新旧对应关系由映射文档解释，不能把旧 CI 误称为验证过新 SHA。
- 剩余边界：GitHub Contributors 与贡献日历存在缓存，官方说明可能需要约 24 小时刷新；7 个开放 Dependabot PR 必须在推送后逐项核验仍开放且 diff 未丢失。
## 4. 阶段签收清单

| 门禁 | 当前结果 |
| --- | --- |
| Python 编译 / Ruff / diff | 通过；git diff --check 仅报告仓库既有换行转换提示，无空白错误 |
| 后端全量与覆盖率 | 177 passed、7 skipped、130 subtests；branch coverage 82.13%，门槛 75% |
| 媒体/存储/安全联合专项 | 109 passed、130 subtests |
| Python 依赖 | uv lock --check、pip check 通过；PYTHONUTF8=1 下 pip-audit --strict 为 No known vulnerabilities found |
| 前端 | ESLint、Sites/vinext + 2 tests、Next.js build 通过；npm audit moderate 为 0 vulnerabilities |
| Compose | Docker Engine 27.4.0；默认与 observability profile 用进程级测试密钥 config --quiet 通过；未改 .env、未启动持久栈 |
| 数据契约 | 已跟踪 15 个 YAML/JSON 全部解析通过，OpenAPI 3.1.0 |
| 厂商中立扫描 | 排除历史交接/本台账和未知私有资料后，阿里/千问/通义/qwen/dashscope/alibaba 命中 0 |
| 私有未知文件 | knowledge/北京周末 CityWalk 路线助手产品资料.txt 未读取、未修改、未暂存 |
| Git/远程 | 实现提交 `8a79658952ebac63ed866c24b57940e3286c023b` 与证据提交 `285de6a32de15124d1f7a59b771b6972b086bce9` 已普通快进到 `main`；[ContentFlow CI #31560723174](https://github.com/heee000/ContentFlow/actions/runs/31560723174) 为 success，Backend/Frontend 两个 Job 均成功 |

后续新增改动继续按本台账逐项记录，并为新的提交重新取得本地门禁与远程 CI；本次成功运行不得替代未来阶段证据。

### CF-20260813-01：交付源码和依赖没有可独立验证的供应链证明

- 状态：已由提交 `38ad07c64d60f19330b4f4b42aebcdd328a4cd63` 的 [ContentFlow CI #31691997756](https://github.com/heee000/ContentFlow/actions/runs/31691997756) 签收；Backend、Frontend、SBOM/可复现源码和签名证明四个 Job 全部 success。Artifact `9177772957` 未过期，摘要为 `sha256:5dad8fa59cab27e89b7a127dd718270f68faab19bea27b9a988d26ac8fbd481b`；证明 Job 已发布并反向验证 SLSA 来源、Python CycloneDX 与前端 CycloneDX 三份 attestation。
- 问题与影响：仓库已有 uv/npm 锁、依赖审计和固定 SHA Action，但接收方无法证明下载的源码归档对应哪个 commit，也没有可携带的 Python/前端依赖清单、摘要清单和签名来源；CI 成功页面不能独立绑定某个下载文件。
- 根因：质量门禁只消费测试/审计结果，没有把源码、依赖清单、摘要和 OIDC 签名组织成可下载、可复验的交付制品；工作流所有 Job 共用全局只读权限，尚未设计证明写权限的最小作用域。
- 解决方案：新增 `scripts/supply_chain.py`，生成零时间戳 gzip 包装的 `git archive`，要求声明 commit 与 checkout 一致，并在验证时重新生成摘要、比对 `git ls-files`、拒绝私钥/本地环境/`.contentflow` 路径；解析 Python/npm CycloneDX，归并 npm 相同身份的多安装路径，检查组件版本、唯一 purl、依赖图、漏洞字段和构建机绝对路径泄漏；生成和严格核验 `SHA256SUMS`。CI 新增只读 `supply-chain` Job 和仅非 PR 的 `attest-supply-chain` Job，后者等待 Backend/Frontend/SBOM 全部成功，再分别生成 SLSA 来源证明和两份 CycloneDX 证明，并用仓库、签名工作流和源码 SHA 反向验签。所有 checkout 禁止持久化令牌，新增 Action 固定到完整提交 SHA。
- 涉及文件：`.github/workflows/ci.yml`、`scripts/supply_chain.py`、`tests/test_supply_chain.py`、`docs/supply_chain.md`、`README.md`、`docs/operations.md`、`docs/production_requirements.md`、`docs/CONTENTFLOW_HANDOFF.md`、`docs/enterprise_readiness_review.md`。
- 验证：新增 10 项定向测试通过；全量 187 passed、7 skipped、130 subtests，分支覆盖率 82.13%；真实 `pip-audit` CycloneDX 为 76 个组件且 0 已知漏洞，npm CycloneDX 原始 627 项包含 6 组重复 `bom-ref`，身份一致归并后为 620 个组件；源码归档包含当前 commit 的 144 个跟踪文件，现有未知未跟踪文件和 `.contentflow` 不在归档；两次生成 SHA-256 均为 `A2559A273ADD925ADE73B04CC735830DDE7778DC826FDCC501473D1AF5631F36`；完整材料离线验真通过。该摘要属于实现前基准 commit `34ffb3f4` 的本地样本，最终提交会因树变化产生新摘要，必须以远程 Artifact 为准。
- 剩余边界：当前证明对象是源码归档，不是 OCI 镜像；Python 清单对应 `--all-extras` 环境，前端对应完整 lockfile，不等于裁剪后的生产容器层；Actions Artifact 只保留 30 天，仍缺制品仓库不可变保留、镜像扫描/签名、部署时验签、受保护分支、环境审批和灰度回滚。

### CF-20260813-02：npm CycloneDX 存在重复 bom-ref，直接签名会留下歧义

- 状态：已修复并由真实 lockfile 验证。
- 问题与影响：当前 npm 10 的 `npm sbom` 为 627 个组件生成 6 组重复 `bom-ref`，共 7 个重复项；这些记录对应同版本包的不同嵌套安装路径。重复引用会使依赖图消费者无法唯一定位组件，而简单去重又可能丢失范围、哈希、许可证或安装路径。
- 根因：npm 以 `name@version` 作为 `bom-ref`，没有把物理安装路径编码进引用；同版本包在 lockfile 中可出现在多个嵌套路径。
- 解决方案：规范化步骤按 `bom-ref` 分组，只允许 type/name/version/group/purl 完全一致的记录合并；scope 取 required 优先，哈希/许可证/外部引用去重，所有 npm 安装路径保存到 `contentflow:npm:package:paths`，依赖边合并去重；任一身份或未知字段冲突失败关闭。签名和上传的始终是规范化后文件。
- 涉及文件：`scripts/supply_chain.py`、`tests/test_supply_chain.py`。
- 验证：真实 npm 清单从 627 归并到 620 项，重复 component/dependency ref 为 0，所有 dependsOn 都能解析；测试覆盖多路径归并和版本冲突拒绝。
- 剩余边界：这是针对 npm 当前输出的保守兼容层，不替代 CycloneDX 官方 Schema 校验；npm 未来改变字段或引用语义时，未知冲突会主动阻断 CI，需要审查后版本化适配。

### CF-20260813-03：API 不可用时缺少受控脚本发布通道

- 状态：完成。实现提交 `a8f58cfc9449e74ec3c2f9d783dbdd98f728228a` 已同步 `codex/enterprise-media-runtime` 与 `main`；远程 CI run `31699801246` 全部成功。
- 问题与影响：原系统只有官方连接器和小红书人工导出。平台 API 不支持、账号能力尚未开放或远程调用前明确失败时，运营只能离开系统手工复制，任务状态、内容版本、素材完整性、操作者和最终结果无法形成一致闭环。直接在 API 异常后自动启浏览器脚本又可能在平台已经受理时重复发布。
- 根因：`PublishJob` 只有连接器隐式路径，没有发布方式、脚本包领域状态、人工结果回填和“确定失败/结果不确定”的降级门禁；渠道模型也默认等同于带凭据的 API 连接。
- 解决方案：增加 `connector/script/manual_export` 显式方式并保存到既有 `request_json`，避免数据库迁移；脚本渠道拒绝凭据并使用 `script_only`。Worker 在平台副作用前生成确定性 ZIP，包含审核版本、素材、manifest、README、固定 Playwright 依赖、逐文件 SHA-256 和只打开内置官方入口的运行器；运行器使用平台/渠道隔离 profile、验证路径/哈希、尽力填充但不点击最终按钮。工作台支持选择方式、下载、人工登记结果、明确失败后改用脚本；`publishing/submitted/reconciliation_required` 强制先对账。脚本/导出任务不能调用自动指标回收。
- 涉及文件：`contentflow/script_publishing.py`、`contentflow/entities.py`、`contentflow/schemas.py`、`contentflow/routers/channels.py`、`contentflow/routers/publishing.py`、`contentflow/routers/metrics.py`、`contentflow/worker.py`、`web/app/contentflow-app.tsx`、`tests/test_script_publishing.py`、`tests/test_script_publish_flow.py`、`tests/test_worker_v2.py` 及发布文档。
- 验证：最终本地后端全量 `194 passed, 7 skipped, 130 subtests passed`，分支覆盖率 `82%`；Ruff 与锁文件检查通过；运行器源码从 ZIP 中取出后真实 `compile()`。测试覆盖可复现包、无凭据、完整哈希、官方入口/路径约束、不含 `.click(`、渠道拒绝凭据/远程测试、脚本排期—Worker—下载—人工结果、非 API 指标拒绝、确定失败切换、结果不确定禁止切换、旧小红书任务兼容。前端 ESLint、2 项渲染测试、Next.js 生产构建通过，`npm audit --audit-level=high` 为 0 vulnerabilities。远程 run `31699801246` 的 PostgreSQL+MinIO 后端/安全、前端、SBOM/可复现源码、SLSA provenance 与双 CycloneDX attest/发布后验证四个 job 全部成功；artifact `9180780462` 摘要为 `sha256:8b852e875588ab637dcdf7f09c4874031424cb59ad10e5bd87f7a29179cd36f1`。
- 剩余边界：未以小红书/抖音真实账号执行浏览器 E2E；平台 DOM、登录挑战、声明/可见范围/定时发布控件会变化，当前选择器失败时只退化为人工复制；Playwright/Chromium 安装依赖外部下载，任务包未签代码签名；平台条款、账号风控和组织批准需逐平台确认；人工“已发布”证据仍依赖 reviewer 真实性，尚无截图/平台导出/双人复核与长期不可变证据。

### CF-20260813-04：脚本发布结果缺少可校验证据和职责分离

- 状态：已由修复提交 `8294a09de3581002d6606b53753826537473a6bb` 的 [ContentFlow CI #31715817953](https://github.com/heee000/ContentFlow/actions/runs/31715817953) 签收；四个 Job 全部 success。
- 问题与影响：原脚本通道只要求 reviewer 填写结果和理由，单人误操作、错误任务包、事后追加附件或对象被替换都可能造成无法可靠复核的成功记录。
- 根因：发布任务没有独立脚本尝试标识、证据实体、规范化摘要、证据清单和确认实体；渠道也没有可选双人策略，任务包下载地址会被最终平台 URL 覆盖。
- 解决方案：每次任务包生成独立 UUID `script_attempt_id` 并绑定包 SHA-256；新增截图/平台 JSON 证据上传、列表和授权下载。PNG/JPEG/WebP 经 Pillow 解码、像素/帧限制和服务端重编码去元数据，JSON 限对象/数组并规范化；对象写入后校验长度与 SHA-256，下载再次按数据库摘要复验。新增不可由 API 修改的确认记录；脚本渠道可选 1 或 2 人确认，双人模式要求不同 reviewer 针对同一尝试、任务包和证据 manifest 作出相同决定，第一次确认后冻结证据，决定或平台引用冲突返回 409。任务包 URI 独立保留，使待二次确认和终态仍能下载核验。
- 涉及文件：`contentflow/publish_evidence.py`、`contentflow/routers/publish_evidence.py`、`contentflow/entities.py`、`contentflow/schemas.py`、`contentflow/routers/publishing.py`、`contentflow/routers/channels.py`、`contentflow/script_publishing.py`、`contentflow/worker.py`、`contentflow/api.py`、`web/app/contentflow-app.tsx`、相关测试和用户/运维文档。
- 验证：规范化单元测试覆盖图片去元数据的确定输出、JSON 规范化、重复键/100 层以上嵌套/畸形输入拒绝、manifest 稳定性和尝试绑定；端到端覆盖无证据拒绝、授权上传/下载与摘要、跨工作区 404、首人冻结、同人重复拒绝、第二名一致确认、冲突决定拒绝和终态。迁移结构覆盖约束、唯一键、复合索引、旧 head 接管和半组表失败关闭；本地后端 `206 passed, 7 skipped, 135 subtests passed`。成功 CI Backend 为 `213 passed, 135 subtests passed`、覆盖率 83.33%，真实 PostgreSQL/pgvector、MinIO 与依赖审计通过；Frontend、SBOM/可复现源码和签名证明 Job 同时成功。Artifact `9187195019` 摘要为 `sha256:37cfa753125f76d76a974efe7f6420ff6ee64e2c161d6d7a9bdb33fa82b593bf`。
- 剩余边界：应用层记录不是平台签名回执、可信时间戳或 WORM 法律证据；数据库管理员仍可绕过 API 改表；对象写入后若数据库事务失败可能留下孤儿对象；没有恶意软件/DLP、法务保留、自动平台交叉核验、确认到期/升级或真正组织级职责分离策略。

### CF-20260813-05：新增迁移未同步灾备默认门槛

- 状态：迁移已由提交 `8294a09de3581002d6606b53753826537473a6bb` 的 [ContentFlow CI #31715817953](https://github.com/heee000/ContentFlow/actions/runs/31715817953) 在真实 PostgreSQL/pgvector 与 MinIO 环境签收；26 表联合恢复演练仍待独立执行。
- 问题与影响：发布证据新增两张表后，备份和恢复脚本仍默认旧 head 与 24 张表，会错误拒绝当前备份，或允许运维继续按旧结构判断完整性。
- 根因：数据库功能迁移与灾备脚本默认值没有作为同一变更面维护；首版 SHA-256 长度约束还曾被误嵌入 kind 约束，差异复核时发现且未进入提交。
- 解决方案：修正迁移为独立 kind/size/SHA-256 约束并补断言；备份默认 revision 更新为 `e28a6b9c4f10`，恢复最低 public 表数更新为 26；运维和交接文档同步当前事实，历史演练保留为历史证据而不冒充当前签收。
- 涉及文件：`migrations/versions/e28a6b9c4f10_add_publish_evidence.py`、`contentflow/migrate.py`、`tests/test_migrations.py`、`scripts/backup_stack.ps1`、`scripts/verify_backup.ps1`、`docs/operations.md`、`docs/CONTENTFLOW_HANDOFF.md`。
- 验证：迁移 Python 编译与 11 项 SQLite 空库/接管/半迁移/降级测试通过；真实 PostgreSQL 和 26 表恢复仍必须由当前 CI/独立恢复演练签收。
- 剩余边界：CI 的数据库迁移不等于数据库与对象联合恢复演练，也不等于 PITR、异地复制或 Object Lock；当前阶段不会启动或改写用户持久数据库。

### CF-20260813-06：CI 的 MinIO 边界 fixture 未同步证据上传上限

- 状态：修复已由提交 `8294a09de3581002d6606b53753826537473a6bb` 的 [ContentFlow CI #31715817953](https://github.com/heee000/ContentFlow/actions/runs/31715817953) 签收；四个 Job 全部 success。
- 问题与影响：实现提交 `20ff9d30179382822af0fca0cabc99152d0dd339` 的 [ContentFlow CI #31715306166](https://github.com/heee000/ContentFlow/actions/runs/31715306166) 中，Frontend 与 SBOM/可复现源码 Job 成功，但 Backend Job 在真实 MinIO fixture 初始化时失败，签名 Job 因依赖失败按设计跳过。
- 根因：该 fixture 为验证 64 字节对象边界显式设置 `max_upload_bytes=64`；新增跨字段保护要求 `publish_evidence_max_bytes <= max_upload_bytes`，但 fixture 仍使用默认 10 MiB。失败发生在测试设置构造阶段，不是 PostgreSQL 迁移、MinIO 读写或产品运行时失败。
- 解决方案：只在该专用 fixture 显式设置 `publish_evidence_max_bytes=64`，保持生产默认 10 MiB 和“证据上限不得超过通用上限”的失败关闭保护不变。
- 涉及文件：`tests/test_minio_integration.py`、`docs/engineering_change_log.md`。
- 验证：本地安全+MinIO 专项 `33 passed, 2 skipped, 28 subtests passed`；CI 失败运行中的其余测试为 `211 passed, 135 subtests passed`、覆盖率 82.56%。真实 MinIO 两项将在下一次 CI 中执行，不用本地跳过结果冒充签收。
- 验证补充：成功运行中 Backend 为 `213 passed, 135 subtests passed`、覆盖率 83.33%，Python 审计 0 已知漏洞；Frontend、SBOM/可复现源码、SLSA provenance 与双 CycloneDX attest/反向验证全部成功。Artifact `9187195019` 摘要为 `sha256:37cfa753125f76d76a974efe7f6420ff6ee64e2c161d6d7a9bdb33fa82b593bf`。
- 剩余边界：该修复只纠正测试 fixture；成功 CI 不等于 26 表数据库+对象联合恢复、PITR/异地或真实平台账号签收。

### CF-20260822-01：脚本尝试没有期限，发起人可确认自己的结果

- 状态：实现提交 `c290f6420e30b365a0af4f7540b1d9b86355c1d7` 已由 [ContentFlow CI #32568712614](https://github.com/heee000/ContentFlow/actions/runs/32568712614) 签收，四个 Job 全部 success。
- 问题与影响：长期有效的任务包可在内容、页面或组织授权变化后继续运行；单人确认策略只校验 reviewer 角色，没有把脚本发起人排除，无法形成最基本的四眼原则。
- 根因：尝试元数据只有 ID、包摘要和确认策略，没有发起人及过期时间；运行器和四个 API 入口也没有统一期限门禁。
- 解决方案：生成时记录 `script_requested_by` 和带时区的 `script_confirmation_expires_at`；默认 TTL 为 1440 分钟，限制 15-43200 分钟。发起人不能确认；过期后运行器、下载、上传和确认失败关闭，允许显式重建新尝试。
- 涉及文件：`contentflow/entities.py`、`contentflow/script_publishing.py`、`contentflow/settings.py`、`contentflow/worker.py`、`contentflow/routers/publishing.py`、`contentflow/routers/publish_evidence.py`、`contentflow/schemas.py`、前端、环境/Compose 和相关测试。
- 本地验证：全量后端 `208 passed, 7 skipped, 137 subtests passed`，分支覆盖率 82.10%；Ruff、编译、锁/依赖/漏洞审计、双 Compose 配置、前端 ESLint/2 项渲染测试/Sites/Next 构建/npm 审计均通过。
- 远程验证：真实 PostgreSQL/pgvector 与 MinIO 为 `215 passed, 137 subtests passed`、分支覆盖率 83.14%；Python/npm 0 已知漏洞，前端双构建、供应链证据和发布后验签成功。Artifact `9474759779` 摘要为 `sha256:272cab55b8bff31e3f3a7bc8b39e2573b79c57a0d7756b2ae99dc49b9ccb5ce2`。
- 剩余边界：无岗位/组织冲突策略、step-up MFA、委派/升级和管理员例外治理；旧尝试历史仍缺运营归档视图。

### CF-20260822-02：对象写入与数据库事务失败可能留下孤儿

- 状态：实现、本地门禁和真实 MinIO CI 已完成。
- 问题与影响：任务包或证据已写入对象存储但数据库 flush/commit 失败时，对象不再有业务引用；过期重建也会持续积累旧包。
- 根因：对象存储契约缺删除能力，API/Worker 没有同步补偿路径。
- 解决方案：本地和 S3 实现有边界校验的幂等 `delete`；数据库失败时回滚并尽力删除本次对象，过期重建在新状态提交后清理旧包。删除失败写日志/审计，不回滚已经成功提交的新状态。
- 验证与边界：本地删除、路径逃逸、S3 delete_object、事务失败和过期重建定向测试通过；CI 真实 MinIO 删除通过。对象存储自身故障仍可能产生孤儿，生产需定期引用对账、告警和异步补偿。

### CF-20260822-03：中断残留与跟踪文档乱码

- 状态：已清理并记录。
- 问题与影响：中断留下已被正式实现取代的供应链补丁副本、三份旧 coverage 数据，并有 README/平台/用户文档的问号乱码。
- 解决方案：逐项核对来源后只删除可再生成/已正式提交的文件，修复四处跟踪文档文本；保留运行数据库、备份、对象、虚拟环境和未知私有资料。
- 删除项：`.contentflow/ci_supply_chain.patch`、根目录 `.coverage`、`.contentflow/.coverage-supply-chain*`；均可由 Git 历史或测试再生成。`.pytest_cache` 因本机 ACL 无法访问但已被 Git 忽略。

### CF-20260822-04：主线新增 nanoid 高危公告

- 状态：已精确修复，并由 Linux/Node 锁定安装、双构建和 npm audit 远程签收。
- 问题与影响：主线 override 固定 `nanoid 3.3.17`，后来发布的公告把 `<3.3.18` 标为高危，导致当前 npm 安全门禁失败。
- 解决方案：只把 override 和 lock 中 nanoid 更新到 `3.3.18`，不使用 `npm audit fix --force`，不混入 Next/vinext 大版本升级。
- 验证与边界：本机 audit 为 0，lint/test/build 通过；CI 使用 Node 22.13+ 从锁文件重建环境，双构建和 audit 为 0 漏洞。其他 Dependabot PR 仍需逐条评估。

### CF-20260822-05：缺少跨目标的阶段完成度与公开交付边界

- 状态：已建立阶段报告，结论随当前提交继续校准。
- 解决方案：新增 [阶段性总结与分层完成度](phase_summary_2026-08-22.md)，以截至实现提交 `c290f64` 的 29 个提交、当前代码/门禁和 GitHub 治理为证据，分别给出个人本地、个人公开、公开 Beta、企业商业项目的当前比例、缺口和完成判据。
- 当前判断：综合 L2+；四项目标约为 80%-85%、60%-65%、45%-50%、25%-35%。比例表示门禁完成度，不表示代码覆盖率或工期。
- 剩余边界：真实平台、Provider、容量、故障恢复、组织流程和法律合规未验证部分继续明确标为未签收。

### CF-20260822-06：Git Smart HTTP 网络不可用时的安全同步

- 状态：两个远端分支已普通快进，未使用 force/force-with-lease；远端提交 SHA 与本地一致。
- 问题与影响：Git HTTPS 多次连接重置/超时；SSH 主机指纹与 GitHub 官方值一致，但本机没有绑定账号的 SSH 私钥。继续重复 push 只会制造失败噪声。
- 解决方案：通过已登录 GitHub CLI 的 Git Data API 上传本地 commit 的 29 个原始 Git blob；每个 blob SHA、tree SHA 和 commit SHA 都必须与本地逐项一致。更新引用前再次确认功能分支和 main 仍指向预期父提交，两个 PATCH 均使用 `force=false`，更新后再次读取 SHA 验证。
- 验证：本地/API commit 均为 `c290f6420e30b365a0af4f7540b1d9b86355c1d7`，tree 均为 `3cfd24e814a208fb7e14f4f1033ebf73eb6e1492`；两个远端引用均为该提交。临时 SSH known-hosts 和 API 归档目录已删除，没有修改全局 Git/SSH 配置。
- 剩余边界：这是本次网络故障的等价传输路径，不替代恢复正常 Git HTTPS/配置正式 SSH key；后续默认仍优先普通 `git push`。

## 2026-08-23 本地 BGE-M3 与人工真实素材阶段

### CF-20260823-01：真实文本 Provider 不提供 Embedding，Hash 不能用于真实检索

- 状态：实现、批量推理、宿主机/离线容器真实推理和完整本地门禁已完成；远程 CI 与阶段提交待回填。
- 问题与影响：当前文本模型 API 可真实生成 JSON，但没有 Embedding 端点；继续使用 Hash 只能保证确定性测试，不能证明中文语义检索可用。
- 根因：文本与向量能力被误假设为同一 OpenAI-compatible 端点，仓库缺少独立、真实且可本地部署的向量 Provider。
- 解决方案：新增显式 `bge-m3-local` Provider，固定 BAAI/bge-m3 官方提交 `5617a9f61b028005a4858fdac845db406aefb181`，禁止 remote code，懒加载并在进程内缓存模型；知识分块使用可配置批次的单次模型调用，输出 1024 维归一化 Dense 向量并拒绝空文本、错维度和 NaN/Inf。CPU PyTorch 从官方专用索引锁定，容器使用非 root 可写的持久模型缓存卷。
- 涉及文件：`contentflow/embeddings.py`、`contentflow/settings.py`、`pyproject.toml`、`uv.lock`、`Dockerfile`、`docker-compose.yml`、`.env.example`、`tests/test_local_embeddings.py` 及文档。
- 当前验证：CPU-only 依赖安装成功；固定提交真实中文推理返回 1024 个有限值，L2 范数 1.0，首次下载/加载/推理 212.43 秒。批量改造后缓存冷加载+4 段为 31.4 秒、热查询 0.06 秒；Linux Worker 镜像以非 root、完全禁网、固定缓存完成 2 段推理。单元测试覆盖 revision、remote code、批量单次调用、归一化、错维度/非有限值和生产配置失败关闭。
- 剩余边界：单次向量正确不等于真实知识集检索质量；仍需热路径延迟、内存、并发、容器离线重启、召回/引用质量与容量门禁。首次下载依赖外网，生产应先预热缓存并设置 offline。

### CF-20260823-02：没有媒体 API 时素材任务会错误排队，人工上传无法填充占位任务

- 状态：实现、定向端到端回归、完整本地门禁和真实运行栈已完成；远程 CI 待回填。
- 问题与影响：原审批逻辑总是创建 `asset.generate` Job；用户选择真实人工封面时会调用不存在的 Provider。旧上传接口只新增 ready 资产，原 planned 资产仍会让发布门禁永久失败。
- 根因：媒体 Provider 只支持 mock/http；上传 API 没有目标 `asset_id`、内容版本和占位任务填充语义。
- 解决方案：图片/视频新增显式 `manual` Provider。审核后资产进入 `awaiting_upload` 且不创建生成 Job；Worker 对尚未产生外部副作用的旧队列任务安全收敛为待上传。上传可选择具体资产或当前版本唯一占位，要求内容已审核、版本/类型一致；PNG/JPEG/WebP 安全解码、像素/单帧限制并重编码去元数据，JSON 规范化；成功填充同一资产为 `manual-upload/ready`，对象写入后若路由内数据库 flush 或审计失败则尽力补偿删除。前端展示待上传状态和具体任务选择。
- 涉及文件：`contentflow/routers/contents.py`、`contentflow/routers/assets.py`、`contentflow/worker.py`、`contentflow/settings.py`、`web/app/contentflow-app.tsx`、`tests/test_manual_media.py`、`tests/test_worker_v2.py` 及文档。
- 当前验证：定向测试证明审批后无生成 Job、未审核/旧版本/类型不匹配失败关闭、非法伪 PNG 返回 415 且状态不变、有效真实 PNG 填充同一资产、对象可下载、重复覆盖被 409 阻断；前后端分别区分图片、视频和分镜 JSON。完整后端为 `219 passed, 7 skipped, 143 subtests passed`，发布 Worker 仍要求当前版本全部资产 ready。
- 剩余边界：普通视频只校验 MIME/大小/文件名，尚未做 ffprobe/恶意文件扫描；用户实际封面及最终公众号草稿视觉质量待体验阶段验收。

### CF-20260823-03：微信公众号白名单更新后的鉴权复验

- 状态：已验证，无写入副作用。
- 证据：2026-08-23 白名单更新后无副作用 token 复验取得 7200 秒有效期；2026-08-24 在隔离生产栈内将凭据加密写入渠道并由 Worker 复验为 `connected`，强制 `auto_publish=false`。没有输出 App Secret、Access Token 或完整响应，也没有创建新的微信素材/草稿或公开发布。
- 剩余边界：该结果只证明当前 IP 白名单和基础凭据有效，不替代草稿异常矩阵、Token 过期、限流、权限撤销、公开发布或最终 article_id 对账。
### CF-20260824-04：官方 CPU PyTorch 本地版本导致 pip-audit/CI 无法查询

- 状态：实现、单元测试和本地真实漏洞审计已完成；远程 CI/SBOM 待本阶段推送后签收。
- 问题与影响：官方 CPU 索引安装 `torch 2.13.0+cpu`，pip-audit 直接按该本地版本查询 PyPI 时报告 Dependency not found；新增本地 Embedding extra 后，原 CI 审计和 CycloneDX Job 会稳定失败。
- 根因：PEP 440 本地版本标识用于区分 CPU wheel，但公开漏洞 advisory 使用基础版本 `2.13.0`；原供应链命令没有这层显式、受约束的身份映射。
- 解决方案：`scripts/supply_chain.py audit-python` 从锁文件导出 all-extras 完整固定版本，仅允许把完全匹配的官方 `+cpu` pin 在 advisory 查询阶段映射为基础版本；其他本地后缀、缺失或重复 pin 均失败关闭。CycloneDX 输出恢复精确安装版本、记录 advisory 版本并补入本地 ContentFlow 项目身份；CI 的漏洞和 SBOM 两条路径统一调用该命令。
- 当前验证：新增供应链回归 12 passed；真实审计为 No known vulnerabilities found，规范化 Python CycloneDX 含 96 个组件、精确 `torch 2.13.0+cpu`、当前 ContentFlow 版本且 0 漏洞。
- 剩余边界：审计使用不带哈希的临时固定版本导出和 `--no-deps`，依赖图完整性由带制品哈希的 `uv.lock` 与 locked sync 保证；仍需 GitHub Linux 环境、远程 SBOM 验证和 attestation 共同签收。
