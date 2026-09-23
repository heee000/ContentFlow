# 生成意图、回执与安全恢复契约

本批对应 A05，以及 A45 的空目标门禁。源码 SHA、测试和 CI 证据见 `IMPLEMENTATION_PROGRESS.md`；本文不代表已部署或整体整改完成。

## 接受一次生成

`POST /api/v1/campaigns/{campaign_id}/runs` 现在要求：

- `Idempotency-Key`：由调用方为一次明确的新生成创建，16–128 个 ASCII 字母、数字、下划线或连字符；推荐 UUID。它不是认证凭据。
- JSON `expected_campaign_updated_at`：从用户所确认的 Campaign 响应原样取得，不能用客户端当前时间代替。用于拒绝尚未接受但 Brief 已变化的旧请求；不是 A34 的完整活动编辑版本协议。
- 原有 `provider` 和 `regenerate_platforms` 仍可选。只接受已声明字段；重生成平台最多三个、不重复且必须属于当前活动。空列表表示活动全部平台，不表示生成零个平台。
- 原有 Cookie 上下文、Bearer、工作区及 editor 权限门禁继续生效。不能用操作编号访问别人的工作区。

示意请求（无真实凭据）：

```http
POST /api/v1/campaigns/<campaign-id>/runs
Idempotency-Key: <once-created-uuid>
Content-Type: application/json
X-ContentFlow-Context: <current-browser-context>

{"expected_campaign_updated_at":"<exact-updated_at-from-campaign-response>"}
```

服务端将协议版本、活动 ID 和规范化请求做摘要。预期时间统一为 UTC；其余请求参数不得在恢复时改写。以 `(workspace_id, request_id)` 为数据库主键保存接受回执，绑定唯一 Run 和原发起人；Run、回执、Job、审计同事务接受。

原样重放返回 **同一个 Run 的当前状态**，不重新排队、不再次记录生成接受审计，不重新执行供应商调用。即使活动后来被编辑/归档或 Prompt/style 状态改变，已接受回执仍先返回。相同编号用于不同活动或请求时 409，不覆盖旧任务。

只想查询、不希望继续尚未接受的请求时，可以调用 `GET /api/v1/generation-intents/{request_id}`：当前工作区有回执返回 200 和原 Run，没有则 404；不生成任何任务。404 只说明查询时尚未见到已提交回执，不能证明另一个仍在途的请求永远不会提交。生成记录可展开完整任务/操作编号，审计也记录操作编号；没有回执的旧批次明确标注，不能伪装成可重放的新意图。

没有已有回执时，服务端锁定活动、锁后重新读取并再查回执，然后检查时间前置条件和当前治理门禁。Brief 已变化返回 `409 / generation_precondition_failed`，不生成任务。PostgreSQL 竞争由锁与唯一约束共同处理；唯一冲突只丢弃本请求尚未接受的 Run，不整笔回滚调用者其他改动。

## 网页行为

1. 点击新生成前创建操作编号，把原始请求写入 sessionStorage 并读回确认；存储拒绝或写入失败就不发送。
2. 按 API base、用户和工作区隔离。网络错误、30 秒请求截止、认证变化、服务端错误或回执不匹配都保留原编号，不静默换号。
3. 重新进入页面或刷新后显示项目名、项目 ID 和操作编号。不会自动重发；用户可选择“核对或继续原生成请求”。若原请求已接受就只返回原任务；从未接受则只有原 Brief 仍匹配时才可能接受。
4. 确认匹配回执后清理本标签页记录；另一组件生命周期已清理同一回执时允许幂等清理，不能误删后来创建的另一个意图。
5. 只有服务端明确返回旧 Brief 前置条件失效时，才允许清除这份未接受请求并提示刷新重新确认。其他失败不等于已证明之前没有接受。

sessionStorage 是本标签页内的恢复保障，不是跨设备持久草稿。关闭标签页或清除浏览器数据可能丢失本地意图；应先按项目/操作编号核对服务器任务，不把重新点击当作安全重试。另一标签页主动创建不同编号属于不同意图，仍可能产生不同费用；A12 配额/预算尚未实现。

## 目标、事务和迁移边界

- A45：API 在入队前拒绝无效平台；Worker 对旧队列的实际 Brief/目标在构造 Provider、Embedding、策划前复核，不能规划后才得到空交集并冒称成功。
- 新表 `generation_intents`，迁移 `c7d8e9f0a1b2 → d8e9f0a1b2c3`；完整 schema 至少 35 表，备份校验脚本同步。旧 Run 不伪造新操作编号或回执，不凭内容相似就自动认领。
- 未版本化旧 schema 识别停在真实已具备的版本；不能把缺少新表的旧库直接 stamp 为新 head。存在新回执表时核对列、复合主键、唯一约束和外键后才采用。
- 本轮没有运行真实迁移。部署必须备份、暂停写入并协调 API/Web/Worker/schema；旧空 body/无操作编号的客户端现在会 422，不保留不安全的静默兼容。
- 回退迁移会删除接受回执、丧失旧编号识别能力。只在临时数据库自动测试 downgrade；生产回退必须保留备份、停止重试并显式处置未决请求，不能直接删表后继续接受旧请求。
- 此幂等边界是“接受一次业务意图”，不是供应商通用 exactly-once。它不完成 A07 失租隔离、A09 模型/Prompt 完整快照、A12 费用治理、A44 通用队列冲突修复或 A47 阶段检查点；结果未知的供应商调用仍按既有核对门禁处理。

## 验证入口

- `tests/test_generation_intents.py`：真实 API/临时 DB 的重放、冲突、时间门禁、工作区隔离、原子回滚、唯一冲突以及旧 Worker 目标拒绝。
- `tests/test_generation_migration.py`：旧数据保留、隔离回退、无版本新旧 schema 与不完整约束拒绝。
- `tests/test_postgres_generation.py`：五种真实行锁/唯一约束竞争；本机 skip 不算通过，必须同一源码 CI。
- `web/tests/generation-intents.test.mjs`：实际 TS 模块的保存、恢复、隔离、坏记录及清理安全。
- `web/tests/browser/generation.spec.ts`：真实页面/API 的丢回执、存储失败、旧 Brief、切区及离页回执乱序。临时服务不启动 Worker、不访问真实模型或平台。
