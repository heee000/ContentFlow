# ContentFlow Media Contract v1

ContentFlow 通过供应商中立的 HTTP 契约连接图片和视频生成服务。机器可读定义见 [`contracts/contentflow-media-v1.openapi.yml`](contracts/contentflow-media-v1.openapi.yml)。仓库不预设服务商、模型名称或专用字段；部署方可以在独立适配服务中把本契约转换为任意内部或外部模型接口。

## v1 强制语义

- 每个请求必须携带 `ContentFlow-Media-Version: 1`；每个成功响应必须回显同一版本。缺失或不兼容版本会被 ContentFlow 视为永久失败。
- 两个生成接口必须携带 8–128 字节 ASCII `Idempotency-Key`。同一键与同一规范化请求必须返回第一次生成的任务或结果，不得重复计费；同一键与不同请求必须返回 `409`。服务端至少保留 24 小时幂等记录。
- ContentFlow 为资产 ID、工作区 ID、素材类型和内容版本计算 SHA-256 不透明键。同一任务重试使用同一键，内容版本变化会产生新键；这些内部标识不会直接发送给媒体服务。
- 请求只发送 `model`、`prompt`、`size` 和白名单 `parameters`。数据库元数据、工作区标识、内容版本和审计信息不会透传。
- `408`、`425`、`429` 和 `5xx` 属于可重试错误；其他 `4xx`、协议版本错误、无效 JSON 或不合规响应属于永久错误。`Retry-After` 使用整数秒，Worker 最多接受 300 秒。
- 错误响应不得包含密钥、堆栈或内部服务详情。ContentFlow 也不会把 Provider 原始错误体写入任务错误或日志。
- 下载 URL 只能使用 HTTP(S)，不得携带 URL 凭据，初始地址与每次重定向都必须命中部署方的精确域名允许列表；内联和下载内容都受上传大小上限约束。

## 端点

- `POST /images/generations`：同步返回单张图片的受限 base64 或下载 URL。
- `POST /videos/generations`：同步返回视频下载 URL，或以 `202` 返回可轮询任务。
- `GET /videos/generations/{task_id}`：返回异步任务当前状态；任务 ID 进入路径前会编码。

`video_storyboard` 是 ContentFlow 内部资产类型，适配器会把它提交到 v1 视频生成端点，避免真实 Provider 模式下误判为不支持的素材类型。

## 当前边界

v1 采用轮询收敛，没有声明取消和 Webhook。能力发现、取消、签名 Webhook、Inbox 去重、任务过期与服务端兼容窗口属于后续兼容扩展；在这些能力形成端到端实现和测试前，不得把它们描述为已交付。