# ContentFlow Media Contract v1

ContentFlow 通过供应商中立的 HTTP 契约连接图片和视频生成服务。机器可读定义见 [`contracts/contentflow-media-v1.openapi.yml`](contracts/contentflow-media-v1.openapi.yml)。仓库不预设服务商、模型名称或专用字段；部署方可以在独立适配服务中把本契约转换为任意内部或外部模型接口。

## v1 强制语义

- 每个请求必须携带 `ContentFlow-Media-Version: 1`；每个成功或错误响应都必须回显同一版本。缺失或不兼容版本会被 ContentFlow 视为永久协议失败。
- 两个生成接口必须携带 8–128 字节可打印 ASCII `Idempotency-Key`，且首尾不得为空白。该值是不可被服务端修剪或重写的不透明键：同一键与同一规范化请求必须返回第一次生成的任务或结果，不得重复计费；同一键与不同请求必须返回 `409`。服务端至少保留 24 小时幂等记录。
- ContentFlow 为资产 ID、工作区 ID、素材类型和内容版本计算 SHA-256 不透明键。同一任务重试使用同一键，内容版本变化会产生新键；这些内部标识不会直接发送给媒体服务。
- 请求只发送 `model`、`prompt`、`size` 和白名单 `parameters`。数据库元数据、工作区标识、内容版本和审计信息不会透传。
- `408`、`425`、`429` 和 `5xx` 属于可重试错误；其他 `4xx`、协议版本错误、无效 JSON 或不合规响应属于永久错误。版本不兼容返回 `400` 与 `error.code=contract_version_unsupported`，同键异请求返回 `409` 与 `error.code=idempotency_conflict`。`Retry-After` 使用整数秒，Worker 最多接受 300 秒。
- 错误响应不得包含密钥、堆栈或内部服务详情。ContentFlow 也不会把 Provider 原始错误体写入任务错误或日志。
- 正式适配器对成功与错误 JSON 都采用有界流式读取：错误体最多 64 KiB，成功体默认最多 32 MiB 且不得超过内联素材派生上限。超过 32 MiB 的素材应返回受 allowlist 保护的下载 URL；响应必须符合 OpenAPI 封闭信封，未知字段、重试语义冲突或状态载荷歧义均永久失败。
- 下载 URL 只能使用 HTTP(S)，不得携带 URL 凭据，初始地址与每次重定向都必须命中部署方的精确域名允许列表；内联和下载内容都受上传大小上限约束。

## 端点

- `POST /images/generations`：同步返回单张图片的受限 base64 或下载 URL。
- `POST /videos/generations`：同步返回视频下载 URL，或以 `202` 返回可轮询任务。
- `GET /videos/generations/{task_id}`：返回异步任务当前状态；任务 ID 进入路径前会编码。

视频活动态为 `queued/pending/processing/running`，成功态为 `ready/completed/succeeded`，失败终态为 `failed/cancelled/expired`。成功终态必须且只能包含一个下载地址，失败终态必须包含稳定、脱敏的 `ErrorDetail`。

`video_storyboard` 是 ContentFlow 内部资产类型，适配器会把它提交到 v1 视频生成端点，避免真实 Provider 模式下误判为不支持的素材类型。

## Live conformance 验收

仓库命令 `contentflow-media-conformance` 会对显式配置的目标服务执行受控真实调用。每种素材只创建一个逻辑生成；其余请求复用同一幂等键，验证同请求重放、同键异请求冲突、旧版本拒绝、缺少鉴权拒绝，以及异步视频轮询。运行必须显式传入 `--confirm-live-generation`，因为图片和视频创建可能计费。

报告文件会在网络请求前以独占方式预留，已存在路径不会覆盖。报告 Schema v2 只记录状态、耗时、次数与运行级指纹；指纹算法显式标为 `hmac-sha256-96-run-scoped`，密钥由每次运行的系统随机数生成且不写入报告，因此只能在同一轮内比较重放结果，不能跨报告关联或枚举低熵端点/错误码。序列化前会同时扫描原始值及其 JSON 转义形式，拒绝写入 API Key、Base URL、模型名、Prompt、幂等键、请求/任务 ID、远端错误消息、媒体 URL、base64 或原始响应。目标配置来自当前进程的 `CONTENTFLOW_*` 环境变量；生产目标必须使用 HTTPS，下载 allowlist 只能包含精确主机名。

该工具能自动验证正常生成、幂等、版本、鉴权和基本状态机，不能凭空触发目标服务的限流、超时、内容审核、凭据过期或下载 URL 过期。上述场景仍需目标服务提供受控测试钩子或在隔离环境执行专项矩阵。报告存在也不等于质量、成本或不重复计费已经由账单侧签收。

## 当前边界

v1 采用轮询收敛，声明任务失败/取消/过期终态，但没有提供取消操作和 Webhook。能力发现、取消端点、签名 Webhook、Inbox 去重、任务续期与服务端兼容窗口属于后续兼容扩展；在这些能力形成端到端实现和测试前，不得把它们描述为已交付。