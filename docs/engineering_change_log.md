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

- 状态：实现、批量推理、宿主机/离线容器真实推理、完整本地门禁和远程 CI 已完成。
- 问题与影响：当前文本模型 API 可真实生成 JSON，但没有 Embedding 端点；继续使用 Hash 只能保证确定性测试，不能证明中文语义检索可用。
- 根因：文本与向量能力被误假设为同一 OpenAI-compatible 端点，仓库缺少独立、真实且可本地部署的向量 Provider。
- 解决方案：新增显式 `bge-m3-local` Provider，固定 BAAI/bge-m3 官方提交 `5617a9f61b028005a4858fdac845db406aefb181`，禁止 remote code，懒加载并在进程内缓存模型；知识分块使用可配置批次的单次模型调用，输出 1024 维归一化 Dense 向量并拒绝空文本、错维度和 NaN/Inf。CPU PyTorch 从官方专用索引锁定，容器使用非 root 可写的持久模型缓存卷。
- 涉及文件：`contentflow/embeddings.py`、`contentflow/settings.py`、`pyproject.toml`、`uv.lock`、`Dockerfile`、`docker-compose.yml`、`.env.example`、`tests/test_local_embeddings.py` 及文档。
- 当前验证：CPU-only 依赖安装成功；固定提交真实中文推理返回 1024 个有限值，L2 范数 1.0，首次下载/加载/推理 212.43 秒。批量改造后缓存冷加载+4 段为 31.4 秒、热查询 0.06 秒；Linux Worker 镜像以非 root、完全禁网、固定缓存完成 2 段推理。单元测试覆盖 revision、remote code、批量单次调用、归一化、错维度/非有限值和生产配置失败关闭。
- 剩余边界：单次向量正确不等于真实知识集检索质量；仍需热路径延迟、内存、并发、容器离线重启、召回/引用质量与容量门禁。首次下载依赖外网，生产应先预热缓存并设置 offline。

### CF-20260823-02：没有媒体 API 时素材任务会错误排队，人工上传无法填充占位任务

- 状态：实现、定向端到端回归、完整本地门禁、真实运行栈和远程 CI 已完成。
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

- 状态：实现、单元测试、本地真实漏洞审计和远程供应链签收已完成。
- 问题与影响：官方 CPU 索引安装 `torch 2.13.0+cpu`，pip-audit 直接按该本地版本查询 PyPI 时报告 Dependency not found；新增本地 Embedding extra 后，原 CI 审计和 CycloneDX Job 会稳定失败。
- 根因：PEP 440 本地版本标识用于区分 CPU wheel，但公开漏洞 advisory 使用基础版本 `2.13.0`；原供应链命令没有这层显式、受约束的身份映射。
- 解决方案：`scripts/supply_chain.py audit-python` 从锁文件导出 all-extras 完整固定版本，仅允许把完全匹配的官方 `+cpu` pin 在 advisory 查询阶段映射为基础版本；其他本地后缀、缺失或重复 pin 均失败关闭。CycloneDX 输出恢复精确安装版本、记录 advisory 版本并补入本地 ContentFlow 项目身份；CI 的漏洞和 SBOM 两条路径统一调用该命令。
- 当前验证：新增供应链回归 12 passed；真实审计为 No known vulnerabilities found，规范化 Python CycloneDX 含 96 个组件、精确 `torch 2.13.0+cpu`、当前 ContentFlow 版本且 0 漏洞。
- 远程验证：实现提交 `0282e9bacd6d553553ad0041096a607c5bceb162` 已普通推送到 `codex/enterprise-media-runtime`；[ContentFlow CI #32652773152](https://github.com/heee000/ContentFlow/actions/runs/32652773152) 四个 Job 全部成功。真实 PostgreSQL/pgvector 与 MinIO 后端门禁、前端 lint/test/build/audit、CPU-wheel-aware Python 漏洞审计、96 组件 CycloneDX、可复现源码归档、SLSA 来源证明和双 CycloneDX attestation 均通过。Artifact `9496650624` 名为 `contentflow-supply-chain-0282e9bacd6d553553ad0041096a607c5bceb162`，摘要为 `sha256:2b9afadcb870ce6be009e6bac980824369112f19ab7ddafc0dcac9c51c853053`。
- 剩余边界：审计使用不带哈希的临时固定版本导出和 `--no-deps`，依赖图完整性由带制品哈希的 `uv.lock` 与 locked sync 保证；GitHub 的 `setup-uv` 并行缓存保存出现一次同键争用提示，不影响作业、制品或证明结论。
## 2026-08-24 发布可靠性与操作体验阶段

### CF-20260824-05：外部写入前失败与结果不确定使用同一重试语义

- 状态：实现提交 `b4b23b76119c31c4e71cef05fe5ad1d816a20521` 已普通推送；[ContentFlow CI #32724822598](https://github.com/heee000/ContentFlow/actions/runs/32724822598) 四个 Job 全部成功。
- 问题与影响：公众号因出口 IP 白名单在获取 Token 时失败，旧 Worker 仍把所有连接器异常视为可能已经写入平台，要求人工对账。这样虽然保守，但用户无法在明确无副作用时修复后安全重试；另一方面通用 Job 重试又可能绕过发布专用门禁。
- 根因：连接器错误没有显式记录发生阶段、是否跨过外部写入边界和是否应使渠道失效；PublishJob API 也没有专用安全重试状态与端点。
- 解决方案：新增 `ConnectorPublishError(stage, retry_safe, invalidate_channel)`；公众号 Token 请求/响应错误、封面缺失和本地素材读取失败声明为写入前安全失败。Worker 持久化失败阶段、脱敏消息、时间、渠道失效标记和有界历史，终止自动退避；鉴权失败把渠道置为 `invalid`。专用 retry 在 PostgreSQL 锁下重新验证工作区、当前审核版本、渠道 connected 和队列非 running，清除分发令牌后立即入队并审计；通用 Job retry 显式拒绝安全发布失败和待对账发布。任何永久素材、草稿或公开发布调用开始后的异常仍保留 `reconciliation_required`，不自动重发。
- 验证：连接器契约断言 40164 后只访问 Token 端点；端到端断言失败阶段、渠道失效、通用重试阻断、复测前阻断、复测后安全重试和最终草稿结果；运行中取消竞态与异常渠道新建任务也有回归。全仓后端 `223 passed, 7 skipped, 143 subtests passed`。
- 边界：旧版本已经进入 `reconciliation_required` 的任务不追溯重分类；平台调用跨过首次写入后的超时仍需人工对账。安全重试不是“所有失败都能重试”，也不替代稳定 NAT/固定出口。

### CF-20260824-06：只有排期入口且工作台信息架构过载

- 状态：已实现并通过本地静态、渲染与生产构建验证；远程 Frontend、供应链与签名 Job 已由 CI #32724822598 签收，真实浏览器主观验收待用户执行。
- 问题与影响：用户只想马上交付内容时仍必须理解并填写排期；十个同级导航和发布表单的所有高级能力同时出现，使第一次使用者难以判断主流程。操作缺少统一的点击和视图反馈。
- 根因：发布请求强制 `scheduled_at`，领域模型没有显式立即/定时语义；前端按模块平铺而不是按运营任务分层，也没有把脚本/人工导出作为渐进选项。
- 解决方案：`publish_now` 立即入队，定时模式继续要求未来带时区时间；请求 ID 支持网络幂等，响应显示执行时机。工作台收敛为创建→审核→素材→发布四步，根据真实数据计算一个下一步；高级资源折叠收纳，移动端使用原生更多选择器。发布页默认立即、匹配同平台渠道、显式提示公众号只建草稿，脚本/人工导出放入高级方式；列表展示执行时机、失败阶段、安全重试和下一步。加入 80–180ms 按压/视图/Toast 动效、键盘焦点和 `prefers-reduced-motion`。
- 设计依据：沿用仓库 IBM Carbon 风格的单一蓝色、高密度、直角与边框层级；在 `web/DESIGN.md` 固化信息架构、渐进披露和动效纪律，没有引入渐变、发光、浮夸弹跳或假进度。
- 验证：ESLint、2 项服务器渲染/源契约测试、vinext Sites 构建和 Next.js 生产构建全部通过；TypeScript 通过。远程 Backend 在真实 PostgreSQL/pgvector 与 MinIO 上为 `230 passed, 143 subtests passed`，分支覆盖率 82.69%，Python/npm 审计无已知漏洞；供应链 Artifact `9519101023` 摘要为 `sha256:737dae20923f594ef1858d5d7072392b2e47ae630c5ecb0dc5fe2246c69cc73c`，SLSA 与双 CycloneDX 证明发布后反向验证成功。Codex 内置浏览器在本机因 Windows sandbox `helper_unknown_error` 无法启动，未用非授权的替代浏览器冒充视觉签收。
- 边界：下一步推荐是基于当前聚合状态的保守规则，不是个性化推荐；复杂历史/多活动并行仍需更细的任务分组。视觉与文案的最终主观验收需真实用户在本地页面完成。

## 2026-08-25 内容工作室 Agent、风格 Skill 与多来源图片阶段

### CF-20260825-01：一次性生成 Prompt 只能产出结构正确但内容单薄的文案

- 状态：实现、本地全量门禁、真实 Prompt Eval、本地运行栈升级和远程 CI/供应链签收均已完成。
- 问题与影响：旧生成链路只有 plan → generate → review 三次独立调用，Prompt 主要约束 JSON 结构与禁用词，没有选题比较、证据账本、平台写作标准、可解释质量维度或编辑修订闭环，导致流程能跑通但正文信息密度和平台原生性不足。
- 解决方案：新增有界 Content Studio Agent。Plan 必须给出至少三个角度候选、选择理由、内容论点、证据账本、叙事结构、平台策略和媒体方向；Generate 输出平台正文、备选标题、证据使用和素材简报；Review 同时承担事实/品牌安全与 hook、specificity、evidence、platform_native、structure、usefulness、voice、originality、cta 九维编辑评审。深度档位最多定向修订一次，标准档位不修订，禁止无限循环、任意工具和代码执行。
- 安全选择：只有修订稿规则、安全评审通过且质量不回退时才采用；高分但仍不安全的修订稿永不替换原稿。质量分不能替代人工审核，内容仍停在 needs_review 或 blocked。
- 追溯：ContentItem/ContentRevision 新增 generation_json，记录 Agent 模式、质量档位、目标/实际分数、修订是否尝试/采用、失败类型、风格版本与清单 SHA-256；人工作品版本继续保留生成来源。

### CF-20260825-02：用户无法选择、安装和冻结写作风格

- 状态：实现和 API/UI/示例回归完成。
- 根因：Campaign 只有自由文本 tone，没有可复用、可版本化、可审计的写作规则对象；若直接把所谓 Skill 设计成代码插件，又会扩大为租户级远程代码执行。
- 解决方案：新增工作区 StyleSkill 表、迁移和 API，内置“专业社媒编辑、场景叙事、专业解释型”三种风格。自定义清单只接受固定 JSON 字段 manifest_version/slug/name/version/description/instructions/forbidden_patterns/platform_instructions/examples，限制 64 KiB、语义版本、平台和文本长度，未知字段一律拒绝；不加载 Python、JavaScript、模板代码或任意依赖。
- 运行边界：活动选择具体 Skill ID；入队时冻结规范化清单与 SHA-256，运行时再次校验，后续停用/升级不改写历史。安装、启用/停用和使用版本均可审计。可安装示例位于 docs/examples/style-skills/warm-city-guide.json，并由测试真实解析。

### CF-20260825-03：素材只有人工或单一生成，缺开放图库和候选选择

- 状态：实现、真实 Openverse 无副作用搜索烟测和本地对象存储选择回归完成；真实计费图片生成 Provider 尚未提供，不能宣称已经生成过真实 AI 图片。
- 解决方案：Campaign 新增 manual/generate/search/hybrid 四种图片来源。Agent 产出搜索词和生成提示；审核通过后 search 入 asset.search，generate 使用既有中立 HTTP 媒体契约，hybrid 并行建立同一 cover 候选组。未选中的可选候选不会阻塞发布，同组只能显式选中一个。
- 搜索边界：Openverse 固定 Wikimedia source，只保留 CC0/PDM/BY/BY-SA；API 响应限制 2 MiB、候选 1–12、禁止重定向，下载仅允许精确 upload.wikimedia.org，落地页仅 commons.wikimedia.org，许可页仅 creativecommons.org。用户必须打开原始页面核验并确认许可后才能下载；图片仍经过字节/像素/单帧限制、重编码去元数据、对象摘要和内容版本门禁。数据库失败会尽力删除新写对象。
- 真实烟测：查询 Beijing city street 返回 2 个 BY-SA 候选，下载域名均为 upload.wikimedia.org，落地页均为 commons.wikimedia.org；未下载、选择或发布任何外部图片。Openverse 许可元数据只作线索，不构成 ContentFlow 的法律保证。

### CF-20260825-04：长文 Agent 暴露 60 秒硬编码和错误的 Eval 正向样本

- 状态：超时配置、Eval 用例和真实治理升级完成。
- 真实证据：第一次 prompt-r2/eval-v2 在 generate 阶段 60 秒 TimeoutError，系统自动恢复 eval-v1；没有审批或激活失败版本。随后把文本请求超时改为 CONTENTFLOW_MODEL_REQUEST_TIMEOUT_SECONDS，默认 120 秒且只允许 10–300 秒，并透传 API/Worker Compose。
- 第二次评测不再超时，但 eval-v2 的 Plan 输入未声明 platforms 却要求 platform_strategies.wechat，Review 又对“测试正文”错误要求 passed=true；模型正确缺省平台策略并拒绝空泛正文，结果 failed，系统再次恢复 eval-v1。
- 修复：eval-v3 明确 goal/audience/platforms=[wechat]，使用包含已确认事实、人工复核边界和 CTA 的正向正文，并要求全部九维评分路径。真实 openai-compatible/deepseek-v4-flash Eval passed；由不同管理员完成套件激活、Prompt 审批和激活。当前 workspace-r2 active、eval-v3 active、generation_ready=true。
- 边界：Eval 证明三个固定契约样本通过，不证明跨主题质量、稳定延迟、成本、事实正确率或模型漂移已签收。

### CF-20260825-05：可选修订的最终复评失败会丢失已评审原稿

- 状态：代码修复和回归完成；为避免继续消耗真实模型额度，修复后未再自动发起第二条真实深度工作流。
- 真实发现：现有 CityWalk 活动在新 Prompt 下完成 Plan、初稿、首次 Review 和定向修订，最后一次 Review 返回不可解析 JSON，工作流以 RuntimeError 失败。脱敏 provenance 记录 5 次调用、4 成功 1 失败、总计 60784 个 Provider 上报 Token；没有保存模型正文，没有创建 ContentItem/Asset，更没有微信素材、草稿或发布副作用。
- 修复：修订是可选增强而非新的单点故障。定向生成或最终复评出现 RuntimeError/TimeoutError 时，保留首次规则与模型评审完成的原稿、原评分和安全状态，记录 revision_attempt_status=failed 与脱敏 error_type；未经最终复评的修订稿绝不采用。初稿或首次安全评审失败仍使工作流失败关闭。
- 验证：新增高分但不安全修订拒绝、最终复评失败保留原稿、风格示例、候选选择/许可确认/互斥等回归。最终本地 Ruff/compile 通过；后端 234 passed、7 skipped、145 subtests passed，分支覆盖率 80.92%；前端 ESLint、2 项渲染测试、Sites 与 Next 生产构建通过；默认/observability Compose 与备份脚本语法通过。

### 数据迁移、恢复与现场边界

- Alembic head 从 e28a6b9c4f10 升为 1a2b3c4d5e6f，新增 style_skills 表和两处 generation_json；备份/恢复默认门槛同步为 27 张 public 表。
- 升级前静默联合备份 .contentflow/backups/20260825-010604 已隔离恢复通过：26 表、旧 head、2 个对象。升级后 .contentflow/backups/20260825-010724 再次隔离恢复通过：27 表、新 head、2 个对象。
- contentflow-live-test 保留 PostgreSQL/MinIO 卷完成迁移；原 1 个活动、2 条内容、6 个发布任务仍在。API database/storage ready、Worker 启动、Web 200。
- 仍未读取、修改或暂存 knowledge/北京周末 CityWalk 路线助手产品资料.txt；本地 .env、账号文件、模型缓存和备份均不进入 Git。

### 远程交付证据

- 实现提交 `9e94d0f58170b3291e9425bfa04ba167a0b3bd8f` 已通过普通 `git push` 同步到 `codex/enterprise-media-runtime`，未使用 force 或 force-with-lease。
- [ContentFlow CI #32758080637](https://github.com/heee000/ContentFlow/actions/runs/32758080637) 四个 Job 全部成功：真实 PostgreSQL/pgvector 与 MinIO 后端门禁、Python/前端审计、前端 lint/test/build、可复现源码归档、SLSA 来源证明和双 CycloneDX attestation 均已签收。
- 供应链 Artifact `9531626220` 名为 `contentflow-supply-chain-9e94d0f58170b3291e9425bfa04ba167a0b3bd8f`，摘要为 `sha256:71cc728211a092020ca3a369785c59e8edb6b28cdfd3482c0a558ad0562c75f3`；CI 后端结果与本地一致为 `234 passed, 7 skipped, 145 subtests passed`，分支覆盖率 80.92%。

## 2026-08-30 真实进度、素材待办与项目辨识阶段

### CF-20260830-01：内容生成只有笼统状态，无法判断是否卡住

- 状态：实现、本地回归、生产构建、公开登录页浏览器验收、提交与远程 CI 完成；重新登录后的业务页主观验收待用户执行。
- 问题与影响：工作流只在知识检索、策划、内容生成和人工审核之间保存粗粒度状态；内容 Agent 最耗时的初稿、编辑评审、定向改写与最终复核都显示为同一个“生产中”，用户无法区分正常等待、阶段推进和故障。
- 根因：Content Agent 没有向工作流暴露阶段回调，前端也没有工作区级运行查询和一致的异步反馈组件。
- 解决方案：Agent 在每个平台模型调用前发出 `drafting/reviewing/revising/final_review` 阶段；工作流附加当前平台序号与总平台数，并用独立短事务只更新 `WorkflowRun.current_stage`，不提前提交内容、素材或审计行。新增工作区级 `GET /runs`，Web 在真实活动期间 2.5 秒轮询，显示平台、阶段名称、序号、说明、转圈和单调递增的真实阶段映射进度；未知时长素材任务只显示不确定进度，不伪造 ETA。
- 涉及文件：`contentflow/content_agent.py`、`contentflow/workflow_service.py`、`contentflow/routers/runs.py`、`web/app/contentflow-app.tsx`、`web/app/globals.css`、`tests/test_content_agent.py`、`tests/test_api_v2.py` 及设计/架构/手册。
- 当前验证：Content Agent 阶段顺序单元测试通过；工作区运行查询与 Job 上下文 API 回归通过；失败 AI provenance 和完整 Worker 流程在隔离 `hash/local/无 Prompt 门禁` 测试配置下通过，证明短事务没有破坏失败恢复或最终事务提交。本地完整回归为 `234 passed, 7 skipped, 145 subtests passed`，前端 ESLint、`tsc --noEmit --incremental false`、2 项源码/SSR 测试和生产 Next 构建通过；Compose 新容器 readiness/database/storage 与启动日志正常。
- 剩余边界：百分比表示已进入的离散阶段，不是模型内部 Token 进度；Worker 进程被强杀时仍依赖租约/超时与任务队列恢复，不保证阶段持续更新。

### CF-20260830-02：“准备素材”混合系统工作和人工工作

- 状态：实现、静态验证、生产构建和公开登录页响应式验收完成；Docker 重建使既有会话过期，认证后素材页主观验收待用户重新登录后完成。
- 问题与影响：旧素材页直接呈现上传表单、候选图和全量表格，用户不知道哪些任务系统会自动完成、何时必须介入、为何上传以及应上传什么。
- 根因：页面按数据类型组织，没有按责任主体和下一步组织；人工素材占位的业务原因只存在于后端状态与元数据中。
- 解决方案：素材页固定为“系统处理中 / 等你操作 / 已就绪”三段，分别给出数量和责任说明；系统生成/检索带自动刷新与不确定进度；人工待办解释真实品牌素材不可凭空获得或活动主动选择人工模式，并把按钮定位到已绑定项目/当前版本的上传表单。表单解释为什么、上传什么、完成后怎样，显示接受类型和授权提示；候选图继续要求原始许可页面核验。
- 涉及文件：`web/app/contentflow-app.tsx`、`web/app/globals.css`、`web/DESIGN.md`、`docs/user_manual.md`、`docs/architecture.md`。
- 剩余边界：界面能说明技术与流程责任，不能判断用户是否真正拥有素材版权，也不能替代品牌视觉审核、恶意文件扫描和普通视频内容探测。

### CF-20260830-03：相似活动跨页面和队列容易混淆

- 状态：实现、API 回归、前端静态验证、生产构建、提交与远程 CI 完成。
- 问题与影响：审核、素材、发布、复盘和任务队列主要显示内容标题或任务类型，多个名称相近的测试活动很难区分，误操作风险随并行项目增加。
- 根因：Job 响应没有非敏感业务归属；前端没有稳定项目展示编号和全局作用域过滤。
- 解决方案：每个 Campaign 由既有 UUID 派生稳定显示编号 `CF-XXXXXX`，不新增可冲突业务 ID；顶部项目筛选统一约束活动、运行、内容、素材、发布和任务，总览按过滤后的实体重算。复盘请求使用 `campaign_id`，后端经 `MetricSnapshot → PublishJob → ContentItem` 关联并重复施加工作区边界，避免只在浏览器过滤造成数据误读或跨工作区引用。审核、素材、发布、复盘与 Job 同时显示编号、活动、产品和内容标题。Job API 先批量解析运行/素材/发布引用，再按工作区查询关联对象并只返回 `JobContextResponse`；原始 payload 继续不对前端暴露，避免 N+1 与敏感字段泄漏。
- 涉及文件：`contentflow/routers/jobs.py`、`contentflow/routers/metrics.py`、`contentflow/schemas.py`、`web/app/contentflow-app.tsx`、`web/app/globals.css`、`tests/test_api_v2.py` 及文档。
- 当前验证：API 测试断言最新 workflow Job 返回正确 campaign/product context 且不含 `payload_json`，并验证项目指标筛选只汇总关联 Campaign；前端 TypeScript/ESLint 通过。公开登录页在桌面 1674px 和移动 375px 视口均无横向溢出，控制台无 warning/error；既有登录会话在容器重建后过期，因此未伪造认证后点击证据。未知未跟踪知识文件、本地 `.env`、账号凭据、模型缓存和运行数据库均未读取或暂存。
- 剩余边界：`CF-XXXXXX` 是便于人工辨识的短展示码，不是数据库唯一键或外部 API 标识；极低概率前缀碰撞仍由完整 UUID 与项目名称共同消歧。

### 本阶段远程交付证据

- 实现提交 `1f94450d7fca8be8059bf2d05ab2621f4da8ea35` 已通过普通 `git push` 同步到 `codex/enterprise-media-runtime`，作者为 `John Wang <182348029+heee000@users.noreply.github.com>`，未使用 force 或 force-with-lease。
- [ContentFlow CI #33313099365](https://github.com/heee000/ContentFlow/actions/runs/33313099365) 四个 Job 全部成功：PostgreSQL/pgvector 与 MinIO 后端和安全门禁、前端 lint/test/生产构建与依赖审计、可复现源码/SBOM，以及签名 SLSA 来源证明和 Python/前端 CycloneDX attestation 均已签收。
- CI 后端结果为 `241 passed, 145 subtests passed`，分支覆盖率 82.07%。供应链 Artifact `9732605974` 名为 `contentflow-supply-chain-1f94450d7fca8be8059bf2d05ab2621f4da8ea35`，摘要为 `sha256:f6112e8429e00c891c5b2d73e8ea87445df848e7d2317252d2088f002a5f72bb`。

## 2026-08-30 封面来源显式选择与单条任务改线阶段

### CF-20260830-04：封面来源藏在 Brief 下拉框，素材任务一旦建立就像被强制人工上传

- 状态：代码、接口定向回归、受真实 `.env` 影响用例的隔离复跑、前端 lint/渲染测试/生产构建、本地生产栈重建、提交和远程 CI/供应链签收已完成；登录后业务页验收仍等待用户完成本地登录。
- 问题与影响：新建活动把 `manual` 作为不醒目的界面默认值，用户可能没有作出选择就创建活动；内容审核后，单条封面任务没有改变来源的 API 和界面，`awaiting_upload` 只能显示上传按钮。因此底层虽然已有 manual/generate/search/hybrid 四种活动级能力，实际体验仍像强制人工上传。
- 解决方案：活动表单改为四张显式路线卡，不再替新活动预选来源；同时展示人工、AI、开放图库和混合路线的用途及当前环境能力，保存前必须选择。新增认证后的只读 `GET /assets/capabilities`，只返回可用性布尔值，不泄露 Provider、模型、端点或密钥。
- 单条改线：新增 editor 权限的 `POST /assets/{id}/source`，允许已审核、当前内容版本的图片在 `planned/failed/awaiting_upload/awaiting_selection` 间切换 manual/generate/search。接口在数据库行锁下校验工作区、内容状态、版本、素材类型与可用 Provider；运行中、已就绪、旧版本和混合候选拒绝改写。切换会清理旧图库候选/许可状态/外部任务引用，递增 `source_revision`，使用包含版本与 revision 的队列幂等键，并记录 `asset.source_change` 审计。
- 界面：素材中心把“选择路线”置于单条封面待办上，人工上传是三种选择之一；AI 未配置时入口仍可见但明确禁用并解释所需配置，开放图库可直接入检索队列。当前路线、最近错误、上传动作和图库许可核验动作分开呈现，避免把“为什么上传”和“只能上传”混为一谈。
- 验证：新增真实 API 回归覆盖能力探测、未配置 AI 失败关闭、人工→图库、处理中并发切换拒绝、图库候选→人工、候选清理、revision 和 Job 数量；素材定向回归 `5 passed`，Ruff、ESLint 与 Next.js 生产构建通过。真实 `.env` 会把 Prompt 门禁、S3、CORS 和 Provider 注入默认 Settings；受影响的四组使用仅进程有效的完整隔离配置复跑为 `58 passed, 32 subtests passed`。未改写 `.env`。
- 运行边界：本地 `image_provider=manual`，所以不能宣称真实 AI 图片生成已接通；人工上传和 Openverse 可用。要启用真实 AI 卡片，仍需供应商中立 ContentFlow Media v1 HTTP 图片生成端点、密钥、模型名和精确下载域名白名单。切换图库仍要求用户核验原始许可页面。
- 现场：`contentflow-live-test` 保留 PostgreSQL/MinIO 数据卷重建 API/Worker/Web，readiness 为 database/storage `ok`、Web HTTP 200。浏览器已打开新的本地登录页；登录后创建一条真实内容并停在待审核队列的证据待补。
- 远程证据：实现提交 `0b3d015d84c3ea74108a4ccd10d50aa1fda39695` 已用 John Wang 身份普通推送；[ContentFlow CI #33315195769](https://github.com/heee000/ContentFlow/actions/runs/33315195769) 四个 Job 全部成功。PostgreSQL/pgvector 与 MinIO 后端为 `242 passed, 145 subtests passed`，总覆盖率 82.04%；前端 lint/test/build/audit、Python 漏洞审计、可复现源码、SLSA 与双 CycloneDX attestation 均通过。Artifact `9733221112` 名为 `contentflow-supply-chain-0b3d015d84c3ea74108a4ccd10d50aa1fda39695`，摘要 `sha256:21467c243812afc956bb2f27ee0c8498fed740d984e77c9ee6b822481e9e94e3`。

## 2026-08-31 公网测试部署规划阶段

### CF-20260831-01：本地换网会改变微信公众号白名单，公网运行平台职责不清

- 状态：完整实现路线已记录；部署资产、云资源和目标环境证据尚未实施，不能写成已上线。
- 问题与影响：本机 Worker 的出口由当前网络或代理决定，换网可能再次触发微信 40164；GitHub Pages、Vercel、GitHub Actions、固定 IP 云主机分别能承担什么没有形成明确决策，容易把静态托管或有时限的 Serverless Function 误当成长驻 Worker。
- 现场证据：2026-08-31 从宿主机与 `contentflow-live-test` Worker 容器测得相同公网出口 `18.183.44.57`，证明当前 Docker Worker 跟随宿主出口，而不是每个 Worker 随机获得公网 IP。该地址只代表当时网络，不是长期固定资源。
- 解决方案：新增[公网测试部署实现计划](public_test_deployment_plan.md)。初版先确定固定公网 IPv4、Caddy 同源 HTTPS、GitHub CI/GHCR 和不把 Worker 放入 Serverless 的边界；随后 CF-20260831-02 用资源/价格实测把运行组合进一步收敛为 Hetzner + 本地 BGE + R2。计划分 M0-M6 规定部署资产、镜像门禁、云环境、真实业务验收、备份回滚和可选 Vercel 拆分。
- 安全边界：首次公网环境默认新建干净数据、关闭公开注册、只开放 80/443、数据库和对象存储不映射公网；微信公众号保持 `auto_publish=false`，先验收渠道和草稿。真实密钥、平台账号、本地 `.env`、运行数据库及未跟踪知识文件继续排除在 Git 外。
- 验收门槛：只有 M1-M5 在目标环境完成，同一固定 Worker IP 经两个客户端网络验证、完整内容到微信草稿闭环、联合恢复与镜像回滚各有证据后，才能标记“个人受控公网测试可用”。本次只改变计划和记录，不调整个人公开部署 60%-65% 的完成度估计。

### CF-20260831-02：Embedding 与公网资源方案缺少成本实测和明确选型

- 状态：性价比路线已定案并写入计划；云资源、R2 和目标环境压力/兼容性证据仍待实施。
- 实测：运行 5 小时后的 `contentflow-live-test` Worker 已加载 BGE-M3，RSS 约 899 MiB；API 约 138 MiB、Web 约 34 MiB、PostgreSQL 约 66 MiB、MinIO 约 247 MiB，容器合计约 1.38 GiB。BGE 缓存 2.2 GiB，后端镜像约 2.47 GB。采样证明 4 GiB 低并发试运行具有现实余量，但不替代索引峰值、OOM、swap 和耐久验收。
- 决策：首选 Hetzner 欧洲区 CX23 x86（2 vCPU/4 GiB/40 GiB）+ €0.50 Primary IPv4，Worker 继续本地 BGE-M3；移除公网栈 MinIO，使用 Cloudflare R2 业务 Bucket 和独立加密备份 Bucket；Web/API/Worker/PostgreSQL/Caddy 同机。按 2026-06-15 后官方价格，主机加 IPv4 约 €5.99/月，未含税、域名和文本模型用量。
- Embedding 边界：不因为“云部署”就自动改 API。Hetzner 最低推荐档已经是 4 GiB，本地 BGE 不增加套餐费用，且保留当前中文向量行为与数据边界。API 仅在 2 GiB 后备主机、持续 OOM、多 Worker 或真实检索评测更优时启用；优先验证 $0.02/百万 Token 的 `text-embedding-3-small` 1024 维路径。
- 代码缺口：当前 OpenAI-compatible Embedding 和文本生成共用 `MODEL_API_BASE/KEY`。进入 API 后备前必须增加独立、供应商中立的 Embedding Base/Key，并为模型切换建立全量重索引/索引代际，禁止混合不同模型向量。
- 存储边界：R2 官方声明 S3 兼容且 Standard 免费额度为 10 GB-month、100 万 Class A、1000 万 Class B和免费出口，但 ContentFlow 仍需真实验证 HeadBucket、上传/Metadata、读取、删除、分段和上限；通过前不能写成 R2 已签收。

### CF-20260831-03：公网计划只有文档，缺少可执行交付面

- 状态：仓库内 M1 和 M2 主体已实现并完成本地静态/定向验证；云主机、DNS、真实 R2、GHCR 构建、SSH 部署、微信固定出口和灾备演练仍待外部环境签收，不能写成已上线。
- 问题与影响：本地 Compose 会发布 PostgreSQL/API/Web/MinIO 端口并在服务器现场 build，不适合直接公网运行；文本与 Embedding 只能共用一个 Base/Key；固定 BGE 缓存、R2 操作矩阵、加密异地备份、不可变镜像和手工批准部署都只有计划，没有可执行入口。
- 公网栈：新增 `deploy/public-test`，仅 Caddy 发布 80/443，同源代理 Web/API/health，不公开 metrics；API/Worker 强制相同 digest，PostgreSQL/Caddy/restic 固定第三方 digest，移除 MinIO，设置健康检查、日志轮转、资源上限、非提权和明确命名卷。静态校验器通过 Compose JSON 拒绝端口、现场 build、弱运行模式、HTTP Provider、通配 CORS、非固定镜像和 release SHA 缺失；CI 新增同一门禁。
- 模型与缓存：增加独立 `CONTENTFLOW_EMBEDDING_API_BASE/KEY`，未设置时向后兼容 `MODEL_API_BASE/KEY`；两条路径都继续执行生产 HTTPS 校验。新增固定 BGE revision 缓存 prepare/verify 命令，写入模型/revision/1024 维 manifest，并用 offline 加载和归一化向量复验；API health 返回 release SHA 便于对照部署制品。
- 存储与灾备：新增只操作唯一随机前缀的 S3 conformance，覆盖 HeadBucket、单段、9 MiB multipart、100 MiB 上限、SHA-256 Metadata、读取和精确删除。公网备份使用独立 R2 Bucket/Token 和 restic 客户端加密；dump 先经 `pg_restore --list`，再抽样检查与 7 日/4 周 retention；验证只创建随机临时数据库。
- 账号安全：公网注册保持硬关闭，没有引入临时开关。新增交互式 offline bootstrap CLI，密码只由 TTY getpass 读取；首个工作区只允许空库创建，第二管理员必须指定 workspace slug、拒绝已有邮箱，PostgreSQL advisory lock 串行化并写系统审计。
- 供应链与部署：新增手工 image workflow，要求同 SHA 既有成功 CI，构建/推送 GHCR amd64 镜像，记录 OCI provenance/SBOM、Trivy 可修复 Critical 门禁和 digest Artifact。部署 workflow 只能从指定成功 build run 下载坐标，通过受保护 Environment 和预置 SSH known_hosts 部署；远端先验配置/磁盘/BGE/备份、再迁移、readiness 与 Worker heartbeat，失败不更新 current symlink，也不宣称数据库迁移可自动回滚。
- 本地验证：Ruff 全仓通过；隔离真实 `.env` 后后端 `245 passed, 7 skipped, 145 subtests passed`，分支覆盖率 80.96%；11 个 YAML、最终公网 Compose 和 4 个 POSIX shell 脚本语法通过。前端首次用不满足 engines 的 Node 22.11.0 时 vinext 暴露 npm 可选原生绑定缺失，未删除锁文件或强制修复；改用满足要求的随附 Node 24.19.0 从同一 `package-lock.json` 执行 `npm ci` 后，ESLint、2 项渲染测试、HTTPS Next 生产构建和 moderate audit 全部通过，0 vulnerability。
- 剩余边界：BuildKit attestation 尚不是独立签名与部署时验签；R2、restic、Caddy TLS、GHCR 和 SSH workflow 未在目标主机执行；Prometheus/Grafana 暂未纳入公网栈；单机 PostgreSQL/BGE 的峰值/OOM/磁盘仍需真实耐久证据。

## 2026-09-02 审计完整性与持续复审阶段

### CF-20260902-01：审计记录只能查询，数据库误改后产品无法发现

- 状态：实现、本地专项/全量验证、真实 PostgreSQL 并发追加和 GitHub CI/供应链签收完成。
- 问题与影响：`audit_logs` 过去只有脱敏与 API 只读，没有记录间完整性关系。数据库误改、漏行、错误恢复或越权写入后，管理工作台仍会把被改变的记录当成正常证据，审批、发布、凭据和 Prompt 治理的追责链不足。
- 根因：审计模型没有稳定序号、前序摘要、记录摘要和独立链头；并发 API/Worker 也没有同一工作区审计追加的数据库串行化协议。
- 解决方案：Alembic head 升级为 `6d4e8f9a0b1c`，新增 `audit_chain_heads`，并为 `audit_logs` 增加 scope、sequence、previous hash、entry hash 与 integrity version。迁移按 `created_at + id` 确定性回填旧记录；新写入对脱敏后规范载荷计算 SHA-256，PostgreSQL 使用基于 scope 的事务 advisory lock 与链头行锁串行追加。唯一约束、正序约束、哈希长度和版本约束在数据库层失败关闭。
- 产品入口：新增管理员 `GET /api/v1/admin/audit-integrity`，逐条重算并区分序号缺口、前序不匹配、载荷/记录哈希异常和链头不一致。管理页进入时核验一次，显示链头与异常序号，并允许人工重新核验；没有把全量核验加入 2.5/15 秒全局轮询。
- 涉及文件：`contentflow/audit.py`、`entities.py`、`routers/admin.py`、`schemas.py`、迁移 `6d4e8f9a0b1c`、管理工作台、迁移/接口/PostgreSQL 测试、架构/运维/生产清单与备份门槛。
- 本地验证：全仓 Ruff、迁移单 head、锁文件与公网部署 fail-closed 校验通过；审计/迁移/安全专项 `53 passed, 32 subtests passed`，新增接管/半迁移测试后相关专项 `17 passed`，部署恢复契约 `2 passed`；最终全量后端 `254 passed, 8 skipped, 145 subtests passed`。8 个跳过项只因本机 Docker/PostgreSQL/MinIO 未运行，不能冒充 PostgreSQL 并发签收。前端 ESLint、Sites/vinext 构建与 2 项渲染测试、Next.js/TypeScript 生产构建通过。
- 剩余边界：数据库管理员仍可同时重算记录和链头；删除整条尾部后若同步改链头也无法由同库自证。当前能力是 tamper-evident，不是 WORM、可信时间戳、外部公证或管理员不可伪造。后续需周期性把链头签名/锚定到独立不可变存储或 SIEM，并建立告警和取证 Runbook。

### CF-20260902-02：客户端 Request ID 无边界会污染日志或触发数据库长度错误

- 状态：已实现并进入同一阶段验证。
- 问题与影响：API 过去直接接受任意 `X-Request-ID`，随后写入结构化日志和最长 64 字符的审计列；超长或非安全字符在 PostgreSQL 中可能使业务事务因审计写入失败，也会增加日志注入与高基数风险。
- 解决方案：只接受 1-64 位、以字母数字开头且后续仅含字母数字、点、下划线、冒号和连字符的 ID；其余值在进入 request state、日志和审计前替换为服务端 32 位 UUID，并把最终值回传响应头。
- 验证：回归覆盖合法 ID 原样保留和 65 字符输入被替换；全量测试与前端门禁继续通过。

### CF-20260902-03：灾备脚本未与新迁移和审计链表同步

- 状态：仓库门槛已同步；当前 head 的真实联合恢复仍待独立演练。
- 解决方案：本地备份/恢复默认 revision 更新为 `6d4e8f9a0b1c`，最低 public 表数更新为 28；公网隔离恢复不再只检查 revision 非空，而是要求精确等于当前 head。部署静态验证器从 `contentflow.migrate` 读取当前常量并校验恢复脚本文本，新增迁移时若忘记同步将让 CI 失败。PowerShell 语法、2 项恢复契约、部署静态验证器和差异检查通过。
- 剩余边界：脚本门槛正确不等于当前 28 表 PostgreSQL+对象联合恢复已经发生；PITR、异地不可变副本和 RPO/RTO 演练继续保持未签收。

### CF-20260902-04：远程 CI 暴露 Linux 导入差异与新披露前端依赖漏洞

- 状态：修复、本地定向验证和修复提交的远程 CI 复验完成。
- 发现方式：实现提交 `52811bb64560751b500aba7bdd529b8982710627` 的首次手工 CI `33648030752` 中，Python 收集阶段在 Linux 找不到未安装的 `scripts` 命名空间；前端 lint、测试和生产构建通过，但 `npm audit` 命中新披露的 Browserslist 高危公告。
- 根因：恢复契约测试直接导入仓库维护脚本，本地 Windows 测试路径偶然可见，正式 Python 包配置却只安装 `contentflow*`；前端锁文件仍固定 `browserslist 4.28.2`，而审计源要求高于 `4.28.6`。
- 解决方案：把恢复门槛校验移入正式安装的 `contentflow.migrate.validate_public_restore_contract`，部署脚本与测试共用同一实现，不把整个 `scripts/` 目录加入生产包；仅更新锁文件中的 Browserslist 及其浏览器数据库依赖到兼容安全版本 `4.28.8`。
- 验证：相关 Ruff 检查通过，迁移/审计/恢复契约 `19 passed`；`npm audit --audit-level=moderate` 为 `0 vulnerabilities`。本机沙箱首次重跑因系统临时目录 ACL 无法创建 SQLite 文件而失败，提升为普通宿主用户权限后同一测试集通过；该权限噪声没有作为代码失败处理。

### 本阶段远程交付证据

- 实现提交 `52811bb64560751b500aba7bdd529b8982710627` 和 CI 修复提交 `3fa5206c4af90ffec6a09e5d2e10474386f579fc` 均以 `John Wang <182348029+heee000@users.noreply.github.com>` 普通推送到 `codex/enterprise-media-runtime`，未使用 force 或 force-with-lease。
- [ContentFlow CI #33648933471](https://github.com/heee000/ContentFlow/actions/runs/33648933471) 四个 Job 全部成功：PostgreSQL/pgvector 与 MinIO 后端/安全门禁、前端 lint/test/生产构建/依赖审计、可复现源码/SBOM，以及签名 SLSA 来源证明和 Python/前端 CycloneDX attestation 均已签收。
- 后端结果为 `262 passed, 145 subtests passed`，总覆盖率 82.03%，包含真实 PostgreSQL 双线程同工作区审计追加测试。供应链 Artifact `9853954616` 名为 `contentflow-supply-chain-3fa5206c4af90ffec6a09e5d2e10474386f579fc`，摘要为 `sha256:fec0569d78774f692f9bffcd498f947c9f5ede8fbcce23419b2504519df9b9df`。

## 2026-09-03 有界列表与增量同步阶段

### CF-20260903-01：高增长运营集合会无界读取工作区全表

- 状态：实现、迁移和本地专项验证完成；远程 PostgreSQL/MinIO CI 待本阶段提交后签收。
- 问题与影响：活动、内容、素材、发布和知识等列表没有统一上限；Job 虽硬限制 200，但无法继续读取。数据增长后会放大数据库扫描、序列化、网络和浏览器内存，并让旧记录不可达。
- 根因：各 Router 独立实现 `order_by(...).all()`，没有共享页长、稳定次序、游标校验和响应元数据契约。
- 解决方案：新增 `contentflow.pagination`。七类运营集合默认 100、最大 200（Run 保持既有最大 100），按 `updated_at DESC, id DESC` 做 keyset；游标是版本化、严格结构、URL-safe 的不透明载荷，畸形/超长/无时区增量输入返回 422。响应数组不变，以 `X-ContentFlow-Next-Cursor`、`X-ContentFlow-Page-Limit` 和 `X-ContentFlow-Sync-Time` 承载控制信息，并由 CORS 显式暴露。
- 数据库：迁移 `7e5f9a0b1c2d` 为 7 张表增加 `(workspace_id, updated_at, id)` 组合索引；本地/公网备份默认 head 同步。企业大表升级仍需维护窗口测量普通建索引的锁等待和空间，不能把空库迁移结果当作在线 DDL 证明。
- 验证：覆盖相同更新时间的稳定翻页、无重复/遗漏、跨工作区隔离、游标篡改、页长、带/不带时区增量输入、索引列序和空库升降级。

### CF-20260903-02：工作台高频轮询重复读取静态控制面

- 状态：前端实现和本地 lint/生产构建/SSR 测试完成，浏览器真实网络预算待后续 E2E。
- 问题与影响：登录后一次并行请求约 17 组数据，随后每 15 秒完整重放；生成/素材处理中每 2.5 秒重放。成员、渠道、知识、Prompt 和审计等低频数据也被反复读取，标签页隐藏后仍消耗资源。
- 解决方案：初次加载通过有界追页最多读取 2000 条并在截断时显式告知；后台只增量读取 Dashboard、Campaign、Run、Content、Asset、Publish、Job 和 Metrics。隐藏标签页暂停，重入轮询拒绝；按 ID 合并并稳定重排。同步水位来自服务端而非浏览器时钟，保留 2 秒重叠；若 10 页/1000 条仍未读完则不推进水位并要求手动全量刷新。
- 兼容边界：没有删除语义的集合可通过增量合并收敛更新；跨页读取不是事务快照，但扫描期间的新写入会在下一轮重叠同步补回。当前不是 WebSocket/SSE，也没有超过 2000 条的专用历史浏览器。

### CF-20260903-03：本地真实环境变量会污染隔离 API 回归

- 状态：测试确定性修正完成。
- 问题与影响：`test_api_v2` 只覆盖少量 Settings，本机真实 Prompt 门禁、S3 和 CORS 环境会把隔离 SQLite 测试改成连接 MinIO或拒绝本地 Origin，产生与代码无关的假失败并浪费诊断时间。
- 解决方案：测试显式固定 development/local/hash/mock、关闭治理/指标并声明本地 CORS；没有读取或修改真实 `.env`。同一 API 测试恢复为离线确定性执行。

### CF-20260903-04：持续复审新增未关闭边界

- 低频/父级集合尚未统一游标：审计日志只有上限，成员、工作区、风格 Skill、内容修订、发布证据和部分 Prompt/Eval 列表仍可能增长。
- `contentflow-app.tsx` 仍是大型单文件；活动期 8 路增量请求、2000 条客户端上限和无虚拟列表仍需 query hooks、历史浏览、SSE/Inbox、E2E 请求预算和负载证据。
- FORCE RLS/数据库角色拆分需要改变 owner/API/Worker 权限，存在业务锁死风险。安全审查要求明确高影响迁移授权，本阶段未应用、未绕过，也没有 RLS 半成品文件。

### 本阶段本地交付证据

- 进程级隔离真实 `.env` 后，全量后端 `258 passed, 8 skipped, 152 subtests passed`，分支覆盖率 81.15%；8 项跳过为本机未启动的 PostgreSQL/MinIO 外部服务，不能冒充真实数据库/对象存储签收。
- Ruff 全仓、`uv lock --check`、Alembic 单 head `7e5f9a0b1c2d`、PowerShell 备份脚本语法、公网部署 fail-closed 校验与 `git diff --check` 通过。
- 前端 ESLint、Sites/vinext 构建和 2 项渲染测试、Next.js/TypeScript 生产构建通过；`npm audit --audit-level=moderate` 为 0 vulnerabilities。
- 远程 Linux + PostgreSQL/pgvector + MinIO、供应链与 attestation 仍必须绑定本阶段提交重新运行；未成功前不把本阶段标为远程签收。

### CF-20260903-05：远程审计在本地验证后新命中 fast-uri 高危公告

- 状态：兼容范围内的锁文件修复和本地前端复验完成；修复提交的远程 CI 仍需重新签收。
- 发现方式：实现提交 `f4172f20b1edd45f7d63848113223161bc7ccfc4` 的首次手工 CI `33655246050` 中，后端 PostgreSQL/pgvector、MinIO、安全门禁与供应链证据任务成功，前端 lint、渲染测试和生产构建也成功；最后的 `npm audit --audit-level=moderate` 新命中 `fast-uri 3.0.0 - 3.1.5` 的四条高危主机混淆/SSRF 公告，因此整次运行正确失败，来源证明任务被跳过。
- 根因：`ajv 8.20.0` 通过 `^3.0.1` 引入 `fast-uri`，锁文件固定在刚被公告覆盖的 `3.1.5`。这不是业务代码缺陷或 CI 网络故障，也不应通过降低审计级别规避。
- 解决方案：仅把传递依赖锁定结果从 `fast-uri 3.1.5` 更新到同一兼容主版本的 `3.1.7`；不新增直接依赖、不跨主版本、不运行 `npm audit fix --force`，也不修改审计门槛。
- 本地验证：更新后 `npm audit --audit-level=moderate` 为 `0 vulnerabilities`。用满足项目 engines 的随附 Node `24.19.0` 从锁文件重建依赖后，ESLint、Vinext/Sites 构建、2 项服务端渲染测试和 Next.js 16.2.12/TypeScript 生产构建通过。本机默认 Node `22.11.0` 低于项目要求的 `22.13.0`，会跳过 Rolldown Windows 可选绑定；该运行时噪声没有通过改锁文件掩盖。
- 证据边界：失败运行 `33655246050` 只作为问题发现和后端集成通过证据，不能当作本阶段最终签收；必须以包含 `3.1.7` 锁文件的后续提交重新跑完四个 Job，才能记录成功 Run、Artifact 和 attestation。

### CF-20260903-06：Prometheus 规则单测跨组求值顺序未声明

- 状态：确定性修复已实现；固定 Prometheus 镜像的远程 promtool 复验待提交后签收。
- 发现方式：安全修复提交 `08f233d71d760e0b17a9dea5e2b31553ae90ca5f` 的 CI `33656868446` 中，前端全部门禁和 SBOM 成功；后端的 Prometheus 配置与 13 条规则语法成功，但 `ContentFlowHighHTTP5xxRate` 在第 13 分钟偶发未触发，后续 PostgreSQL/MinIO 测试因此被失败关闭。
- 根因：测试同时包含 `contentflow-recording` 与 `contentflow-alerts` 两个规则组，后者消费前者生成的记录指标，但测试文件没有声明同一求值时刻的组顺序。此前把断言从 12 分钟移到 13 分钟只扩大了余量，没有消除跨组顺序不确定性。
- 解决方案：在 Prometheus 单测顶层增加 `group_eval_order`，明确 `contentflow-recording` 先于 `contentflow-alerts`。不改告警表达式、持续时间、阈值或生产配置，不用放宽期望掩盖问题。
- 证据边界：本机 Docker 服务未运行，无法用固定容器镜像本地执行 promtool；YAML 改动必须由远程固定 digest 的 Prometheus `v3.13.1-distroless` 验证。失败运行 `33656868446` 只作为前端安全修复成功和规则时序缺口的发现证据，不是完整签收。
