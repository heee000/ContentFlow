# 素材异步结果与内容版本契约

2026-09-24，A51/A52 本地候选。实现状态及确切测试结果见 [实施进度](IMPLEMENTATION_PROGRESS.md)；本文不是实机部署或 PostgreSQL/MinIO 签收证明。

## 需要同时成立的两个条件

1. Worker 仍拥有当前 Job 的领取令牌及有效租约，参见 [执行权契约](worker_execution_contract.md)。
2. 外部操作对应的素材来源、输入、异步任务身份及内容版本仍然有效。

拥有 Job 执行权不意味着可以覆盖用户在生成期间作出的编辑。远程返回成功也不意味着结果已被当前内容采用。

## 素材操作快照与写入边界

`asset_work.AssetWork` 在生成、轮询、图库搜索及下载前捕获资产 ID、工作区和输入身份：关联内容及版本、类型、来源、prompt、外部 task id、原 storage URI、状态与输入元数据。`selected` 单独排除：用户选择另一个混合候选，不应取消本候选生成，也不能被旧 metadata 覆盖；写入合并时保留最新选择。

对已有内容的素材，还必须核对父内容仍为 approved 且版本相同。单独的素材测试/内部记录可无父内容，不将缺父内容关联的测试资产强行伪造为正式稿件。

- 外部调用前、生成/轮询返回后、下载后用无写锁重读避免不必要的后续 I/O，不信任 ORM identity map。
- 最后关联文件、记录 processing/poll Job 或保存搜索候选前，先核对执行权，再按父内容→素材的顺序取行锁并重读；校验与业务提交在同一事务内。
- 不持有内容/素材写锁跨 Provider、下载或对象 PUT。SQLite 的 Worker 写边界由执行权条件写取得数据库写锁；PostgreSQL 使用行锁并检查锁等待后的最新状态。
- 不用起始时内存中的 `asset.status = generating` 覆盖用户更新。现有队列 running 状态仍是正在执行的可见证据。

## A51 的对象写入协议

Worker 对象写入采用短事务先记录计划 URI、checksum、大小、来源 Job/领取令牌和 `staging`，并计入已用配额，然后释放事务做 PUT。返回对象必须与计划身份相同；业务事务激活分配并关联素材。业务失败或 A52 判定过期时，Worker 回滚该次业务事务，已经持久化的 staging 保留。

| 情况 | 业务结果 | 对象/账本 | 后续动作 |
| --- | --- | --- | --- |
| 远程返回前用户已改稿 | 不改旧素材，不启动多余下载/PUT/轮询 | 如尚无上传，不新增对象分配 | 返回已被更新取代的结果，不自动重发 |
| PUT 期间用户改稿 | 不关联文件、不激活 allocation | 已上传文件保留为 charged staging | 管理员在确认无在途写入后按既有回收入口处理 |
| 写入后审计/业务事务失败 | 不提交资产与 active 分配 | staging/对象仍保留 | 保留失败或未知核对，不自动删除证据 |
| Worker 已失权 | 不允许旧 Worker 完成/失败/清理 | 保留独立调用证据和暂存写入 | 由后续有权的恢复或人工核对处理 |
| 当前版本最终正常提交 | ready/processing 等业务状态与本次版本一致 | active 与业务引用原子提交 | 正常审核/发布门禁仍生效 |

staging 不等于孤儿，不因 TTL 到期自动删除或返还配额。管理员必须确认原来源任务已终态/人工核对、满足冷却、无业务引用且无在途写入；删除成功后才返还配额。网络 PUT 超时不代表没有写入；没有承诺跨数据库/对象服务 exactly-once。

## 被新编辑取代与未知失败必须区分

确定观察到素材/内容改变时抛出 `AssetWorkSuperseded`，由 Worker 在业务事务回滚后，重新核对执行权并保存 `asset.result_superseded` 审计与终态回执：

```json
{"asset_id":"...","status":"stale","outcome":"superseded","reason":"asset_or_content_changed"}
```

队列技术状态为 succeeded，含义是“旧工作已被确定终结”，不是“生成结果已采用”。网页显示“旧结果未采用”，说明有暂存对象时去存储管理核对，不提供自动重放操作。没有新增 Job 状态或数据库迁移。

若远程操作抛出错误、结果未知，仍走原失败/人工核对和调用账本协议；不能仅因为用户编辑就把未知执行伪装成成功取消。失败回写也必须检查素材快照与父内容，不把 stale、后来替换的 ready 或未经批准的新版本改成 failed。租约过期的维护路径没有原始内存快照，因此保守保留终态素材和不再有效的内容版本。

已开始的异步 Provider 任务不能由本地状态自动取消；HTTP 调用账本仍保存外部 task id 等回执供核对。不自动轮询已失去当前业务归属的结果，也不因此再次调用 generate。

## 测试及部署边界

- `tests/test_asset_work_concurrency.py`：实际 Worker + 独立线程真实改稿 API，生成/轮询/搜索/下载返回、失败、异步 processing、PUT 期间编辑、过期任务拒绝调用、同版本替换/task id/审批变化、保留候选选择；真实临时 SQLite 和本地对象。
- `tests/test_postgres_asset_work.py`：实际 Worker 的 Provider/存储阶段编辑，以及通过 `pg_stat_activity` 证明最终写回确实等待父内容行锁，再验证锁后重读。专用 PostgreSQL 未配置时跳过，不记为通过。
- `tests/test_postgres_integration.py`：已有下载与编辑同锁序测试保留，按新的事务回滚协议断言不关联旧结果。
- `tests/test_worker_storage_transactions.py`：慢 PUT/真实续租、失权、业务失败、竞争配额、对账及管理员回收。
- `web/tests/browser/jobs.spec.ts`：真实临时 API 返回合成 superseded 回执，生产页面显示正确说明与无重放按钮；不声称该浏览器用例执行了真实 Provider/Worker。

A52 不修复 A39 文件解码校验、A40 最终载荷审核或 A47 多平台检查点。组合候选需要迁移 f0a1b2c3d4e5、同版 API/Worker/Web、真实 PG/MinIO/恢复和部署验收；不混跑旧 Worker，不借旧 CI 绿色签收。

后续 A39/A40 实施另见 [媒体有效性契约](media_validity_contract.md) 和 [审核契约](content_review_contract.md#31-a40-发布字段复查2026-09-24)。生成/轮询结果在 PUT 前解码，发布读取重新检查实际字节；原 A52 并发、旧结果和 staging 规则继续生效。上段为 A52 阶段范围，不代表 A39/A40 当前仍无实现。
