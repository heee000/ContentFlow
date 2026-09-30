# 外部调用资源限制（2026-10-01）

本阶段实现 A12 的调用准入与有界输入/输出，并修复 A35 的 HTTP Embedding 响应校验。它不提供金额预算，也不代表真实供应商、PostgreSQL、部署或容量验收已经完成。

## 工作区调用准入

所有经过 Provider 调用账本的文本、HTTP Embedding、媒体生成/轮询、图片搜索与下载共用以下服务端策略；默认值按工作区分别计数。API 与 Worker 必须配置相同值。目前没有按工作区定制政策的管理界面。

| 环境变量（均带 `CONTENTFLOW_` 前缀） | 默认值 | 含义 |
| --- | --- | --- |
| `WORKSPACE_PROVIDER_DAILY_CALLS` | 1000 | UTC 日内已预留的调用 attempt 数 |
| `WORKSPACE_PROVIDER_DAILY_INPUT_BYTES` | 67108864 | UTC 日内 attempt 对应的 canonical evidence 字节之和 |
| `WORKSPACE_PROVIDER_CONCURRENT_REQUESTS` | 4 | 账本中状态为 started 的本地请求数 |

单位是账本操作与证据输入字节，不是供应商 Token、账单金额、线上实际 HTTP 请求数或同时执行的远端视频任务数。例如一次下载操作可能含多次重定向；一次异步视频生成返回任务 ID 后释放本地请求占用，但远端任务可能继续运行。Mock、Hash、本地 Embedding 不作真实外部调用计费。

准入在外部调用之前进行：执行权检查、工作区锁、用量读取、attempt 插入和审计提交使用同一事务。SQLite 通过写锁串行化；PostgreSQL 使用工作区行的 no-op UPDATE 获取锁，保留工作区 updated_at。失败回滚不占用额度；午夜边界用同一个准入时间写入 attempt。复用已有账本，无新增数据库迁移。

只要 attempt 已提交，失败、结果未知、迟到结果和人工重试均按各自 attempt 计数，不退还当日额度。终态释放本地并发占用；孤立 started 需按现有调用核对/恢复流程处理，不能删除证据来腾额度。旧账本记录也参与当前 UTC 日统计。读取 `GET /api/v1/admin/provider-resources` 需要当前工作区管理员权限，返回 UTC 周期、用量、限制、剩余额度及 `monetary_budget=false`。

## 文本、Embedding 与知识索引

| 环境变量（均带 `CONTENTFLOW_` 前缀） | 默认值 |
| --- | --- |
| `MODEL_MAX_OUTPUT_TOKENS` | 8192 |
| `MODEL_OUTPUT_LIMIT_FIELD` | max_tokens |
| `MODEL_MAX_REQUEST_BYTES` | 262144 |
| `MODEL_MAX_RESPONSE_BYTES` | 4194304 |
| `EMBEDDING_API_BATCH_SIZE` | 32 |
| `EMBEDDING_MAX_TEXT_CHARS` | 8192 |
| `EMBEDDING_MAX_REQUEST_BYTES` | 262144 |
| `EMBEDDING_MAX_RESPONSE_BYTES` | 8388608 |
| `KNOWLEDGE_MAX_CHUNKS` | 2000 |

文本请求发送输出 Token 限制；若兼容端点要求 `max_completion_tokens`，显式选择该字段，不在失败后自动切换或重发。实际供应商是否遵守参数尚待真实受控验收。文本检查实际序列化请求字节，最多读取响应上限加一个字节后关闭。Embedding 限制批次、单条文本与序列化输入，以流式读取限制响应；要求 identity 响应编码，拒绝压缩响应后立即关闭，避免自动解压绕过字节限制。

预检超大输入发生在账本预留前，不发 HTTP、不消耗 attempt；响应超大已发出请求，保留未知结果及额度，不自动重发。Worker 返回固定错误码/处理指引，不持久化任意异常文本。资源限制拒绝终止自动重试：workflow/prompt_eval/HTTP 索引沿用人工核对，其他素材任务沿用失败及显式人工重试入口；响应超大仍要求先核对原调用结果。

Embedding 返回必须包含与输入数量一致的 data，index 为严格整数、恰好覆盖 0..N-1，再恢复输入顺序。向量维度必须匹配，元素必须是可表示的有限数字，拒绝 bool、字符串、重复/负数/越界 index、NaN/Infinity 与转换溢出。

知识索引读取最多 20 MiB，增量遍历段落并限制总分块数，按批次嵌入。所有批次成功后才替换原分块；后续批次失败保留旧索引，但已完成外部调用依然计入额度。本阶段没有保存可继续使用的 Embedding 批次检查点。营销 Brief 三种事实/规则数组各最多 32 项，每项 1–2048 字符，平台列表 1–3 项；尚未解决重复平台及并发版本更新问题。

示例 `.env.example`、普通/公网测试 Compose 的 API 与 Worker 已提供同一组配置入口；私测显式导出工具保留这些政策。没有读取/修改真实环境配置或启动部署。

## 验证及仍待完成

本地合成 SQLite 与 HTTP Mock 已覆盖最后一份额度的竞争、UTC/跨工作区隔离、审计回滚、未知结果计数、预检无调用、真实素材 Worker 的共享限额、管理员权限、响应关闭、严格向量与索引保留。真实 PostgreSQL 竞争用例已新增，未配置真实测试服务时跳过。阶段完整回归结果见 [实施进度](IMPLEMENTATION_PROGRESS.md) 最后一节。

A12 的金额定价、费用预留/结算、积压和公平调度、远端任务容量仍开放。A47 部分生成成果检查点、A09 队列冻结配置、A34 剩余校验以及其他独立整改继续开放；本阶段不是整体交付验收。
