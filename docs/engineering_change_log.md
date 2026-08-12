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
