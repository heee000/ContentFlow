# ContentFlow 项目交接文档

> 更新日期：2026-09-24（历史阶段记录按时间保留，当前断点见末节与 `docs/IMPLEMENTATION_PROGRESS.md`）
> 适用仓库：ContentFlow 仓库根目录
> GitHub：<https://github.com/heee000/ContentFlow>
> 当前工作分支：`codex/enterprise-media-runtime`
> 本轮接手基线：`34ffb3f4 Complete GitHub history attribution rewrite`

## 0. 给接手者的最短说明

ContentFlow 是一个把 AI 内容营销拆成“知识检索、内容生成、规则校验、人工审核、素材生成、发布和数据复盘”等可追踪步骤的全栈工作流系统。当前应维护的主链路是：

```text
Next.js 工作台
  -> FastAPI API
  -> SQLAlchemy 数据库
  -> Job 数据库任务队列
  -> 独立 Worker
  -> workflow_service / Provider / Connector
```

请不要把 `contentflow/cli.py`、`contentflow/workflow.py` 中保留的早期本地批处理原型，误当成当前 Web 产品的主链路。真正需要优先追踪的是：

```text
web/app/contentflow-app.tsx
  -> contentflow/routers/runs.py:create_run
  -> contentflow/job_queue.py:enqueue_job
  -> contentflow/worker.py:Worker.run_once
  -> contentflow/workflow_service.py:execute_workflow_run
  -> ContentItem / ContentRevision / Asset / WorkflowRun
```

接手后第一条命令应是 `git status --short`，因为当前工作区有尚未提交的用户修改，不能 reset、checkout 或覆盖。

## 1. 产品目标和设计原则

### 1.1 项目解决什么问题

ContentFlow 面向营销内容生产，把一份活动 Brief 和品牌/产品知识转成不同平台的内容及素材，并在发布前保留人工审核和版本控制。覆盖的业务链路是：

```text
活动 Brief
  -> 知识库检索
  -> 跨平台策划
  -> 小红书/抖音/公众号内容生成
  -> 确定性规则校验 + 模型风险审核
  -> 人工编辑与审批
  -> 图片/视频素材生成
  -> 排期与分发
  -> 指标回收或人工录入
```

### 1.2 核心设计原则

1. **概率性 AI 与确定性规则分离**：模型负责策划、文案、排版和脚本；禁用词、必含事实、CTA、长度、权限、版本一致性等由普通代码控制。
2. **外部副作用必须经过人工审核**：内容未被 reviewer 明确批准时，不生成正式素材，也不能进入发布。
3. **长任务异步化**：知识索引、生成、素材、连接测试、发布和指标拉取都进入数据库 Job 队列。
4. **状态可追踪**：生成批次、内容版本、素材状态、发布结果和审计日志都持久化。
5. **能力边界要诚实**：离线 Mock 不冒充真实模型；小红书无公开发布能力时只生成 ZIP 投放包；外部平台只有真正返回成功时才保存外部 ID。
6. **多租户隔离**：所有业务对象带 `workspace_id`，工作区来自服务端签名令牌，不允许客户端随意指定。

## 2. 当前技术栈

| 层级 | 当前实现 | 说明 |
|---|---|---|
| Web | Next.js 16.3.5、React 19.2.8、TypeScript | 单页运营工作台，入口为 `web/app/contentflow-app.tsx` |
| API | FastAPI 0.115+、Pydantic | REST API 前缀默认 `/api/v1` |
| ORM/迁移 | SQLAlchemy 2、Alembic | 仓库当前唯一迁移 head：`a5b6c7d8e9f0` |
| 隔离测试数据库 | SQLite | 仅在测试显式指定 URL 时使用，不是默认生产运行库 |
| 生产数据库 | PostgreSQL 16 + pgvector | 迁移创建 1024 维向量表与 HNSW 索引 |
| 异步任务 | 数据库 Job 队列 + 独立 Python Worker | 不依赖 Redis/Celery |
| 本地存储 | `.contentflow/storage` | 按工作区隔离文件路径 |
| 生产存储 | MinIO/S3 | 通过 `ObjectStorage` 抽象切换 |
| 文本模型 | Mock / OpenAI-compatible | 当前本机默认 Mock |
| Embedding | Hash / OpenAI-compatible | 当前本机默认 1024 维 Hash |
| 图片/视频 | Mock / 中立 HTTP 媒体契约 | 当前本机默认 Mock |
| 发布连接器 | 官方 API、本机脚本辅助、小红书人工导出 | 真实 API 能力受外部账号、scope 和平台审核限制；脚本最终提交由人工完成 |

Python 项目版本为 `0.2.0`，要求 Python 3.11+。前端要求 Node.js 22.13+。

## 3. 仓库结构和关键文件

```text
ContentFlow/
├─ contentflow/                  后端源码
│  ├─ api.py                     FastAPI 应用、CORS、日志、路由注册、健康检查
│  ├─ settings.py                环境变量与运行时校验
│  ├─ db.py                      Engine、Session、SQLite 外键
│  ├─ entities.py                SQLAlchemy 领域实体
│  ├─ schemas.py                 API 输入输出模型
│  ├─ dependencies.py            当前用户、工作区和 RBAC 依赖
│  ├─ security.py                密码、令牌、凭据加密
│  ├─ audit.py                   审计记录和敏感字段脱敏
│  ├─ routers/                   REST API
│  ├─ job_queue.py               入队、幂等、领取、租约、重试
│  ├─ worker.py                  Job Handler 和 Worker 主循环
│  ├─ workflow_service.py        当前 Web 主工作流
│  ├─ knowledge_service.py       文档切块、向量写入和检索
│  ├─ embeddings.py              Embedding Provider
│  ├─ text_generation.py         当前文本 Provider 选择入口
│  ├─ providers.py               Mock/OpenAI-compatible 文本实现；还含旧入口
│  ├─ media_providers.py         Mock/中立 HTTP 图片和视频 Provider
│  ├─ connectors.py              小红书/抖音/公众号连接器
│  ├─ object_storage.py          Local/S3 对象存储
│  ├─ review.py                  确定性规则审核与一次自动修复
│  ├─ workflow.py                素材任务构建；也保留早期工作流代码
│  └─ cli.py                     早期 CLI/兼容入口，不是当前 Web 主入口
├─ migrations/                  Alembic 迁移
├─ tests/                       后端、接口、迁移、安全和连接器测试
├─ web/                         Next.js/vinext 工作台
│  ├─ app/contentflow-app.tsx   前端主要界面和状态逻辑
│  └─ lib/contentflow-api.ts    API Base、Cookie 会话刷新、fetch 和下载封装
├─ docs/                        架构、使用、运维和平台边界说明
├─ scripts/supply_chain.py      CycloneDX 归并、可复现源码包、哈希与离线验真
├─ scripts/validate_stack.py    完整容器栈验收脚本
├─ docker-compose.yml           PostgreSQL、MinIO、API、Worker、Web
└─ .contentflow/                本机数据库和素材，已被 Git 忽略
```

## 4. 本地运行方式

### 4.1 当前本机事实

- 当前仓库根目录没有 `.env`，因此运行时读取 `settings.py` 的开发默认值。
- 当前有效本地数据库是 `.contentflow/contentflow-v2.db`，迁移状态为 `8b6c1f3a9d21 (head)`。
- `.contentflow/contentflow.db` 是旧数据库，不应和 `contentflow-v2.db` 混用。
- `.contentflow/storage` 中存在此前测试产生的知识、素材和导出文件。删除 `.contentflow` 会丢失本地工作区、账户和草稿数据，操作前必须备份。
- 本地工作台固定使用 `3001`，因为 `3000` 曾被另一个项目使用。不要为了启动 ContentFlow 杀掉占用 `3000` 的无关项目。

### 4.2 三个终端启动

仓库路径：

```powershell
Set-Location '.'
```

终端 1：API。

```powershell
& 'F:\python\python.exe' -m contentflow.migrate
& 'F:\python\python.exe' -m uvicorn contentflow.api:app --reload
```

终端 2：Worker。没有 Worker 时，Job 会一直停在 `queued`。

```powershell
Set-Location '.'
& 'F:\python\python.exe' -m contentflow.worker
```

终端 3：Web。

```powershell
Set-Location 'web'
npm run dev:local
```

访问地址：

- 工作台：<http://localhost:3001>
- Swagger：<http://localhost:8000/docs>
- API 就绪检查：<http://localhost:8000/health/ready>
- 默认 API Base：`http://localhost:8000/api/v1`

首次使用时在登录页切到“注册”，系统会同时创建账户、默认工作区和管理员成员关系。

### 4.3 一次性 Worker 调试

只领取一个可运行 Job 后退出：

```powershell
& 'F:\python\python.exe' -m contentflow.worker --once
```

它适合逐个观察数据库状态，但如果队列为空，会直接退出。

## 5. 一次内容生成的真实调用链

### 5.1 API 入队

前端在活动页调用：

```http
POST /api/v1/campaigns/{campaign_id}/runs
```

`contentflow/routers/runs.py:create_run` 会：

1. 校验活动属于当前工作区且未归档。
2. 创建 `WorkflowRun(status=queued, current_stage=queued)`。
3. 创建 `workflow.execute` Job，幂等键为 `workflow.execute:{run_id}`。
4. 写入 `workflow.enqueue` 审计日志。
5. 立即返回 HTTP 202，不在请求线程中调用模型。

### 5.2 Worker 领取任务

`contentflow/job_queue.py:claim_next_job` 会选择：

- `run_at <= 当前时间`
- `attempts < max_attempts`
- 状态为 `queued/retry`，或租约已过期的 `running`

PostgreSQL 下使用 `FOR UPDATE SKIP LOCKED`，允许多个 Worker 并发领取不同任务；SQLite 只适合本地单 Worker。领取后状态变成 `running`，记录 `locked_by/locked_at`，并将 `attempts + 1`。

默认租约 300 秒、最大尝试 4 次。失败后按 5、10、20……秒指数退避，单次最多 300 秒。`asset.poll` 的最大尝试次数单独设为 60。
任务执行期间 LeaseHeartbeat 会以“30 秒与租约三分之一中的较小值”为周期使用独立数据库会话续租；续租条件同时绑定 job_id、worker_id、attempt 和 running 状态。任务完成或失败落库前会再次校验所有权，旧 Worker 在任务已被重新领取后不能覆盖新执行结果。


### 5.3 工作流执行

`contentflow/workflow_service.py:execute_workflow_run` 的顺序是：

1. 读取 `Campaign` 并转成结构化 `CampaignBrief`。
2. 用产品、目标、人群、城市、必含信息和产品事实拼接检索 Query。
3. 在当前工作区检索最多 6 个知识块。
4. 调用文本 Provider 的 `plan` 阶段生成跨平台计划。
5. 对活动中的每个平台调用 `generate` 阶段，生成标题、正文、标签和平台结构。
6. 用 `RuleReviewer` 检查禁用词、必含信息、CTA 和长度等规则。
7. 规则不通过时做一次确定性修复，然后重新检查一次。
8. 调用 Provider 的 `review` 阶段做模型风险审核。
9. 两类审核都通过时，内容状态为 `needs_review`；否则为 `blocked`。
10. 保存 `ContentItem`、首条 `ContentRevision(version=1)` 和 `planned` 素材。
11. 将使用过的知识块 ID 保存到 `source_chunk_ids`。
12. `WorkflowRun` 最终进入 `awaiting_review/current_stage=human_review`。

注意：`WorkflowRun=awaiting_review` 只表示自动阶段完成，不表示所有内容都通过检查；需要继续查看每条 `ContentItem` 是 `needs_review` 还是 `blocked`。

## 6. 人工编辑、版本和素材门禁

### 6.1 编辑内容

`PATCH /api/v1/contents/{content_id}` 会：
请求体必须携带页面读取到的 expected_version。PostgreSQL 会锁定内容行并比较版本；不一致返回 409，旧页面不能覆盖新版本。


1. 更新正文、标题、标签、CTA 或平台结构。
2. `version + 1`。
3. 内容重新变为 `needs_review`，清除批准人和批准时间。
4. 把已有非 stale 素材标记为 `stale`。
5. 为新内容版本建立新的 `planned` 素材。
6. 写入新的 `ContentRevision(change_reason=human_edit)`。

因此，审核后修改正文导致“重新审核、重新生成素材”是设计行为，不是 Bug。

### 6.2 人工审批

`POST /api/v1/contents/{content_id}/review` 需要 `reviewer` 权限。

请求体同样必须携带 expected_version，并且只有 needs_review/blocked 内容可以直接审批。被驳回内容需要先编辑形成新版本，再重新审核。

- `approve`：内容变为 `approved`，所有 `planned/failed` 素材变为 `queued`，并创建 `asset.generate` Job。
- `reject`：内容变为 `rejected`，清空批准信息，不生成素材。

素材生成可能有两种返回：

- 同步完成：写入对象存储，素材状态直接变成 `ready`。
- 异步视频：素材变成 `processing`，记录外部 task ID，并创建 `asset.poll` Job；未完成时抛出 `JobNotReady` 进入延迟重试，不立即视为业务失败。

## 7. RAG 实现

### 7.1 入库

知识库上传支持 Markdown、TXT、CSV 和 JSON。上传后先写对象存储并创建 `KnowledgeDocument`，然后入队 `knowledge.index`。Worker 会：

1. 安全读取文件并按格式解码。
2. 切分成 `KnowledgeChunk`。
3. 使用配置的 Embedding Provider 生成 1024 维向量。
4. 保存正文、来源、序号、向量和模型名。
5. 更新文档状态和块数量。

知识文件默认限制 20MB。

### 7.2 检索

- SQLite：向量保存在 JSON 字段，由应用层计算余弦相似度。
- PostgreSQL：向量另写入 `knowledge_vectors`，使用 pgvector `<=>` 和 HNSW 余弦索引排序。
- 两种路径都必须带 `workspace_id`，防止跨租户检索。
- 生成内容保存 `source_chunk_ids`，用于审核时回看知识来源。

### 7.3 当前边界

Hash Embedding 只用于离线可复现验收，不具备真实语义向量质量。真实效果评估必须切换 OpenAI-compatible Embedding，并重新索引知识库；不能用 Hash 模式的结果宣称线上 RAG 质量。

当前没有独立 Reranker、混合 BM25、Query 改写或系统化召回评测集，这些可以作为后续改进方向。

## 8. Provider 层

### 8.1 文本

当前入口是 `contentflow/text_generation.py:build_text_provider`：

- `mock`：离线、确定性、无需密钥；当前本机默认。
- `openai-compatible`：调用 `/chat/completions`，要求 API Base、API Key 和模型名。

Provider 按 `plan / generate / review` 三个阶段接收结构化 JSON，并要求返回 JSON 对象。温度当前为 0.3。

`contentflow/providers.py:build_provider` 是早期入口，维护主工作流时不要绕过 `build_text_provider(settings, override)`。

### 8.2 Embedding

- `hash`
- `openai-compatible`

PostgreSQL 迁移固定向量维度为 1024；使用其他维度会被运行时校验拒绝。

### 8.3 图片和视频

- Mock 图片会生成明确标注的离线预览 PNG。
- Mock 视频只生成 `storyboard.json`，不是 MP4。
- HTTP 图片契约支持受限 base64 或下载地址。
- HTTP 视频契约可异步返回 task ID，由 Worker 轮询，成功后再转存本地或 S3。
- 模型生成素材下载上限为 100MB。

### 8.4 重要表达边界

“已经实现真实 Provider 适配层”不等于“本次本机运行调用了真实模型”。截至本交接文档，当前本机没有 `.env`，实际默认仍是 Mock 文本、Hash Embedding、Mock 图片和 Mock 视频。

## 9. 发布连接器

### 9.1 小红书

当前是 `XiaohongshuExportConnector`：

- 连接测试成功状态为 `export_only`，不是 `connected`。
- 发布时生成包含 `content.md`、`manifest.json`、`layout.json` 和素材的 ZIP。
- 运营人员下载 ZIP 后人工发布。
- 指标通过工作台人工回填。

不要把该能力描述成“小红书自动发布”。

### 9.2 三种显式发布方式与脚本安全边界

- `connector`：调用经测试的官方 API；远程调用开始后出现异常即进入 `reconciliation_required`，禁止自动重试或静默切脚本。
- `script`：不保存平台账号密码。Worker 在外部平台副作用前生成带 `SHA256SUMS`、固定 `playwright==1.62.0`、审核文案/排版/素材和本机运行器的 ZIP；状态为 `script_ready`。运行器只接受内置官方入口、校验包内路径与哈希、按平台/渠道隔离 browser profile，只尽力填充，绝不点击最终提交。reviewer 人工登记 `script_published` 或 `failed`。
- `manual_export`：仅小红书，维持纯人工投放包。

创建时用户可直接选择。失败后只有 `scheduled/queued/failed/exported` 可显式切脚本；`publishing/submitted/reconciliation_required` 必须先到平台核对，确认未发布后才能切换。自动指标拉取只允许 `connector`；脚本/导出使用人工指标。既有小红书任务缺少方式字段时由 Worker 归一为 `manual_export`。

运行器当前依赖平台 DOM 选择器，页面改版可能使自动填充退化为人工复制；这不应通过更激进的“自动找发布按钮”修复。使用前还需确认目标平台规则允许相应辅助操作。

### 9.3 抖音

当前适配器实现：

1. 使用 `access_token + open_id` 测试用户身份。
2. 上传视频。
3. 用 `video_id` 创建作品。
4. 根据 `item_id` 拉取播放、点赞、评论和分享。

能否真实调用取决于开放平台应用审核、账号授权、scope、素材和内容审核。仓库中的 HTTP 契约测试不等于真实账号验收。

### 9.4 公众号

当前适配器实现：

1. App ID/Secret 换取 access token。
2. 上传封面永久素材。
3. 创建草稿。
4. 仅在渠道配置 `auto_publish=true` 时提交发布。
5. 提交后使用 `publish_id` 调用 `freepublish/get`；未取得 `article_id` 时保持 pending，取得后才记为 published。

默认只创建草稿。自动对账和指标接口仍受具体公众号权限限制，仓库契约测试不等于真实账号验收。

### 9.5 发布前的二次校验

`handle_publish_dispatch` 不信任排期时的旧状态，会重新检查：

- 内容仍为 `approved`。
- 当前内容版本等于排期保存的版本。
- 当前版本至少有一个素材。
- 当前版本的全部素材均为 `ready`。
- 内容和连接器平台匹配。

这用于防止审核后改稿、使用旧素材或素材未完成就发布。
PostgreSQL 下，取消接口与 Worker 分发入口都会先锁定同一条 PublishJob。取消先获得锁时，PublishJob 进入 cancelled、队列 Job 同步进入 succeeded/cancelled；Worker 先获得锁并持久化 publishing 时，取消接口等待后返回 409。因此不能出现接口返回取消成功、Worker 随后仍按旧状态发布的结果。


### 9.5 发布结果不确定、自动对账与人工接管

Worker 在调用平台前先持久化 `dispatch_token`、`dispatch_started_at` 和 publishing 状态，再提交事务。远程结果返回后，PublishJob、原始响应和后续对账 Job 会先于 publish.dispatch 队列完成落库；若进程在两次提交之间退出，重放会读取 submitted/published 等领域终态，不会再次调用发布接口。

公众号自动对账链路如下：

1. `freepublish/submit` 返回 `publish_id` 后，PublishJob 进入 submitted，并创建幂等键为 `publish.reconcile:{publish_job_id}` 的 Job；Worker 周期扫描也会补建历史 submitted 任务。
2. `freepublish/get` 未返回 `article_id` 时保守视为 pending，通过队列指数退避重试；只有取得 `article_id` 才进入 published。
3. 对账远程 HTTP 期间不持有 PublishJob 行锁；返回后重新 `FOR UPDATE` 并校验当前状态和原查询键，避免慢查询阻塞人工操作。
4. 若人工处置或其他状态在查询期间先提交，自动结果写入 `publish.reconciliation_stale_ignored` 审计后被忽略，不覆盖新状态。
5. 查询异常或最大尝试耗尽后进入 reconciliation_required，保留最后错误并交给人工，不会重新调用发布接口。

reviewer 可在 submitted 或 reconciliation_required 时执行人工接管：

- “确认已发布”：可补充平台内容 ID/链接，PublishJob 与原 publish.dispatch Job 分别进入 published 和 succeeded。
- “确认未发布”：PublishJob 与原 publish.dispatch Job 进入 failed；之后才可以重试原分发任务。
- 如果自动对账 Job 已 queued/retry/running，人工处置在同一事务中把它记为 succeeded/manual 并清除租约；旧 Worker 随后因所有权失效或领域终态不能覆盖人工决策。
- 两种处置都会写入 `publish.reconcile` 审计、人工依据和两类队列 Job ID。

当前边界：微信公众号已具备确定查询键下的自动轮询闭环；抖音在不确定响应且没有 `item_id` 时仍只能人工核对。系统仍缺跨平台回调验签/去重、平台级幂等键和发布 Outbox，因此不能表述为严格端到端 exactly-once。

## 10. 主要领域对象和状态

| 对象 | 作用 | 关键状态/字段 |
|---|---|---|
| `Workspace` / `Membership` | 租户和成员关系 | `viewer/editor/reviewer/admin` |
| `Campaign` | 营销 Brief | `draft/active/archived` |
| `KnowledgeDocument` / `KnowledgeChunk` | 原文、切块和向量 | `pending/indexing/indexed/failed` |
| `WorkflowRun` | 一次生成批次 | `queued/running/awaiting_review/failed`，另有 `current_stage` |
| `ContentItem` | 某个平台的一条当前内容 | `needs_review/blocked/approved/rejected`，带 `version` |
| `ContentRevision` | 不可变版本记录 | 生成或人工编辑原因、正文和 `layout_json` |
| `Asset` | 图片、视频或离线分镜 | `planned/queued/generating/processing/ready/failed/stale` |
| `ChannelConnection` | 平台凭据和配置 | `disconnected/pending_test/connected/export_only/invalid` |
| `PublishJob` | 发布排期和外部结果 | `scheduled/publishing/reconciliation_required/published/exported/draft_created/submitted/failed/cancelled` |
| `MetricSnapshot` | 某次发布的分时指标 | 曝光、点击、点赞、评论、分享及原始数据 |
| `Job` | 系统异步任务 | `queued/running/retry/succeeded/failed` |
| `AuditLog` | 操作审计 | 操作者、动作、实体、脱敏元数据 |

Job 类型目前包括：

```text
knowledge.index
workflow.execute
connector.test
asset.generate
asset.poll
publish.dispatch
metrics.pull
```

## 11. 权限、安全和审计

角色等级由低到高为：

```text
viewer < editor < reviewer < admin
```

- viewer：查看数据。
- editor：维护活动、知识和内容。
- reviewer：审批内容、排期发布、执行需要审核权限的操作。
- admin：成员、角色、工作区和审计管理。

当前安全实现包括：

- PBKDF2-SHA256 密码哈希和随机 salt。
- Base64URL + HMAC-SHA256 签名短访问令牌，校验 `sid/jti/iss/aud/iat/nbf/exp`。
- 数据库 `auth_sessions`、旋转 Refresh Token、复用检测、单会话/全会话撤销。
- 浏览器 HttpOnly/SameSite Cookie、生产 Secure 和 Cookie 写请求可信 Origin 校验；CLI 保留短期 Bearer。
- 由应用密钥派生 Fernet key，加密平台凭据。
- API 不回传凭据密文。
- 审计元数据递归脱敏 token、secret、password 等键。
- 防止移除自己或降级最后一名管理员。
- 生产环境拒绝默认应用密钥。

生产化仍应补 OIDC/SAML、MFA、设备会话管理、集中密钥管理、CSP、共享网关限流、签名密钥轮换和更完整的可观测平台。

## 12. 前端工作台

主界面集中在 `web/app/contentflow-app.tsx`。主要区域包括：

- 总览
- 营销活动
- 内容审核与版本历史
- 素材中心
- 发布管理
- 知识库
- 平台连接
- 数据复盘
- 任务队列
- 团队与审计

登录后的数据由 `loadData` 并行请求，之后每 15 秒静默刷新一次。它是普通轮询，不是 WebSocket/SSE。

前端只在 `localStorage` 保存 `contentflow_api_base`。访问/刷新令牌只存在 HttpOnly Cookie 中，旧版 `contentflow_token` 会被主动清除；平台账号凭据也不会保存在前端。

目前大部分界面和业务逻辑都集中在一个较大的 `contentflow-app.tsx` 中。继续扩展功能时，优先按业务域拆组件和 hooks，避免文件继续膨胀。

## 13. API 功能地图

所有业务 API 默认位于 `/api/v1`：

| 模块 | 主要能力 |
|---|---|
| `/auth` | 注册、登录、会话、工作区列表/创建/切换 |
| `/admin` | 成员管理、角色调整、审计日志、Worker 与工作区队列健康 |
| `/campaigns` | 活动查询、创建、修改和归档 |
| `/campaigns/{id}/runs`、`/runs/{id}` | 创建和查看生成批次 |
| `/knowledge` | 知识文档列表和上传索引 |
| `/contents` | 内容筛选、详情、版本、编辑和人工审核 |
| `/assets` | 素材列表、上传、重新生成和鉴权下载 |
| `/channels` | 平台连接创建、列表和异步测试 |
| `/publishing` | 发布排期、取消、人工对账和导出包下载 |
| `/metrics` | 人工指标、平台拉取和汇总 |
| `/jobs` | 任务查询和失败任务重试 |
| `/dashboard` | 工作台摘要 |

表中的 `/metrics` 是带 `/api/v1` 前缀的业务复盘接口（实际为 `/api/v1/metrics`）；根级 `/metrics` 是不进入 Swagger、需要独立 Bearer Token 的 Prometheus 抓取端点，两者用途和鉴权边界不同。`r`n`r`n准确请求体和响应体以运行中的 Swagger 为准，不要只根据前端 TypeScript 类型猜测。

## 14. 当前 Git 工作区现场

### 14.1 已跟踪但尚未提交的修改

截至 2026-08-03，以下修改存在于工作区，属于用户当前工作，不得丢弃：

1. `contentflow/routers/channels.py`
   - 重复点击正在执行的连接测试时复用现有活动 Job。
   - 上一次测试已经 terminal/failed 后，允许把渠道重新置为 `pending_test` 并创建新 Job。
2. `contentflow/worker.py`
   - Alembic 初始化日志后用 `logging.basicConfig(..., force=True)` 恢复 Worker 日志，避免 Worker 实际运行但终端只看到迁移日志。
3. `tests/test_api_v2.py`
   - 增加“连接器失败后可以重新入队，同时 pending 重复点击不重复入队”的回归测试。
4. `web/app/contentflow-app.tsx`
   - 增加 `invalid -> 连接异常` 的中文状态映射。

### 14.2 未跟踪文件

```text
knowledge/北京周末 CityWalk 路线助手产品资料.txt
docs/CONTENTFLOW_HANDOFF.md
```

前者是用户放入仓库的测试知识资料；是否纳入版本控制应由用户确认。本文档是本次交接新增文件。

### 14.3 接手时禁止做的操作

- 不要运行 `git reset --hard`。
- 不要对上述文件执行 `git checkout --`。
- 不要删除 `.contentflow` 试图“重置环境”，除非先获得用户确认并备份。
- 不要擅自提交或推送；先让用户确认提交范围和测试知识文件是否公开。

## 15. 本次交接时的验证结果

2026-08-03 在当前未提交修改上实际执行：

```powershell
& 'F:\python\python.exe' -m pytest -q
```

结果：

```text
18 passed in 10.51s
```

前端实际执行：

```powershell
npm run lint
npm run build
```

结果：ESLint 通过；Next.js 16.2.12 生产构建、TypeScript 检查和静态页面生成通过。

数据库实际执行 `alembic current`，结果与 head 一致：

```text
8b6c1f3a9d21 (head)
```

本次没有重新执行以下高成本或外部依赖验证：

- `npm test` 的 Sites/vinext 构建链。
- Docker Compose 的 PostgreSQL + pgvector + MinIO 全栈验收。
- `scripts/validate_stack.py`。
- 真实 外部模型/媒体 Provider 调用。
- 真实抖音或公众号账号发布。

因此，仓库文档中既有的容器验收记录可以作为历史材料，但不能说这些外部路径在 2026-08-03 本轮被重新验证过。

## 16. 常见问题排查

### 16.1 工作台一直显示“正在连接工作台”或 `Failed to fetch`

按顺序检查：

1. 打开 <http://localhost:8000/health/ready>，确认 API 可达。
2. 确认前端地址为 <http://localhost:3001>。
3. 确认 API Base 为 `http://localhost:8000/api/v1`，不要漏掉 `/api/v1`。
4. 浏览器开发者工具查看失败请求是网络错误、CORS 还是 401。
5. 如果之前保存过错误地址，清除 `localStorage.contentflow_api_base` 后刷新。
6. 如果是 401，重新登录；不要手工伪造 Token。

### 16.2 前端自动跑到 3001

这是预期行为。`npm run dev:local` 固定使用 3001，用来避开另一个占用 3000 的项目。

### 16.3 Job 一直 queued

通常是 Worker 未启动。检查独立终端是否正在运行：

```powershell
& 'F:\python\python.exe' -m contentflow.worker
```

然后在任务队列页面查看 Job 类型、尝试次数和 `last_error`。

### 16.4 Worker 只显示 Alembic 日志

当前未提交的 `worker.py` 修改专门修复了这一点。迁移完成后应继续看到 `worker started id=...`。Worker 空闲时不会持续刷日志，这是正常现象。

### 16.5 平台连接显示 `invalid`

1. 先看对应 `connector.test` Job 的 `last_error`。
2. 确认 Worker 正在运行。
3. 小红书不需要账号密码，正常结果应为 `export_only`。
4. 抖音需要有效 `access_token/open_id`；公众号需要有效 App ID/Secret 和接口权限。
5. 修复配置后重新点“测试连接”。当前未提交补丁允许 terminal/failed 测试重新入队。

### 16.6 内容批准后又变成待审核

如果内容被编辑，系统会增加版本并撤销旧批准，这是版本门禁的正常行为。重新审核后才会为新版本生成素材。

## 17. 事实边界：可以说什么，不能说什么

### 可以基于代码和本次验证说明

- 已实现 FastAPI + SQLAlchemy + 数据库 Job 队列 + Worker 的全栈主链路。
- 已实现多工作区、RBAC、内容版本、人工审核、素材任务、发布排期和审计。
- 已实现 SQLite 本地模式，以及 PostgreSQL/pgvector、S3/MinIO 的生产适配和迁移配置。
- 已实现 Mock、OpenAI-compatible 和中立 HTTP 媒体 Provider 适配层。
- 已实现小红书 ZIP 导出、抖音 API，以及公众号草稿/可选提交和 `publish_id` 状态查询连接器代码。
- 当前工作区全量 Ruff 通过、后端 51 个测试通过，前端 Next.js 生产构建和 TypeScript 检查通过。

### 不能据此夸大的内容

- 不能把本地 Mock 演示说成真实外部模型或付费媒体服务调用。
- 不能把小红书导出包说成自动发布。
- 不能把 HTTP Mock/契约测试说成真实平台账号已经验收。
- 不能声称已经验证生产级高并发、成本或稳定性。
- 不能声称 RAG 有高召回率；当前没有正式评测集和 Reranker。
- 不能把项目说成一个自主 Agent；它是显式状态机和工作流系统。

## 18. 建议的后续优先级

### P0：先保护并收尾当前工作区

1. 阅读 `git diff`，确认连接器重试和 Worker 日志补丁是否符合预期。
2. 手工回归“小红书连接测试：pending_test -> export_only”。
3. 手工回归“失败后重新测试会创建新 Job；重复点击同一次 pending 测试复用旧 Job”。
4. 让用户决定是否提交 `knowledge/北京周末 CityWalk 路线助手产品资料.txt`。
5. 经用户确认后再 commit/push。

### P1：补齐完整本地验收

1. 从注册开始跑一遍：工作区 -> 知识上传 -> 索引 -> 活动 -> 生成 -> 编辑 -> 审批 -> 素材 -> 小红书导出 -> 指标。
2. 为每一步记录数据库对象和状态变化，而不只看前端提示。
3. 执行 `npm test`，确认 Sites/vinext 路径是否仍然有效。
4. 如 Docker 可用，再运行 Compose 和 `scripts/validate_stack.py`。

### P2：真实模型联调

1. 在不提交 `.env` 的前提下配置文本、Embedding、图片和视频 Provider。
2. 先只切文本，验证结构化 JSON、错误和费用，再切 Embedding 并重新索引。
3. 图片和视频分开联调，特别记录异步视频超时、轮询和下载地址过期。
4. 增加外部调用耗时、token/费用和错误码的结构化记录。

### P3：工程改进

1. 把超大的 `contentflow-app.tsx` 按业务域拆分。
2. 为任务状态增加更清楚的前端时间线；数据量大时考虑 SSE，而不是全页面 15 秒轮询。
3. 增加 RAG 评测集、混合检索和可选 Reranker。
4. 增加 API 限流、集中密钥管理、指标监控和告警。
5. 在 PostgreSQL 多 Worker 环境做并发、租约恢复和幂等压力测试。

## 19. 建议新对话首先阅读的文件

按以下顺序阅读，能最快建立正确心智模型：

1. `docs/CONTENTFLOW_HANDOFF.md`
2. `git status --short` 和 `git diff`
3. `README.md`
4. `docs/architecture.md`
5. `contentflow/routers/runs.py`
6. `contentflow/job_queue.py`
7. `contentflow/worker.py`
8. `contentflow/workflow_service.py`
9. `contentflow/entities.py`
10. `contentflow/routers/contents.py`
11. `contentflow/routers/publishing.py`
12. `contentflow/knowledge_service.py`
13. `contentflow/text_generation.py`、`embeddings.py`、`media_providers.py`
14. `contentflow/connectors.py`
15. `web/app/contentflow-app.tsx`

## 20. 可直接发给新对话的开场指令

```text
请接手并继续完善以下 ContentFlow 项目：
.

第一步请完整阅读 docs/CONTENTFLOW_HANDOFF.md，然后执行只读的 git status --short、git diff 和关键文件检查。当前工作区存在尚未提交的用户修改，不要 reset、checkout、覆盖、提交或推送。

理解系统时请以 Web -> FastAPI -> SQLAlchemy -> Job -> Worker -> workflow_service 的当前主链路为准，不要把早期 CLI/ContentMarketingWorkflow 原型当成主链路。所有结论要区分：已由当前代码实现、本轮实际验证、只有适配层但未做真实外部联调、尚未实现。

在提出或实施下一步改动前，先用一个具体业务案例说明它会改变哪些 API、数据库对象、Job、状态和前端页面，并保护 .contentflow 中的现有本地数据。
```


## 21. 2026-08-08 可靠性加固增量交接

### 21.1 本轮保护边界

本轮继续在既有未提交工作区上增量修改，没有执行 reset、checkout、暂存、提交或推送，也没有删除 .contentflow、本地知识资料或用户已有改动。后续接手仍必须先执行只读 git status --short 和 git diff。

### 21.2 已实现

1. Worker 租约：
   - job_queue.py 增加按 job_id、worker_id、attempt 条件续租，以及完成/失败前的所有权校验。
   - worker.py 增加独立会话的 LeaseHeartbeat；心跳失败或任务被重新领取时，旧 Worker 不再落成功/失败结果。
   - settings.py 为轮询间隔、租约和最大重试次数增加安全范围。
2. 内容并发：
   - 编辑和审核请求新增必填 expected_version。
   - PostgreSQL 使用行锁串行化竞争请求，版本冲突返回 409。
   - 审核只接受 needs_review/blocked；rejected 必须先编辑形成新版本。
   - Web 已随编辑/审核请求发送当前版本。
3. 发布防重与对账：
   - 调平台前先持久化 dispatch_token、开始时间、尝试次数和 publishing。
   - 结果不确定时进入 reconciliation_required，自动重试被禁止。
   - 新增 reviewer 人工对账 API、发布管理按钮和 publish.reconcile 审计。
   - 确认已发布后同步把队列置为 succeeded；确认未发布后保持 failed，才允许人工重试。
4. 构建兼容：
   - web/tsconfig.json 显式允许 TypeScript 扩展导入，Next.js 与 vinext/Vite 两条生产构建链均可通过。

### 21.3 本轮实际验证

- 全量 Ruff：通过。
- 后端 pytest：45 passed。
- 前端 ESLint：通过。
- 前端 vinext/Sites 构建与渲染测试：2 passed。
- 前端 Next.js 生产构建与 TypeScript 检查：通过。
- PostgreSQL 16/pgvector 临时 schema 并发验证：
  - 两个未提交领取事务通过 FOR UPDATE SKIP LOCKED 获取不同 Job；
  - 第二个旧版本内容请求在首事务持锁时等待，首事务提交 version=2 后返回 409；
  - 取消请求在 Worker 持发布行锁时等待，Worker 提交 publishing 后取消返回 409；取消先完成时队列原子进入 succeeded/cancelled，后到 Worker 只返回 cancelled；
  - 随机临时 schema 已删除，验证用 PostgreSQL 容器已停止，数据卷未删除。
- 完整 Compose 重建未能在本轮重跑：Docker Hub 拉取 python:3.12-slim 时网络连接失败。该项应记录为外部环境阻塞，不能据此宣称 2026-08-08 已重新通过完整容器栈验收。

### 21.4 当时尚未关闭的生产门禁（最新见 21.8）

1. 当时发布只完成保守防重和人工对账；21.8 已部分关闭微信公众号查询式自动对账，平台幂等键、抖音确定对账、回调验签/去重和 Outbox 仍未实现。
2. Worker 已具备优雅停机、数据库节点心跳和管理员队列健康接口；仍缺可观测指标、自动告警、编排层健康探针，以及在 CI 中持续运行的 PostgreSQL 双 Worker 长任务/进程终止故障注入。
3. 浏览器令牌仍在 localStorage，尚无企业 SSO/MFA、会话撤销、安全 Cookie、CSP 和共享限流。
4. 真实 外部模型/媒体 Provider、抖音、公众号测试租户与 RAG 评测集仍未签收。
5. 一致性备份、对象级校验、PITR、KMS/TLS/WAF、CI/CD、SLO 和数据治理仍是企业成熟度主要差距。

更完整的风险分级、证据和路线图见 docs/enterprise_readiness_review.md。

### 21.5 第二轮复审补充

- Worker 的发布异常路径、取消和人工对账现统一为 PublishJob → Job 锁顺序；最终租约耗尽使用两个事务，先释放 Job 锁，再更新领域状态，避免跨表反向锁死。
- publish.dispatch 在 publishing 状态最终租约耗尽时进入 reconciliation_required，不能通过通用 Job 重试绕过人工核对。
- 最后一名管理员检查在 PostgreSQL 中先锁定 Workspace 行，并发降级/移除同一工作区管理员时会串行执行。
- 新增发布租约耗尽回归场景；Worker/发布定向测试 2 passed，管理员定向测试 2 passed。
- 依赖组合已出现 Starlette TestClient/httpx 弃用告警；后续应通过依赖锁文件和 CI 升级矩阵治理，不能继续只依赖宽版本范围。
- PostgreSQL 临时 schema 管理员竞态实测结果为一个提交、一个 409、最终保留一名管理员；临时 schema 已删除，验证容器已停止。
- 本轮最终全量验证仍为 Ruff 通过、后端 45 passed、前端 ESLint/两条生产构建通过、vinext 渲染测试 2 passed。

### 21.6 Worker 优雅停机与容器验证

- Worker 已处理 SIGTERM/SIGINT：停止领取新任务，空闲轮询立即唤醒，在途任务完成后退出。
- 领取事务提交前若检测到停机请求会回滚，避免留下已锁定但未执行的 Job。
- Alembic 后会重新启用 Worker logger，启动/失败/停机日志不再静默丢失。
- Compose Worker 默认 stop_grace_period=10m；API 使用 exec 启动 Uvicorn，长期服务默认 restart=unless-stopped，可由环境变量覆盖。
- 定向队列测试 7 passed；全量后端 45 passed。
- 真实 Docker SIGTERM 验证为 0.43 秒、ExitCode=0、signal=15；一次性容器已删除。
- 已补数据库 Worker 业务心跳和管理员队列健康接口；仍缺编排层自动探针、滚动升级和宽限期耗尽/SIGKILL 的 CI 故障注入。

### 21.7 Worker 业务心跳、队列健康与迁移增量

- 新增 `worker_nodes` 表和 Alembic head `c9e7b4a2d610`；启动迁移可安全识别未版本化初始结构、上一 head `8b6c1f3a9d21` 和当前结构。
- 长驻 Worker 使用独立数据库会话周期写入节点心跳，正常退出写 `stopped`；`--once` 一次性执行不注册为长驻节点。
- 新增 `/api/v1/admin/worker-health`。Worker 容量是全局汇总，Job 队列按当前工作空间过滤；接口不暴露 hostname、process_id 或节点明细。
- 健康问题代码包括 `no_active_workers`、`stale_worker_nodes`、`ready_jobs_without_active_workers` 和 `queue_ready_age_exceeded`。
- 全量 Ruff 通过、后端 45 passed；相关定向测试 30 passed。
- 随机临时 PostgreSQL 数据库从 base 升级到 head 后有 18 张 public 基础表，Worker ORM 心跳最终为 stopped，4 个索引齐全；临时库残留 0，验证容器恢复停止。
- `verify_backup.ps1` 已更新为 18 张表和当前 head，但新迁移头的 PostgreSQL+MinIO 联合备份/隔离恢复仍需重跑，不能沿用上一 head 的灾备签收结论。
- 后续优先补编排层自动健康探针、指标/告警、失联节点保留与清理策略，以及 PostgreSQL CI 故障注入。

### 21.8 微信自动发布对账、人工接管与第四轮企业复审

#### 保护边界

- 本轮继续在既有未提交工作区上增量修改，没有 reset、checkout、暂存、提交或推送，也没有删除用户知识资料或其他既有改动。
- 对账实现只在平台提供确定查询键时自动运行；微信公众号使用 `publish_id`，抖音缺少 `item_id` 的不确定响应不做标题/时间模糊匹配。

#### 已实现

1. `ChannelConnector` 增加显式对账能力契约；微信公众号调用官方 `POST /cgi-bin/freepublish/get`，只有响应包含 `article_id` 才返回 published，其余成功响应均保持 pending。
2. `publish.dispatch` 得到 submitted 后，在同一领域事务中保存远程响应并创建幂等 `publish.reconcile:{publish_job_id}` Job；队列任务完成发生在下一次提交，缩小“平台已接收、本地未记录”的崩溃窗口。
3. Worker 启动时及此后默认每 60 秒扫描微信公众号 submitted 任务并补建对账 Job；扫描在 `LIMIT` 前排除已有活动对账 Job，避免旧任务占满批次并饿死后续缺失项。PostgreSQL 使用 `FOR UPDATE OF publish_jobs SKIP LOCKED`，多 Worker 不会围绕同一 PublishJob 重复补偿。
4. `publish.reconcile` 支持 pending 退避、最终 article_id 收敛、最大尝试耗尽转人工，以及 queued/checked/auto/stale_ignored 审计。
5. 自动查询改为两阶段事务：远程 HTTP 前释放 PublishJob 行锁，返回后重新加锁并比较状态与 `publish_id`。人工或其他事务已先提交时，迟到结果只审计、不覆盖。
6. reviewer 可在 submitted 或 reconciliation_required 时人工接管；人工处置同时终结自动对账 Job、清除租约，并保持原 publish.dispatch Job 与 PublishJob 的一致状态。
7. `CONTENTFLOW_PUBLISH_RECONCILIATION_INITIAL_DELAY_SECONDS`、`CONTENTFLOW_PUBLISH_RECONCILIATION_MAX_ATTEMPTS`、`CONTENTFLOW_PUBLISH_RECONCILIATION_SWEEP_POLL_SECONDS` 和 `CONTENTFLOW_PUBLISH_RECONCILIATION_SWEEP_BATCH_SIZE` 已接入 `.env.example`；分别控制单任务首次查询、最大尝试、恢复扫描频率和单批补建上限。
8. 自动 published 会把仍 running/failed 的原 publish.dispatch Job 收敛为 succeeded；人工确认未发布后再次提交新 `publish_id` 时，旧终态对账 Job 会清空旧结果、租约和尝试次数，写 `publish.reconciliation_requeued` 后重新 queued。

#### 验证证据

- 严格 UTF-8 校验、全量 Ruff 和 Python 语法检查通过。
- 连接器与 Worker 定向测试 `11 passed`，覆盖 submitted 持久化、pending→published、查询耗尽、原分发 running Job 收敛、人工接管、终态对账 Job 复活，以及迟到 published 结果不得覆盖人工 failed 决策。
- 全量后端 `51 passed`；前端 Next.js 生产构建、TypeScript 检查通过。
- 随机临时 PostgreSQL 数据库从空库迁移到 `c9e7b4a2d610` 后实测：锁住的 PublishJob 被扫描跳过；释放后只入队 1 个 Job；再次扫描新增 0；旧 succeeded Job 在新 `publish_id` 下原位 requeued 1 次；自动 published 将仍 running 的原分发 Job 收敛为 succeeded。
- PostgreSQL 远程查询竞态使用 1 秒 `lock_timeout`：并发人工事务成功锁定并写入 failed，迟到 published 结果被 `publish.reconciliation_stale_ignored` 拒绝，最终仍为 failed。
- 本机 5432 端口被其他进程占用，本轮容器临时映射 55432；随机验证数据库已精确核对并删除，PostgreSQL 容器已停止，数据卷保留。
- 当前仍有 Starlette TestClient/httpx 弃用提示和 Windows `.pytest_cache` 权限警告；二者未造成断言失败，但应由依赖锁与干净 CI 工作目录治理。

#### 当前仍最关键的 5 个不足

1. 真实外部租户尚未签收；微信只有查询式闭环，抖音不确定响应仍需人工，跨平台回调验签/去重、平台幂等键和发布 Outbox 未完成。
2. 企业身份与会话仍缺 OIDC/SAML、MFA、Refresh Token 旋转、会话撤销、HttpOnly Cookie、登录审计和跨实例共享限流。
3. 软件交付仍缺正式 CI/CD、独立迁移 Job、环境晋级、灰度/回滚、依赖锁、CODEOWNERS、SBOM、镜像签名和覆盖率门禁。
4. 可观测与韧性仍缺 Prometheus、OpenTelemetry、SLO/告警、Provider 熔断限流、编排层健康探针、容量模型和持续故障注入。
5. 数据治理与灾备仍缺 RLS、PITR/WAL 归档、数据库与对象一致性快照、异地恢复、租户导出/删除、留存策略和不可篡改审计；当前 head 的联合备份恢复仍待重签。

#### 接下来最值得继续做的 5 项改进

1. 使用隔离的真实公众号和抖音租户完成授权、发布、查询/回调与指标签收；为抖音补可靠查询键或回调，设计回调验签、事件去重、渠道级幂等与 Outbox。
2. 建立 PostgreSQL/pgvector/MinIO CI，固化本轮行锁实验，并加入 Alembic、双 Worker、SIGTERM/SIGKILL、数据库闪断、Playwright、依赖审计和覆盖率门禁。
3. 以 OIDC 为首选改造身份体系，并落地短 Access Token、旋转 Refresh Token、HttpOnly/SameSite Cookie、jti 撤销、CSP 和 Redis 共享限流。
4. 接入 Prometheus/OpenTelemetry，定义 API、队列、Worker、Provider、自动对账、外部发布成功率和成本 SLI/SLO，让编排层消费现有健康信号。
5. 为核心租户表设计 RLS 与数据库租户上下文，启用 PITR 和异地备份，重跑当前 head 的 PostgreSQL+MinIO 联合恢复，并建立数据生命周期制度。

#### 成熟度结论

自动对账关闭了微信公众号 submitted 永久悬挂和慢查询持锁两处关键可靠性缺口，使发布链路进一步接近 L3；但单个平台的本地契约与 PostgreSQL 实测不能替代真实租户、企业 IAM、自动交付、SRE、灾备和治理的持续生产证据。当前仍应表述为“可部署产品基线，部分可靠性能力接近生产就绪”，不能表述为“成熟企业生产项目已签收”。

## 21.9 PostgreSQL 自动门禁、依赖锁与第五轮企业复审

### 保护边界

- 本轮仍在已有未提交修改上增量工作，没有 reset、checkout、暂存、提交或推送，也没有删除用户资料或 PostgreSQL 数据卷。
- 新集成测试只创建名称为 `contentflow_test_<uuid>` 的随机临时数据库；结束时先终止该库残留连接，再按已校验名称删除。验证后残留临时库为 0，PostgreSQL 容器已停止。

### 已实现

1. 新增 `tests/test_postgres_integration.py`。未提供 `CONTENTFLOW_TEST_POSTGRES_URL` 时安全跳过；提供后从空 PostgreSQL/pgvector 数据库迁移到 Alembic head，并验证 vector 扩展、关键表、`SKIP LOCKED`、幂等对账 Job 复活、原分发终态收敛和远程查询竞态。
2. 新增 `uv.lock`，固定 74 个 Python 包及制品哈希；CI 使用 `uv sync --all-extras --locked`，不再由宽版本范围临时解析运行环境。
3. 新增 `.github/workflows/ci.yml`：后端在固定 digest 的 pgvector 服务上运行 Ruff、真实 PostgreSQL 测试、75% 分支覆盖率和严格漏洞审计；前端执行 `npm ci`、lint、Sites/vinext 测试、Next.js 生产构建和高危依赖审计。
4. GitHub Actions 均固定完整提交 SHA，CI pgvector 固定镜像 digest；新增 `.github/dependabot.yml` 每周检查 uv、npm、Actions 和 Docker 依赖。
5. 严格审计发现并修复 Python 漏洞：`cryptography 48.0.1` 命中 PYSEC-2026-3552、3553、3554，现约束并锁定为 `50.0.0`。
6. 前端审计发现 `image-size`、`nanoid` 和 `undici` 高危链路。Cloudflare Vite Plugin/Wrangler 升至 1.51.1/4.120.0，nanoid 固定 3.3.17；vinext 回退到不引入无修复 `image-size` 的 0.0.45，并通过双构建验证。

### 本轮实际证据

- GitHub Workflow 与 Dependabot JSON Schema：均通过。
- `uv lock --check`、全仓 Ruff：通过。
- 后端：`54 passed`；包含真实 PostgreSQL 集成测试 `3 passed`；分支覆盖率 `76.47%`，高于 75% 门槛。
- Python：`pip-audit --strict` 为 `No known vulnerabilities found`。
- 前端：从空依赖目录 `npm ci` 后 ESLint、Sites/vinext 构建与 2 项渲染测试、Next.js 生产构建全部通过；`npm audit --audit-level=moderate` 为 0 vulnerabilities。
- PostgreSQL：随机测试数据库残留 0，容器停止，数据卷保留。
- 仍有 Starlette TestClient/httpx 弃用警告和本机 `.pytest_cache` 权限警告；两者不影响断言，但前者需要后续兼容迁移。
- 工作流尚未提交或推送，故没有远程 GitHub Actions 运行记录；当前证据是仓库配置、Schema 验证和本地等价流水线通过，不能写成“远程 CI 已签收”。

### 当前仍最关键的 5 个不足

1. 真实外部闭环仍未签收：公众号、抖音、外部模型/媒体 Provider 和对象存储需要隔离租户的成功、超时、限流、审核、授权过期与重复回调证据；跨平台 Outbox、平台幂等键、回调验签和事件去重仍缺。
2. 企业身份与会话仍不足：OIDC/SAML、MFA、短 Access Token、旋转 Refresh Token、jti 撤销、HttpOnly/SameSite Cookie、设备/登录审计、CSP 和 Redis 共享限流均未落地。
3. CI 只是仓库基线：尚无远程首次运行、受保护分支必需检查、独立迁移 Job、环境晋级、灰度/回滚；MinIO 联合验证、浏览器 E2E、双 Worker、SIGKILL、数据库闪断、负载和混沌测试未进入持续门禁。
4. 可观测和 SRE 运营未闭环：缺 Prometheus/OpenTelemetry、SLO/告警、Provider 熔断与配额治理、编排层探针/扩缩、容量模型、On-call、事故复盘和变更治理。
5. 数据治理、灾备和供应链证明不足：缺 RLS、PITR/WAL、当前 head 的 PostgreSQL+MinIO 联合恢复、异地备份、数据生命周期、不可篡改审计、SBOM、镜像扫描/签名和集中 KMS。

### 接下来最值得继续做的 5 项改进

1. 先扩展 CI：加入 MinIO 真实对象测试、Alembic 升降级、Playwright、双 Worker 长任务、SIGTERM/SIGKILL 和数据库闪断；随后在远程仓库启用 backend/frontend 必需检查与分支保护。
2. 用隔离公众号、抖音和 外部模型/媒体 Provider 租户完成外部验收，补回调验签/去重、渠道级幂等与 Outbox，并把质量、费用和配额阈值变成门禁。
3. 以 OIDC 为首选重构认证与浏览器会话，配套 MFA、短令牌、刷新旋转、撤销、HttpOnly Cookie、CSP、共享限流和登录审计。
4. 接入 Prometheus/OpenTelemetry，覆盖 API、数据库、队列、Worker、Provider、对账、成本和质量 SLI；建立 SLO、告警、Runbook、容量与故障演练。
5. 落地 RLS/PITR/异地备份和当前版本联合恢复，建立租户导出/删除/留存制度；为构建产物生成 SBOM、执行镜像扫描并签名。

### 最新成熟度结论

本轮把“依赖本机手工证明”的 PostgreSQL 关键场景变成了仓库内可重复测试，并补上依赖锁、覆盖率和双栈漏洞门禁；测试与供应链成熟度从 L1-L2 推进到 L2-L3。由于远程门禁、真实租户、企业 IAM、SRE、灾备和治理尚未形成持续生产证据，ContentFlow 仍应描述为“具备可重复质量门禁、可靠性持续加固的可部署产品基线”，不能描述为“成熟企业生产系统已签收”。

## 21.10 MinIO 完整性门禁与第六轮企业复审

### 已实现

1. `S3ObjectStorage.put` 现在把完整 SHA-256 写入 S3 用户元数据；`read` 在关闭响应流的 `finally` 路径下校验读取上限、Content-Length 和内容哈希。
2. 对升级前的对象保持兼容：没有 `sha256` 元数据时，使用 ContentFlow 既有对象键中的 16 位哈希前缀校验；既无元数据又不符合旧键规则的对象会被拒绝。
3. 新增 `tests/test_minio_integration.py`。测试使用随机 bucket，覆盖真实上传/读取、Content-Type/哈希元数据、错误 bucket、读取/上传上限、错误覆盖篡改检测和旧对象读取；清理同时处理版本、删除标记和普通对象。
4. 后端 CI 使用固定 digest 的无持久卷 MinIO，健康后执行集成测试，最后通过 `if: always()` 停止容器；测试凭据仅为隔离 CI 常量。

### 实际验证

- 真实 MinIO 定向集成测试：`2 passed`；随机 bucket 已清空并删除，无持久卷临时容器已自动移除。
- PostgreSQL/pgvector 与 MinIO 同时启用的全量后端：`56 passed`，分支覆盖率 `77.60%`。
- 对象存储 Ruff、全仓 Ruff、Workflow Schema：通过。
- PostgreSQL 临时数据库残留 0；PostgreSQL 容器已停止；用户既有 MinIO 容器、bucket 和数据卷未启动、未写入、未删除。
- 该校验能发现偶发损坏和未同步元数据的错误覆盖，但不是恶意篡改证明；具备 S3 管理权限的攻击者若同时重写数据与元数据仍需依靠 Object Lock、版本保留、KMS 和不可篡改清单防护。

### 当前仍最关键的 5 个不足

1. 真实公众号、抖音、外部模型/媒体 Provider 仍未完成隔离租户签收；跨平台 Outbox、幂等键、回调验签/去重和成本/质量阈值仍缺。
2. 企业身份与会话仍缺 OIDC/SAML、MFA、刷新旋转与撤销、HttpOnly/SameSite Cookie、CSP、登录审计和 Redis 共享限流。
3. CI 已覆盖 PostgreSQL/pgvector/MinIO，但尚无远程运行与分支保护；Alembic downgrade、浏览器 E2E、双 Worker、SIGKILL、数据库闪断、负载/混沌、独立迁移和灰度回滚未闭环。
4. 可观测与 SRE 仍缺 Metrics/Trace/SLO/告警、Provider 熔断与配额、编排层探针/扩缩、容量模型、On-call 和事故/变更治理。
5. 数据与供应链治理仍缺 RLS、PITR/WAL、当前 head 联合恢复、异地演练、Object Lock/版本保留、生命周期、不可篡改审计、SBOM 和镜像签名。

### 接下来最值得继续做的 5 项改进

1. 在 CI 补 Alembic 升降级、Playwright、双 Worker 长任务、SIGTERM/SIGKILL 和数据库闪断；远程首次通过后启用受保护分支必需检查。
2. 重跑当前 head 的 PostgreSQL+MinIO 联合备份/隔离恢复，并加入对象逐项哈希、S3 版本保留/Object Lock、PITR 和异地恢复证据。
3. 用隔离外部租户完成公众号、抖音与 外部模型/媒体 Provider 成功/失败矩阵，落地回调事件表、验签、去重、Outbox 与渠道级幂等。
4. 以 OIDC 为首选完成企业认证与浏览器会话治理，配套 MFA、短令牌、刷新旋转、撤销、安全 Cookie、CSP、共享限流和登录审计。
5. 接入 Prometheus/OpenTelemetry，建立 API、数据库、对象存储、队列、Worker、Provider、发布、成本与质量 SLO、告警、容量和故障演练。

### 成熟度结论

真实 MinIO 已从“只有适配器和历史整栈证据”升级为每次可重复执行的完整性门禁，测试与质量维度更接近 L3 前段。但对象校验不等于不可篡改存储，CI 配置也不等于远程发布治理；项目综合成熟度仍约为 L2，不能宣称成熟企业生产签收。

## 21.11 当前 head 联合恢复、持久库迁移与第七轮企业复审

### 保护边界

- 继续在 `main` 分支既有未提交修改上增量工作；没有 reset、checkout、暂存、提交或推送。用户提供的账号资料文件未读取、未修改，已用根目录精确规则加入 `.gitignore`，防止误提交。
- 迁移前回滚备份 `.contentflow\backups\20260808-235410` 与当前 head 备份 `.contentflow\backups\20260809-000908` 均保留；没有删除 PostgreSQL/MinIO 数据卷。
- Compose 首次因本地必填加密环境变量缺失而在解析阶段停止；补入仅用于本次本地容器解析的临时值后重跑，没有把临时值写入仓库。

### 已实现与修正

1. `backup_stack.ps1` 默认拒绝 API/Worker 仍在写入、数据库不在预期 Alembic 版本或时间戳目录已存在的备份；检查通过后才创建目录和 `.incomplete` 标记。
2. manifest v2 记录静默模式、运行服务、数据库 SHA-256/大小/迁移版本/表数，以及每个对象的相对路径、大小和完整 SHA-256。
3. `verify_backup.ps1` 在恢复前验证清单与每个对象；数据库恢复到随机临时库，对象恢复到随机临时 bucket、重新下载并逐项验真，最后精确清理它创建的库、bucket 和目录。
4. 当前发布版门槛默认为 `c9e7b4a2d610` 和 18 张表；历史回滚包必须用 `-ExpectedAlembicRevision` 与 `-MinimumPublicTableCount` 显式声明，避免把旧版本误当成当前发布版。
5. 发现持久 `contentflow` 库仍停留在上一迁移头。先验证迁移前联合回滚包，再执行单向增量迁移；迁移前 17 张表的逐表行数全部保持不变，新建 `worker_nodes` 表和 4 个索引。

### 实际恢复证据

| 恢复点 | Alembic | public 表 | 对象 | 结果 |
| --- | --- | ---: | ---: | --- |
| 迁移前回滚包 | `8b6c1f3a9d21` | 17 | 39 | 数据库与对象隔离恢复、逐对象验真通过 |
| 当前 head 恢复点 | `c9e7b4a2d610` | 18 | 39 / 165208 字节 | manifest v2、随机库和随机 bucket 恢复通过 |

两次验证结束后，`contentflow_verify_%` 临时数据库、`contentflow-verify-*` bucket 和 `.contentflow\restore-verification` 子目录残留均为 0；PostgreSQL 与 MinIO 已停止，数据卷保留。`uv sync --locked --no-dev --extra s3` 的依赖阶段与非 editable 项目安装已在隔离临时环境通过，运行时导入通过；Docker Engine 管道缺失，因此镜像构建仍待 Docker Desktop 可用后签收。

### 第七轮复审新增发现：当前最关键的 5 个不足

1. 真实公众号、抖音、外部模型/媒体 Provider 与 RAG 质量/成本仍未形成可审计签收矩阵；抖音无可靠查询键时仍需人工，跨平台 Outbox、回调验签/去重和渠道级幂等未完成。
2. 浏览器令牌仍在 localStorage，Access Token 默认 480 分钟；只有 HS256/exp，没有 Refresh Token 旋转、jti 撤销、OIDC/MFA、安全 Cookie、登录/设备审计和跨实例共享限流。
3. 生产 Dockerfile 已改为使用固定 `uv==0.11.2` 与 `uv sync --locked`，但 Docker 引擎不可用使镜像构建尚未签收；Compose 服务/基础镜像仍多为可变 tag，API 容器启动时自行迁移，远程必需检查、独立迁移 Job、SBOM/签名和灰度回滚仍缺。
4. 可观测和运行平台仍是 L1-L2：没有 Prometheus/OpenTelemetry、SLO/告警、Provider 熔断/配额、资源 requests/limits、自动扩缩、多区高可用和持续故障注入。
5. 本地静默联合恢复已关闭“当前 head 无恢复证据”缺口，但仍没有 PITR/WAL、异地/不可变备份、Object Lock、RPO/RTO、RLS、租户导出/删除/留存和不可篡改审计。

### 下一步最值得继续做的 5 项改进

1. 先用受控公众号资料做无发布副作用的鉴权/scope 测试，再按明确测试内容验收草稿、发布和自动对账；抖音补 OAuth 刷新、确定对账或回调，所有平台落地 Outbox、事件验签与去重。
2. 改造企业身份：OIDC 优先，短 Access Token、旋转 Refresh Token、jti 撤销、HttpOnly/SameSite Cookie、CSP、Redis 限流和登录审计。
3. Docker Desktop 可用后完成锁定镜像构建验收；随后固定基础/服务镜像 digest，拆出独立迁移 Job，加入远程分支保护、Playwright、进程/数据库故障门禁、SBOM、扫描和签名。
4. 接入 Prometheus/OpenTelemetry，定义 API、数据库、队列、Worker、对象存储、Provider、发布对账、成本与质量 SLI/SLO，补告警、容量、资源限制和演练。
5. 建立 PostgreSQL PITR/WAL 与异地不可变备份，启用对象版本/Object Lock，制定并演练 RPO/RTO；随后推进 RLS、数据生命周期与审计归档。

### 成熟度结论

当前 head 的数据库与对象联合恢复已经从“待办”变成可重复、逐项验真的本地证据，灾备维度仍属于 L2：它证明能从静默恢复点恢复，不证明能恢复任意时间点、跨故障域或抵抗管理员级篡改。综合成熟度仍约 L2，测试、队列可靠性和本地恢复局部达到 L2-L3；在真实外部签收、企业 IAM、可复现生产制品、SRE、PITR/异地治理完成前，不能表述为成熟企业生产系统已签收。


## 21.12 数据库会话安全改造与第八轮企业复审

### 本轮已经落地

1. 新增 Alembic 迁移 `f4c2d8e7a190` 与 `auth_sessions` 表。会话绑定用户和当前工作区，只保存 Refresh Token 的 HMAC 摘要，并记录过期、最后使用、撤销原因以及脱敏后的 User-Agent/IP 指纹。
2. Access Token 默认从 480 分钟降为 15 分钟，加入 `sid/jti/iss/aud/iat/nbf/exp`，解码时固定 HS256/typ 并校验必要声明、签发方、受众和时间边界。
3. 浏览器改为 HttpOnly Cookie 会话：Access/Refresh Cookie 使用 SameSite=Lax，生产环境自动 Secure；Cookie 写操作校验精确 Origin。前端不再读写 Bearer Token，并主动删除旧 `contentflow_token`。
4. Refresh Token 默认 14 天且每次刷新轮换；任意已轮换历史令牌再次出现都会记录 `auth.refresh_reuse_detected`、撤销整个会话并拒绝最新令牌；令牌历史独立保存在 `auth_refresh_token_history`。支持当前会话退出和全部会话退出。
5. CLI/自动化调用仍可使用登录响应中的短期 Bearer Token；服务端每次请求回查数据库会话、用户状态、工作区和成员关系，角色变更或成员移除立即生效。
6. 登录、注册、刷新、退出、创建/切换工作区已统一到新会话模型。即使请求同时携带格式合法但无效的 Refresh Cookie 与有效 Access/Bearer Token，退出仍会撤销被有效令牌证明的会话。
7. 部署参数新增 `CONTENTFLOW_ACCESS_TOKEN_MINUTES`、`CONTENTFLOW_REFRESH_TOKEN_DAYS` 与可选 `CONTENTFLOW_AUTH_COOKIE_DOMAIN`；默认建议 host-only Cookie。

### 本轮验证证据

- 认证、安全、迁移专项：27 passed。
- 全量后端：59 passed、5 skipped，分支覆盖率 76.80%（门槛 75%）；跳过项仍是需要运行中 PostgreSQL/MinIO 的集成测试。
- Ruff 全量通过。
- 前端 ESLint 通过。
- Next.js 16.2.12 生产构建和 TypeScript 检查通过。
- SQLite 已覆盖空库迁移、未版本化旧结构、未版本化 `c9e7b4a2d610` 接管升级、Cookie 属性、可信 Origin、刷新轮换/复用、单会话/全会话撤销和 Bearer 兼容。
- 当前仍有 Starlette TestClient/httpx 弃用提示与 Windows `.pytest_cache` 权限提示；均未造成断言失败。

### 环境与敏感数据边界

- 根目录本地平台凭据文件已被精确加入 `.gitignore`，没有读取、打印、复制或加入测试夹具。
- 微信公众号真实联调将在连接器代码和隔离环境准备完成后才读取所需字段；任何请求/响应日志必须继续脱敏。
- 抖音真实发布需要经授权的 `access_token/open_id`、所需 scope 和许可测试素材；长期接入还需 Client Key/Secret、Refresh Token 与回调配置。
- 小红书当前保持审核后 ZIP 导出，不接受个人账号密码；只有获得官方合作方/开放平台资质后才设计自动发布。
- 用户要求：若真实联调缺少字段或外部权限，停止无效尝试并一次性列出所需材料，不反复消耗执行次数。

### 当前环境尚未完成的迁移

仓库 head 已是 `f4c2d8e7a190`，但持久 PostgreSQL/MinIO Compose 栈因 Docker Desktop/Linux Engine 当前不可用而保持停止状态，数据库仍在上一 head `c9e7b4a2d610`。备份和恢复脚本默认门槛已提升为新 head 与至少 20 张表，因此在迁移前会 fail-fast，不会把旧库误签成当前发布版。待 Docker 引擎可用后必须按“现有 c9 回滚点 -> 迁移 f4 -> 新备份 -> 随机库和随机 bucket 恢复复验”的顺序签收。

### 第八轮复审：当前最关键的 5 个不足

1. **真实外部业务仍未签收**：微信公众号、抖音、外部模型/媒体 Provider 缺受控租户的成功、超时、限流、授权过期、审核拒绝和成本/质量矩阵。
2. **企业身份仍未完成**：本地数据库会话已关闭 localStorage 长令牌、刷新和撤销缺口，但仍无 OIDC/SAML、MFA、企业 IdP 生命周期、设备会话管理、异常登录检测、nonce/strict-dynamic CSP 与非对称签名密钥轮换。
3. **跨平台发布一致性仍不完整**：缺统一 Outbox、平台幂等键、回调验签/去重和抖音无查询键场景的自动收敛，不能承诺严格端到端 exactly-once。
4. **SRE 与规模化证据不足**：缺 Prometheus/OpenTelemetry、Trace、SLO/告警、容量模型、浏览器 E2E、多副本压力、数据库闪断和滚动升级持续演练。
5. **数据治理与发布治理不足**：新 head 联合恢复尚未签收，且仍缺 RLS、PITR/WAL、异地不可变备份、租户导出/删除/留存、不可篡改审计、SBOM、镜像扫描/签名和远程受保护分支证据。

### 下一步最值得继续做的 5 项改进

1. 在不输出凭据的隔离租户中完成微信公众号连接、草稿、可选发布和查询对账矩阵；只有字段或权限确实不足时一次性向用户索取。
2. 引入 OIDC 优先的企业身份层，配套 MFA、账号生命周期、设备/会话管理、异常登录告警、签名密钥轮换、nonce/strict-dynamic CSP 与 Redis 共享限流。
3. 建设事务 Outbox、渠道级幂等键、回调验签与事件去重，并为抖音等平台设计确定查询键或强制人工收敛协议。
4. 加入 Playwright 浏览器 E2E、OpenTelemetry/Prometheus、SLO 告警、多副本容量与故障注入门禁。
5. Docker 引擎恢复后先完成 `f4c2d8e7a190` 的持久库迁移和 20 表联合恢复，再推进 RLS、PITR、异地/Object Lock、数据生命周期和制品签名。

### 成熟度结论

本轮把浏览器身份从“长效令牌保存在 localStorage、不可撤销”提升为“短令牌、数据库会话、旋转刷新、复用检测、Cookie 隔离和立即撤销”，身份与应用安全局部达到 L2-L3 基线。项目仍不能称为成熟企业生产系统：真实租户、企业 IdP、跨平台一致性、SRE、当前版本灾备和组织治理尚未共同签收。综合成熟度仍约 L2，但已进一步脱离玩具 Demo。


## 21.13 Web CSP、生产 API 信任边界与第九轮安全复审

### 本轮已经落地

1. 新增 `web/security.ts` 作为 Next.js 和 Sites/vinext 的统一安全头来源，避免两个部署路径分别维护后产生漂移。
2. Web 响应新增 CSP：限制默认源、Base URI、表单目标、框架祖先、对象、脚本、样式、图片、媒体、Worker 和网络连接；明确 `frame-ancestors 'none'` 与 `object-src 'none'`。
3. 生产构建的 `connect-src` 只允许自身和 `NEXT_PUBLIC_CONTENTFLOW_API_BASE` 的精确 Origin；登录页不再允许运行时改写 API 地址，并清理旧的 `contentflow_api_base`。
4. 本地开发仍允许修改 API Base，但只接受完整 HTTP(S) URL，拒绝内嵌账号/密码、查询参数和片段，并只放行 localhost/127.0.0.1 与 HTTPS 开发连接。
5. 仅当构建时 API Base 为 HTTPS 时启用 HSTS 和 `upgrade-insecure-requests`；默认本地 HTTP Compose 不会被错误升级到不存在的 HTTPS 端点。
6. Sites Worker 对普通 HTML/RSC 与图片优化响应统一附加安全头；Next 路由清单使用相同常量。
7. CI 的前端任务固定使用非敏感 HTTPS 示例 API，持续覆盖 HTTPS 生产分支。

### 验证证据

- 前端 ESLint 通过。
- 默认 HTTP 配置下，Sites/vinext 构建与 2 项 Node 渲染/响应头测试通过；断言 CSP 存在且本地不发送 HSTS/强制升级。
- HTTPS 示例配置下，Next.js 生产构建通过；构建路由清单实测包含精确 API Origin、HSTS 和 `upgrade-insecure-requests`，并拒绝 `unsafe-eval`。
- 生产 API Base 固定、旧浏览器 Token 禁写、CSP/HSTS 源码约束均进入前端测试。
- 没有读取平台凭据，也没有重试 Docker 或外部平台调用。

### 仍需诚实保留的边界

当前静态 Next/App Router 输出仍需要 `script-src 'unsafe-inline'` 承载框架内联脚本，因此这是可执行的基础 CSP，不是 nonce/hash + `strict-dynamic` 的最高强度 CSP。要进一步关闭此边界，需要改为可生成 nonce 的动态请求路径或验证稳定脚本哈希，并配套真实浏览器 E2E。数据库共享认证限流已完成；WAF/全业务网关限流、OIDC/MFA 和异常登录检测仍未完成。


## 21.14 PostgreSQL 共享认证限流与第十轮安全复审

### 本轮已经落地

1. 新增 Alembic 迁移 `a73f9c2e4b61` 和 `auth_rate_limits`，仓库当前为 21 张基础表。
2. 登录同时按账号和客户端 IP 限流；注册按客户端 IP 限流；刷新同时按会话族和客户端 IP 限流。生产环境禁止关闭该能力。
3. 限流键使用带作用域 HMAC，数据库不保存邮箱、IP、Session ID 明文；窗口过期记录在认证流量中自动清理。
4. 同一 PostgreSQL 限流键先获取事务级 advisory lock，再读取/创建行并计数；多个 API 副本不会因“空行并发插入”同时放行。
5. 达到门槛返回统一 JSON 错误、HTTP 429 和 `Retry-After`；统一异常处理器现在保留标准异常响应头。
6. 成功登录只清除账号维度计数，不清除 IP 维度计数，避免攻击者用自己的正确账号重置来源 IP 配额。
7. 默认完全忽略 `X-Forwarded-For`；只有显式设置 `CONTENTFLOW_TRUSTED_PROXY_HOPS` 后才从右侧可信代理链提取客户端地址，避免伪造请求头绕过。
8. 新增生产可调参数：窗口、阻断时间、登录账号/IP、注册 IP、刷新会话/IP 门槛；Compose 和环境示例已同步。
9. 未版本化数据库接管可区分 c9、f4 与 a73，并继续拒绝同名但缺列的半成品增量表。

### 验证证据

- 认证/安全/迁移专项：34 passed。
- 最终全量后端：68 passed、6 skipped，分支覆盖率 77.49%（门槛 75%）。
- 本地跳过项为 4 项 PostgreSQL 和 2 项 MinIO 集成测试；其中新增 PostgreSQL 并发限流门禁将在 CI 验证同一键严格“一次 200、一次 429”。
- Ruff 全仓通过；Alembic 唯一 head 为 `a73f9c2e4b61`。
- Compose 配置解析和 PowerShell 备份脚本解析通过。
- 前端 CSP/Sites/Next 的上一轮门禁继续通过，未改变依赖锁。

### 当前环境与剩余边界

持久 Compose 数据库仍在 `c9e7b4a2d610`，没有在 Docker 引擎不可用时强行迁移。恢复后应按 `c9 -> f4 -> a73` 升级，随后生成 21 表联合备份并在随机 PostgreSQL 数据库和随机 bucket 中复验。

数据库共享限流关闭了多副本认证端点的基础暴力尝试缺口，但不等于 WAF、DDoS 防护或全业务 API 配额平台。仍需边缘网关的全局/IP/租户策略、异常登录告警、OIDC/MFA、设备管理与真实压力证据。

## 21.15 平台渠道入口/运行时契约一致性复审

复审真实账号准备流程时发现：抖音连接器运行时要求 `access_token + open_id`，但渠道创建入口过去只检查 `access_token`；公众号入口也只检查键名，空白值可以被保存。这会产生“渠道保存成功、异步连接测试才失败”的延迟错误。

本轮已将入口校验与连接器契约统一：抖音必须提供非空 `access_token` 和 `open_id`，其中 `open_id` 可位于加密凭据或渠道配置；公众号必须提供非空 App ID/Secret；小红书仍为无账号密码的审核后导出模式。新增 API 回归覆盖缺少 Open ID、配置提供 Open ID 和空白公众号 Secret，定向 6 passed，全量 68 passed、6 skipped，覆盖率 77.49%。

真实联调仍必须等待目标平台授权和测试素材；入口校验只能证明字段完整，不能证明 scope、账号审核状态、内容权限或平台稳定性。

## 21.16 微信公众号真实草稿签收与 AI 生成追溯

### 真实公众号受控验收

用户明确授权创建测试永久素材和草稿，但未授权公开发布。2026-08-09 在白名单生效后完成以下真实调用：

1. Access Token 获取成功，素材计数与草稿计数接口可用；调用前图片素材 15、草稿 5。
2. 使用内存生成的测试 PNG 和标记 `CF-20260809-1443-9209E2` 调用实际 `WechatConnector.publish`，强制 `auto_publish=false`。
3. 连接器返回 `draft_created`；调用后图片素材 16、草稿 6，分别只增加 1。
4. 请求跟踪确认 `publish_submit_called=false`，未调用 `/cgi-bin/freepublish/submit`，因此没有任何公开发布。
5. 永久图片素材与草稿仍保留在公众号后台。清理时必须先在平台后台核对测试标记，不允许凭数量或时间范围批量删除。
6. 全程没有把 App ID、Secret、Access Token、完整平台响应或草稿标识写入仓库。脱敏证据集中记录在 [外部服务真实验收记录](external_acceptance.md)。

该结果只签收“真实鉴权 + 永久素材 + 不发布草稿”。公开发布、`publish_id/article_id` 自动对账、限流、令牌过期、权限撤销、审核拒绝和不确定结果仍未签收。

### AI 生成追溯能力

1. 新增稳定 Prompt 集版本与逐模板 SHA-256 清单；每次工作流记录实际 Provider、模型和 Embedding 身份。
2. 策划、分平台生成与自动修复均记录调用序号、阶段、平台、开始时间、时延、响应模型、输入输出摘要与字节数。
3. OpenAI 兼容与特定云模型平台响应中的 Token 用量按 Provider 原值保存；Mock 或 Provider 未上报时明确标记 `not_reported`，不估算账单。
4. 追溯数据保存在 `WorkflowRun.result_json.ai_provenance`；成功审计写入 Provider/模型/调用次数/Token 来源，终态失败也保留已完成调用证据。
5. 追溯记录不复制原始 Prompt、知识文本或模型正文；异常只记录脱敏错误类型。
6. 活动页新增按需展开的“生成记录”，最多读取最近 5 次运行，普通查看者也可核对模型、Prompt 版本、调用次数、Token 来源和追踪摘要。
7. API 的运行列表增加 `1..100` 条受限分页参数，避免活动历史无限读取。

### 本轮阶段验证

- AI 追溯、Worker 成功/失败留证和 API 分页定向回归：18 passed。
- 前端 lint、Next.js 生产构建、Sites/vinext 构建与 2 项服务端渲染/源码契约测试通过。
- 全量后端：73 passed、6 skipped，分支覆盖率 78.42%（门槛 75%）；跳过项仍是需要运行中 PostgreSQL/MinIO 的集成测试。
- Ruff 全量、Alembic 唯一 head `a73f9c2e4b61`、`uv.lock` 一致性、Compose 示例环境解析、所有 PowerShell 脚本解析、严格 UTF-8 和 `git diff --check` 通过。
- 前端 `npm audit` 为 0 个已知漏洞；阶段提交只包含项目代码、测试、迁移、运维与文档，继续排除本地平台账密和未确认知识资料；实际提交与推送结果以 Git 历史为准。

## 21.17 Prompt 双人治理、运行时完整性与第十三轮复审

### 已实现并接入主链路

1. 新增 `prompt_releases` 与 Alembic head `b84e0d3f7c92`，当前仓库基线为 22 张业务表；每个工作区按递增版本保存完整的 plan/generate/review Prompt、逐阶段 SHA-256、变更摘要和创建/复核/激活身份与时间。
2. Prompt 版本由 API 保持不可变：只能创建新草稿，没有覆盖或删除接口。创建者不能审批或拒绝自己的草稿；另一名管理员审批后才可激活，激活新版本会退役旧版本，退役版本可以回滚。
3. 数据库用工作区级部分唯一索引保证最多一个 `active` 版本；创建和激活在 PostgreSQL 中锁定 Workspace 行，避免版本号和激活竞态。
4. 激活前与每次工作流运行前都重新计算正文哈希；记录哈希不一致时返回明确冲突或让工作流失败关闭，不会静默使用、回退或调用模型。
5. `AIProvenanceRecorder` 现在记录 `prompt_source`、`prompt_release_id`、版本和实际阶段哈希，并把已审批的 system prompt 传给 OpenAI 兼容 Provider。运行证据仍不复制 Prompt 正文。
6. 管理工作台可查看当前生效来源/版本/哈希，基于当前版本创建草稿，展开待审批正文与完整哈希，并执行审批、拒绝、激活和回滚。审计只保存版本、哈希与决策元数据，不保存正文。
7. 备份/恢复脚本默认门槛同步为 `b84e0d3f7c92` 与至少 22 张 public 表；未版本化旧结构可识别 a73 并向前升级。

### 发布 Outbox 复审纠正

此前记录把“缺发布 Outbox”概括得过宽。当前 `schedule_publish` 在同一个 SQLAlchemy 事务中创建 `PublishJob`、调用 `enqueue_job` 写入带幂等键的 `Job`，请求事务统一提交；`publish.dispatch:{publish_job_id}` 因而已经承担了**事务型命令 Outbox**的职责。Worker 还会在远程调用前持久化 `publishing + dispatch_token`，不确定结果进入对账/人工接管。

仍未完成的是外部平台事件方向的通用能力：回调 Inbox、签名验证、事件去重、渠道原生幂等键、跨平台状态归一和抖音无确定查询键时的自动收敛。因此可以表述为“内部发布命令已具备事务 Outbox 语义”，不能表述为“跨平台端到端 exactly-once 已完成”。本节结论覆盖早期文档中笼统的“完全没有 Outbox”表述。

### 本阶段本地验证

- 全仓 Ruff 通过；Alembic 唯一 head 为 `b84e0d3f7c92`。
- 后端全量 76 passed、6 skipped，分支覆盖率 79.07%（门槛 75%）；本地跳过项仍为需要运行服务的 PostgreSQL/MinIO 集成测试。
- 前端 ESLint、Sites/vinext 构建与 2 项测试、Next.js 生产构建、TypeScript 检查通过；`npm audit --audit-level=high` 为 0 个漏洞。
- Prompt 专项覆盖双人审批、拒绝、激活、回滚、租户隔离、空白/阶段校验、审计不含正文、运行时选择和激活/运行前篡改阻断。
- 提交 `beaeaf183a51a35484b25a2e5d90c870dafa7689` 的 [ContentFlow CI #31359992207](https://github.com/heee000/ContentFlow/actions/runs/31359992207) 已成功；Backend/PostgreSQL/security 与 Frontend/build/security 两个 Job 均通过，新迁移已获得真实 PostgreSQL 远程门禁证据。

### 持续复审：当前仍最关键的 5 个不足

1. **Prompt 有发布治理但没有质量门禁**：仍缺版本绑定的金标评测集、RAG 召回/事实性/安全指标、模型与 Prompt 对比基准、PII/版权/提示注入检查、Provider 价格目录和租户预算；双人审批证明责任分离，不证明质量。
2. **真实渠道签收矩阵不完整**：公众号目前只签收真实素材和不公开草稿；公开发布/最终对账与异常矩阵未签，抖音企业账号未就绪，小红书仍为人工导出；回调 Inbox/验签/去重与渠道原生幂等仍缺。
3. **SRE 与规模化运行证据不足**：尚无 OpenTelemetry/Prometheus、端到端 Trace、SLI/SLO/告警、Provider 熔断/降级、容量与成本看板、多副本耐久、滚动升级和持续故障注入。
4. **企业身份、数据与合规治理未签收**：缺 OIDC/SAML、MFA、企业目录生命周期、异常登录与 SIEM、PostgreSQL RLS、租户导出/删除/留存、数据分类、合法依据和不可篡改审计归档。
5. **制品与灾备证据仍有上限**：新 head 尚未完成持久 PostgreSQL+MinIO 联合恢复；仍缺 PITR/WAL、异地不可变副本、RPO/RTO、SBOM、镜像扫描/签名、独立迁移、环境晋级和灰度回滚。

### 接下来最值得继续做的 5 项改进

1. 建立版本化 AI Eval：固定输入、期望事实/引用与安全规则，记录模型/Prompt/知识快照，对候选 Release 自动比较并设置事实性、召回、安全、PII/版权与成本预算门禁。
2. 在现有公众号授权边界内继续签收公开发布前置条件、最终对账和错误矩阵；抖音账号可用后补 OAuth/上传/发布/回调/指标，并建设通用回调 Inbox、验签、去重和渠道幂等策略。
3. 优先接入 OpenTelemetry + Prometheus，定义 API、队列、Worker、数据库、对象存储、模型调用和发布对账的 SLI/SLO、告警与值班处置，再补负载和故障演练。
4. 推进 OIDC/MFA 和设备会话治理，同时设计 RLS、租户生命周期与审计归档；所有身份/数据策略需用企业 IdP 与 PostgreSQL 的真实集成证据签收。
5. 在新 head 上完成 PostgreSQL+MinIO 联合恢复与 GitHub CI 后，继续补 PITR/异地/Object Lock、锁定 Linux 制品、SBOM/签名、独立迁移 Job、受保护环境晋级和回滚演练。

### 成熟度判断

Prompt 治理从“只能追溯内置常量”提升到“工作区不可变版本、双人审批、可回滚、运行时校验和完整审计”，AI 变更控制局部达到 L2-L3。综合项目仍约为 L2：已有可部署主链路、可靠任务队列、真实公众号草稿证据和较强仓库门禁，但成熟企业完整项目要求质量、渠道、IAM、SRE、合规、制品和跨故障域灾备同时形成长期生产证据。

## 21.18 版本化 Prompt Eval 晋级门禁与第十四轮复审

### 已实现并接入主链路

1. Alembic head 更新为 `c95f1e4a8d73`，新增 `prompt_eval_suites` 与 `prompt_eval_runs`，PostgreSQL public schema 备份门槛增至 24 张表（含 Alembic 版本表与迁移专用向量表）；未版本化接管可从 `b84e0d3f7c92` 安全向前升级，并拒绝只存在半组 Eval 表的结构。
2. 每个工作区可创建递增版本的不可变 Eval 套件。套件以完整 cases JSON 和 canonical SHA-256 固化，必须包含 3 至 60 个用例并覆盖 `plan/generate/review`；每个用例至少有 required path、expected value、required/forbidden substring 之一。
3. Eval 套件创建者不能激活自己的版本；另一名管理员激活新套件时旧套件自动退役。数据库部分唯一索引与 Workspace 行锁保证每个工作区最多一个活动套件并避免版本/激活竞态。
4. 候选 Prompt 通过 `prompt_eval.execute` 进入现有数据库队列，由 Worker 使用候选 Prompt、活动套件和选定 Provider 执行；输出只保存 SHA-256、字节数和确定性断言失败项，不保存模型正文、用例输入或敏感 substring 原文。
5. Provider 异常沿用租约、重试和终态失败处理；最终错误只持久化 `AI prompt evaluation failed (<ErrorType>)` 与脱敏 AI provenance，测试确认 Provider 错误消息中的秘密不会进入运行或审计。
6. Prompt 审批、激活、历史回滚和每次工作流首次模型调用现在都 fail closed：必须存在与当前 Prompt 哈希、活动 Suite ID/哈希、该次运行实际 Provider 和模型完全匹配的 `passed` 运行。切换套件、修改目标模型、使用运行级 Provider override 或探索性/Mock Provider 的旧证据都会立即失效。
7. 管理工作台新增活动套件摘要、完整用例快照、JSON 用例编辑器、双人激活、评测运行/重跑、运行历史和哈希化证据；没有当前通过证据时审批、激活和回滚按钮禁用，服务端仍再次强制校验。
8. 审计新增套件创建/激活、评测排队/完成/错误事件，只记录版本、哈希、计数、Provider/模型标识和状态，不复制 Prompt、case input 或模型输出。
9. 备份/恢复默认门槛同步为 head `c95f1e4a8d73` 与至少 24 张 public 表。

### 本阶段验证证据

- 全仓 Ruff 通过；Alembic 空库升级/降级、b84 未版本化接管、Eval 约束/唯一索引和半组表拒绝均有回归测试。
- 后端全量 81 passed、6 skipped；分支覆盖率 79.43%（CI 门槛 75%）。本机跳过项仍是需要运行服务的 PostgreSQL/MinIO 集成测试。
- Eval 专项覆盖：双人套件激活、无证据阻断、通过/失败断言、套件轮换使旧证据失效、Prompt/Suite 篡改、错误 Provider/模型证据拒绝、多租户隔离、审计/运行不含输入输出正文和 Provider 错误脱敏。
- 前端 ESLint、Next.js 生产构建、Sites/vinext 构建和 2 项渲染测试通过。
- 提交 `4a9f8da4a56330c09e0b1c173f4480471ce29509` 的 [ContentFlow CI #31362922394](https://github.com/heee000/ContentFlow/actions/runs/31362922394) 已成功；Backend 作业在真实 PostgreSQL/pgvector 与 MinIO 服务上完成迁移、集成测试、分支覆盖率和 Python 安全审计，Frontend 作业完成 lint、Sites 测试、Next.js 生产构建和 npm 安全审计。

### 持续复审：当前仍最关键的 5 个不足

1. **Eval 已有确定性晋级门禁，但还不是完整语义金标体系**：当前擅长结构、精确值和字符串契约；尚无 RAG recall/precision、引用真实性、事实性评分、LLM-as-judge 校准、人工金标一致性、提示注入、PII/版权检测、统计置信度和真实费用阈值。
2. **真实渠道签收矩阵仍不完整**：公众号只签收真实永久素材与不公开草稿，公开发布/最终对账和异常矩阵未签；抖音企业账号未就绪，小红书仍是人工导出；平台回调 Inbox、验签、去重和原生幂等仍缺。
3. **SRE 与规模化运行证据不足**：没有 OpenTelemetry/Prometheus、端到端 Trace、SLI/SLO/告警、Provider 熔断/配额、Eval/生成成本看板、多副本耐久、滚动升级和持续故障注入。
4. **企业身份、数据和合规治理未签收**：缺 OIDC/SAML、MFA、SCIM/企业目录生命周期、设备与异常登录响应、PostgreSQL RLS、租户导出/删除/留存、数据分类和不可篡改审计归档。
5. **生产制品和灾备仍有证据上限**：新 c95 head 尚未完成持久 PostgreSQL+MinIO 联合恢复；仍缺 PITR/WAL、异地不可变副本、RPO/RTO、SBOM、镜像扫描/签名、独立迁移、环境晋级和灰度回滚。

### 接下来最值得继续做的 5 项改进

1. 在现有 Eval Registry 上加入版本化知识快照与人工金标，建设 RAG 召回/引用/事实性、注入/PII/版权、格式、安全和真实 Token/费用预算指标；对真实目标模型做候选/基线对比、重复采样与阈值校准。
2. 在公众号现有授权边界内补最终状态与错误矩阵；抖音账号可用后完成 OAuth/上传/发布/回调/指标，并建设通用事件 Inbox、验签、去重和渠道原生幂等策略。
3. 接入 OpenTelemetry + Prometheus，覆盖 API、数据库、队列、Worker、对象存储、AI/Eval 和发布对账，定义 SLO、告警、值班处置、容量与成本预算并持续演练。
4. 推进 OIDC/MFA/SCIM 和设备会话治理，同时落地 PostgreSQL RLS、租户生命周期、数据分类与审计归档，并用真实企业 IdP/PostgreSQL 证据签收。
5. 在 c95 head 上完成 PostgreSQL+MinIO 联合恢复与远程 CI 后，继续补 PITR/异地/Object Lock、锁定 Linux 制品、SBOM/签名、独立迁移 Job、受保护环境晋级和回滚演练。

### 成熟度判断

Prompt/模型变更控制已从“人工审批后直接发布”推进到“不可变版本、双人职责分离、确定性自动 Eval、目标 Provider/模型绑定、切换失效和失败关闭”，AI 治理局部达到 L2-L3。它实质关闭了无评测证据仍可审批的缺口，但不能把结构断言等同于语义质量、合规或成本最优。综合项目仍约为 L2；成熟企业完整项目仍要求真实多平台、语义/安全/成本 Eval、企业 IAM/SRE/合规、签名制品和跨故障域灾备共同形成长期生产证据。

## 21.19 生产受治理 Prompt 强制门禁与第十五轮复审

### 已关闭的生产绕过风险

1. `Settings` 新增 `require_governed_prompts`。开发/离线环境默认关闭以保持可测试性；生产环境必须显式设置 `CONTENTFLOW_REQUIRE_GOVERNED_PROMPTS=true`，否则 API/Worker 启动 fail fast。Compose 的 API 与 Worker 默认启用该门禁。
2. 生产不再允许工作区长期使用 builtin Prompt 生成。没有活动工作区 Release 时，创建运行请求直接返回明确 409，不创建 `WorkflowRun` 或队列 Job；Worker 仍在首次模型调用前复核，防止入队后 Release、Suite、Provider 或模型发生漂移。
3. 即使已有活动 Release，入队前也会校验 Prompt/Suite 完整性，以及与该次请求 Provider/模型匹配的当前 passed Eval；完整性失败返回统一安全错误，普通门禁失败返回可操作原因。
4. 管理 API 新增 `governance_required`、`ready_for_generation` 与 `generation_block_reason`。管理工作台用 active/blocked 徽标、阻断提示和五步初始化顺序展示真实可生成状态，不把“存在内置 Prompt”误报为生产就绪。
5. 运维文档规定受限网络内的双管理员初始化：门禁保持开启，临时允许注册；完成 Eval 双人激活、真实目标模型评测、Prompt 双人审批和激活后关闭注册。禁止通过临时关闭治理门禁完成 bootstrap。

### 本阶段验证证据

- 安全与 Prompt 治理专项 24 passed；全量后端 83 passed、6 skipped，分支覆盖率 79.38%，高于 75% 门槛；本机跳过项仍为需真实服务的 PostgreSQL/MinIO 集成测试。
- Ruff 格式与静态检查通过；前端 ESLint、Sites/vinext 构建与 2 项渲染测试、Next.js 生产构建通过。
- 新回归证明：生产配置关闭治理门禁时启动拒绝；builtin + 强制治理时 readiness 为 blocked；生成请求返回 409 且数据库中没有 `workflow.execute` Job。
- 提交 `47fe3444d9a4a2f7c2c8a284c4e6b0b95fcad4c2` 的 [ContentFlow CI #31364881430](https://github.com/heee000/ContentFlow/actions/runs/31364881430) 已成功：Backend 在真实 PostgreSQL/pgvector 与 MinIO 服务上完成测试、覆盖率和 Python 安全审计，Frontend 完成 lint、Sites、Next.js 构建和 npm 安全审计。

### 持续复审：当前仍存在的 5 个不足

1. **Eval 仍偏结构契约而非语义质量**：缺版本化知识快照、RAG recall/precision、引用支持度、事实性、注入/PII/版权、安全红队、重复采样置信度和真实 Token/费用阈值。
2. **治理初始化仍是人工运维流程**：虽然已有安全顺序与双人约束，但没有一次性邀请令牌、组织级 bootstrap wizard、审批 SLA、break-glass 双人授权、周期复核和签名 Eval 制品。
3. **真实渠道生产矩阵仍不完整**：公众号公开发布/最终 article_id 对账与异常矩阵未签；抖音企业账号未就绪，小红书仍为人工导出，通用事件 Inbox/验签/去重和平台原生幂等不足。
4. **SRE 与成本运营证据不足**：缺 OpenTelemetry/Prometheus、端到端 Trace、SLI/SLO/告警、Provider 熔断/配额、生成/Eval 成本看板、多副本耐久、滚动升级和持续故障注入。
5. **企业安全、合规与灾备仍未共同签收**：OIDC/SAML/MFA/SCIM、RLS、租户生命周期、不可篡改审计、PITR/异地/Object Lock、SBOM/镜像签名、独立迁移和灰度回滚仍缺长期证据。

### 接下来最值得继续做的 5 项改进

1. 扩展 Eval Registry：绑定知识与生成参数快照、人工金标和候选/基线，增加事实性、RAG、注入、PII/版权、安全、统计稳定性及真实账单阈值。
2. 建设组织级安全初始化与治理运维：一次性邀请、bootstrap 完成状态、审批 SLA、双人 break-glass、定期再认证和签名 Eval 数据集/结果制品。
3. 在公众号授权边界内补齐发布/对账异常矩阵；抖音就绪后签收 OAuth、上传、发布、回调和指标，同时统一 Inbox、验签、去重和幂等状态机。
4. 接入 OTel/Prometheus，定义 API、数据库、队列、Worker、对象存储、AI/Eval、发布与成本 SLO，配套告警、容量、值班和混沌演练。
5. 用真实企业 IdP/PostgreSQL 和恢复环境签收 IAM/RLS/租户生命周期/审计归档，再完成 c95 联合恢复、PITR/异地不可变恢复、SBOM/签名、独立迁移与灰度回滚。

### 成熟度判断

本轮把 AI 治理从“有 Release/Eval，但生产仍可停留在 builtin”推进到“生产必须显式启用治理、入队前可见阻断、Worker 二次复核、安全 bootstrap 有操作手册”。AI 变更控制继续位于 L2-L3，综合成熟度仍约 L2：它已是一套可部署、可治理、可持续验证的产品基线，但距离成熟企业完整交付还缺语义质量、组织治理、真实多渠道、SRE/成本、企业 IAM/合规和跨故障域灾备的长期共同证据。

## 21.20 受保护 Prometheus 指标基线与第十六轮复审

### 已实现并接入运行时

1. 使用锁文件固定的 `prometheus-client 0.26.0`，每个 FastAPI 应用使用独立 Registry，避免测试/多应用共享默认全局注册表。
2. `/metrics` 默认关闭且不进入 OpenAPI。生产必须显式开启并提供独立的 32 位以上 Bearer Token；关闭、弱 Token 或复用应用签名/凭据密钥时启动失败。禁用返回 404，未授权返回 401，Collector 异常返回不含内部错误的 503，所有响应均 `no-store`。
3. HTTP Counter/Histogram/In-flight Gauge 只使用固定方法、完整 FastAPI 模板 route 与状态类别。未知方法归入 `OTHER`，原始路径 ID、workspace、用户、对象与异常消息不会成为标签或响应内容。
4. PostgreSQL Collector 在抓取时汇总 Job 状态、可领取任务和最长等待、Worker active/stale/stopped、Workflow/Eval 状态与 `reconciliation_required`。已知状态使用固定集合，异常数据库值归入 `unknown`。
5. Collector 延迟取得当前 Session factory，兼容开发 lifespan 完成迁移后重新配置数据库连接，避免捕获旧 Engine 和 SQLite 文件锁；注册 Collector 时不访问数据库，抓取失败安全关闭。
6. 本阶段不增加数据库迁移。Compose 已把相同配置传入 API 与 Worker，确保生产启动策略一致。

### 本阶段本地验证

- 可观测性/安全/PostgreSQL 专项 26 passed、5 skipped；全量后端 88 passed、7 skipped，分支覆盖率 79.85%，高于 75% 门槛。
- 新测试覆盖：禁用/鉴权/OpenAPI 隐藏、完整模板路由、资源 ID 不泄露、未知 method/status 聚合、数据库 Gauge、Worker active/stale、Collector 异常脱敏和生产密钥约束。
- 本机 PostgreSQL/pgvector 与 MinIO 服务不可用，因此本地跳过 5 项 PostgreSQL 和 2 项 MinIO 集成测试。提交 `fe3ee101799e36dc05e644f51efbca8204cc7b02` 的 [ContentFlow CI #31367481260](https://github.com/heee000/ContentFlow/actions/runs/31367481260) 已成功：Backend 在真实 PostgreSQL/pgvector 与 MinIO 上完成全部测试、Collector 渲染、覆盖率和 Python 安全审计；Frontend 完成 lint、Sites/Next 构建、测试与 npm 安全审计。

### 持续复审：当前仍存在的 5 个不足

1. **指标端点不是完整监控系统**：尚未部署 Prometheus/Alertmanager/Grafana、recording/alert rules、SLO/错误预算、通知路由、值班与告警演练。
2. **端到端关联不足**：没有 OpenTelemetry Trace/exemplar、集中日志和请求—队列—Worker—Provider—平台发布链路关联；Provider 成本/限流、数据库池/慢查询和对象存储错误指标仍缺。
3. **AI Eval 仍偏确定性契约**：语义事实性、RAG 召回/引用、安全红队、PII/版权、统计置信度和真实费用门禁未完成。
4. **真实渠道矩阵未闭合**：公众号公开发布/最终对账和异常矩阵、抖音企业链路、通用回调 Inbox/验签/去重与平台原生幂等仍缺。
5. **企业 IAM、数据、供应链与灾备未共同签收**：OIDC/SAML/MFA/SCIM、RLS、租户生命周期、PITR/异地、SBOM/签名和灰度回滚尚无长期生产证据。

### 接下来最值得继续做的 5 项改进

1. 部署指标平台，提交版本化 Dashboard 与告警规则，定义 API/队列/Worker/发布 SLO、错误预算、通知升级和演练流程。
2. 接入 OpenTelemetry，贯通请求、Job、AI/Eval 与发布 Trace，并补 Provider 成本/限流、数据库、对象存储和平台连接器专用指标。
3. 扩展 Eval Registry，绑定知识/参数快照与人工金标，加入 RAG、事实性、安全、PII/版权、统计与真实账单阈值。
4. 继续公众号异常矩阵；抖音就绪后完成 OAuth/发布/回调/指标，统一事件 Inbox、签名验证、去重与状态收敛。
5. 用企业 IdP、真实 PostgreSQL 和恢复环境签收 IAM/RLS/数据生命周期/PITR，并建设 SBOM、镜像签名、独立迁移与受保护灰度发布。

### 成熟度判断

可观测性从仅有健康检查和结构化日志推进到可安全抓取、低基数、覆盖 API 与核心数据库运行状态的 L2 基线。综合项目仍约为 L2：已经能部署、治理和获得关键运行信号，但成熟企业交付还要求真实监控平台、长期 SLO/告警证据、端到端追踪、语义质量、多渠道、IAM/合规、供应链和跨故障域恢复共同成立。

## 21.21 版本化监控资产与第十七轮复审

### 已交付的监控运行资产

1. Compose 新增可选 `observability` profile：Prometheus 3.13.1 distroless 与 Grafana 13.1.0 均固定多架构 manifest digest，使用独立持久卷；默认业务栈不受 profile 影响。
2. Prometheus 从内部 `api:8000/metrics` 抓取，Bearer Token 通过 `credentials_file` 读取 Compose secret；Prometheus 只 `expose` 内部 9090，不向宿主映射端口。
3. Grafana 管理密码通过独立 Compose secret 注入。一次性 `grafana-secret-check` 要求密码至少 32 字符且不同于指标 Token，失败时 Grafana 不启动，检查命令不输出任何秘密。
4. Grafana 默认只绑定 `127.0.0.1:3301`，关闭匿名访问、注册、Gravatar、版本检查和统计上报；支持显式 root URL 与 Secure Cookie，生产仍必须置于 TLS/VPN/受控网关后。
5. Prometheus 配置包含内部 API 与自身抓取、15 秒采集、30 秒规则计算和可配置 15 天默认本地 retention。
6. 5 条 recording rules 固化总请求速率、5xx 比例、按模板路由 P95、最长队列等待和 Worker 状态聚合；全局数据库 Gauge 使用 `max`，HTTP 指标使用 `sum(rate(...))`，避免多副本重复计数。
7. 8 条告警覆盖 API 不可抓取、5xx、P95、无活跃 Worker、陈旧 Worker、队列等待、人工发布对账和 Prometheus 规则计算失败。每条都有持续时间、warning/critical 严重度和仓库 runbook URL。
8. Grafana 数据源、Dashboard provider 和 11 面板 Operations Overview 全部以只读文件 provisioning；UI 修改不是事实来源。Dashboard 覆盖 API、Worker、队列、Workflow、Prompt Eval 与发布对账。
9. CI 使用同一固定 Prometheus 镜像执行 `check config`、`check rules` 和 `test rules`。行为测试用持续时序验证 7 类业务告警确实在规定时间后触发；Python 契约测试同时约束 secret、镜像摘要、loopback/内部端口、低基数表达式与多副本聚合。

### 本阶段本地证据

- 默认 Compose 与 `--profile observability` 配置均可解析；Docker 引擎当前不可用，因此尚未把 Prometheus/Grafana 容器在本机真实启动，不能把配置解析等同于运行签收。
- 监控资产专项 4 passed；与指标 API 合并专项 7 passed。全量后端 92 passed、7 skipped，分支覆盖率 79.85%；全仓 Ruff 通过。
- `uv.lock` 已加入测试专用 PyYAML 6.0.3。提交 `c9d73101e7318da5fed5e496ad9a78eb7fb09832` 的 [ContentFlow CI #31374854714](https://github.com/heee000/ContentFlow/actions/runs/31374854714) 已完成正式 promtool config/rules/行为测试、真实 PostgreSQL/pgvector、MinIO、覆盖率和双端构建/安全签收。

### 持续复审：当前仍存在的 5 个不足

1. **告警通知闭环仍未签收**：仓库有规则和 runbook，但没有企业 Alertmanager/托管平台 receiver、路由、抑制、静默权限、值班日历、升级策略与真实通知演练；不能声称 7x24 运维已完成。
2. **监控平台仍是单机参考拓扑**：缺 Prometheus/Grafana 高可用、remote-write/长期留存、租户访问控制、备份恢复、容量评估和目标环境真实运行；本机 Docker 不可用也使本轮缺少容器启动证据。
3. **端到端可观测性仍不完整**：缺 OpenTelemetry Trace/exemplar 与集中日志关联，AI Provider 成本/限流、数据库池/慢查询、对象存储和渠道连接器深度指标尚未覆盖。
4. **SLO 阈值尚未以生产数据校准**：当前 5xx/P95/队列阈值是保守起点，缺真实流量基线、SLO/错误预算、多副本负载/耐久和故障注入证据。
5. **产品其余企业短板仍存在**：语义 AI Eval、真实多渠道异常矩阵、OIDC/MFA/SCIM、RLS/数据生命周期、PITR/异地、SBOM/签名与灰度回滚尚未共同签收。

### 接下来最值得继续做的 5 项改进

1. 在目标监控环境接入企业 Alertmanager/托管平台，配置 receiver、分组/抑制/升级、值班与静默权限，并用受控故障逐条验收通知和恢复消息。
2. 建设 Prometheus HA、remote-write/长期留存和 Grafana 企业访问边界；对 Dashboard/规则做版本晋级、备份恢复和容量测试。
3. 接入 OpenTelemetry，贯通 API—Job—Worker—AI/Eval—发布 Trace，并补 Provider 成本/限流、数据库、对象存储与渠道信号。
4. 用真实负载建立 SLI 基线和 SLO/错误预算，校准当前阈值，执行多副本、数据库闪断、Worker 失联和队列积压演练。
5. 并行推进语义 Eval/真实渠道与 IAM/RLS/数据生命周期/PITR/SBOM/签名/灰度发布，避免局部 SRE 改善掩盖企业整体短板。

### 成熟度判断

可观测性已从“安全 instrumentation”推进到“可版本化部署、规则、看板和告警行为测试”的 L2-L3 仓库基线；它解决了无抓取配置、无规则、无看板和无规则行为验证的问题。由于没有真实通知路由、HA/长期存储、目标环境运行、SLO 校准和端到端 Trace，仍不能称为成熟生产 SRE。综合项目继续约为 L2。
## 21.22 供应商中立化纠偏、CI 稳定与第十八轮复审

### 产品边界纠偏

用户已明确：ContentFlow 是独立的内容营销自动化产品，不针对任何单一云厂商或特定模型做产品定位、默认值、专用流程或验收设计。可以采用通用协议和可替换适配器，但所有生产配置必须显式选择，文档和界面不得暗示项目与某一厂商绑定。

### 本轮已完成

1. 文本与 Embedding Provider 收敛为 `mock/hash` 和显式配置的 `openai-compatible`；删除供应商专用地址拼装、密钥、地域、工作区和 provider 分支。
2. 删除预设文本/Embedding 模型名。真实 Provider 必须显式提供 API Base、API Key 和模型名，缺任一项均在启动时失败关闭。
3. 图片/视频由供应商专用实现改为 ContentFlow 中立 HTTP 媒体契约：`POST /images/generations`、`POST /videos/generations`、`GET /videos/generations/{task_id}`。图片支持受限 base64 或下载 URL，视频支持同步完成或异步轮询。
4. HTTP 媒体响应不复制原始错误体；内联内容与下载内容都受大小上限约束，任务 ID 进入路径前编码，凭据只进入 Authorization 请求头；生产必须配置精确下载域名允许列表，初始 URL 与重定向目标都要通过校验。
5. README、架构、运维、用户手册、能力、验收、生产清单和历史成熟度记录均改为供应商中立表述；已跟踪文件专项扫描不再包含被废止的厂商/模型定向引用。
6. GitHub 推送始终使用普通 `git push origin main`，没有 force。邮件来自推送后自动触发的 Actions 失败。Prometheus CI 已补只读占位 metrics secret，使 `promtool check config` 能验证生产 `credentials_file` 而不需要真实秘密。

### 当前验证证据

- 全量后端 `104 passed、7 skipped`，分支覆盖率 `81.11%`，高于 75% 门槛；本机跳过项仍仅为需要真实 PostgreSQL/pgvector 和 MinIO 的集成测试。全仓 Ruff 静态检查与本轮文件格式检查通过。
- 前端 ESLint、Sites/vinext 两项渲染测试、Next.js 生产构建通过；`npm audit` 与 `pip-audit --strict` 均为 0 个已知漏洞，`uv lock --check` 通过。
- 默认与 observability Compose、Alembic 单 head、`.env.example`、严格 UTF-8、YAML/JSON 均通过；同版本 Prometheus 3.13.1 `check config`、`check rules` 和 `test rules` 已在本机通过。
- 提交 `c9d73101e7318da5fed5e496ad9a78eb7fb09832` 的 [ContentFlow CI #31374854714](https://github.com/heee000/ContentFlow/actions/runs/31374854714) 已通过：Backend 16 个步骤、Frontend 11 个步骤均成功，包含 promtool、真实 PostgreSQL/pgvector、MinIO、104 项后端测试、分支覆盖率和双端依赖审计。

### 持续复审：当前仍存在的 5 个不足

1. **真实模型/媒体服务尚未签收**：中立契约已有实现和 MockTransport 证据，但没有目标服务的成功、超时、限流、鉴权过期、内容审核、成本和质量矩阵。
2. **媒体契约治理仍是第一版**：缺显式协议版本、能力发现、服务端幂等键、取消、回调验签/去重、任务过期和兼容性政策。
3. **AI 网关与成本控制不足**：缺按工作区/模型的预算、配额、并发、熔断、退避、降级、账单核对和供应商切换演练。
4. **密钥与配置治理未企业化**：模型/媒体密钥仍通过环境变量进入应用，尚无 KMS/Vault 动态凭据、轮换审计、配置签名和环境策略证明。
5. **交付治理仍会产生噪声**：Actions 能阻止错误合入后的假签收，但 main 仍可直接推送，缺受保护分支、必需检查、预合并 PR、失败通知分级和面向维护者的状态面板。

### 接下来最值得继续做的 5 项改进

1. 建立供应商无关的 Provider conformance suite，用受控测试服务覆盖协议版本、错误分类、超时/限流、幂等、取消、轮询、下载过期和响应大小。
2. 发布 HTTP 媒体契约 v1 Schema/OpenAPI，加入 capability discovery、Idempotency-Key、Webhook 签名与 Inbox 去重，并保留人工收敛路径。
3. 增加 AI gateway policy：工作区/模型配额、并发、熔断、退避、降级、Token/媒体成本账本和月度账单核对。
4. 接入企业 Secret Manager/KMS，采用文件或工作负载身份注入、双密钥轮换、最小权限和不泄密审计。
5. 启用 PR + 受保护 main + 必需 CI，区分代码失败、外部网络和通知级别；把提交前本地门禁与远程状态汇总进发布清单，减少重复失败邮件。

### 成熟度判断

本轮关闭了“产品默认绑定单一厂商/模型”的定位和实现风险，并保留了可替换的真实 Provider 路径；架构中立性由 L1-L2 提升到 L2 基线。综合成熟度仍约 L2：真正的企业交付还需要真实 Provider 合同签收、协议治理、成本与密钥平台、受保护交付流程，以及既有多渠道、SRE、IAM、数据治理和灾备能力共同形成长期证据。

## 21.23 HTTP Media Contract v1、幂等重试边界与第十九轮复审

### 本轮已完成

1. 发布机器可读的 `docs/contracts/contentflow-media-v1.openapi.yml` 和人类可读的 `docs/media_provider_contract.md`，固定三个端点、Bearer 鉴权、封闭请求 Schema、响应状态与稳定错误信封。
2. 每个媒体请求携带 `ContentFlow-Media-Version: 1`，成功响应必须回显；生成请求强制 8–128 位可打印 ASCII `Idempotency-Key`。Worker 用 workspace、asset、素材类型和 content version 计算 SHA-256 不透明键，同一内容版本重试稳定、版本变化换键，内部 ID 不直接出站。
3. HTTP 适配器只发送 `model`、`prompt`、`size` 和白名单 `ratio/aspect_ratio/duration_seconds/shots`，不再透传整个 `metadata_json`；Prompt、比例、时长、分镜、任务 ID、下载 URL 和内联 base64 均增加边界校验。
4. 修复真实视频路径：工作流内部的 `video_storyboard` 现在进入 v1 视频端点，不会再被 HTTP Provider 当作未知 kind 拒绝。
5. 新增稳定 `MediaProviderError`：408/425/429/5xx 可重试，其余 4xx、版本不兼容、无效 JSON/响应和本地契约错误立即终止；429 等响应的整数 `Retry-After` 进入队列，最多 300 秒。Provider 原始错误体和未知远端状态不进入任务错误。
6. `fail_job` 保留原指数退避，同时支持有界服务端退避提示；Worker 只对永久媒体错误设置 terminal，网络/限流/服务端错误仍按任务上限重试。

### 当前验证证据

- Media Provider、OpenAPI 与 Worker 错误语义专项 `20 passed`，另有 8 个输入边界子用例通过；Ruff 与 `git diff --check` 通过。
- 测试覆盖版本回显、幂等头、同资产同版本键稳定、内容版本换键、参数最小化、`video_storyboard` 路由、400/429 分类、`Retry-After`、无效响应脱敏、内联大小、下载 allowlist 与重定向拒绝。
- 全量后端 `116 passed、7 skipped`，另有 8 个参数子用例通过，分支覆盖率 `81.35%`；全仓 Ruff 通过。本机跳过项仍仅为需要真实 PostgreSQL/pgvector 和 MinIO 的集成测试。
- 前端 ESLint、Sites/vinext 构建与 2 项渲染测试、Next.js 生产构建通过；`npm audit` 与 `pip-audit --strict` 均为 0 个已知漏洞，`uv lock --check` 通过。
- 默认与 observability Compose 使用仅当前命令有效的占位 secret 解析通过；Alembic 保持单 head `c95f1e4a8d73`，关键监控资产/迁移/安全专项 `40 passed`。实现提交 `58238f3fc694da4ab884ed3d0c158b9e49bc593e` 的 [ContentFlow CI #31390831127](https://github.com/heee000/ContentFlow/actions/runs/31390831127) 已成功，Backend 与 Frontend 两个 Job 均通过，补齐真实 PostgreSQL/pgvector、MinIO、Linux、覆盖率、双构建和双端依赖审计证据。

### 持续复审：当前仍存在的 5 个不足

1. **目标媒体服务仍未签收**：仓库端契约和 MockTransport 已成立，但没有真实服务证明相同幂等键不重复生成/计费，也没有鉴权过期、限流、审核、超时成功、下载过期、时延、成本和质量矩阵。
2. **v1 外部任务生命周期不完整**：尚无 capability discovery、取消、任务过期/续期、签名 Webhook、Inbox 去重和明确的兼容/弃用窗口；当前只能依赖轮询与人工收敛。
3. **AI/媒体网关治理仍缺**：没有工作区预算、模型/媒体配额、并发舱壁、熔断、降级、成本账本、账单核对与可审计的服务切换演练。
4. **密钥与配置仍非企业级**：长期 API Key 仍通过环境变量配置，缺 KMS/Vault/工作负载身份、动态短凭据、轮换审计、配置签名和环境策略证明。
5. **整体企业闭环仍不共同成立**：真实多渠道异常矩阵、端到端 Trace/集中日志、SLO/告警到人、OIDC/MFA/SCIM、RLS/数据生命周期、PITR/异地、SBOM/签名、受保护分支和灰度发布仍未同时签收。

### 接下来最值得继续做的 5 项改进

1. 建设独立 conformance test service 与可复现测试向量，再用目标媒体服务覆盖同键重放/冲突、错误/超时/限流/审核、轮询、过期下载、质量和成本矩阵。
2. 设计兼容的媒体契约扩展：能力发现、取消、任务过期、签名 Webhook、事件 Inbox/去重、重放防护与版本弃用政策，并先补端到端状态机测试。
3. 落地 AI gateway policy 和成本账本：工作区预算、并发/速率、熔断/退避/降级、用量归属、月账核对和异常成本告警。
4. 接入企业 Secret Manager/KMS 与工作负载身份，完成双密钥轮换、最小权限、访问审计和不泄密故障演练。
5. 继续真实公众号异常矩阵和抖音就绪后的企业链路，同时推进 OpenTelemetry、企业 IAM/RLS、恢复、签名供应链与 PR 必需检查。

### 成熟度判断

媒体对接从“中立适配器草案”推进到“有机器契约、稳定幂等键、数据最小化、明确重试语义和 Worker 回归证据”的 L2-L3 仓库基线。它显著降低了重试重复计费、永久错误反复消耗和内部元数据外泄风险，但服务端是否遵守契约仍需真实证据；综合项目继续约为 L2，不能据此宣称完成企业生产签收。
## 21.24 受保护 Live Media Conformance、目标信任边界与第二十轮复审

### 本轮已完成

1. 新增供应商中立的 `contentflow-media-conformance` 命令。目标配置只从当前进程的 `CONTENTFLOW_*` 环境变量读取；没有 `--confirm-live-generation` 时在读取环境或联网前拒绝，避免无意触发可能计费的图片/视频生成。
2. 对每种所选素材只创建一个逻辑生成，并用同一幂等键验证同请求重放、同键异请求 `409/idempotency_conflict`、旧协议版本 `400/contract_version_unsupported`、缺少鉴权 `401/403`；异步视频继续轮询到成功或明确失败/超时。对遵约服务，报告给出逻辑计费生成上界。
3. runner 禁止 URL 凭据、query、fragment 和未显式许可的 HTTP，下载地址必须命中精确主机名 allowlist；所有响应均以流式有界方式读取并要求版本头、JSON Content-Type 和封闭信封。输出文件在网络前独占预留，已存在路径不会覆盖，失败时不会留下空证据占位。
4. 脱敏报告只保存状态、耗时、次数和 SHA-256 截断指纹，不保存 API Key、Base URL、模型名、Prompt、幂等键、任务 ID、媒体 URL、base64 或远端原始响应；序列化前还有秘密值扫描和持久化刷盘。
5. OpenAPI v1 现在显式列出 400/401/403/404/409/429/500，规定至少 24 小时幂等保留、两个保留错误码、视频活动/成功/失败终态和复用 `ErrorDetail`；成功视频必须且只能有一个下载地址，失败终态必须有稳定错误详情。
6. API/Worker 生产启动新增外部模型与媒体目标校验：生产只允许 HTTPS，所有环境拒绝 URL 凭据/query/fragment，媒体下载 allowlist 只接受无 scheme、路径、端口或凭据的精确主机名；开发环境仍可显式连接隔离的本地 HTTP 服务。
7. 异步媒体创建会保存不含端点、模型或密钥明文的目标配置指纹；轮询前必须与当前配置一致。配置已变化或历史任务缺少指纹时永久失败并转人工核对，不会把旧任务 ID 发送到新服务。运维文档同时要求变更 Provider/Base/模型前停止入队并排空在途任务。

### 当前验证证据与事实边界

- Media Contract、Provider、生产配置与 live runner 联合专项当前 `59 passed`，另有 24 个参数/子场景通过；Ruff、Python 编译和 OpenAPI YAML 解析通过。
- 测试覆盖显式计费确认、报告预留早于 HTTP Client、独占写入、秘密拒绝、HTTPS/URL/allowlist、响应大小、版本回显、同键重放/冲突、鉴权拒绝、视频轮询/终态、配置指纹稳定性、创建时持久化与漂移前置拒绝。
- 本阶段没有取得目标媒体服务的 Base、API Key、模型名和下载域名，因此没有执行任何真实媒体生成，也没有产生媒体服务费用。runner 已可执行不等于目标服务已签收；外部验收记录继续标为“待验收”。
- runner 无法自行制造目标服务的 408/425/429/5xx、内容审核、凭据过期或下载 URL 过期，也不能从接口响应证明没有重复计费；这些仍需服务端测试钩子、账单和人工质量证据。

### 持续复审：当前仍存在的 5 个不足

1. **目标媒体服务仍无真实签收**：尚无真实生成、时延分位、同键账单去重、输出质量、审核、限流、超时成功查询和下载过期证据。
2. **异常矩阵仍依赖目标服务配合**：runner 能验证可安全主动触发的路径，但没有标准故障注入控制面，无法确定性覆盖 408/425/429/5xx、凭据过期、审核拒绝、恶意媒体和任务长期悬挂。
3. **v1 生命周期仍不完整**：虽声明 `failed/cancelled/expired` 终态，但没有 capability discovery、取消操作、续期、签名 Webhook、事件 Inbox/去重、重放防护与兼容/弃用窗口。
4. **AI/媒体经济与容量治理仍缺**：工作区预算/配额、并发舱壁、熔断、降级、用量/媒体成本账本、月账核对和异常费用告警尚未落地；当前计费上界只是协议断言，不是财务证据。
5. **企业整体能力仍未共同签收**：KMS/工作负载身份、真实多渠道异常矩阵、Trace/集中日志、SLO 告警到人、企业 IAM/RLS/数据生命周期、PITR/异地、SBOM/签名和受保护发布仍有缺口。

### 接下来最值得继续做的 5 项改进

1. 在取得供应商中立目标服务配置后先跑图片、再跑视频 conformance，保存脱敏报告，并联合账单、时延、质量抽检和同键不重复计费证据完成首轮签收。
2. 建设隔离的 conformance test service/故障控制面，确定性注入限流、超时后成功、5xx、鉴权过期、审核拒绝、超大/恶意媒体、过期 URL 和长期任务，形成可重复矩阵。
3. 以向后兼容方式设计 Media Contract v1.1：capabilities、cancel、expiry/renewal、签名 Webhook、Inbox 去重与重放窗口，并完成乱序、重复和迟到事件状态机测试。
4. 落地统一 AI/media gateway policy：按工作区预算、速率/并发、熔断/退避/降级、用量与成本账本、账单核对和异常费用告警；同时接入 Secret Manager/KMS 与工作负载身份。
5. 并行推进真实公众号异常矩阵、抖音账号就绪后的企业链路、OpenTelemetry/SLO、企业 IAM/RLS、恢复与签名供应链，防止单一 Provider 契约改进掩盖产品整体差距。

### 成熟度判断

媒体外部依赖治理已经从“仓库端契约”推进到“可受控执行、失败关闭、脱敏留证且能阻断目标漂移”的 L2-L3 仓库基线。它显著降低了误计费、秘密泄露、不安全端点和旧任务误投新服务的风险，但没有目标服务、账单、质量和异常矩阵证据，不能称为生产 Provider 已签收。综合项目成熟度仍约 L2。

### 变更记录维护

本轮以及后续阶段的“问题、根因、解决方案、涉及文件、验证、剩余边界、提交与 CI”统一维护在 [工程变更台账](engineering_change_log.md)。本交接文档继续记录接手规则、真实调用链和阶段摘要；两者冲突时必须以当前代码和当前 HEAD 的重新验证为准，不得用旧阶段测试结果代替当前签收。

## 21.25 正式媒体运行时加固、完整变更台账与第二十一轮复审

### 本轮接手与改动范围

1. 新增 [工程变更台账](engineering_change_log.md)，把本阶段每项改动固定为“问题与影响、根因、方案、文件、验证、剩余边界、提交/CI”结构；历史阶段只作索引，未知私有文件继续不读取、不修改、不暂存。
2. 正式 HTTP Media Provider 不再无界加载响应：错误体限 64 KiB，成功 JSON 默认硬限 32 MiB 且不能超过内联素材派生上限；所有状态要求版本头、JSON Content-Type、封闭对象和一致的 HTTP/retryable 语义。
3. 图片、视频与错误响应按机器契约执行来源互斥、状态载荷互斥、稳定标识/错误详情、文件名/MIME/模型/API Key/Prompt/Shot 边界；非法 Unicode、错误 Python 类型和畸形 URL 统一在网络或持久化前失败关闭。
4. 媒体下载初始地址及每次重定向都先校验非空精确 allowlist、URL 凭据、fragment、生产 HTTPS 默认端口与主机；违规 Provider URL 在响应接收边界即拒绝，实际下载时再次逐跳校验。网络错误脱敏，临时/永久状态和 Retry-After 有界分类。
5. 异步媒体任务保存非敏感目标配置指纹，轮询前拒绝 Provider/Base/模型漂移；下载安全/大小 ValueError 在 Worker 中转换为脱敏永久错误，不再无效重试。
6. 新增共享 filenames.py 与 network_validation.py，统一本地/S3/Provider/runner 的跨平台安全 basename 和 DNS/IPv4/IPv6 精确主机规则；OpenAPI 新增 PortableFilename、封闭 Shot 和更严格的标识、状态、base64 与终态重试语义。
7. live conformance 报告保持联网前独占预留、不可覆盖和最小化证据；报告 Schema v2 使用每轮随机且不落盘的 HMAC key 生成运行级指纹，秘密扫描覆盖原始/JSON 转义值、API Key、端点、模型、Prompt、幂等键、请求/任务 ID、错误消息、URL 与媒体内容；幂等键首尾空白在网络前拒绝。

### 当前 HEAD 本地验证证据

- Ruff 全仓通过，Python 编译与 git diff --check 通过。
- 后端全量 177 passed、7 skipped、130 subtests passed；分支覆盖率 82.13%，门槛为 75%。7 个跳过项仍是需要运行中 PostgreSQL/MinIO 的集成场景，必须由当前提交的远程 CI 签收。
- 媒体/安全/存储/Worker 契约联合专项 109 passed、130 subtests passed；最终 HMAC/秘密扫描/null ID/幂等键专项 43 passed、74 subtests passed。
- uv lock --check、pip check 通过；pip-audit 在与 CI 相同的 PYTHONUTF8=1 环境下为 No known vulnerabilities found。本机第一次未设置 PYTHONUTF8 时因中文路径输出解码失败，属于工具环境问题，不是漏洞结论。
- 前端 ESLint、Sites/vinext 构建及 2 项 Node 测试、Next.js 生产构建和 npm audit --audit-level=moderate 全部通过，0 vulnerabilities。
- Docker Engine 27.4.0 可用；默认 Compose 与 observability profile 在仅当前进程临时测试密钥下 config --quiet 通过。未启动或重建持久服务，未修改 .env。
- 已跟踪的 15 个 YAML/JSON 全部解析通过；排除历史交接/台账和未知私有资料后的供应商定向词扫描为 0。
- 实现提交 `8a79658952ebac63ed866c24b57940e3286c023b` 与证据提交 `285de6a32de15124d1f7a59b771b6972b086bce9` 已用普通 fast-forward 同步到 `main`；[ContentFlow CI #31560723174](https://github.com/heee000/ContentFlow/actions/runs/31560723174) 为 success，Backend/PostgreSQL/pgvector/MinIO/覆盖率/Python 审计与 Frontend/lint/Sites/Next/npm 审计两个 Job 均成功。该证据签收当前仓库提交，不等于真实媒体目标服务、账单或质量已签收。

### 第二十一轮复审：当前仍存在的 5 个不足

1. 目标媒体服务仍没有真实账号配置、生成、账单幂等、质量、时延和异常矩阵证据；仓库契约通过不等于远端签收。
2. v1 仍缺 capability discovery、取消/续期、签名 Webhook、Inbox 去重与重放窗口，异步生命周期仍以轮询为主。
3. 下载边界虽已纵深校验，但 DNS 解析固定、出口 ACL/代理、mTLS/工作负载身份和 Secret Manager/KMS 仍未形成生产闭环。
4. 工作区预算、速率/并发舱壁、熔断/降级、成本账本、账单核对和异常费用告警尚未落地，不能证明规模化成本可靠性。
5. 项目综合层面仍缺浏览器 E2E、多副本/闪断/压力演练、企业 IAM/RLS/生命周期、PITR/异地、SBOM/镜像签名、受保护分支和灰度回滚的当前证据。

### 接下来最值得继续做的 5 项改进

1. 提交并让当前 GitHub Actions 在 Linux、真实 PostgreSQL/pgvector、MinIO、覆盖率和双端安全门禁上签收本阶段；失败必须修复并新增记录。
2. 建设本地隔离的 Media Contract 测试服务与 schema/property-based fuzz，确定性覆盖 408/425/429/5xx、超时后成功、恶意媒体、过期 URL、悬挂/乱序/重复任务和 Python/OpenAPI 差分。
3. 设计向后兼容 v1.1，增加 capabilities、cancel、expiry/renewal、签名 Webhook、Inbox 去重/重放和弃用窗口，并完成状态机迁移测试。
4. 落地 AI/media gateway 的工作区预算、速率/并发、熔断/退避/降级、成本账本和账单核对，同时接入 KMS/Secret Manager 与工作负载身份。
5. 并行补齐 Playwright E2E、OpenTelemetry/集中日志/SLO 告警、多副本故障演练、企业 IAM/RLS/PITR 和供应链签名，避免媒体单点改进掩盖整体成熟度差距。

### 成熟度判断

本轮把正式媒体路径从“有 v1 客户端”推进为“机器契约、正式运行时、下载器、Worker 与 live runner 多层一致，输入/响应/网络/证据均有失败关闭和回归”的更可靠 L2-L3 子系统基线。综合 ContentFlow 仍约 L2：它已明显不是玩具 Demo，但在真实外部服务、规模化运营、组织治理、灾备和受保护交付共同签收前，仍不能称为成熟企业生产系统。

## 21.26 可验证源码供应链与第二十二轮复审

### 本轮已完成

1. CI 生成只包含 Git 跟踪文件的可复现源码归档、Python 与前端 CycloneDX SBOM，以及严格校验的 `SHA256SUMS`；归档拒绝私钥、本地环境和 `.contentflow` 运行数据。
2. npm SBOM 对同一组件的多安装路径执行保守归并，保留路径、哈希、许可证和依赖关系；身份或未知字段冲突时失败关闭。
3. Pull Request 只运行只读供应链门禁；非 PR 在 Backend、Frontend 与供应链 Job 全部成功后，使用最小 OIDC 权限分别发布 SLSA 来源证明和两份 CycloneDX attestation，并立即按仓库、工作流和源码 SHA 反向验真。
4. 所有 GitHub Action 固定到完整提交 SHA，checkout 禁止持久化令牌；接收方可在 30 天 Artifact 保留期内独立下载、核对摘要和验证证明。

### 验证与边界

- 实现提交 `38ad07c64d60f19330b4f4b42aebcdd328a4cd63` 的 [ContentFlow CI #31691997756](https://github.com/heee000/ContentFlow/actions/runs/31691997756) 四个 Job 全部成功；Artifact `9177772957` 摘要为 `sha256:5dad8fa59cab27e89b7a127dd718270f68faab19bea27b9a988d26ac8fbd481b`，三份证明已发布并反向验证。
- 当前证明绑定源码归档，不是生产 OCI 镜像；前后端 SBOM 描述锁文件/完整测试环境，不等价于裁剪后的容器层。
- 仍缺镜像按 digest 构建/扫描/签名、不可变制品库、部署时策略验签、受保护环境审批、灰度/回滚和长期证明保留。综合项目仍约 L2。

## 21.27 受控脚本辅助发布、状态机防重复与第二十三轮复审

### 本轮已完成

1. 发布任务和渠道新增 `connector/script/manual_export` 显式方式，存入既有 JSON 以兼容当前数据库；小红书既有默认导出与旧排队任务保持兼容。
2. 脚本连接拒绝平台凭据；Worker 在平台副作用前生成带审核版本、素材、manifest、README、固定 Playwright 依赖和逐文件 SHA-256 的确定性 ZIP。
3. 本机运行器仅接受内置官方入口，约束包内路径/重复摘要/渠道 ID，使用按平台和渠道隔离的持久 profile；可尽力上传/填充，但绝不点击最终发布按钮。
4. 发布工作台可选择方式、下载脚本包、登记已发布/未发布，并从尚未产生平台副作用的失败任务显式切换脚本；API 已开始或结果不确定时必须先对账，禁止直接切换。
5. 脚本结果和审计持久化，脚本/人工导出任务只允许人工指标；下载响应提供完整包 SHA-256。旧小红书导出任务由 Worker 归一并持久化 `manual_export`。

### 当前验证证据

- 最终本地后端全量 `194 passed, 7 skipped, 130 subtests passed`，分支覆盖率 `82%`；Ruff、锁文件检查和运行器从 ZIP 读取后的真实编译均通过。
- 覆盖确定性/哈希/无凭据/官方入口/路径边界/无最终 click、端到端状态、明确失败切换、不确定结果阻断、自动指标 API 拒绝、旧任务兼容。
- 前端 ESLint、2 项渲染测试、Next.js 生产构建通过，`npm audit --audit-level=high` 为 0 vulnerabilities。实现提交 `a8f58cfc9449e74ec3c2f9d783dbdd98f728228a` 已同步功能分支与 `main`；远程 CI run `31699801246` 的 PostgreSQL+MinIO 后端/安全、前端、SBOM/可复现源码、SLSA provenance 与双 CycloneDX attest/发布后验证四个 job 全部成功。artifact `9180780462` 摘要为 `sha256:8b852e875588ab637dcdf7f09c4874031424cb59ad10e5bd87f7a29179cd36f1`。
- 未读取、修改或暂存 `knowledge/北京周末 CityWalk 路线助手产品资料.txt`。

### 持续复审与边界

- 小红书/抖音未完成真实浏览器账号 E2E；平台 DOM、登录挑战和条款变化可能使自动填充失效，失效时应退化为人工复制，不能扩张为自动最终提交。
- ZIP 尚无独立代码签名、过期/撤销、浏览器制品校验和不可变证据保留；人工结果也缺证据附件哈希与双人复核。
- 这项能力解决可用性与系统内追踪，不替代官方 API、平台幂等/回调/对账、真实多渠道签收或企业 IAM/SRE/灾备/成本/部署供应链。

### 成熟度判断

多路径发布子系统从“API 或纯人工导出”提升为“API、受控脚本、人工导出均有显式状态与防重复门禁”的 L2-L3 仓库基线。综合 ContentFlow 仍约 L2；真实账号矩阵、平台适配治理、证据防抵赖和整体企业能力闭环前，不能宣称成熟生产签收。

## 21.28 脚本发布证据、双人确认与第二十四轮复审

### 本轮已完成

1. 每个脚本包使用独立 `script_attempt_id`，任务包 SHA-256、证据和确认均绑定该尝试；包 URI 独立保存，不再被最终平台 URL 覆盖。
2. reviewer 必须先上传至少一项受控证据。截图仅接受 PNG/JPEG/WebP，限制字节、像素和单帧，服务端解码并重新编码去元数据；平台导出仅接受 UTF-8 JSON 对象/数组并规范化。原始与规范化 SHA-256 均入库。
3. 证据写入对象存储后校验长度/摘要，工作区授权下载时再次比对数据库摘要；证据列表、下载、上传和确认均经工作区与角色门禁。
4. 脚本渠道可配置 1 人或 2 人确认。双人模式要求不同 reviewer 对同一任务包和证据 manifest 给出一致决定；第一次确认后任务进入 `script_confirmation_pending` 并冻结证据，决定或平台引用冲突失败关闭。
5. Alembic head 更新为 `e28a6b9c4f10`，新增 `publish_evidence_items` 与 `publish_confirmations`；未版本化上一 head 可安全接管，半组表拒绝。备份默认 head 同步，恢复最低 public 表数从 24 更新为 26。

### 当前验证与事实边界

- 迁移编译和 11 项空库/接管/半迁移/降级测试通过；证据规范化与端到端流程覆盖上传、下载摘要、跨工作区 404、冻结、同人重复、冲突和第二人确认。后端全量 `206 passed, 7 skipped, 135 subtests passed`；Ruff、Python 编译、依赖/锁文件、前端 ESLint/生产构建、默认与 observability Compose、灾备脚本语法均通过。实现提交 `20ff9d30179382822af0fca0cabc99152d0dd339` 的首轮 CI `31715306166` 因 MinIO 64 字节 fixture 未同步证据上限失败；修复提交 `8294a09de3581002d6606b53753826537473a6bb` 的 [ContentFlow CI #31715817953](https://github.com/heee000/ContentFlow/actions/runs/31715817953) 四个 Job 全部成功。Backend 为 `213 passed, 135 subtests passed`、覆盖率 83.33%，真实 PostgreSQL/pgvector、MinIO 与依赖审计通过；Artifact `9187195019` 摘要为 `sha256:37cfa753125f76d76a974efe7f6420ff6ee64e2c161d6d7a9bdb33fa82b593bf`，SLSA 与双 CycloneDX 证明已发布并反向验证。
- `CONTENTFLOW_PUBLISH_EVIDENCE_MAX_BYTES` 默认 10 MiB，`CONTENTFLOW_PUBLISH_EVIDENCE_MAX_PIXELS` 默认 4000 万；前者不能超过通用上传上限。
- 当前是应用层可审计证据，不是平台签名回执、可信时间戳或 WORM 法律证据；数据库管理员可绕过 API，写对象后事务失败可留下孤儿，尚无恶意扫描/DLP/法务保留。
- 双人模式只有不同用户与一致性约束，不含岗位冲突、step-up MFA、确认到期/升级/委派。启用前应确保至少两名可用 reviewer，否则任务会合理停留在待二次确认。
- 仍未读取、修改或暂存 `knowledge/北京周末 CityWalk 路线助手产品资料.txt`。

### 第二十四轮成熟度判断

脚本发布子系统已从“人工文本登记”升级为“任务包绑定、受控证据、摘要复验、冻结和可选双人一致确认”的 L2-L3 仓库基线。综合 ContentFlow 仍约 L2：真实平台真实性、组织职责策略、合规证据保留、真实浏览器矩阵、SRE/灾备/IAM/成本和部署供应链共同签收前，不能宣称成熟企业生产系统。

## 21.29 脚本发布期限、发起/确认分离与第二十五轮复审

### 本轮续接与已完成改动

1. 续接中断前未提交的脚本发布治理：每次任务包记录发起人和带时区的过期时间；发起人不能确认自己的尝试，单人策略也要求另一名 reviewer。
2. 任务包默认 24 小时有效，可配置为 15 分钟至 30 天。过期后嵌入运行器、下载、证据上传和结果确认全部失败关闭；工作台只能显式重建新的尝试。
3. 重建先持久化新的尝试和有效期，再尽力删除旧包；任务包/证据写入后若数据库 flush/commit 失败，会回滚并尽力删除刚写入的对象。删除失败被记录，不会伪装已提交状态。
4. 本地和 S3/MinIO 对象存储实现边界约束、幂等删除；测试覆盖跨根目录拒绝、S3 prefix 边界、MinIO 删除和数据库异常补偿。
5. 前端展示发起人和期限，过期后禁用下载/上传/确认并开放重建；一名/两名确认策略分别要求至少 2/3 名工作区用户。
6. 修复 README、平台连接和用户手册中断残留的问号乱码；删除已被正式提交取代的供应链补丁副本和可再生成 coverage 文件。
7. 精确升级 `nanoid` override 到修复高危公告的 `3.3.18`，没有顺带引入 Next/vinext 大版本变更。

### 当前验证状态

- 全仓 Ruff 与 Python 编译通过；全量后端 `208 passed, 7 skipped, 137 subtests passed`，分支覆盖率 82.10%。7 个跳过项需要运行中的 PostgreSQL/pgvector 或 MinIO。
- uv lock、pip check、pip-audit strict 通过；默认和 observability Compose 使用仅当前进程虚拟密钥完成 `config --quiet`。前端 ESLint、2 项渲染测试、Sites 和 Next 生产构建通过，npm audit moderate 为 0 vulnerabilities。
- 本机 Node 22.11 低于项目要求的 22.13+，且 `npm ci` 命中 Windows 可选原生依赖安装问题；产品锁文件只精确改变 `nanoid`，最终以 Linux/Node 22.13 CI 为准。
- 实现提交 `c290f6420e30b365a0af4f7540b1d9b86355c1d7` 已普通快进到功能分支与 `main`；[ContentFlow CI #32568712614](https://github.com/heee000/ContentFlow/actions/runs/32568712614) 四个 Job 全部成功。Backend 在真实 PostgreSQL/pgvector 与 MinIO 上为 `215 passed, 137 subtests passed`、分支覆盖率 83.14%；前端、Python/npm 审计、SBOM/可复现源码均成功。
- Artifact `9474759779` 摘要为 `sha256:272cab55b8bff31e3f3a7bc8b39e2573b79c57a0d7756b2ae99dc49b9ccb5ce2`；SLSA 来源证明和两份 CycloneDX attestation 已发布并反向验真。
- 未读取、修改或暂存 `knowledge/北京周末 CityWalk 路线助手产品资料.txt`，忽略的账号资料也未读取或输出。

### 第二十五轮复审：当前 5 个不足

1. 公众号外的真实平台和媒体 Provider 仍未签收，小红书/抖音脚本没有真实浏览器、DOM 漂移和风控挑战矩阵。
2. 历史脚本尝试及其证据虽保留在数据库，运营 API/UI 仍只聚焦当前尝试，缺归档查询、导出和 legal hold 视图。
3. 应用层发起/确认分离仍不是企业职责分离：缺岗位冲突策略、step-up MFA、委派、升级和管理员例外控制。
4. 仓库没有 LICENSE、SECURITY、CONTRIBUTING、CODEOWNERS、正式 Release 和 main 分支保护，公开协作与发布治理未完成。
5. 综合项目缺公开服务的隐私/条款/账号恢复/防滥用，也缺企业 RLS、HA/PITR/异地、SLO 值班、成本计费和 OCI 晋级闭环。

### 接下来最值得继续做的 5 项改进

1. 让当前提交通过全量本地门禁和 GitHub Linux/PostgreSQL/MinIO/供应链门禁，并回填 commit、run、artifact 与 attestation 证据。
2. 用已有公众号授权补齐草稿查询、超时、限流、Token 失效和人工对账矩阵；抖音账号未就绪前保持明确未验收。
3. 建立个人公开部署参考环境：TLS/WAF、Secret Manager、托管数据库/对象存储、自动备份恢复、关闭注册和可回滚 Release。
4. 增加历史脚本尝试/证据归档视图，并推进 Object Lock、可信时间戳、恶意扫描、DLP、保留/删除和 legal hold。
5. 拆分超大前端/Worker/媒体模块，同时推进浏览器 E2E、企业 IAM/RLS、OpenTelemetry/SLO、容量成本和 OCI 签名。

### 阶段完成度

综合成熟度为 L2+：个人本地部署约 80%-85%，个人公开部署约 60%-65%，公开 Beta 约 45%-50%，企业完整商业项目约 25%-35%。这些比例是目标门禁完成度估计，不是测试覆盖率或工期承诺；详细依据和完成判据见 [2026-08-22 阶段性总结](phase_summary_2026-08-22.md)。

## 21.30 本地 BGE-M3、人工真实素材与白名单复验

### 本轮已实现

1. 新增显式 `bge-m3-local` Embedding Provider：固定 BAAI/bge-m3 官方提交，禁用 remote code，1024 维归一化 Dense 向量，懒加载、进程缓存、知识分块批量推理、线程串行调用和错维度/非有限值失败关闭。
2. 本地推理依赖作为 `local-embeddings` 可选组锁定；PyTorch 明确来自官方 CPU 索引，避免 Linux 容器安装 CUDA 依赖。Docker 镜像安装该 extra，Worker 使用非 root 可写的持久 Hugging Face 缓存卷；供应链审计把官方 `+cpu` 本地版本仅在漏洞查询时映射到公开 advisory 版本，SBOM 恢复精确安装版本。
3. 图片/视频 Provider 新增 `manual`。内容审核通过后资产进入 `awaiting_upload`，不创建 `asset.generate`；未发生外部副作用的遗留生成任务也会安全收敛为待上传。
4. 素材上传支持按 `asset_id` 填充当前版本原占位任务；内容必须已审核，任务/版本/类型必须一致。封面 PNG/JPEG/WebP 经安全解码、像素和单帧限制、重编码去元数据后写入对象存储；成功后同一资产变为 `manual-upload/ready`，避免新增 ready 素材但旧 planned 资产仍阻塞发布。
5. 素材工作台展示“待上传”、目标任务选择和人工上传操作；发布门禁继续要求当前内容版本全部素材 ready。
6. 已忽略的本地 `.env` 切换为真实文本 Provider、固定 BGE-M3 和人工图片/视频，不再保留媒体 Provider 占位配置；未输出任何密钥。

### 当前证据

- 本地 BGE-M3 固定提交真实中文推理：1024 维、全部有限值、L2 范数 1.0；首次下载/加载/推理 212.43 秒。批量实现后，宿主机缓存冷加载+4 段批量推理 31.4 秒、同进程热查询 0.06 秒；Linux Worker 镜像以非 root、完全禁网、offline cache 完成 2 段 1024 维推理，耗时 15.23 秒。
- 最终本地门禁：全仓 Ruff/编译通过；后端 `219 passed, 7 skipped, 143 subtests passed`，分支覆盖率 81.67%；前端 ESLint、Sites 两项渲染测试、Next 生产构建和 npm audit 0 漏洞；uv lock、pip check 通过，CPU-wheel-aware Python 审计 0 漏洞，CycloneDX 96 组件。默认/observability Compose 均通过，API/Worker/Web 镜像完成构建。7 个集成跳过项由当前 GitHub PostgreSQL/MinIO CI 签收，不能用本地业务闭环冒充测试项结果。
- 实现提交 `0282e9bacd6d553553ad0041096a607c5bceb162` 已普通推送到功能分支；[ContentFlow CI #32652773152](https://github.com/heee000/ContentFlow/actions/runs/32652773152) 四个 Job 全部成功。远程真实 PostgreSQL/pgvector、MinIO、后端覆盖率门禁、前端、Python/npm 漏洞审计、96 组件 Python SBOM、可复现源码归档、SLSA 来源证明及双 CycloneDX attestation 均签收；Artifact 为 `contentflow-supply-chain-0282e9bacd6d553553ad0041096a607c5bceb162`（ID `9496650624`，摘要 `sha256:2b9afadcb870ce6be009e6bac980824369112f19ab7ddafc0dcac9c51c853053`）。
- 隔离 `contentflow-live-test` 生产配置栈已运行：PostgreSQL/MinIO/API/Worker/Web 健康；双管理员职责分离、三阶段真实 DeepSeek Eval passed、Prompt Release active、生成门禁 ready。授权微信公众号凭据已加密保存并复验为 `connected`，`auto_publish=false`；本轮没有创建新的微信素材、草稿或公开发布。
- 隔离测试副本经 MinIO 存储、离线 BGE 索引为 4 个知识块；受治理 DeepSeek 工作流生成 1 篇公众号内容并停在 `awaiting_review`，创建 1 个 planned 素材任务等待用户审核和上传真实封面。用户提供的未跟踪知识文件仍未读取、修改或暂存。

### 仍需完成

1. Web 保持在 `http://localhost:3000`；由用户登录主账号，审核当前真实内容并上传实际封面，再创建微信公众号草稿。实际封面视觉质量和本轮草稿结果在完成前保持未签收。
2. 继续排除未知知识文件、本地 `.env`、模型与账号文件；本次实现提交和 CI/供应链证据已经回填，后续记录提交也必须普通推送并重新通过自己的 CI。
3. 后续继续补真实异常矩阵、视频内容探测/恶意扫描、浏览器 E2E、公开部署与企业 IAM/SRE/合规门禁。
## 21.31 立即发布、可证明安全重试与主流程界面

### 本轮已实现

1. 发布 API 支持 `publish_now=true` 立即进入可靠队列，也保留必须填写未来带时区时间的定时模式；客户端 `request_id` 与业务身份组成幂等键。官方 API 新任务要求渠道已经通过连接测试。
2. 新增连接器副作用边界错误：公众号鉴权、封面检查和本地素材读取失败明确标记为外部写入前；Worker 保存阶段、失败历史和审计，鉴权错误使渠道失效，并停止盲目自动退避。
3. reviewer 专用 `POST /publishing/jobs/{id}/retry` 只接受 `retry_safe` 失败，在行锁下复核内容版本、渠道状态和队列租约，再清除旧分发标记并立即入队。通用 Job retry 不能绕过；平台写入可能已经开始的异常仍必须人工对账。
4. 取消接口拒绝已经 running 的分发任务，避免界面取消与 Worker 执行并发。任务队列中的发布失败引导到发布页，不再展示误导性的通用重试。
5. 主导航收敛为工作台与创建→审核→素材→发布四步，其他模块收入“资源与系统”；总览根据真实状态给出一个建议下一步。发布页默认立即执行，定时和高级交付方式渐进展开，明确区分公众号草稿与公开发布。
6. 加入统一 80–180ms 点击、视图与 Toast 反馈、1px 按压、键盘焦点和 reduced-motion；响应式导航和发布表单在平板/手机重排。设计规则已写入 `web/DESIGN.md`。

### 状态机与接手规则

- 只有 `dispatch_failure.retry_safe=true` 才能“安全重试”。若渠道为 invalid，先到平台连接页复测恢复 connected；缺素材则先补齐素材。安全重试会保留旧失败历史。
- `publishing`、`submitted`、`reconciliation_required` 或任何平台写入开始后的错误一律不得安全重试。旧任务不会因新版本上线而追溯改变分类。
- 当前数据库有 4 条旧版本公众号任务处于 `reconciliation_required`；它们均记录 40164 且无 external ID。用户关闭代理后先复测现有微信渠道；这 4 条旧任务仍需在公众号后台确认无草稿后分别登记“确认未发布”，再创建新的立即任务，不要直接重试旧队列 Job。
- 微信渠道继续保持 `auto_publish=false`。本阶段没有执行新的真实平台调用、创建永久素材/草稿或公开发布。

### 本地验证与剩余边界

- 隔离测试配置下全仓 Ruff 通过；后端 `223 passed, 7 skipped, 143 subtests passed`。直接加载本地真实 `.env` 的首轮测试曾因生产 Prompt 门禁、S3、CORS 和 Provider 配置污染出现 17 项失败；不修改 `.env`，用仅进程有效的开发默认覆盖后，受影响 62 项与全量测试均通过，证明不是产品回归。
- 前端 ESLint、2 项渲染契约、vinext Sites 构建和 Next.js/TypeScript 生产构建通过。
- Codex 内置浏览器因本机 Windows sandbox `helper_unknown_error: setup refresh had errors` 无法启动，未完成自动截图/点击视觉验收；用户需要刷新本地 Web 完成主观验收。
- 隔离 `contentflow-live-test` 已无数据清理地重建 API/Worker/Web；PostgreSQL 与 MinIO 保留，API `/health/ready` 返回 database/storage ok，Web 200，Worker 新实例在线。实现提交 `b4b23b76119c31c4e71cef05fe5ad1d816a20521` 已普通推送；[CI #32724822598](https://github.com/heee000/ContentFlow/actions/runs/32724822598) 四个 Job 全部成功，真实 PostgreSQL/pgvector 与 MinIO 为 `230 passed, 143 subtests passed`、分支覆盖率 82.69%，供应链 Artifact `9519101023` 摘要为 `sha256:737dae20923f594ef1858d5d7072392b2e47ae630c5ecb0dc5fe2246c69cc73c`，SLSA 与双 CycloneDX 证明已反向验证。未读取、修改或暂存 `knowledge/北京周末 CityWalk 路线助手产品资料.txt`。

## 21.32 内容工作室 Agent、风格 Skill 与多来源图片增量交接

### 已实现

1. 主生成链路从单轮模板升级为有界内容工作室 Agent：策略候选/证据账本 → 平台化初稿 → 确定性规则修复 → 九维编辑与安全评审 → 深度档位最多一次定向修订 → 安全且不回退才采用。
2. Campaign 可选择声明式 Style Skill、自由风格补充、standard/deep 质量档位和 manual/generate/search/hybrid 图片来源。Style Skill 工作区隔离、语义版本、SHA-256 和启停审计齐全，不允许任何可执行代码。
3. 新增 Openverse/Wikimedia 开放授权搜索、asset.search Worker、待选择素材 UI、人工许可确认、安全下载/图片规范化、来源/作者/许可/摘要追溯和混合候选互斥选择。AI 生成继续走供应商中立 HTTP 媒体契约；没有真实媒体 Provider 时明确失败或人工上传，不伪造结果。
4. ContentItem/ContentRevision 保存 Agent、质量、修订和风格元数据；审核页展示总分、九维分数、主要问题、风格版本和修订状态。管理页可从最新内置 Agent 基线创建受治理 Prompt 草稿，仍必须真实 Eval 和双人激活。
5. Alembic head 为 1a2b3c4d5e6f，public 表门槛 27；迁移器支持上一 head 安全增量接管。备份/恢复脚本默认值、Compose 环境透传、README/架构/手册/运维和示例均已同步。
6. 文本模型单次请求超时从硬编码 60 秒改为 CONTENTFLOW_MODEL_REQUEST_TIMEOUT_SECONDS，默认 120、范围 10–300 秒。可选修订或最终复评的 RuntimeError/TimeoutError 不再丢失已评审原稿，也不会采用未复评修订稿。

### 真实运行证据

- Openverse 无副作用搜索返回 2 个 BY-SA Wikimedia 候选；下载和落地页域名均符合精确白名单，没有下载/选择素材。
- prompt-r2 首次因 60 秒 generate 超时进入 error，系统自动恢复旧 Eval；修改超时后，eval-v2 暴露错误用例设计并 failed，再次自动恢复。修正后的 eval-v3 在 openai-compatible/deepseek-v4-flash 上 passed，由不同管理员完成激活/审批；当前 workspace-r2 和 eval-v3 active，generation_ready=true。
- 新 Prompt 的真实 CityWalk 深度工作流完成前四次模型调用，最终复评 JSON 解析失败；provenance 为 5 次调用、4 成功 1 失败、60784 Provider 上报 Token。没有内容、素材或平台写入。该发现已通过安全降级代码和单元回归修复，但为避免继续消耗真实额度，修复后没有自动再跑第二条真实工作流；下一次用户体验生成即为该路径的最终外部验收。
- contentflow-live-test 无数据卷清理地从 e28a6b9c4f10/26 表迁移到 1a2b3c4d5e6f/27 表；原 1 个活动、2 条内容、6 个发布任务保留，API database/storage ok、Worker 在线、Web 200。
- 迁移前备份 20260825-010604（26 表、2 对象）和迁移后备份 20260825-010724（27 表、2 对象）均完成随机临时数据库/bucket 隔离恢复。
- 最终本地 Ruff、compile、双 Compose 和 PowerShell 语法通过；后端 234 passed、7 skipped、145 subtests passed，覆盖率 80.92%；前端 lint、2 项渲染测试、Sites 与 Next 构建通过。
- 实现提交 `9e94d0f58170b3291e9425bfa04ba167a0b3bd8f` 已普通推送；[CI #32758080637](https://github.com/heee000/ContentFlow/actions/runs/32758080637) 四个 Job 全部成功，Artifact `9531626220` 摘要为 `sha256:71cc728211a092020ca3a369785c59e8edb6b28cdfd3482c0a558ad0562c75f3`，SLSA 与双 CycloneDX attestation 已反向验证。

### 接手与使用注意

- 现有旧活动的 brief 没有显式新字段时，后端按 builtin:editorial、deep、manual 补默认值；用户在活动页保存后才会把选择写回 brief。
- Openverse 候选必须由编辑人员打开原始页面核验许可并确认；系统筛选和元数据不构成法律意见。
- generate/hybrid 的真实 AI 图片需要 CONTENTFLOW_IMAGE_PROVIDER=http、媒体端点/密钥/模型和精确下载域名。当前本地仍是 manual，不能声称真实 AI 图片生成已验收。
- 最新真实 Prompt 已生效；不要直接改数据库 Prompt 正文。后续变更继续走 Eval 套件、目标模型运行、双人审批与激活。
- 失败的真实 run 5ff6da16-a534-4953-9982-378316b3795e 是保留的审计证据，不要把它手工改成成功或通用重试；创建新运行即可使用安全降级代码。
- 未跟踪 knowledge/北京周末 CityWalk 路线助手产品资料.txt 继续视为用户私有文件，禁止读取、暂存、提交或删除。

## 21.33 真实生成进度、素材责任分层与项目辨识增量交接

### 本轮实现

1. `WorkflowRun.current_stage` 新增可观察的真实细阶段：知识检索、策划、逐平台初稿、编辑评审、定向修订、最终复核和人工审核。平台内阶段附带 `当前序号/总平台数`，Web 据此保持多平台进度单调递增。阶段在模型调用前由独立短事务持久化；内容、素材和审计仍在主工作流成功时统一提交，不能为了进度提前暴露半成品。
2. 新增当前工作区运行列表 `GET /api/v1/runs?limit=100`。Web 有活动运行或素材任务时每 2.5 秒刷新，否则 15 秒；顶部与活动卡片显示转圈、阶段文本和离散阶段进度，不显示虚构 ETA。
3. 素材中心按“系统处理中 / 等你操作 / 已就绪”分层。人工上传待办明确解释原因、接受文件、目标项目/内容版本和完成后的发布门禁；生成/检索只显示不确定进度并自动刷新。
4. 每个 Campaign 使用稳定展示码 `CF-XXXXXX`；顶部可按项目过滤。总览在前端按同一作用域重算，`GET /api/v1/metrics/summary?campaign_id=...` 在后端通过发布记录与内容关联做工作区受限汇总；审核、素材、发布、数据复盘和任务队列携带项目、产品和内容上下文，减少相似测试活动误操作。
5. Job API 通过工作区受限的批量关联查询补充只读 `context`，仍不返回 `payload_json`。原始 payload 可能包含内部引用，禁止为了前端方便重新暴露。

### 当前验证与接手注意

- 本地隔离回归合计 `234 passed, 7 skipped, 145 subtests passed`；前端 ESLint、无增量 TypeScript、2 项源码/服务端渲染测试和生产 Next 构建均通过。Compose 只重建 API、Worker、Web，保留 PostgreSQL/MinIO 数据卷；API readiness 返回 database/storage `ok`，Web HTTP 200，Worker 启动无错误。
- 浏览器签收确认桌面和 375px 移动登录页无横向溢出、控制台无 warning/error。Docker 重建后既有登录会话已过期，未猜测密码、改数据库或新建污染性账号；认证后业务页的最终主观走查仍需用户重新登录后完成，不得把本轮写成已做真实点击验收。
- 本机 `.env` 启用了真实 BGE、MinIO 和 Prompt 门禁，直接跑隔离测试会污染默认值；测试时只用进程级 `CONTENTFLOW_EMBEDDING_PROVIDER=hash`、`CONTENTFLOW_STORAGE_BACKEND=local`、`CONTENTFLOW_REQUIRE_GOVERNED_PROMPTS=false` 覆盖，禁止改写真实 `.env`。
- 继续排除 `knowledge/北京周末 CityWalk 路线助手产品资料.txt`、`.env`、模型缓存、备份和运行数据。微信渠道保持 `auto_publish=false`；本轮没有调用任何平台接口，也没有创建素材、草稿或公开发布副作用。
- 实现提交 `1f94450d7fca8be8059bf2d05ab2621f4da8ea35` 已用 John Wang 身份普通推送到 `codex/enterprise-media-runtime`，未使用 force；[ContentFlow CI #33313099365](https://github.com/heee000/ContentFlow/actions/runs/33313099365) 四个 Job 全部成功。CI 在 PostgreSQL/pgvector 与 MinIO 上为 `241 passed, 145 subtests passed`，分支覆盖率 82.07%；Artifact `9732605974` 摘要为 `sha256:f6112e8429e00c891c5b2d73e8ea87445df848e7d2317252d2088f002a5f72bb`，SLSA 与 Python/前端 CycloneDX attestation 已反向验证。

## 21.34 封面来源显式选择与单条任务改线增量交接

### 本轮实现

1. 新活动不再在 Web 中隐式预选人工封面。用户必须在人工上传、AI 生成、开放图库、图库+AI 四张路线卡中明确选择；卡片同步显示当前环境是否已配置对应能力，并说明内容审核后还能针对单条封面改线。后端 Campaign 默认值仍保留 `manual`，避免破坏既有 API 客户端兼容性。
2. 新增认证只读 `GET /api/v1/assets/capabilities`，只返回图片生成、图片搜索和视频生成三个可用性布尔值，不返回内部 Provider、模型、端点或密钥。
3. 新增 editor 权限的 `POST /api/v1/assets/{asset_id}/source`。仅允许已审核、当前内容版本、非混合候选的图片，在没有运行中任务和未就绪时切换 manual/generate/search；工作区、内容和素材均加锁校验。切换清除旧候选、许可、错误和外部任务引用，递增 source_revision，队列键包含 revision/content version，并审计 `asset.source_change`。
4. 素材中心对每条可改线封面显示三路选择。人工上传不再是唯一动作；AI 未配置时保留可发现但禁用的入口，开放图库可直接检索，人工路线才显示文件选择。运行中、ready、旧版本或混合候选保持拒绝改写，防止旧 Worker 结果覆盖用户新选择。

### 当前验证与边界

- 新增能力/改线回归与既有候选选择合计 `5 passed`；Ruff、前端 ESLint、TypeScript/Next 生产构建通过。根目录真实 `.env` 会污染默认单元测试 Settings，首轮全量出现 Prompt 门禁/S3/CORS/Provider 相关失败；未修改 `.env`，用进程级完整隔离配置复跑受影响 `test_api_v2/test_script_publish_flow/test_security/test_worker_v2` 为 `58 passed, 32 subtests passed`。远程 PostgreSQL/MinIO 全量 CI 仍是最终签收门禁。
- 当前真实栈明确报告图片生成不可用、Openverse 可用；没有媒体端点时 API 对 AI 改线 409 失败关闭，界面不会用 mock 冒充。启用真实 AI 生成仍需 ContentFlow Media v1 HTTP 端点、密钥、图片模型和精确下载域名白名单。
- `contentflow-live-test` 保留数据卷重建后 API/Worker/Web/PostgreSQL/MinIO 正常。新的本地浏览器页因重建后会话失效停在登录界面；用户登录后再创建测试内容并停在 `needs_review/awaiting_review`，不得继续审核素材或创建发布任务。
- 继续禁止读取、修改、暂存或提交 `knowledge/北京周末 CityWalk 路线助手产品资料.txt`；`.env`、账号资料、模型缓存、备份和运行数据同样排除。公众号 `auto_publish=false`，本轮未调用平台接口、创建微信永久素材/草稿或公开发布。
- 实现提交 `0b3d015d84c3ea74108a4ccd10d50aa1fda39695` 已用 John Wang 身份普通推送到 `codex/enterprise-media-runtime`，未使用 force；[ContentFlow CI #33315195769](https://github.com/heee000/ContentFlow/actions/runs/33315195769) 四个 Job 全部成功。真实 PostgreSQL/pgvector 与 MinIO 结果为 `242 passed, 145 subtests passed`、覆盖率 82.04%，前端/审计/可复现源码/SLSA/双 CycloneDX 全部签收。Artifact `9733221112` 摘要为 `sha256:21467c243812afc956bb2f27ee0c8498fed740d984e77c9ee6b822481e9e94e3`。

## 21.35 公网测试部署规划增量交接

### 已确认的路线

1. 当前目标是个人、非商业、受控公网测试，不要求中国大陆可用，不开放匿名注册，也不把结果表述为公开 Beta 或商业上线。
2. 首次上线推荐固定公网 IPv4 的境外云主机：Caddy 作为唯一 80/443 入口，同源反代 Next.js 与 FastAPI；该固定 IP 加入微信公众号白名单，从而不受用户本机换网影响。初版曾保留同机 MinIO，当前具体组合已由 21.36 收敛为 R2，不再以本条初版为准。
3. GitHub 承担源码、CI、GHCR 镜像、Release 和受控部署，不承担长期服务运行。GitHub Pages 只能可选发布静态说明/文档。
4. Vercel 只作为 M6 可选前端托管；常驻数据库队列 Worker、BGE-M3 模型缓存、对象存储和微信公众号发布连接器不迁入 Vercel Functions。拆分前端时必须使用同一注册域的 Web/API HTTPS 子域并重新验证 Cookie/CORS/CSP。
5. 完整 M0-M6 路线、文件清单、初始化、真实业务验收、备份、监控和回滚门槛见 [公网测试部署实现计划](public_test_deployment_plan.md)。

### 当前现场与后续规则

- 2026-08-31 宿主机和 `contentflow-live-test` Worker 当时的公网出口均为 `18.183.44.57`；它只适用于当前网络，不是项目持有的固定地址。不得把它写入长期部署模板。
- 规划记录不等于已上线，当前个人公开部署完成度仍保持约 60%-65%。先在仓库完成 `deploy/public-test`、GHCR 镜像和手动批准部署工作流；实际创建云资源时再向用户索取云账号/受限入口、域名、R2 和预算。Embedding 默认继续本地 BGE，不再作为前置选择。
- 首次公网环境默认新建干净数据库和对象，不直接复制本机历史任务与账号；如用户明确要求迁移，PostgreSQL dump、MinIO 对象和凭据解密密钥必须作为原子迁移单元先做隔离恢复。
- 公网测试初期保持 `CONTENTFLOW_ALLOW_REGISTRATION=false`（初始化短窗口除外）和微信公众号 `auto_publish=false`；真实公开发布仍需单独授权。
- 继续禁止读取、修改、暂存或提交 `knowledge/北京周末 CityWalk 路线助手产品资料.txt`；`.env`、平台账密、模型缓存、备份和运行数据继续排除。

## 21.36 公网测试性价比与 Embedding 选型增量交接

### 定案

1. 公网个人测试优先使用 Hetzner 欧洲区 CX23 x86（2 vCPU/4 GiB/40 GiB）和 Primary IPv4；按 2026-06-15 后官方价约 €5.49 + €0.50 IPv4/月，未含税。若 Hetzner 账号/支付/资源不可用，再退到 AWS Lightsail。
2. 不默认改 Embedding API。当前已加载 BGE-M3 的 Worker 实测约 899 MiB，API/Web/PostgreSQL/MinIO 分别约 138/34/66/247 MiB，完整容器约 1.38 GiB；BGE 缓存 2.2 GiB、后端镜像约 2.47 GB。4 GiB 是需要峰值验收的测试起点，不是容量承诺。
3. 公网栈移除同机 MinIO，业务对象和加密 PostgreSQL 备份使用两个隔离的 Cloudflare R2 Bucket/Token。官方 S3 兼容与免费额度不替代真实 ContentFlow 操作矩阵，R2 未签收前保留 MinIO 回退。
4. Web、API、Worker、PostgreSQL 和 Caddy 同机，不用 Vercel；Web 当前约 34 MiB，不值得为个人测试增加跨域 Cookie 与第二套部署面。固定 IPv4 作为微信唯一白名单出口。
5. 详细成本、升级阈值、R2 验收、BGE offline 缓存、AWS 后备和 M0-M6 路线已更新到 [公网测试部署实现计划](public_test_deployment_plan.md)。

### API 后备规则

- 只有 Hetzner 不可用而选择 2 GiB 主机、BGE 持续 OOM/高 swap、多 Worker 扩展或真实中文召回评测更优时才切 Embedding API。
- 当前适配器会发送 `dimensions=1024`，数据库结构可兼容支持缩短维度的模型；切换模型仍必须重建全部知识向量，禁止混用。
- 当前 Embedding 与文本共用 `CONTENTFLOW_MODEL_API_BASE/KEY`。若文本继续 DeepSeek、Embedding 使用其他服务，先增加独立的供应商中立 Embedding Base/Key 配置和安全校验，不要覆盖文本 Provider 配置。
- API 后备优先验证 `text-embedding-3-small`；官方价 $0.02/百万输入 Token。价格低不等于中文 RAG 已签收，仍需检索金标、时延、限流、账单和失败恢复证据。

## 21.37 公网测试部署资产与受控交付增量交接

### 已实现

1. `deploy/public-test` 已包含独立 Compose、Caddy、无密钥 env 模板、部署/备份/隔离恢复脚本和操作手册。公网栈只发布 Caddy 80/443，不运行 MinIO；API、Worker、Web、PostgreSQL 和维护工具均为内部网络，API/Worker 使用同一不可变后端 digest。
2. `scripts/validate_public_test_deployment.py` 渲染 maintenance profile 后 fail-closed 检查所有镜像 digest、端口、Provider、HTTPS、CORS、注册、release SHA 和 Caddy 路由；现有 CI 已接入该检查。
3. OpenAI-compatible Embedding 可使用独立 `EMBEDDING_API_BASE/KEY`，未设置时保持与文本共用 `MODEL_API_BASE/KEY` 的兼容行为。固定 BGE 缓存有 prepare/offline verify manifest；S3 conformance 覆盖单段、multipart、100 MiB、Metadata、读取和精确删除。
4. 公网 PostgreSQL 使用 restic 客户端加密到独立 R2，保留 7 日/4 周；每次已有环境部署前备份，验证恢复只创建随机临时数据库。业务 R2 与备份 R2 的 Bucket/Token 不得复用。
5. 公网注册保持关闭。`contentflow-bootstrap-admin` 从 TTY 读取密码，在空库创建首个 workspace/admin，再按 slug 创建第二 admin；拒绝非空首建、已有邮箱和注册开启状态，并写审计。
6. `build-images.yml` 要求 exact SHA 已有成功 CI，向 GHCR 推送 amd64 镜像、OCI provenance/SBOM、Trivy Critical 报告和 digest Artifact。`deploy-public-test.yml` 仅手工触发、受 Environment 批准，从指定 build run Artifact 读取镜像坐标，以预置 known_hosts 严格 SSH，远端通过备份、迁移、readiness 和 Worker heartbeat 后才更新 current symlink。
7. 本地最终验证为 Ruff 通过；后端 `245 passed, 7 skipped, 145 subtests passed`、覆盖率 80.96%；11 个 YAML、公网 Compose 和 4 个 shell 语法通过；Node 24.19.0 锁文件重建后的 ESLint、2 项 vinext 渲染测试、HTTPS Next 构建和 npm audit 通过。Node 22.11.0 低于项目 engines 并漏装 rolldown Windows 可选绑定，只记录为本机运行时问题，未改锁文件。

### 尚未签收

- 尚未创建 Hetzner/后备 Lightsail 主机、Primary IPv4、DNS、R2 Bucket/Token 或 GitHub Environment；没有执行真实 GHCR build/deploy workflow。
- Caddy ACME、R2 完整矩阵、restic init/备份/月度恢复、BGE 冷缓存下载、4 GiB 峰值、镜像回退和主机丢失恢复都只有仓库实现，必须在目标 Linux 主机留证后才能签收。
- 微信仍需把目标 Worker 固定出口 IPv4 加白名单，并从两个客户端网络验证同一出口和“不公开发布”草稿链路；`auto_publish=false` 不变。
- 镜像当前具备 BuildKit OCI attestation 和扫描报告，但独立签名、注册表保留、防篡改部署验签仍是后续门槛。

### 继续接手规则

- 用户下一步只需提供外部资源或受限部署入口，不需要重新设计拓扑。优先按 `deploy/public-test/README.md` 的资源清单创建主机、域名和双 R2 Bucket。
- 不把本机 `.env`、账号文档、数据库、MinIO 或 BGE cache 直接上传。目标环境从 `env.example` 新建密钥，平台凭据在 Web 重新录入。
- 继续禁止读取、修改、暂存或提交 `knowledge/北京周末 CityWalk 路线助手产品资料.txt`；所有提交只显式暂存本轮文件。

## 21.38 审计哈希链、在线核验与第二十六轮复审增量交接

### 已实现

1. Alembic head 更新为 `6d4e8f9a0b1c`，新增 `audit_chain_heads`，public 表门槛更新为 28；既有审计按 `created_at + id` 确定性回填。未版本化 `1a2b3c4d5e6f` 结构可安全接管，缺头表或缺链字段的半迁移结构会失败关闭。
2. 每条审计保存 chain scope、递增 sequence、previous hash、entry hash 和 integrity version。哈希覆盖事件 ID、工作区/操作者、动作、实体、受限 Request ID、脱敏 metadata 与 UTC 时间；数据库约束拒绝重复序号、错误版本、非正序号和错误哈希长度。
3. PostgreSQL 同一 scope 先取得事务 advisory lock，再锁定/更新独立链头；API 与 Worker 并发追加不会生成分叉。真实 PostgreSQL 双线程测试已由 CI `33648933471` 在 pgvector 服务上签收。
4. 管理员 `GET /api/v1/admin/audit-integrity` 顺序重算完整工作区链，报告 sequence gap、previous hash、entry hash、payload 或 chain head 异常。管理页进入时核验一次并可手动重跑，不把全表核验塞进高频全局轮询。
5. API 只接受 1-64 位安全 `X-Request-ID`；无效或超长值在进入日志/审计前替换为服务端 UUID。备份/恢复默认 revision 与最低表数同步，公网隔离恢复要求精确当前 head。

### 当前本地验证

- 全仓 Ruff、`uv lock --check`、Alembic 单 head、PowerShell 语法、公网部署 fail-closed 校验和 `git diff --check` 通过。
- 审计/迁移/安全专项 `53 passed, 32 subtests passed`；新增接管与半迁移专项后迁移/审计合计 `17 passed`。
- 全量后端 `254 passed, 8 skipped, 145 subtests passed`。8 个跳过项为本机 Docker/PostgreSQL/MinIO 未运行；不能把 SQLite 结果写成 PostgreSQL 并发已本地签收。公网恢复版本/表数防漂移契约另有 `2 passed`。
- 前端 ESLint、Sites/vinext 构建与 2 项渲染测试、Next.js 16.2.12/TypeScript 生产构建通过。

### 仍需保留的边界与接手规则

- 同库哈希链只能检测常见篡改；数据库管理员若同时重算记录和链头仍可伪造。后续应把链头签名并锚定到独立不可变存储/SIEM，加入周期核验、告警、可信时间和保留/取证制度。
- 当前 head 的真实 PostgreSQL 并发、迁移、覆盖率与 MinIO 回归已由本阶段 GitHub CI 签收；28 表 PostgreSQL+对象联合恢复仍需独立演练，CI 迁移不等于灾备签收。
- 下一批高价值缺口是统一分页/增量刷新与前端拆分、PostgreSQL RLS/租户生命周期、OIDC/MFA/step-up、OpenTelemetry/容量故障演练，以及真实渠道/媒体质量成本矩阵。
- 公网部署按用户要求继续冻结；本轮没有购买、创建或配置外部资源。公众号 `auto_publish=false` 不变，没有调用平台接口或创建素材/草稿/公开发布。
- 继续禁止读取、修改、暂存或提交 `knowledge/北京周末 CityWalk 路线助手产品资料.txt`；`.env`、平台账密、模型缓存、备份和运行数据同样排除。

### 首次远程 CI 反馈与修复

1. 实现提交 `52811bb64560751b500aba7bdd529b8982710627` 已普通推送；手工 CI `33648030752` 确认前端 lint/test/build、后端锁文件/lint/部署校验均先通过，但暴露两个门禁问题，不能把该次运行记为签收成功。
2. Linux Python 环境不会安装或解析仓库 `scripts` 命名空间，导致恢复契约测试收集失败。校验逻辑现已移入正式包 `contentflow.migrate`，脚本与测试共同引用；不要通过扩大 setuptools 包范围重新引入维护脚本。
3. 前端依赖审计新命中 `browserslist <=4.28.6` 高危公告；锁文件已把 Browserslist 更新到 `4.28.8`，连同其浏览器数据依赖做兼容范围内更新，没有扩大到应用依赖重构。更新后 moderate 审计为 0。
4. 修复后本地 Ruff 通过，迁移/审计/恢复契约为 `19 passed`。成功运行 [ContentFlow CI #33648933471](https://github.com/heee000/ContentFlow/actions/runs/33648933471) 绑定提交 `3fa5206c4af90ffec6a09e5d2e10474386f579fc`，四个 Job 全部成功；失败运行 `33648030752` 只作为问题发现证据，不作为签收证据。
5. 成功 CI 后端为 `262 passed, 145 subtests passed`，总覆盖率 82.03%；前端 lint/test/build/audit、Python 审计、可复现源码、SLSA 与双 CycloneDX attestation 均通过。Artifact `9853954616` 名为 `contentflow-supply-chain-3fa5206c4af90ffec6a09e5d2e10474386f579fc`，摘要 `sha256:fec0569d78774f692f9bffcd498f947c9f5ede8fbcce23419b2504519df9b9df`。

## 21.39 有界游标、服务端同步水位与第二十七轮复审增量交接

### 本轮实现

1. `campaigns/runs/contents/assets/publishing/jobs/knowledge/documents/jobs` 七类主运营列表统一为有界 keyset 分页；数组响应保持兼容，下一页、页长和服务器同步水位通过 `X-ContentFlow-*` 响应头返回并由 CORS 暴露。游标严格校验版本、键集合、UTC 时间和 ID，`updated_after` 必须带时区。
2. Alembic head 更新为 `7e5f9a0b1c2d`，为七张表建立 `(workspace_id, updated_at, id)` 组合索引；本地与公网备份验证默认 revision 同步，public 表门槛仍为 28。
3. Web 初次用有界追页加载，最多 20 页/2000 条并明确提示截断。后台不再全量读取约 17 组数据，只增量刷新 8 组运营状态；隐藏标签页停止、并发轮询抑制，服务器水位加 2 秒重叠规避时钟偏差/边界竞态。超过 10 页更新时不推进水位，要求手动重载。
4. `test_api_v2` 显式隔离本机真实 Prompt/S3/CORS/Provider 环境，未读取或修改 `.env`。新增分页、租户、篡改、时区与索引迁移回归。

### 当前验证与边界

- Ruff 全仓、锁文件、单 Alembic head、PowerShell 语法、公网部署校验和差异检查通过；隔离真实 `.env` 的全量后端为 `258 passed, 8 skipped, 152 subtests passed`、分支覆盖率 81.15%。8 项本地跳过只因未启动 PostgreSQL/MinIO；真实外部服务已由本阶段远程 CI 单独签收。前端 ESLint、Sites/vinext 构建与 2 项渲染测试、Next.js/TypeScript 生产构建、moderate 依赖审计 0 漏洞均通过。
- 仍未分页的低频/父级集合包括审计、成员、工作区、风格 Skill、内容修订、发布证据和部分 Prompt/Eval 聚合；超过 2000 条的 Web 历史浏览、虚拟列表和服务端搜索仍需实现。
- 前端单文件仍大，活动期仍有 8 路增量请求。下一阶段应拆分领域 query hooks/components，先做视图感知轮询和 Playwright 请求预算，再评估带恢复游标的 SSE/Inbox。
- FORCE RLS 与 owner/migrator/API/Worker 角色拆分没有实施。它会改变数据库权限并可能导致 API/Worker 全面失去访问，必须在用户明确授权后以备份、回滚、分批迁移和跨租户负向测试执行；不得偷偷借分页阶段带入。
- 公网部署仍按用户要求冻结；没有购买、创建或配置外部资源，也没有调用平台接口。继续禁止读取、修改、暂存或提交 `knowledge/北京周末 CityWalk 路线助手产品资料.txt`；`.env`、平台账密、模型缓存、备份和运行数据同样排除。

### 首次远程 CI 反馈与依赖修复

1. 分页实现提交 `f4172f20b1edd45f7d63848113223161bc7ccfc4` 已用 John Wang 身份普通推送；手工 CI `33655246050` 的后端 PostgreSQL/pgvector、MinIO、安全门禁与供应链证据 Job 成功，前端 lint/test/build 成功，但依赖审计失败，所以该 Run 不是本阶段最终签收。
2. 失败原因是新披露公告覆盖锁文件中的传递依赖 `fast-uri 3.1.5`：四条高危主机混淆/SSRF 公告要求离开 `3.0.0 - 3.1.5`。上游 `ajv 8.20.0` 的范围为 `^3.0.1`，因此只将锁定版本更新到兼容的 `3.1.7`；没有增加直接依赖、跨主版本、使用 force 修复或降低 `moderate` 审计门槛。
3. 本地 `npm audit --audit-level=moderate` 已回到 0 漏洞。用符合 engines 的随附 Node 24.19.0 重新 `npm ci` 后，ESLint、Vinext/Sites 构建、2 项 SSR 测试和 Next.js/TypeScript 生产构建通过。默认 Node 22.11.0 会因低于仓库要求跳过 Rolldown Windows 原生可选包，接手时不要按错误提示删除锁文件。
4. `web/package-lock.json` 与首轮记录已经由提交 `08f233d71d760e0b17a9dea5e2b31553ae90ca5f` 普通推送。只有包含后续 Prometheus 确定性修复的 CI 四个 Job 全成功后，才能把新 Run、PostgreSQL/MinIO 测试数量、覆盖率、Artifact 摘要和 SLSA/CycloneDX attestation 补写为最终证据。
5. 修复提交 `08f233d71d760e0b17a9dea5e2b31553ae90ca5f` 的 CI `33656868446` 已证明 fast-uri 修复有效：前端 install/lint/test/build/audit 与 SBOM 成功；但 Prometheus 单测随后暴露跨规则组求值顺序未声明。测试现应以 `group_eval_order` 固定 `contentflow-recording` 先于 `contentflow-alerts`，不得继续只增加 alert `eval_time`，也不得改生产阈值让 CI 变绿。本机 Docker 未运行，最终以固定 Prometheus digest 的新远程 CI 为准。
6. 最终提交 `19eb1773f367362e8a288dfbbd59103f95a47bd5` 的 [ContentFlow CI #33657538096](https://github.com/heee000/ContentFlow/actions/runs/33657538096) 四个 Job 全部成功：固定 Prometheus digest 的配置/13 条规则/规则单测通过；PostgreSQL/pgvector 与 MinIO 后端为 `266 passed, 152 subtests passed`、覆盖率 82.05%；前端审计、Python 审计、可复现源码、SBOM、SLSA 与双 CycloneDX attestation 全部签收。Artifact `9857357210` 摘要为 `sha256:d0c52084bdbf96afaefaa80c9c28e08007c75201a337f2d642245b9109625122`。

## 21.40 控制面有界历史与第二十八轮复审增量交接

### 本轮实现

1. `auth/workspaces`、`admin/members`、`channels`、`style-skills`、内容修订、发布证据/确认、审计以及 Prompt/Eval 历史全部改为默认 100、最大 200 的稳定 keyset 分页；所有查询只取 `limit + 1`。时间游标支持升/降序和连接结果，版本号/链序号使用严格序列游标，畸形或篡改输入返回 422。
2. 新增 `admin/prompt-releases/history`、`admin/prompt-eval/suites` 和 `admin/prompt-eval/runs`。原管理摘要仍返回 active/staged/latest 等控制状态，嵌套历史固定最多 100 条，Web 用分页历史覆盖显示数组；旧客户端结构保持兼容。
3. 风格 Skill 第一页返回有限内置项加一页工作区记录，后续游标页不重复内置项。成员/工作区连接查询的游标实体固定为 Membership，避免用展示对象字段生成错误游标。
4. Alembic head 更新为 `8f6a1b2c3d4e`，增加 11 个控制面/历史复合索引；备份与恢复脚本同步 revision，public 表门槛保持 28。
5. Web 的所有控制面集合、修订和发布证据均使用有界追页，达到 2000 条显式提示；不再存在直接 `api<T[]>` 的可增长集合读取。渠道分页是在第二次无界查询复扫中发现并补齐，不能从首轮清单遗漏。

### 当前验证与接手边界

- 相关测试 fixture 已显式禁用 dotenv 并声明自身 local/hash/mock 配置；最终不设置任何进程级覆盖的全量后端为 `259 passed, 8 skipped, 160 subtests passed`、分支覆盖率 81.27%。8 项跳过仅是本机未启动 PostgreSQL/MinIO。全仓 Ruff、锁文件、单 Alembic head、编译、PowerShell 语法、公网部署 fail-closed 校验、ESLint、Next.js/TypeScript 生产构建、Vinext 构建、2 项渲染测试和 moderate 依赖审计 0 漏洞均通过。最初的 BGE/S3/安全配置污染已保留在工程台账，没有读取或修改真实 `.env`。
- 面向用户的可增长集合已经有界，但 2000 条后的专用历史浏览、服务端搜索/导出和虚拟列表仍未实现。发布证据清单哈希等内部全量一致性扫描仍需先增加业务数量/存储配额，再做容量测试，不能直接删掉完整性校验。
- `contentflow-app.tsx` 仍约 5031 行，活动期仍有 8 路轻量轮询；领域 hooks/components、SSE/Inbox、Playwright 请求预算和断线恢复是下一批可独立交付的改进。
- FORCE RLS 与数据库角色拆分继续等待用户明确的高影响迁移授权；不得偷偷实施。公网部署继续冻结；本轮没有调用平台接口、创建素材/草稿/发布或外部资源。
- 继续禁止读取、修改、暂存或提交 `knowledge/北京周末 CityWalk 路线助手产品资料.txt`；`.env`、账号资料、模型缓存、备份和运行数据同样排除。
- 实现提交 `950323dbe499291fc14758d6674e276b7711e112` 已用 John Wang 身份普通推送，未使用 force；[ContentFlow CI #33663045854](https://github.com/heee000/ContentFlow/actions/runs/33663045854) 四个 Job 全部成功。真实 PostgreSQL/pgvector 与 MinIO 为 `267 passed, 160 subtests passed`、覆盖率 82.18%；前端、安全审计、Prometheus、可复现源码/SBOM、SLSA 和双 CycloneDX attestation 全部签收。Artifact `9859471526` 摘要为 `sha256:e0cdc89417ca2a5883ea878e2be375ed935bedc5de9d20e562716471675b0a27`。

## 21.41 证据/素材业务配额与第二十九轮复审增量交接

### 本轮实现

1. 脚本发布证据新增单尝试数量与累计字节边界，默认分别为 20 个和 50 MiB；连同原有单文件 10 MiB、4000 万像素限制均可配置且有启动校验。上传在持有当前 `PublishJob` 行锁时读取数据库真实 count/sum，重复判断和配额判断均先于对象写入，成功后同步 `script_evidence_count` 与 `script_evidence_total_bytes`。
2. `assets.content_version` 成为非空、可查询字段；迁移 `9a7b2c3d4e5f` 通过 SQLite/PostgreSQL 方言内集合更新从既有 JSON 元数据回填，非法、非正整数、32 位溢出或缺失旧值保守归为版本 1，并新增工作区/内容/版本/状态复合索引。迁移不会把全表 JSON 拉入 Python 内存；JSON 字段暂时保留用于旧客户端和媒体契约兼容，但运行时正确性查询不再依赖 JSON 扫描。
3. 工作流初建、内容改版和无任务人工补建都显式写素材版本；当前内容版本默认最多 20 个素材。内容修改只读取上一版本非 stale 记录，审核、发布、候选互斥和人工上传都在 SQL 层限定当前版本。超过配置的异常遗留集合失败关闭，不继续放大。
4. 已被内容改版淘汰的 `stale` 生成/轮询任务在构建媒体 Provider 之前幂等结束，不再产生无效外部调用或媒体成本。
5. 备份、恢复和公网恢复校验默认 head 同步为 `9a7b2c3d4e5f`；public 表数仍为 28。公网部署仍冻结，没有创建云资源、调用平台 API、生成草稿或发布内容。

### 当前验证与接手边界

- Ruff 与编译检查通过；证据专项为 `46 passed, 37 subtests passed`，素材/迁移/安全/脚本定向回归为 `96 passed, 70 subtests passed`，最终迁移专项为 `15 passed`。本机全量为 `265 passed, 9 skipped, 167 subtests passed`、覆盖率 81.27%；9 项均为本机未启动的 PostgreSQL/MinIO 外部服务用例。前端 ESLint、Vinext 构建与 2 项渲染测试、Next.js 生产构建通过，npm moderate 审计为 0 漏洞；Alembic 单 head、锁文件、部署清单、备份脚本语法、`pip check` 与项目 UTF-8 供应链审计也通过。新增 PostgreSQL 双线程测试验证同一尝试在上限 1 时两个并发上传只能一个落库；本机结果不包含该性质，远程签收见下一条。
- 实现提交 `7bf99aa0b16cf9977faaedfcdf375c05d1c1d031` 已用 John Wang 身份普通推送，未使用 force；[ContentFlow CI #33668048927](https://github.com/heee000/ContentFlow/actions/runs/33668048927) 四个 Job 全部成功。真实 PostgreSQL/pgvector 与 MinIO 为 `274 passed, 167 subtests passed`、覆盖率 82.19%，并发证据上限已被真实数据库签收；前端、安全审计、Prometheus、可复现源码/SBOM、SLSA 与双 CycloneDX attestations 全部通过。Artifact `9861374770` 摘要为 `sha256:facce3722cb5ca1ffb4627fc43b0788a0acc7134810e38faf0414b2c1e3e1c07`。
- Alembic 回归覆盖从旧 head 插入版本 3 和非法版本元数据、升级回填、索引列序、空库 head 以及降级移除字段；大表生产迁移仍需维护窗口和副本容量测量。
- 下一步资源治理不能只跨 Asset/Knowledge/Evidence/PublishJob 做临时求和。可靠的工作区总存储上限还需要统一对象分配账本，在每条写入链路锁定工作区并预留，记录删除待办/失败，处理重复物理对象、事务回滚补偿、旧数据回填与孤儿巡检。
- 继续禁止读取、修改、暂存或提交 `knowledge/北京周末 CityWalk 路线助手产品资料.txt`；`.env`、账号资料、模型缓存、备份和运行数据同样排除。所有提交只显式暂存本轮文件，使用 John Wang 身份普通推送，不使用 force。

## 21.42 统一工作区对象账本与第三十轮复审增量交接

### 本轮实现

1. Alembic head 更新为 `b0c1d2e3f4a5`，public 表门槛从 28 提升为 30；新增 `workspace_storage_usage` 和 `storage_object_allocations`，状态、非负计数、预留形态、持久 URI、删除时间和 SHA-256 长度都有数据库约束。旧未版本化结构只有两张表完整成组且依赖审计链表存在时才允许接管，半迁移继续失败关闭。
2. 知识上传、人工/检索/生成素材、发布证据、脚本包和人工导出统一走 `LedgeredObjectStorage`。写入先在工作区行锁/条件更新下原子预留字节和对象数，物理键包含 allocation UUID，成功后转为正式用量；对象写入后数据库事务回滚会触发物理补偿。未核实大小的旧对象存在时新增写入失败关闭，避免未知存量下继续超卖。
3. 素材替换和过期脚本包不再同步尽力删除后立即释放，而是进入幂等 `storage.delete` Job。物理删除失败保留 `delete_pending`、错误与尝试次数并继续计费，成功后在数据库锁下只释放一次；删除实现按 URI 选择 local/S3 后端，并再次校验对象位于当前工作区前缀。
4. `storage.reconcile` 使用有界页长扫描当前配置后端，释放过期预留、补齐旧大小、识别验证后对象缺失与物理大小变化、报告/可选删除超过 24 小时宽限期的孤儿。跨页携带固定开始水位，扫描期间的新对象不误判；大小变化标为 `integrity_error` 并按实际大小修正用量，缺失对象仍保守计费。
5. 迁移对 Knowledge/Asset/Evidence 的重复旧 URI 做集合检测；共享对象标记 `shared_legacy + integrity_error`，运行时拒绝自动删除，防止替换一个引用时破坏另一个引用。新对象的 allocation UUID 从源头消除同名同内容碰撞。
6. 管理员新增用量、异常清单和对账接口；异常筛选只返回缺失、完整性异常、待删除和已释放预留，不暴露真实 URI。管理页按项目设计系统展示配额/预留/异常表，普通核对与孤儿清理分开；清理必须浏览器再次确认，同一工作区已有仅核对任务时升级为清理会返回 409，避免界面把未执行的清理误报为已排队。

### 已验证与待签收

- 存储账本、迁移、素材替换和脚本包清理专项为 `40 passed`；补齐对象协议透传后定向回归为 `20 passed, 14 subtests passed`，最终本机全量为 `281 passed, 11 skipped, 167 subtests passed`，分支覆盖率 80.52%。11 项均为本机没有启动 PostgreSQL/MinIO 的外部服务用例，不能冒充真实并发或对象后端签收。
- Ruff、Python 编译、锁文件、Alembic 单 head、PowerShell 语法、公网部署 fail-closed 校验、`pip check` 和 Python/npm 漏洞审计均通过；ESLint、Next.js/TypeScript 生产构建、Vinext/Sites 构建和 2 项 SSR 渲染测试通过。本地隔离 API/Web 登录页加载成功且控制台无 warning/error；没有使用真实账号登录或调用平台。本机 WSL Bash 服务被宿主 ACL 拒绝，`verify-backup.sh` 的 Linux 语法/运行仍由远程 CI 签收。
- 实现提交 `69786faad32e3fc231ac6a53ceaa9289972a84f1` 已以 John Wang 身份普通推送；首次 [CI #33678743444](https://github.com/heee000/ContentFlow/actions/runs/33678743444) 由 Linux 暴露暂存文件名在最终截断前已超过 255 字节，前端/SBOM 成功但后端失败，未被记为签收。修复提交 `1496cc9aaf9ab02753dd2e87377cb7a30debcef1` 将暂存名改为固定短随机名；[CI #33679198143](https://github.com/heee000/ContentFlow/actions/runs/33679198143) 四个 Job 全部成功，真实 PostgreSQL/pgvector 与 MinIO 为 `292 passed, 167 subtests passed`、覆盖率 81.62%，Prometheus、前后端依赖审计、可复现源码/SBOM、SLSA 和双 CycloneDX attestations 均签收。Artifact `9865599206` 摘要为 `sha256:5d438a812060e07e0a2d3bfa2bfa3c2f1292c96da3e84becd105daa254639be3`；全程未使用 force。

### 继续保留的边界

- 对账只遍历当前配置后端；切换 local/Bucket 时需独立清点旧后端。列表扫描验证存在性与大小，尚无周期性全对象内容哈希、S3 Metadata/版本历史巡检或云账单归因。
- S3/MinIO 开启版本控制后，逻辑删除可能保留历史版本容量；ContentFlow 配额只统计当前逻辑对象，Bucket 生命周期、Object Lock、保留期和云成本告警仍需单独治理。
- 目前只有替换/过期对象进入删除状态机，没有知识、证据、活动/工作区通用保留、归档、合法删除和备份删除传播。共享旧对象选择保守隔离而不是自动拆引用，需维护人员迁移。
- 存储核对仍由管理员手动发起，缺少周期调度、Prometheus 指标、告警到人、百万对象查询/扫描预算和目标环境故障注入。Local 枚举适合开发，不是生产大规模方案。
- FORCE RLS 与数据库角色拆分继续等待明确高影响授权；公网部署继续冻结。本轮没有创建云资源、调用平台 API、创建素材/草稿或公开发布。
- 继续禁止读取、修改、暂存或提交 `knowledge/北京周末 CityWalk 路线助手产品资料.txt`；`.env`、账号资料、模型缓存、备份和运行数据同样排除。所有提交只显式暂存本轮文件，使用 John Wang 身份普通推送，不使用 force。

## 21.43 自动存储核对、主动告警与第三十一轮复审增量交接

### 本轮实现

1. Worker 在每次领取普通任务前执行有界到期选择；默认每 24 小时、每轮最多 25 个工作区，可通过三项 `CONTENTFLOW_STORAGE_RECONCILE_*` 配置启停和调节。新工作区从创建时间开始计算，迁移旧工作区空水位进入首次扫描。
2. PostgreSQL 对 Workspace 使用 `FOR UPDATE SKIP LOCKED`，每个工作区复用 `storage.reconcile:{workspace_id}:entry` 幂等入口。活动任务不会重复，终态失败按周期冷却，人工管理员仍能立即重启；多 Worker 并发测试要求总计只创建一个任务。
3. 自动任务固定 `delete_orphans=false` 且写入 `trigger=scheduled`；人工任务写 `trigger=manual`。自动计划不会删除孤儿，不改变现有二次确认、只读核对和 409 请求升级保护。
4. Prometheus 新增 allocation 固定状态、used/reserved 字节与对象、unverified、调度开关、超期工作区、终态失败和最老待删除时长。没有 workspace、对象 URI、Job ID 等高基数/敏感标签。
5. 告警增加存储完整性、核对超期/失败和删除超过一天三类规则；Grafana 只读看板从 11 增至 14 个面板。运维手册说明核对、备份、人工删除和告警恢复边界。
6. Alembic head 更新为 `c1d2e3f4a5b6`，新增 `(last_reconciled_at, workspace_id)` 调度索引和不受失败重试刷新影响的 `delete_requested_at`；既有待删记录以旧 `updated_at` 保守回填，不改写已发布迁移。备份、恢复和公网隔离恢复默认 head 同步，public 表门槛保持 30。

### 当前验证与签收

- Ruff、锁文件、公网部署 fail-closed 校验和 Alembic 单 head 通过；设置上下界、SQLite 迁移升降级、存储计划周期/禁用/冷却、Worker 自动执行、管理 API、指标低基数、Prometheus/Grafana 资产和公网恢复契约均已回归。本机全量为 `286 passed, 12 skipped, 171 subtests passed`、分支覆盖率 80.75%。12 项均为本机未启动的 PostgreSQL/MinIO 外部服务用例；远程固定 Prometheus/PostgreSQL/pgvector/MinIO 的补充签收见本节后续证据。
- 本机 Docker/WSL 约束不变；Prometheus 规则行为和真实 PostgreSQL `SKIP LOCKED` 并发以远程 CI 为最终签收。本地 SQLite 通过不能冒充生产并发结论。
- 公网部署继续按用户要求冻结。本轮未读取 `.env`/账号资料，未调用微信公众号或其他平台，未创建素材、草稿、发布或云资源。
- 实现提交 `9c822cc3b175b53d29e5dabb868dc754c0ad795e` 已以 John Wang 身份普通推送，未使用 force。[ContentFlow CI #33683730898](https://github.com/heee000/ContentFlow/actions/runs/33683730898) 四个 Job 全部成功：固定 Prometheus 配置/规则/行为测试、前端与依赖审计、可复现源码/SBOM、SLSA 和双 CycloneDX attestations 均通过；真实 PostgreSQL/pgvector 与 MinIO 为 `298 passed, 171 subtests passed`、覆盖率 81.89%。Artifact `9867276819` 摘要为 `sha256:0401d311bbb01c36c1fa8216c8c7902446a5510b70b18756b3b7bafe02345b2c`。

### 继续保留的边界

- 自动核对只遍历当前配置后端并检查存在性/大小；没有全对象哈希抽检、跨旧/新 Bucket inventory、S3 版本历史或云账单核对。百万对象吞吐、分页预算和目标后端故障注入未验证。
- 没有业务实体通用保留、归档、合法删除、legal hold 和备份删除传播。自动删除继续被明确禁止，不能因已有调度和告警就放开。
- 三条规则仍未接入真实 Alertmanager receiver/值班系统；看板和规则是可交付配置，不是“告警到人”生产签收。
- FORCE RLS、数据库角色拆分仍需用户单独授权；不得随存储阶段偷偷实施。前端历史体验、模块化、SSE/Inbox、OIDC/MFA/SCIM/KMS、PITR/异地和真实平台异常矩阵继续在成熟度记录中保留。
- 继续禁止读取、修改、暂存或提交 `knowledge/北京周末 CityWalk 路线助手产品资料.txt`；`.env`、平台账密、模型缓存、备份和运行数据同样排除。提交只显式暂存本轮文件，使用 John Wang 身份普通推送，不使用 force。

## 21.44 存储计划查询节流与第三十二轮复审增量交接

### 本轮实现

1. 第三十一轮自动核对接入后继续沿调用链复查，确认 `Worker.run_once()` 会按默认 1 秒队列轮询频率查询日级到期工作区；空闲副本和 Worker 数量会线性放大无效数据库查询。
2. 新增 `CONTENTFLOW_STORAGE_RECONCILE_SCHEDULE_POLL_SECONDS=60`。每个 Worker 启动时立即检查一次，此后最多每 60 秒执行一次到期选择；允许范围为 5 至 3600 秒。到期截止使用进程单调时钟，系统时间回拨不会把下一次检查无限推迟。
3. 进程内节流不承担分布式互斥：进程重启会再次立即检查，多副本仍由 Workspace `FOR UPDATE SKIP LOCKED` 和 `storage.reconcile:{workspace_id}:entry` 幂等入口保证只创建一个任务。每工作区 24 小时周期、每轮 25 个工作区及 report-only 安全边界均未改变。
4. 新增设置默认值/上下界与连续空闲轮询回归；原 Worker 端到端核对用例继续证明首次检查不会被节流掉。运维手册明确区分“Worker 检查频率”和“工作区核对周期”，避免把 60 秒误配为对象扫描周期。

### 当前验证与边界

- Ruff 全仓通过；Worker/队列/设置定向为 `60 passed, 45 subtests passed`，本机全量为 `287 passed, 12 skipped, 173 subtests passed`、分支覆盖率 80.76%。12 项均为本机未启动的 PostgreSQL/MinIO 外部服务用例。锁文件、公网部署 fail-closed、`pip check`、Python/npm 漏洞审计、备份脚本语法、前端 lint、Vinext/Sites 构建、2 项 SSR 测试和 Next.js 生产构建均通过。
- 实现提交 `5022dfb9581893576eab140f52ada72c073b7086` 已以 John Wang 身份普通推送；[ContentFlow CI #33685818900](https://github.com/heee000/ContentFlow/actions/runs/33685818900) 四个 Job 全绿，真实 PostgreSQL/pgvector 与 MinIO 为 `299 passed, 173 subtests passed`、分支覆盖率 81.90%。Prometheus、前后端依赖审计、可复现源码/SBOM、SLSA 与双 CycloneDX attestations 均通过；Artifact `9868068572` 摘要为 `sha256:daf540d85f7c8798115add306de02c973b971526bf2f18c82526c197523980b1`。
- 该改动只减少日级存储计划的到期查询，不改变普通 Job 领取频率，也不改变发布自动对账的即时建任务路径。最多会给存储核对增加一个检查间隔的发现延迟；生产可结合 Worker 副本数与工作区规模调整，但不得低于 5 秒。
- 公网部署继续冻结；本轮没有读取 `.env`、账号资料或受保护知识文件，没有调用平台、创建草稿/素材、发布或创建云资源。
- 下一轮继续审查 Worker 维护查询、数据生命周期、前端历史读取和真实运维证据；FORCE RLS 与数据库角色拆分仍等待单独高影响授权。

## 21.45 发布对账恢复扫描公平性与第三十三轮复审增量交接

### 本轮实现

1. 复查 `schedule_pending_publish_reconciliations()` 确认两个真实问题：每次默认 1 秒队列轮询都会执行全局 submitted 查询；查询先取最老 100 条再由 Python 判断是否已有任务，因此前 100 条活动对账可永久挡住后续缺失任务。
2. 查询现在通过 `publish.reconcile:{publish_job_id}` 唯一键外连接 Job，在数据库 `LIMIT` 前排除 queued/retry/running 对账任务；无 Job 或旧 Job 已 succeeded/failed 的记录才进入有界候选集。终态 Job 仍按既有协议原位重置，活动 Job 不重复创建。
3. 新增默认 60 秒、范围 5–3600 秒的 `CONTENTFLOW_PUBLISH_RECONCILIATION_SWEEP_POLL_SECONDS`，以及默认 100、范围 1–1000 的 `CONTENTFLOW_PUBLISH_RECONCILIATION_SWEEP_BATCH_SIZE`。正常发布仍在保存 submitted 结果的同一领域事务中即时创建对账 Job；周期扫描只是恢复兜底，因此默认最多一分钟的恢复延迟不会推迟正常路径。
4. Worker 使用单调时钟独立节流发布恢复扫描，与存储计划、普通队列轮询互不耦合。每个进程启动后立即扫描一次，跨进程仍由 PostgreSQL `FOR UPDATE SKIP LOCKED` 与 Job 唯一幂等键保证正确性。
5. Alembic head 更新为 `d2e3f4a5b6c7`，新增 `publish_jobs(status, updated_at, id)` 恢复扫描索引；ORM、迁移升降级、备份/恢复脚本和公网隔离恢复契约同步，public 表数仍为 30。
6. 配置透传复核发现，上一阶段已经写入 `.env.example` 的存储配额/调度参数未被本地 Compose 显式传给 API/Worker。当前本地两服务和公网共享 backend environment 已同步存储边界与发布恢复参数，公网 env 模板给出无秘密默认值；契约测试防止以后再次出现“文档可配置、容器实际忽略”。

### 当前验证与边界

- Ruff 全仓、Alembic 单 head、SQLite 公平性、连续 Worker 节流、设置上下界、全部迁移、Compose 参数透传与公网恢复契约均通过；本阶段完整定向为 `81 passed, 10 skipped, 49 subtests passed`，本机全量为 `290 passed, 13 skipped, 177 subtests passed`、分支覆盖率 80.77%。13 项均为本机未启动的 PostgreSQL/MinIO 外部服务用例；`uv lock --check`、`pip check`、Python 漏洞审计、两份 PowerShell 备份脚本语法、公网 fail-closed 渲染、前端 ESLint、Vinext/Sites 构建、2 项 SSR 渲染测试、Next.js/TypeScript 生产构建和 npm moderate 审计 0 漏洞均通过。
- 实现提交 `1ba8251be9f75c1c4d52d41cba0ff317c6acffe0` 已以 John Wang 身份普通推送；[ContentFlow CI #33688561251](https://github.com/heee000/ContentFlow/actions/runs/33688561251) 四个 Job 全部成功。真实 PostgreSQL/pgvector 与 MinIO 为 `303 passed, 177 subtests passed`、分支覆盖率 81.91%，从而签收“活动任务位于批次前方、后续缺失项仍被选中”的 PostgreSQL 回归、迁移和对象后端；前端、Prometheus、Python/npm 漏洞审计、可复现源码/SBOM、SLSA 与双 CycloneDX attestations 全部通过。Artifact `9869112197` 摘要为 `sha256:c716780bb2f62e63f443e2ae0ff2c247a0fbb85e0aced27d1cffaad0913862e5`。
- 恢复扫描只处理具备确定 `external_id` 的微信公众号 submitted 任务；不为抖音等不具备可靠查询键的平台做模糊匹配，不调用发布接口，也不改变人工接管与最大尝试语义。
- 普通 `CREATE INDEX` 在大表可能产生锁等待、WAL 与额外空间；个人/低数据量可随常规迁移升级，企业生产应在副本测量并安排维护窗口或改用受控在线索引流程。
- 公网部署继续冻结；本轮未读取 `.env`、平台账密或受保护知识文件，未调用平台、创建素材/草稿、发布或创建云资源。
- 下一轮优先审查 Worker 在数据库瞬断时依赖进程退出/容器重启的恢复与退避证据，并继续保留数据生命周期、数据库纵深隔离、企业 IAM 和真实外部异常矩阵。

## 21.46 Worker 数据库可用性恢复与第三十四轮复审增量交接

### 本轮实现

1. 沿 `run_forever → run_once → handler/fail_job` 复核确认：领取/维护阶段的数据库异常会直接退出进程；处理阶段的异常先进入通用业务失败分支，基础设施瞬断可能被错误记录为 Job/领域失败。对于已经发生外部副作用的任务，这一混淆会放大误重试风险。
2. 新增严格数据库可用性分类，仅覆盖 SQLAlchemy `DisconnectionError`、`InterfaceError`、`OperationalError`、连接池 `TimeoutError`，以及显式标记 `connection_invalidated` 的 DBAPI 错误；约束、数据和编程错误不重试。异常 cause/context 链也会受同一分类，避免包装异常逃逸。
3. `run_once()` 在通用 `fail_job` 前重新抛出可用性故障。已经提交领取状态的 Job 保持 running/租约，不把数据库故障伪装成业务失败；后续由租约过期、幂等键和发布对账协议恢复。该策略刻意接受最多一个租约周期的恢复延迟，以换取不盲目重放外部副作用。
4. 服务模式新增有界指数退避：默认从 1 秒增长到 30 秒、最多 8 次重试、20% 抖动，名义等待总计约 121 秒。连续一次成功即重置计数；等待使用可中断 Event，停机信号不会被 30 秒 sleep 阻塞。失败的维护扫描截止会重置，数据库恢复后立即重新检查。
5. 重试预算耗尽后抛出不含原始连接异常正文的 `WorkerDatabaseUnavailable`，再由 Compose/编排器重启；数据库可用性相关的 Job/节点心跳日志同样只保留错误类型。非可用性错误继续输出完整堆栈，不能靠重试掩盖坏迁移或代码错误。
6. 本地和公网 Compose 现在显式透传原有 poll/lease/max-attempts/heartbeat/stale/queue-stall 以及四个数据库恢复参数；两份 env 模板给出供应商中立默认值，公网校验器检查正数、抖动范围、max ≥ initial 和 stale > 2 × heartbeat。没有数据库迁移或平台副作用。

### 当前验证与边界

- 定向测试覆盖处理阶段不写业务失败、一次故障后恢复、指数上限/抖动、30 秒等待被停机立即唤醒、预算耗尽后脱敏退出、IntegrityError 不重试、节点心跳日志脱敏、设置上下界和公网配置失败关闭；相关门禁为 `61 passed, 59 subtests passed`。本机全量为 `299 passed, 13 skipped, 187 subtests passed`、分支覆盖率 80.86%，13 项均为未启动的 PostgreSQL/MinIO 外部服务用例。全仓 Ruff、锁文件、`pip check`、Python 漏洞审计、编译、Alembic 单 head、双 Compose、公网 fail-closed、PowerShell 备份脚本语法、前端 ESLint、Vinext/Sites 构建、2 项 SSR 测试、Next.js/TypeScript 生产构建和 npm moderate 审计 0 漏洞均通过。
- 实现提交 `b3f2d6a19516d9265d9d2c8b32ff6be14b078f8c` 已以 John Wang 身份普通推送；[ContentFlow CI #33691253662](https://github.com/heee000/ContentFlow/actions/runs/33691253662) 四个 Job 全部成功。真实 PostgreSQL/pgvector 与 MinIO 为 `312 passed, 187 subtests passed`、分支覆盖率 81.99%；前端、Prometheus、Python/npm 漏洞审计、可复现源码/SBOM、SLSA 与双 CycloneDX attestations 全部签收。Artifact `9870116498` 摘要为 `sha256:6c8207ee31365941f739509add585a8c803e1deb0023988bafcee41f8f7b76cf`。
- 当前证据是 SQLAlchemy 确定性异常注入，不是 PostgreSQL 容器 kill/restart、DNS 失败、连接池耗尽、网络分区、故障主从切换或多 Worker 惊群演练。默认预算是安全基线，不是生产 RTO/SLO 结论。
- 完全断库时 Worker 无法把 degraded 状态写进同一数据库；现有 API 指标只能在数据库可读时观察 stale/no-active，仍需真实 Alertmanager receiver、集中日志和编排器重启指标形成闭环。
- `contentflow-worker --once` 保持单次失败即退出；服务模式预算耗尽后也必须退出，避免永久重试掩盖凭据、网络策略或迁移错误。公网部署仍冻结，本轮未访问 `.env`、平台账号或受保护知识文件。
- 下一轮继续审查跨实体数据生命周期、前端历史/大型模块、数据库纵深隔离和真实运维演练；若继续深入 Worker，应先建立目标 PostgreSQL 故障注入矩阵和恢复时延预算，而不是继续堆叠未经测量的重试层。

## 21.47 PostgreSQL SQLSTATE 恢复矩阵与第三十五轮复审增量交接

### 本轮实现

1. 对第三十四轮的“OperationalError 过宽”结论沿 `run_once → handler → fail_job/mark_domain_failure` 再次取证：死锁、序列化失败、锁竞争、语句取消和永久配置错误此前都会被视为断库，导致已领取任务保留到租约过期；若简单缩窄分类，又可能让已进入 `publishing` 的平台调用按普通失败自动重试。
2. 新增供应商中立的数据库异常分类接口，并对 PostgreSQL SQLSTATE 建立首版矩阵：`08xxx`、`53300`、`57P01`-`57P04`、`58030` 为可用性；`40001`/`40P01` 为事务可重试；`55P03` 为锁竞争；`57014` 为查询中断；驱动提供的其他有效 SQLSTATE 及 SQLAlchemy `DataError`/`IntegrityError`/`ProgrammingError` 为永久错误。解析遍历 SQLAlchemy 包装、`orig`、cause/context 和 driver diagnostics，不通过格式化异常获取编码。
3. 没有 SQLSTATE 的旧驱动 `OperationalError` 保持上一阶段的保守 availability 回退。事务冲突、锁竞争和查询中断在 Handler 事务回滚后进入 Job 级退避；永久错误立即终结 Job，不再浪费全部尝试。若这些数据库错误发生时 `publish.dispatch` 已持久化为 `publishing`，队列尝试立即失败并把领域任务转为 `reconciliation_required`，审计 reason 精确记录数据库类别，禁止重复平台写入。
4. Worker 领取/维护边界允许可用性、事务冲突、锁竞争和查询中断使用已有有界进程退避；永久 SQLSTATE 直接抛出交给编排器和人工修复。Worker、Job、租约心跳和节点心跳只记录 `kind/sqlstate/error_type`，持久化错误同样不含 SQL、参数、DSN 或驱动正文。
5. 单元回归覆盖九类 SQLSTATE、未知驱动回退、事务重排、永久错误一次终结、服务级死锁恢复/鉴权失败不重试、敏感 SQL/参数/驱动正文负向断言，以及发布开始后的事务冲突强制对账。PostgreSQL 集成门禁新增真实 `statement_timeout`、`LOCK ... NOWAIT` 和缺表语句，由 psycopg 实际产生 `57014`、`55P03`、`42P01`，防止测试只验证伪造属性。

### 当前验证与边界

- 全仓 Ruff 与 Worker/发布定向 `35 passed, 9 subtests passed` 通过；本机全量为 `306 passed, 14 skipped, 196 subtests passed`、分支覆盖率 80.96%。14 项均为本机未启动的 PostgreSQL/MinIO 外部服务，本地结果不用于签收真实驱动分类；远程证据见下一条。锁文件、`pip check`、Python 漏洞审计、编译、Alembic 单 head、双 Compose、公网 fail-closed、备份脚本语法、前端 ESLint、Vinext/Sites 构建、2 项 SSR、Next.js/TypeScript 生产构建和 npm moderate 审计 0 漏洞均通过。
- 实现提交 `9c2a6518dc7258ee354e7f7632bc6cfa9ae54797` 已以 John Wang 身份普通推送；[ContentFlow CI #33694647116](https://github.com/heee000/ContentFlow/actions/runs/33694647116) 四个 Job 全部成功。真实 PostgreSQL/pgvector 与 MinIO 为 `320 passed, 196 subtests passed`、分支覆盖率 82.10%，其中 psycopg 实际返回的 `57014/55P03/42P01` 分类、迁移和对象后端全部通过；前端、Prometheus、Python/npm 漏洞审计、可复现源码/SBOM、SLSA 与双 CycloneDX attestations 均签收。Artifact `9871335459` 摘要为 `sha256:ef9f75479585b2552c77c59231f78fb2849efc44521b08c3691c57b4f4da65a0`。
- 本轮没有新增配置、迁移或平台调用，也没有读取 `.env`、账号、模型缓存、运行数据或受保护知识文件。公网部署继续冻结。
- SQLSTATE 是错误语义，不是端到端恢复证明。真实 PostgreSQL kill/restart、DNS、网络分区、连接池耗尽、主从切换和多 Worker 惊群仍未执行；`40001`/`40P01` 当前有确定性包装测试，尚未由真实并发事务制造。
- 除发布任务外，AI、对象存储和纯数据库 Job 仍共享粗粒度中断策略；下一步应以副作用契约和成本为依据声明每类 Job 能否快速接管，不能因为已有 SQLSTATE 就统一降低 300 秒租约。

## 21.48 PostgreSQL 真实事务冲突与第三十六轮复审增量交接

### 本轮实现

1. 第三十五轮虽已把 `40001` 和 `40P01` 纳入 `transaction_retryable`，但真实 PostgreSQL 门禁只制造了 `57014/55P03/42P01`。本轮补齐实际并发事务证据，避免用伪造 SQLSTATE 代表 psycopg/SQLAlchemy 真实包装行为。
2. 序列化用例在随机临时集成数据库中创建专用探针表，让两个 `SERIALIZABLE` 事务读取同一版本并同步更新同一行；断言恰好一个事务提交、另一个由 PostgreSQL 返回 `40001`。
3. 死锁用例让两个事务各自先更新一行，再通过 Barrier 同步、以相反顺序请求另一行；断言恰好一个事务提交、一个成为 PostgreSQL `40P01` 死锁牺牲者。
4. 两个真实异常直接进入现有 `database_error_sqlstate`、`classify_database_error` 和 `sanitized_database_error`，必须归为 `transaction_retryable`，且摘要不得包含探针 SQL/表名。数据库语句、Barrier 和 Future 都有有界超时；失败事务回滚，探针表在 `finally` 中删除。
5. 本轮只修改 PostgreSQL 集成测试，没有改 API、数据库迁移、领域状态、生产 Worker 配置或前端，也没有触发任何外部平台副作用。

### 当前验证与边界

- 本机 Docker Engine 管道仍不存在，因此真实服务用例安全跳过；`uv lock --check`、全仓 Ruff 和完整后端覆盖率门禁为 `306 passed, 14 skipped, 196 subtests passed`、分支覆盖率 80.96%。14 项跳过均为未启动的 PostgreSQL/MinIO，未被写成真实签收。
- 实现提交 `a65a411fe2d8db46db2c2746be19dad4b1cc1765` 已以 John Wang 身份普通推送；[ContentFlow CI #33696052795](https://github.com/heee000/ContentFlow/actions/runs/33696052795) 四个 Job 全部成功。真实 PostgreSQL/pgvector 与 MinIO 为 `320 passed, 196 subtests passed`、分支覆盖率 82.10%，并实际签收 `40001/40P01`；前端、Prometheus、Python/npm 漏洞审计、可复现源码/SBOM、SLSA 与双 CycloneDX attestation 均通过。Artifact `9871817974` 摘要为 `sha256:6549a79f8875e86ce3d05835c18334734dc5796d0b47bd48fc9d4ad46d902f5c`。
- 该证据关闭的是“真实驱动是否能被正确分类”，不是实际业务 Handler 在目标负载下的端到端冲突恢复，也不是数据库高可用证明。PostgreSQL kill/restart、DNS、网络分区、连接池耗尽、主从切换和多 Worker 恢复 RTO 仍未执行。
- 下一轮应优先为无外部副作用的实际 Job 建立 live 冲突恢复测试，并形成按任务类型的副作用/幂等/补偿/fencing 矩阵；不能将发布或可能重复计费的模型调用机械套用普通自动重试。
- 公网部署继续冻结；本轮未读取 `.env`、平台账密、模型缓存、备份、运行数据或受保护知识文件，未调用平台、创建素材/草稿/发布或云资源。继续禁止读取、修改、暂存或提交 `knowledge/北京周末 CityWalk 路线助手产品资料.txt`。

## 21.49 PostgreSQL 停机恢复与第三十七轮复审增量交接

### 本轮实现

1. 第三十六轮已经证明真实 `40001/40P01` 能被正确分类，但连接不可用仍只有构造异常。本轮让 GitHub Actions 把它创建的一次性 PostgreSQL service container ID 仅注入集成测试步骤，测试可以在受控边界内实际停止和重新启动该容器。
2. 容器控制器只接受 `start/stop`，容器 ID 必须是 12–64 位十六进制 Docker ID；命令使用参数数组、禁用 shell 并设置 30 秒超时。没有环境变量时整个恢复用例跳过，因此本机或普通 pytest 不会误停其他数据库。
3. 恢复测试在随机临时数据库中先让同一个 `Worker.run_forever()` 处理一次探针 Job，再停止 PostgreSQL，等待 Worker 产生脱敏的 availability 重试信号并确认线程仍存活。数据库重新就绪后创建第二个探针 Job，要求仍由同一 Worker 成功处理。
4. 两个 Job 都必须只有一次处理尝试、没有业务错误，处理顺序必须恰为 before/after restart；重试日志不得包含 DSN 标志或本机地址。Worker 收到安全停止请求后还必须把 `worker_nodes` 状态写为 `stopped`。
5. 测试使用 0.1–0.5 秒、最多 100 次的专用快速重试预算，并要求从容器启动到第二个 Job 成功不超过 15 秒。这个上限只用于防止 CI 悬挂，不是生产默认参数的 RTO/SLO，也没有修改生产 Worker、数据库迁移或领域状态。

### 当前验证与边界

- 本机 `uv lock --check`、全仓 Ruff 和完整后端门禁通过：`306 passed, 15 skipped, 196 subtests passed`、分支覆盖率 80.96%；新增 skip 是因为本机没有 GitHub 一次性 service container ID。前端 ESLint、Vinext/Sites 构建、2 项 SSR 测试及 Next.js/TypeScript 生产构建也通过。
- 实现提交 `6b972e28e31388704d23c833df6f99f0e99d90c7` 已以 John Wang 身份普通推送；[ContentFlow CI #33697780446](https://github.com/heee000/ContentFlow/actions/runs/33697780446) 四个 Job 全部成功。真实 PostgreSQL/pgvector 与 MinIO 为 `321 passed, 196 subtests passed`、分支覆盖率 82.15%，Python 漏洞审计为 0 已知漏洞；前端、Prometheus、npm 审计、可复现源码/SBOM、SLSA 与双 CycloneDX attestation 均通过。Artifact `9872418882` 摘要为 `sha256:fd7ce858d9e631dc8525ef192e7cc828dcf34dc8ace26c97b66903e7440d91fa`。
- 本轮关闭的是“空闲单 Worker 遇到一次 PostgreSQL service container 优雅 stop/start 后能否进程内恢复”的证据缺口。它没有杀死 Worker、没有在 Handler/完成提交中途断库，也没有覆盖进程重启、过期租约接管或外部副作用不重复。
- 单次 CI 的 15 秒上限不是 P50/P95；优雅 stop/start 也不等同于 crash、DNS、网络分区、连接池耗尽、主从切换或多 Worker 同时恢复。生产默认 8 次预算仍需在目标环境按故障持续时间签收。
- 公网部署继续冻结；本轮未读取 `.env`、平台账密、模型缓存、备份、运行数据或受保护知识文件，未调用平台、创建素材/草稿/发布或云资源。下一轮优先建立在途无副作用 Job 的断库/进程终止恢复，以及多 Worker 与独立监控证据。

## 21.50 在途 Worker 强制终止与第三十八轮复审增量交接

### 本轮实现

1. 第三十七轮只在 Worker 空闲领取时停止数据库，没有证明 Job 已领取、Handler 正在执行时进程崩溃后的接管。本轮在 PostgreSQL 随机临时数据库中创建无外部副作用的专用 Job，并用 spawn 独立进程运行真实 `Worker.run_forever()`。
2. 第一 Worker 完成领取提交、启动 LeaseHeartbeat 并进入阻塞 Handler 后向父测试发信号；父进程确认 Job 为 `running`、attempt=1、owner 正确，再调用进程 `kill()`。Linux CI 明确要求退出码为 `-SIGKILL`，不是优雅 stop 或伪造数据库记录。
3. 测试租约为 6 秒。第二 Worker 先执行一次 `run_once()`，必须返回 false，证明租约未过期不能抢占；随后以服务循环等待真实 `locked_at` 过期，重新领取同一 Job，attempt 增至 2 并写入唯一成功结果。
4. 崩溃 Worker 不可能写入 stopped，因此其 `worker_nodes` 状态应仍为 online，但 heartbeat 必须达到 stale 阈值；恢复 Worker 处理完成后应正常写为 stopped。该差异为现有健康检查和运维判断提供了真实 PostgreSQL 证据。
5. 用例运行前只在一次性数据库中终结之前残留的可运行 Job 和 submitted 发布探针，避免测试之间互相领取；没有调用内置 AI、对象存储或发布 Handler，没有外部副作用，也没有改生产代码、配置或迁移。

### 当前验证与边界

- 本机 `uv lock --check`、全仓 Ruff 与完整后端门禁通过：`306 passed, 16 skipped, 196 subtests passed`、分支覆盖率 80.96%；新增用例因本机 PostgreSQL 集成服务未运行而跳过。
- 实现提交 `b2b01ca5153a611167024dff9095af21ba61fcc3` 已以 John Wang 身份普通推送；[ContentFlow CI #33699168801](https://github.com/heee000/ContentFlow/actions/runs/33699168801) 四个 Job 全部成功。真实 PostgreSQL/pgvector 与 MinIO 为 `322 passed, 196 subtests passed`、覆盖率 82.15%，Python 无已知漏洞；前端、Prometheus、npm 审计、可复现源码/SBOM、SLSA 与双 CycloneDX attestation 均通过。Artifact `9872885169` 摘要为 `sha256:679207f6e4dc315db35124acb528322f24cf906802c555dafa7ec94329ed79c0`。
- 本轮证明的是“无副作用自定义 Handler 在进程被 SIGKILL 后，另一个 Worker 遵守租约并最终接管”。它没有覆盖内置业务 Handler、Handler 已产生 AI 费用/对象写入/平台发布、完成事务提交时断连，或旧执行者仍存活但网络隔离的双写竞争。
- 6 秒租约和单次 CI 只为有界测试；生产默认 300 秒租约、多副本 P50/P95、Kubernetes/Docker 的 TERM→grace→KILL、滚动升级和编排器指标仍需目标环境演练。不能据此缩短生产租约或宣称 exactly-once。
- 公网部署继续冻结；未读取 `.env`、账密、模型缓存、备份、运行数据或受保护知识文件。下一轮优先审查实际 Handler 的副作用/幂等/补偿/fencing 契约和完成提交边界，再决定能否安全缩短不同 Job 类型的恢复时间。

## 21.51 Provider Job 防盲重放与第三十九轮复审增量交接

### 本轮实现

1. 对全部 12 个生产 Handler 建立强制完整的 `JOB_RECOVERY_POLICIES` 注册表，分为 `replay_safe`、`provider_idempotent`、`domain_guarded`、`configuration_guarded` 和 `manual_review`。测试要求策略键集合与 `HANDLERS` 完全相等，新增 Handler 若未声明恢复语义会直接失败。
2. `workflow.execute` 与 `prompt_eval.execute` 没有向文本 Provider 传递稳定幂等键，因此 Handler 报错不再进入通用自动退避；Worker 进程消失且租约过期后也不会由下一 Worker 自动执行。Job 进入 failed，保留错误并允许操作者核对 Provider 活动后显式重试。
3. `knowledge.index` 按实际配置决策：本地 `hash`/`bge-m3-local` 只重复本地计算和可回滚数据库写入，保留自动恢复；`openai-compatible` 可能形成外部计费调用，进入人工核对策略。`asset.generate` 继续依赖既有稳定 `Idempotency-Key` 与 Media Contract；发布/对账/删除使用领域状态机或账本保护，查询型任务按只读重放处理。
4. 租约过期人工核对扫描使用有界 `FOR UPDATE SKIP LOCKED`；`claim_next_job()` 同时在领取条件中排除这些过期 Job，因此即使超过每轮 100 条扫描上限，后续记录也不会绕过策略被自动领取。所有生产调用者必须显式传入人工核对类型集合，避免未来入口默认放行。
5. Job 失败、Workflow/Prompt Eval/Knowledge 等领域失败和相关审计现在在同一数据库事务提交；不再先提交队列失败、再用第二个事务更新页面状态。日志区分 `job lease replay blocked` 与最终尝试耗尽，并记录低基数策略名。

### 当前验证与边界

- 本机全仓 Ruff、完整后端覆盖率门禁通过：`310 passed, 17 skipped, 196 subtests passed`、分支覆盖率 81.02%。17 项跳过均为本机未配置的 PostgreSQL/MinIO/CI 容器用例。`uv lock --check`、`pip check`、Python 编译、依赖漏洞审计、公网部署 fail-closed 校验、Alembic 单 head、前端 ESLint、Vinext/Sites、2 项 SSR、Next.js/TypeScript 生产构建和 npm moderate 审计均通过。
- 实现提交 `a1b38fed51c3d1193026619c0d67daae3d28ad54` 已以 John Wang 身份普通推送；手动触发的 [ContentFlow CI #33701395214](https://github.com/heee000/ContentFlow/actions/runs/33701395214) 四个 Job 全部成功。真实 PostgreSQL/pgvector 与 MinIO 为 `327 passed, 196 subtests passed`、覆盖率 82.24%，并确认过期 `workflow.execute` 不会调用第二 Worker 的 Handler、Job 与 WorkflowRun 原子失败；Python 无已知漏洞，前端、Prometheus、npm、可复现源码/SBOM、SLSA 与双 CycloneDX attestation 全部通过。Artifact `9873652701` 摘要为 `sha256:8700cea0b2d9ea8e969bb47625b8f04479965cc20ce590c20197b9d952058545`。
- 该实现提供的是 fail-closed 防重复，不是 AI Provider exactly-once。通用 failed 任务页仍只靠错误文字提示核对；尚无独立 `manual_review` 状态、Provider 请求账本、费用/响应查询、负责人、证据附件、告警和 SLA。操作者不能在未核对供应商活动前机械点击重试。
- 脚本包对象写入、对象账本补偿、真实媒体 Provider、完成提交丢失和网络分区旧 Worker 恢复仍需逐类故障注入。`replay_safe` 只表示没有外部写副作用，不表示远程查询一定免费或没有速率限制。
- 公网部署继续冻结；本轮没有读取 `.env`、平台账密、模型缓存、备份、运行数据或受保护知识文件，没有调用 Provider/平台、创建素材/草稿/发布或云资源。FORCE RLS 与数据库角色拆分继续等待单独高影响授权。

## 21.52 Provider 专用人工核对与第四十轮复审增量交接

### 本轮实现

1. 第三十九轮把无稳定 Provider 幂等语义的 Job 改为 fail-closed，但仍借用通用 failed 状态。本轮新增 `job_manual_reviews` 历史表和 `manual_review` 状态；Handler 异常或过期租约都会在原事务中创建含原因码、风险说明和必查步骤的未关闭核对，不再混入普通失败重试队列。
2. 表级约束要求未关闭记录不能预填结论/备注/确认位，关闭记录必须 `provider_checked=true`、选择 retry/abandon 且备注至少 8 个字符；部分唯一索引保证一个 Job 同时最多一条未关闭记录。迁移 head 为 `e3f4a5b6c7d8`，公开表门槛为 31，备份和隔离恢复校验同步。
3. 新增 reviewer/admin 专用 `POST /api/v1/jobs/{job_id}/manual-review`。PostgreSQL 下锁定 Job 与核对记录；retry 会清零 attempts、立即排队并清除旧错误，abandon 保留失败终态和错误。普通 editor 不能处置，通用 retry 同时拦截当前及旧版高风险 failed Job，重复处置返回 409。
4. 人工核对请求和决策进入防篡改审计链；核对备注只保存在专用记录中，不复制到审计 metadata。任务列表返回当前轮核对上下文，前端解释副作用风险、检查步骤和权限，要求确认框与书面依据，并对重试/放弃提供独立忙碌状态和放弃确认。
5. Dashboard、Worker health、Prometheus 与 Grafana 增加待核对数量和最老时长。最老未关闭记录超过 1 小时并持续 15 分钟时触发 `ContentFlowJobManualReviewOverdue`；指标保持全局低基数，不暴露 workspace、Job ID 或核对备注。

### 当前验证与边界

- 本机完整后端为 `312 passed, 17 skipped, 196 subtests passed`、分支覆盖率 81.17%；17 项均是本机没有 PostgreSQL/MinIO/CI 容器条件的安全跳过。全仓 Ruff、Python 编译、Alembic 单 head、锁文件、`pip check`、公网部署 fail-closed、前端 lint/test/构建和 npm moderate 审计均通过。
- 实现提交 `3ce6e6259ddf56738b37146435ad44d2c4a3dfb2` 已以 John Wang 身份普通推送；[ContentFlow CI #33704597235](https://github.com/heee000/ContentFlow/actions/runs/33704597235) 四个 Job 全部成功。真实 PostgreSQL/pgvector 与 MinIO 为 `329 passed, 196 subtests passed`、覆盖率 82.36%；Prometheus 规则行为、前端、Python/npm 漏洞审计、可复现源码/SBOM、SLSA 与双 CycloneDX attestation 均通过。Artifact `9874760182` 摘要为 `sha256:94f2c8060028bfa9305a1eafc3b63ac5dfa62b4639e412e1b249fd77ba0765e0`。
- 本轮不等于 Provider exactly-once：仍无调用账本、供应商请求 ID/费用/结果自动查询、证据附件、负责人认领、双人确认和真实告警接收器。下一优先项应是 Provider invocation ledger，而不是放宽人工核对。
- 脚本包对象写入、对象删除、真实媒体 Provider、完成提交丢失和网络分区旧 Worker 恢复仍需逐故障点签收；不能把当前状态机推广为所有外部副作用已经安全。
- 公网部署继续冻结。本轮没有读取 `.env`、平台账密、模型缓存、备份、运行数据或 `knowledge/北京周末 CityWalk 路线助手产品资料.txt`，没有调用真实 Provider/平台或创建任何素材、草稿、发布、云资源。继续保留该知识文件为未跟踪用户文件，不读取、不修改、不暂存、不提交。

## 21.53 Provider 调用账本与第四十一轮复审增量交接

### 本轮实现

1. 新增 `provider_invocations` 与 `provider_invocation_attempts`。前者以 64 位稳定请求键聚合同一逻辑请求并固定请求指纹，后者保存每次尝试的受控状态、幂等键是否发送、Provider 请求 ID、响应证据哈希、字节数、模型和 token；数据库约束限制状态、非负计数、尝试序号和完成时间一致性。
2. `ProviderInvocationLedger.start()` 使用独立 Session，在外部调用前提交 `started` 与审计事件；账本初始化失败会 fail-closed，不会在没有取证记录时继续计费调用。完成路径同样独立提交，失败/中断标记 `outcome_unknown`，同一逻辑请求后续成功可标记 `late_succeeded`。账本不保存 Prompt、响应正文、HTTP 错误正文、Authorization 或平台密钥。
3. Worker 通过 ContextVar 把当前已领取 Job 绑定到文本与 Embedding Provider。OpenAI-compatible 文本生成、Prompt Eval、远程知识索引与知识搜索均接入账本并发送稳定 `Idempotency-Key`；Provider 适配器只抽取受控 body `id`/请求 ID header、模型与用量。发送该头不证明供应商接受或提供幂等保证。
4. 为避免调用前独立提交与业务长事务互锁，远程知识索引和 Prompt Eval 先提交领域执行态，再进入 Provider；工作流先完成全部 Provider 调用并收集结果，随后统一持久化 ContentItem/Revision/Asset。测试证明调用发生时独立 Session 已能看到 started，且完整工作流在 SQLite 下不锁死。
5. 人工核对开始时会把该 Job 尚处于 started 的尝试收束为 outcome_unknown。新增 reviewer/admin 专用分页接口 `GET /api/v1/jobs/{job_id}/provider-invocations`；前端核对抽屉展示脱敏证据与幂等免责声明。Prometheus 增加历史状态、当前未解决不确定结果和最老时长，持续 5 分钟触发 `ContentFlowProviderInvocationOutcomeUnknown`。
6. 迁移 head 更新为 `f4a5b6c7d8e9`，公开表门槛更新为 33；未版本化数据库对两张账本表部分存在时失败关闭。三套备份/隔离恢复校验同步，不改写历史迁移。

### 当前验证与边界

- 本机完整后端为 `318 passed, 17 skipped, 196 subtests passed`、分支覆盖率 81.31%；17 项均为本机未提供 PostgreSQL/MinIO/CI 容器条件的安全跳过。全仓 Ruff、Alembic 单 head、锁文件、`pip check`、Python/npm 漏洞审计、公网 fail-closed、前端 lint/test/build 和备份脚本语法通过。
- 实现提交 `56e563de1725a40c9eddbf05128dff6e812b5cfc` 已以 John Wang 身份普通推送；手动触发的 [ContentFlow CI #33708300286](https://github.com/heee000/ContentFlow/actions/runs/33708300286) 四个 Job 全部成功。真实 PostgreSQL/pgvector 与 MinIO 为 `335 passed, 196 subtests passed`、覆盖率 82.47%；Prometheus、前端、Python/npm 漏洞审计、可复现源码/SBOM、SLSA 与双 CycloneDX attestation 全部通过。Artifact `9876032648` 摘要为 `sha256:ef9130d8941c8e135c6e4e4968b814b37d2c4a7779bce7bcf0c3f6072a616174`。
- 账本当前只覆盖 OpenAI-compatible 文本和远程 Embedding。它没有证明真实 Provider 支持幂等头，也没有自动查询费用/结果、自动调和迟到响应、证据附件、负责人或双人批准；人工核对仍是最终安全边界。
- 请求/响应只保存 SHA-256 证据哈希，避免正文落库，但低熵输入仍可能被离线猜测。未来若把哈希提供给更广泛角色，应改为密钥 HMAC 或进一步收紧访问，且需要设计密钥轮换与历史验证策略。
- 公网部署继续冻结；未读取 `.env`、平台账密、模型缓存、备份、运行数据或受保护知识文件，未调用真实 Provider/平台或创建素材、草稿、发布或云资源。继续禁止读取、修改、暂存或提交 `knowledge/北京周末 CityWalk 路线助手产品资料.txt`。

## 21.54 媒体/搜索调用账本与第四十二轮复审增量交接

### 本轮实现

1. `provider_kind` 从 text/embedding 扩展为 text/embedding/media/search。新增 `LedgeredMediaProvider` 与 `LedgeredSearchProvider`，HTTP 图片/视频生成、异步媒体轮询和 Openverse 图片搜索现在都在调用前用独立事务写入 Provider attempt，并绑定当前 Worker 已领取的 Job。
2. 媒体生成继续向目标服务发送既有 `media_generation_idempotency_key(asset)`，即按 workspace、Asset、kind 和 content_version 生成的 `cfm-*` 键；账本的逻辑请求键只用于内部聚合，不替换 Media Contract 的幂等键。轮询和 Openverse GET 明确记录 `idempotency_key_sent=false`。
3. 媒体成功只保存状态、外部任务 ID、mime/filename 及 inline 内容或下载 URL 的 SHA-256 摘要；搜索只保存查询和候选集合摘要。Prompt、分镜、搜索词、候选详情、媒体字节、URL、Authorization、API Key 和错误正文不落账本。Media Contract 错误信封经过既有封闭 Schema 校验后，可把受控 request_id/来源带入失败 attempt。
4. 同一逻辑请求创建新 attempt 时，会在 invocation 行锁保护下把旧 `started` 转为 `outcome_unknown`，完成时间和 `superseded_by_retry` 原因进入审计；完成路径改为 invocation→attempt 的一致锁顺序。旧执行者迟到成功只能把自身 attempt 标记为 `late_succeeded`，不会自动写入领域结果。
5. 迁移 `a5b6c7d8e9f0` 只替换 provider_kind 检查约束，不新增表，公开表门槛仍为 33。PostgreSQL 使用原位约束替换，SQLite 使用 Alembic batch 重建；未版本化数据库仍先识别 `f4a5b6c7d8e9` 的两张账本表，再正常升级到新 head。三套备份/隔离恢复校验同步。

### 当前验证与边界

- 定向媒体合同、适配器、Worker 绑定、账本和迁移为 `68 passed, 50 subtests passed`；本机完整后端为 `324 passed, 17 skipped, 196 subtests passed`、分支覆盖率 81.46%。17 项均为本机未提供 PostgreSQL/MinIO/CI 容器条件的安全跳过。全仓 Ruff、锁文件、`pip check`、Alembic 单 head、公网 fail-closed、备份脚本语法、Python/npm 漏洞审计、前端 lint/test/build 均通过。
- 实现提交 `9a6c154ba154356bda6ff6089137d1e0b473e506` 已以 John Wang 身份普通推送；手动触发的 [ContentFlow CI #33710709007](https://github.com/heee000/ContentFlow/actions/runs/33710709007) 四个 Job 全部成功。真实 PostgreSQL/pgvector 与 MinIO 为 `341 passed, 196 subtests passed`、覆盖率 82.60%；Prometheus、前端、依赖审计、可复现源码/SBOM、SLSA 与双 CycloneDX attestation 全部通过。Artifact `9876813444` 摘要为 `sha256:d113346727fab94672907a3f7bf171fcf4719437f908685b606b792202e79adc`。
- 当前没有真实 HTTP 媒体 Provider 的 live conformance、账单、质量或时延签收，因此不能仅凭仓库测试认定 `asset.generate` 在任意第三方服务上安全自动恢复。启用真实 HTTP Provider 前必须运行既有显式计费确认的 conformance runner。
- 媒体结果 URL 下载与 Openverse 候选选中后的下载还没有独立 Provider attempt；最终文件由对象存储账本与 checksum 保护，但网络请求、来源响应与配额诊断仍可继续完善。Asset 页面也尚未提供通用调用证据入口。
- 普通 SHA-256 低熵猜测、证据 retention/export/legal hold、负责人/双人核对、真实 receiver、Provider 自动结果/费用查询、完成提交丢失和网络分区 fencing 仍未关闭。
- 公网部署继续冻结；未读取 `.env`、账密、模型缓存、备份、运行数据或受保护知识文件，未调用真实 Provider、Openverse、微信或其他平台。继续禁止读取、修改、暂存或提交 `knowledge/北京周末 CityWalk 路线助手产品资料.txt`。

## 21.55 下载证据、异步候选下载与 Ubuntu 内部测试准备

日期：2026-09-17。本节承接中断前未提交的下载账本改动；早期本机运行快照不作为当前有效配置证明。

1. 新增 `LedgeredMediaDownloader`：媒体 URL 下载和 Openverse 候选下载在网络请求前独立提交 attempt，绑定真实 Worker Job；只记录请求摘要、精确响应字节的 SHA-256/大小及受控请求号，不落原始 URL、文件字节或错误正文。下载没有发送幂等头。初始 URL 白名单/HTTPS 预检在开始账本前执行，重定向仍逐跳校验；收到最终响应头时立即保存请求号，正文超限或读取失败也可留证。
2. Openverse 的选用接口只记录许可确认和候选选择并创建 `asset.download`，不再持有 API 事务同步下载。Worker 完成下载和图片规范校验后锁定、刷新并重检素材/内容版本与审批状态，成功存储后才切换同组封面。对象写入后的异常走既有补偿；同一候选已 ready 时重放不重新下载。
3. 失败下载的素材重试保留已选候选和许可依据，不重新执行搜索；每轮重试使用递增序号，避免固定幂等键返回旧失败 Job。素材行锁串行化序号更新。`asset.download` 已加入恢复策略注册表，采用领域状态保护。
4. 修复上一阶段遗漏的 API/TypeScript `provider_kind` 类型：数据库已有 media/search，但响应 Schema 仍只接受 text/embedding，媒体证据查询可能报 500。新增 reviewer/admin 专用素材证据分页接口，复用 Job 证据的脱敏序列化。前端区分搜索与下载，提供素材级证据入口，并防止快速切换素材时旧响应覆盖新面板。
5. 没有新增迁移，head 保持 `a5b6c7d8e9f0`，公开表数仍为 33。定向合同/下载/Worker/账本回归为 `90 passed, 53 subtests passed`；完整回归及提交/CI 结果继续写入工程台账 `CF-20260917-01`。前端 ESLint、2 项 SSR、Vinext 和 Next.js/TypeScript 生产构建通过。
6. 用户新授权自有 Ubuntu 机器内部测试，公网和付费云部署继续暂停。已验证 TCP/22 与主机身份，已生成专用 SSH 密钥；用户追加公钥后仍被服务器拒绝，正在等待权限/指纹诊断。没有把已验证的网络连接写成已部署。实施顺序和资源边界见 `docs/ubuntu_private_test_setup.md`。

本轮仍未读取 `.env`、平台账密、模型缓存、备份、运行数据或受保护知识文件；未调用真实 Provider/社媒或创建素材、草稿、公开发布。普通 SHA-256、证据生命周期、真实 Provider 合同、多 Worker 完成提交/fencing、企业运行体系仍是后续审计项。

### 21.55.1 提交与部署前检查增量

- 实现及记录提交 `eb16d37d53050dd39f1a93cf78241bd13a33913a` 已以 John Wang 身份普通推送。最终本地后端为 `328 passed, 17 skipped, 199 subtests passed`，分支覆盖率 81.41%。
- 首轮 [CI #35202826024](https://github.com/heee000/ContentFlow/actions/runs/35202826024) 的失败来自新收录依赖漏洞和原 MinIO Docker Hub 地址不可拉取；后端未执行，不能记为真实 PostgreSQL 签收。
- 前端已更新 Next.js/eslint-config-next 16.3.5、sharp 0.35.4、js-yaml 4.3.2、fflate 0.7.5；Node 24.19.0 的 lint/test/双构建与 npm/Python 审计通过。MinIO 改用官方 Quay 同一内容摘要，开发 Compose 同步固定 Server/Client 摘要。详见工程台账 `CF-20260917-03`，修复后 CI 另记。
- SSH 的空 authorized_keys 已由用户追加修复，服务器身份和默认认证配置匹配；另发现本地新私钥的意外口令，修正需明确确认，当前没有成功免密或完成部署的证据。最新状态见内部测试准备文档。

### 21.55.2 CI 签收与 SSH 排障完成

- 修复提交 `8544dabfd97607a997f798e63681b288c95d88f4` 已以 John Wang 身份普通推送；[CI #35203918464](https://github.com/heee000/ContentFlow/actions/runs/35203918464) 四个 Job 全部成功。真实 PostgreSQL/MinIO 为 `345 passed, 199 subtests passed`，分支覆盖率 82.53%；前端、依赖审计、Prometheus、源码/SBOM 和签名证据通过。第一轮失败已完成修复，不再把 GitHub 认证或推送当作阻塞。
- 用户明确授权修正专用新密钥后，空口令验证成功；经用户给出的 IPv6 登录成功，主机别名沿用已验证记录，未降低严格主机验证。SSH 已通，不要再次要求用户追加公钥、改认证规则或提供密码。
- 实机为 AMD A6-9210、2 逻辑 CPU、AVX2、3.7 GiB RAM（当时可用约 1.6 GiB）、已有 3.7 GiB swap、机械盘根分区空闲约 846 GiB。既有后台和桌面进程保留；这些是静态预检，不是整栈/BGE 容量验收。
- 新增 `deploy/private-test/install-docker.sh`，通过目标机 bash 语法/帮助、参数/非 root 拒绝和传输哈希核验。使用现有 Ubuntu 源安装 Docker/Compose，需要操作者手动 sudo，并显式接受 docker 组 root 等价权限；不修改 sudoers 或 SSH，不启动应用容器。Docker 会初始化自身网络规则。`.sh` 固定 LF 防止 Windows 换行破坏 Linux 执行。
- 当前等待操作者执行安装脚本；Docker 和应用尚未安装。下一步从新 SSH 会话核验组权限、daemon 与 Compose，再建立独立私有栈，不能直接运行带开发默认端口/弱口令的根 Compose。尚未搬迁业务数据、凭据、知识文件或模型缓存，公网部署继续暂停。

### 21.55.3 Docker 已就绪，改用 Embedding API

- 用户已执行安装，新会话确认 Docker 29.1.3/Compose 2.40.3、docker 组、enabled/active，替代上一节“Docker 未安装”状态。当前用户选择 Embedding API，并允许安全迁移现有 API 配置；仅缺合适的 Embedding 服务/Key，不再要求用户处理 SSH/Docker 安装。
- 新增私人基础 Compose、随机凭据/bucket 初始化脚本和离线 pgvector 覆盖，目标目录与 `.env` 分别为 700/600；只是基座，不含应用服务。Ubuntu Quay 镜像已取得，Docker Hub 超时由 Windows 同摘要离线搬运处理；压缩包必须先核验完整哈希再 load，勿加载残留的未完成 archive。
- 后端可选不安装本地 Embedding 依赖，默认保持原行为；约 412 MB API-only 镜像已在本机完成无网络导入验证。构建上下文改白名单，不读取/传送受保护知识资料。9 项部署配置测试通过，其他运行签收边界见工程台账 `CF-20260917-04`。
- 用户最新询问 Embedding 与 LLM 的区别及高性价比选择；应先解释并给注册/Key 获取步骤，再接真实配置。未选择目标服务前，不读取并搬运现有密钥或发送知识内容到新的供应商。应用部署、持久化、登录和真实检索尚未完成，公网继续冻结。

### 21.55.4 Embedding Key 已验证，等待 Ubuntu 连接恢复

- 用户已直接提供并授权使用硅基流动 Key，**不要再次要求提供 Key 或另建文件**。本轮忽略目录下已有独立运行配置，禁止打印/提交。实际 `BAAI/bge-m3` 两次各 3 句合成文本验证通过（合计服务报告 50 tokens），包括 ContentFlow 自身适配器、1024 维、有限数值与基础语义排序。
- 新增通用 `CONTENTFLOW_EMBEDDING_SEND_DIMENSIONS=false` 适配固定输出维度的接口，默认 true 不变；返回维度检查继续强制。账本区分可选参数模式，同时保留默认请求身份。详见台账 `CF-20260917-05`，不能把调用成功说成完整 RAG 或费用签收。
- 原有 SSH IPv6 与 IPv4 在本次重连都超时，已请用户唤醒电脑或提供 `hostname -I` 最新输出。传输进程已完成，但压缩包目标 hash/load 与 PostgreSQL 启动必须在恢复后实际核验；不得加载此前中断的未压缩 archive。现有 Windows 运行服务未动，公网仍冻结。
- 用户已确认电脑未休眠、地址仍相同；路由为 WLAN 直连，后续 SSH 重连成功，不再等用户改地址或安装软件。Windows 完整覆盖率测试出现原生 access violation 而非完整通过；定向 26 项/6 subtests、真实适配器和新版 Docker 镜像通过。提交后必须用 Linux CI 完整签收，保留 Windows 崩溃边界，不反复盲跑。

### 21.55.5 Ubuntu 私人应用栈与安装态迁移修复

- `f290824` 的 [CI #35209824845](https://github.com/heee000/ContentFlow/actions/runs/35209824845) 四个 Job 全绿，351 passed / 199 subtests。SSH 已恢复；PostgreSQL 16.14 / vector 0.8.5、MinIO 与单 bucket 应用权限已实测。Ubuntu 真实 Embedding 探针亦通过，不再等待操作者给地址、Key 或安装 Docker。
- 已新增生产门禁保留的私人应用 Compose、Caddy、白名单 Provider 导出和独立运行密钥生成。只复制授权 API 配置，旧业务数据/账户不迁移。Windows localhost:3600 经 SSH 到 Ubuntu 回环入口，独立 Secure/HttpOnly Cookie 避免旧 Windows 实例干扰，不开放校园网/公网。
- 三个应用镜像的压缩包完整 hash 与源/目标全部层、运行字段匹配已确认。Docker 27→29 归一旧空字段导致 image ID 改变，已逐项解释；不能随意忽略任何 ID 不一致。生产 Settings 验证通过，但首次安装态迁移 CLI 暴露漏打包资产错误，数据库迁移尚未执行。
- 修复 `contentflow-migrate` 的非 editable 安装路径：wheel 明确携带迁移资产，使用受控安装前缀及绝对路径，不受 CWD 影响；新增含 `%` 路径和错误 CWD 的真实临时迁移回归。相关 13 项通过，新镜像构建/真实 CLI 与整栈签收继续执行。详见台账 `CF-20260917-06/07`，不能把基础服务就绪写成用户全流程可用。

### 21.55.6 私人测试环境已启动并验证真实索引

- 应用源码部署为 `8a5e300`；[CI #35212620332](https://github.com/heee000/ContentFlow/actions/runs/35212620332) 四个 Job 全绿，356 passed / 199 subtests。已安装 CLI 从 `/tmp` 对实际 PostgreSQL 迁移通过；迁移 head 未变。
- Caddy 实机暴露文件 capability 冲突、仅 internal 网络无法发布端口和默认指令排序绕过 Host 拒绝三个问题，均已修复并实测。仅 Caddy 加普通 ingress bridge，data/app 保持 internal，仍只映射 Ubuntu 回环端口；Caddy 不是出口隔离。SSH 去掉影响 IPv4 本地绑定的 `-6`，实际 HTTP 和浏览器登录通过。详见 `CF-20260917-08`。
- 新管理员与工作区完全独立，凭据只在私人运行目录和本机忽略目录；保留旧实例全部数据。一个合成资料已完成真实 Embedding 索引与调用账本。重启私人 DB/MinIO/API/Worker 后对象 checksum、文档、1024 维向量和单次调用证据仍正确，无 runnable Job。
- 入口为 Windows http://localhost:3600/；浏览器 Secure/HttpOnly Cookie 保持开启。当前无经评测、独立审核和激活的 Prompt release，也未迁移微信渠道或验证本轮内容/媒体/发布全链路。不要关闭生产治理或自行伪造双人批准；下一阶段按明确测试流程推进。
- 空闲采样六容器合计约 550 MiB，宿主约 1357 MiB available，swap 843 MiB；不是峰值/长稳保证。异机备份、固定出口和企业运行体系仍未完成。公网继续暂停；受保护知识文件仍未读、改、暂存或提交。
- 最终配置提交 `611399e` 的 [CI #35214913017](https://github.com/heee000/ContentFlow/actions/runs/35214913017) 全绿，356 passed / 199 subtests。浏览器重载仍登录、知识库显示真实索引成功；六容器无 OOM/自动重启。登录资料与本机重连说明在 `.contentflow/private-test-transfer-20260917/`，不提交其秘密。应用源码 SHA 仍为 `8a5e300`，此后为入口配置与记录变更，不虚改应用镜像的来源声明。

### 21.55.7 Tailscale 客户端准备完成一侧，等待设备授权

- 用户明确选择私人跨网络访问并授权安装。Ubuntu 官方 Tailscale 1.102.4 已验签安装，daemon enabled/active；不接管 DNS、不接受子网路由、不启用出口节点、Tailscale SSH 或 Funnel。原六服务和 readiness 复核正常，原 localhost SSH 入口未改。
- 设备尚为 Logged out：控制端已返回授权地址，但 Edge 新设备页要求用户重新登录。管理台已注册不等于已授权该机器。HTTPS 的永久 CT 域名公示确认也未提交。Windows 官方 MSI 已验签，但 UAC 返回用户取消，已询问是否重发，不能自动绕过。
- 浏览器控制故障已由用户指定的“电脑相关”任务修复，并在本任务重置后实测管理台读取成功；不再重复排查已解决的控制工具初始化。修复是工具侧局部等待预算调整，不属于本仓库源码。
- 下一步先确认用户设备登录/Windows 安装/HTTPS 授权，取得实际节点域名与权限，再做 Serve 和 ContentFlow Web API Base、公共 URL/CORS、Host/代理一致性改造及跨网验收。更新源、异机备份和固定微信出口仍未完成；不要声称已可随时随地访问，也不要关闭生产保护。
- 详细安装证据、遇到的问题及待验收边界见 `CF-20260917-09` 与 `docs/ubuntu_private_test_setup.md` 最新节。授权地址/密码/安装包留在私人上下文或忽略目录，受保护知识文件不动。

### 21.55.8 私有 HTTPS 服务端已签收，待客户端安装与跨网实测

- 用户随后完成设备授权并同意 HTTPS/CT；管理台与服务器均验证已连接，HTTPS 开启。`tailscale serve --bg` 已代理回环 3800，无 AllowFunnel；不是公网发布，也不改变微信出站 IP。
- 已应用 `compose.tailnet.yml` 覆盖和 `Caddyfile.tailnet`，Web 按真实域名重建；旧 SSH-only 配置及旧镜像保留可回退。现在使用 Serve HTTPS 网址，旧 localhost:3600 UI 不再适用；不能只启动原 compose.app.yml 后声称 HTTPS 仍正常。维护命令必须含 `--env-file tailnet.env -f compose.tailnet.yml`，完整顺序见 README。
- 服务器自检通过真实证书、登录/刷新/退出、Secure/HttpOnly Cookie、匿名/Host 拒绝、知识读取和治理；既有对象 checksum、1024 维向量、1 次 Provider attempt 保持不变，runnable Job 为 0，无新增 AI 调用。18 项定向测试、Ruff、目标 Caddy 验证通过。应用源仍 `8a5e300`，本轮只有部署覆盖/验证脚本/前端构建参数与记录变化。
- Windows 官方安装器曾被 UAC 取消，已请求明确重发或由用户手动安装，尚无 Windows 客户端与手机异网验收。当前最终阻塞不是 Ubuntu sudo、API Key 或浏览器控制；不要重复要这些信息或重装已完成的服务器。
- 后续还需精细 ACL、真实客户端 IP 限流、设备凭据/更新维护、备份和长稳。细节见 `CF-20260917-09`、私人部署记录；不要以服务端 curl 自检替代真实浏览器签收。

### 21.55.9 Windows 已安装入网，等待单域名代理兼容授权

- 2026-09-18 用户已手动安装并完成同一 tailnet 的 Windows 设备登录。实测客户端 1.102.4、服务 Running/Automatic、两端在线、Health 无告警；不要再要求安装、重新注册或给 API Key。前一次后台提权“用户取消”不等于用户实际点击取消，弹窗未出现原因未确认。
- Tailscale ping 直连成功；Windows 对已验证 Ubuntu 节点地址、保留 CA/域名校验的 HTTPS readiness 为 200，数据库/存储 ok。应用来源仍 `8a5e300`；`75a473e` 的 CI #35229216117 四个 Job 全绿。
- 真正剩余阻塞：本机 Mihomo fake-IP DNS 和系统代理处理该域名时失败，Edge `ERR_CONNECTION_CLOSED`。已请求用户确认仅调整 ContentFlow 单域名的解析/直连；尚未写代理配置，不能假定用户同意改全局网络。不要重复之前双核心/浏览器控制超时的修复，也不要绕过证书校验。
- 完成单域名兼容后才继续真实浏览器登录、手机异网测试和更新私人使用说明。入网成功不等于业务全流程或跨网已签收；当前证据及边界见 `CF-20260918-01`、`docs/ubuntu_private_test_setup.md` 最新节。原受保护未跟踪知识文件仍不动。

### 21.55.10 单域名兼容已修复，Windows 浏览器 HTTPS 已签收

- 用户已明确批准单域名修改，不再等待同一授权。Clash 两份原文件已独立备份，扩展脚本/当前运行 YAML 已应用 exact-host 映射、fake-IP 排除、优先 DOMAIN 规则和绑定 Tailscale 网卡的专用 direct 出站。普通 DIRECT 曾超时，专用出站后代理及系统直连 HTTPS 均 200；系统代理/TUN/其他规则不变，未改系统 hosts、注册表或开放公网。
- 已实际在 Edge 登录 Ubuntu 私人工作区，读取 1 份已索引合成知识并验证刷新后会话保持；双 Cookie Secure/HttpOnly/Lax 正常，不打印值。页面已保留供用户体验。无需重装客户端/服务器或重新要 API Key，应用来源仍 `8a5e300`。
- 配置差异白名单、脚本输出等价/幂等、官方 Mihomo `-t`、命名管道重载及既有代理公共 HTTPS 控制请求均通过。扩展脚本已持久保存，但尚未实际进行主机/Clash 重启或订阅更新验收。节点重建/网卡改名需复核定向映射；回退位置和操作见本机私人使用说明，不上传代理配置与凭据。
- 下一步由用户用手机移动网络加入同一授权 tailnet 后访问私人 HTTPS，独立验收异网场景。生产 Prompt 治理、微信渠道迁移、完整生成/素材/发布链路、备份与长稳仍未签收；不要用此次网络验收替代业务全流程。详见 `CF-20260918-02`。

### 21.55.11 代理专项修复完成，当前等待 Ubuntu 本机状态

- 用户已将 Windows 代理/浏览器问题交给“电脑相关”，该任务报告节点 DNS 修正及正式 Edge/内置浏览器验收完成。不要重复修复或改全局代理；早期 7890/rule/TUN 基线已变化，不用旧私人校验器 reload 或整份备份覆盖后续修复。专项对照及因果边界见 `CF-20260918-03`。
- 用户恢复 ContentFlow 任务后，本任务重新检查 Windows Tailscale 为 Running，但 Ubuntu 节点 Online=false；私网 HTTPS/SSH 和旧 IPv6 入口超时，旧 IPv4 在 banner 阶段超时，未取得远端身份认证。不是浏览器工具未恢复，也不是缺 API Key/密码/安装包；主机离线原因尚未确定。
- 暂停需要远端的验收，等用户在 Ubuntu 本机提供 `hostname -I`、`tailscale status`、`systemctl is-active tailscaled docker` 输出。不要要求重复安装或追加公钥。恢复后先复核六服务、Serve/HTTPS 与历史合成知识数据，再继续生产 Prompt 治理和真实生成/素材测试；微信渠道与手机异网仍未签收。
- 本轮只读排查及更新断点，没有重启或变更服务器、没有新增 AI/社媒操作。受保护知识文件保持原样；此前已通过的验收保留为历史证据，不宣称当前远程服务可访问。

### 21.55.12 网络恢复，转入单人内测治理选择

- 用户提供的 Ubuntu 错误显示控制域名 DNS 解析失败；不能仅凭 Logged out/NoState 判定凭据失效。用户随后修复网络，本任务已通过 Tailscale 和原局域网 SSH 实际登录，保持严格主机校验；不要再让用户重装、追加公钥或重复提供密钥。
- 六容器运行，Worker 最新心跳在线，数据库/存储 readiness 正常，指定已验证节点且保留 TLS 校验的 Windows 首页为 200；Serve 仍为私网 HTTPS，应用来源仍 `8a5e300`。Tailscale/Docker/SSH enabled，容器 unless-stopped；Worker 有 1 次自动重启且历史日志含数据库错误，当前恢复不能写成从未失败或重启根因已关闭。原合成资料和索引任务状态保留。
- 新库只读盘点为 1 个 admin 成员，Prompt release/Eval suite/Eval run/活动/内容/渠道均为 0；真实文本/Embedding 已配置，mock 禁止，production Prompt 治理开启。下一步不是直接生成，而是确认单人测试采用何种审批策略。
- 现有规则禁止套件创建者激活自己的 Eval，禁止 Prompt 创建者审批自己的版本；不得自行造第二个账号完成形式上的双人审批。建议显式的单人内测策略，但这会减少人员分离保护，需用户确认后实现；保留企业默认、真实评测、人工确认、认证/权限/审计，不关闭治理。
- 本轮仅更新恢复与后续验收记录，未改变应用配置、创建账号、调用模型或发布社媒。具体顺序见 `docs/ubuntu_private_test_setup.md` 的“下一阶段”；受保护未跟踪知识文件仍不读取、修改、暂存或提交。

### 21.55.13 单人内测已获授权，显式策略实现完成

- 用户已同意，仅指定测试工作区例外。默认双人规则不变；单人模式需 UUID、治理开启、注册关闭、确切私人 HTTPS/同源 CORS，部署另查 Serve 无 Funnel。本人确认必须填写说明并真实记录，不增加或伪装第二名审核者。
- 后端、治理界面和审计已支持；切回双人会重新阻止单人历史审批满足门禁。所有真实 Eval、哈希/租户/权限和内容审核门禁保留；不把 production 改为 development，无迁移。
- 定向 `83 passed, 59 subtests passed`、Ruff、前端 lint/Next.js 类型与构建/Vinext/2 项 SSR 通过。实现尚待构建部署与真实模型验收；不要把源代码测试通过写成服务器已启用。部署/回退见 `docs/private_single_operator_policy.md`，合成输入见 `deploy/private-test/acceptance_fixture.py`。

### 21.55.14 已部署单人策略；真实 Eval 超时后安全暂停

- `6871ac9` 的 CI #35326064907 四项全绿，384 passed / 199 subtests；新 API/Worker/Web 已实际部署，旧镜像/配置和数据保留，当前源 SHA 不再是 8a5e300。运行配置须额外带 `single-operator-20260918/activation.env` 与最后的 `single-operator-20260918/compose.single-operator.yml`；不要漏掉已授权的覆盖。
- 升级前后原对象 checksum、向量维度、文档/索引状态一致；真实 HTTPS、安全 Cookie、鉴权、Host 和治理自检通过。本轮浏览器控制仍 fetch 失败，未重新签收浏览器；没有修代理或开放公网。
- 单人授权已完成，不再追问同一许可。当前 1 个管理员、1 个 active Eval suite、1 个 draft Prompt、1 个 error Eval run；Job 为 manual_review。实际首个模型计划调用成功，第二次 120 秒超时，供应商执行/计费未知，后续用例没跑。未审批/激活 Prompt，未创建活动/内容/素材/发布。
- 下一步需核对该次供应商请求或取得明确风险处置授权，再走真实受控恢复；禁止填假 provider_checked、静默重复调用、删失败用例或绕过 Eval。详情和时间见内部测试记录与 CF-20260918-05。受保护知识文件仍原样。

### 21.55.15 关机后连接与数据复核完成，评测断点未变化

- 2026-09-19 通过原专用密钥和严格主机验证重新连接 Ubuntu；两端 Tailscale 在线，六容器运行，Serve 仍为 tailnet-only。没有重装、改代理/DNS、重启服务或开放公网。
- 真实 TLS、readiness、Host 拒绝、登录/刷新/退出和 Secure/HttpOnly Cookie 自检通过；原合成文件 checksum、1024 维向量及 indexed 状态正确。可运行任务为 0，账本仍为 2 次 succeeded（含原 Embedding）与 1 次 outcome_unknown；未新增模型调用。
- 昨日未提交的六个文件已逐项复核；部署测试重新取得 20 passed，Ruff 与 diff 检查通过。初次受沙箱临时目录权限限制，正常权限重跑通过，没有通过改系统权限处理。待按既有阶段同步授权普通提交/推送，不触碰受保护知识文件。
- 当前 Prompt 仍 draft、Eval 仍 error、没有工作流运行。已询问是否接受可能重复计费并只重跑一轮（最多六次模型请求）；用户未明确同意前，不假定“继续”已经核对供应商结果，也不填假 provider_checked。

### 21.55.16 已执行用户接受风险的一轮；转入离线诊断补强

- 用户已明确接受一次重跑风险，本轮通过正常 API 创建新 Eval，旧未知任务不改、不冒填已核对。实际新增两个请求：首个 plan 成功、报告 6173 tokens；第二个约 4 秒 RuntimeError，无结果/用量，另一个 Job 进入 manual_review，余下四例未调用。没有继续自动重跑，也没有创建活动或放行 Prompt。
- 已修复源码的两处可观测性不足：文本错误保留安全的 HTTP/网络/响应格式类别；Eval 后续用例异常时经 Worker 既有租约门禁保留前面已完成断言的哈希证据。不会记录上游正文、放宽评测或增加重试。110 passed / 11 subtests、Ruff/diff 通过；Linux CI 和部署结果另记，当前运行镜像仍 `6871ac9`。
- 浏览器控制恢复，私人 HTTPS 登录页已实际打开；没有再次改网络配置。下一步完成诊断修复的 CI/部署，不发送第三轮模型调用；下一次收费定位需新的明确范围。不能补造两个历史失败的 HTTP 状态/网络根因或第一例断言结果。详见 CF-20260919-02。

### 21.55.17 诊断修复已部署，等待限定下一次真实定位范围

- `1502460` 已普通推送，CI #35435079201 四项全绿：409 passed / 201 subtests、覆盖率 82.98%。诊断版 API/Worker 已实际部署，应用 SHA 为 `1502460aaf59e49ba2956f69ea7ec91b680a01eb`；Web 镜像仍为上一版，未改前端源码。
- 完整大镜像传输失败后没有加载半包；改用校验过的约 1.1 MB 源码/wheel，在目标旧镜像上禁网构建。目标 ID 为 `146655ac40417080db59e774ac6bd841383e9e9940211154190a371df7c4944c`，保留 13 个基础层、运行配置不变（仅新增源码标签），源码与安装目录各 63 个文件签收。不能把它冒称 Windows 原完整镜像的相同 ID，详细来源见 CF-20260919-02。
- 维护命令须在此前四个环境文件之后再加 `--env-file diagnostics-20260919/activation.env`，Compose 文件覆盖顺序不变。旧配置/镜像保留；不带新增环境文件会回退旧诊断能力，去掉单人覆盖会另行改变治理策略，两者不要混淆。
- 重建前后原合成对象/向量/文档正确，真实 TLS/鉴权/安全 Cookie/生产 Settings 通过。两个历史 Eval 仍 error，对应 Job 仍 manual_review、provider_checked 未改，Prompt draft、无 runnable Job/工作流；账本 3 succeeded/2 outcome_unknown，没有新增模型调用。当前问题不是缺 Key、服务器未安装或授权未给，而是历史调用具体错误被旧版丢失；下一次需限定一次定位请求，不能再次盲跑六例或跳过门禁。

### 21.55.18 已授权单例定位成功；完整评测仍未通过

- 用户已允许一次失败用例诊断。本次通过生产适配器/账本执行原注入用例，仅一条请求，规范输入/模型/Prompt 哈希与两次历史失败相同；先落库一次性授权，已有记录即禁止重放。没有新建完整 Eval 或改变审批状态。
- 北京时间 2026-09-19 19:35:32 起，约 20.89 秒返回，原单例断言全部通过；服务报告 4279 tokens，账本恰好 1 条 succeeded。诊断审计 ID 与供应商请求 ID 见 CF-20260919-03，私人脚本在忽略的 diagnostics-20260919 目录，仅 status 可以无副作用复查。
- 没有复现错误，历史根因和未知计费仍不能确定；不要再称单例“仍然必然失败”，也不能说问题已彻底修复。旧两个 Eval/Job 仍 error/manual_review，Prompt draft、ready_for_generation=false、没有工作流。运行源码仍 `1502460`，下一阶段需要完整六例通过才能审批/激活/生成，不能把这次单例诊断拼成通过证明。

### 21.55.19 完整评测已返回，5/6 通过；非回显候选 r2 保持草稿

- 用户继续后执行了一轮完整六例，Eval `7af1a537-90c0-4bf3-9e15-ec84b0b9d9ff` 为 failed，Job 执行成功，六次模型调用全部返回、报告 23300 tokens。不是再次网络中断；唯一失败是注入用例回显禁止标记，其他五例通过。保留全部断言和历史失败，不生成内容。
- 原文未持久化，无法确定标记出现在拒绝说明还是实际策划中；不能说已执行攻击或肯定误报。通用 plan 非回显补强已以独立 r2 `a5cb3511-2bc7-47d1-b394-c1c2103fcbb1` 保存，r1 不变；仅 plan 哈希不同，未评测/批准/激活，全局 builtin 和运行源码 `1502460` 未改。
- `build_no_echo_prompt_candidate` 在私人验收 fixture 中可复现候选；30 passed / 2 subtests、Ruff 通过，仅证明构造与门禁，不证明真实模型改进有效。套件 v1 原 hash 不变、数据库对象/向量正确、runnable Job 0，账本 10 succeeded / 2 outcome_unknown，旧两个 manual_review 仍 provider_checked=false。
- 下次从 r2 的一轮完整真实 Eval 继续，通过后才按单人内测说明确认/激活并生成测试稿，停在人工审核。不要复用旧脚本的 bootstrap/rerun_once/continue_eval_once 反复提交：这些历史一次性授权已经消耗；原 generate 动作仍指向 r1，不能误激活它。新候选未通过前不得绕过治理、删除输出标记或放宽断言。详见 CF-20260919-04。

### 21.55.20 重启恢复签收，r2 六例通过，首篇真实稿件待人工审核

- 2026-09-21 用户说明电脑重启并明确允许 r2 一轮 Eval、通过后生成一篇测试稿。原 LAN 入口 banner 超时，但已在线 Tailscale 严格主机校验 SSH 成功；实机 uptime 约 10 分钟、六容器自动启动。TLS/Host/登录刷新退出/安全 Cookie、原合成对象 checksum/1024 维向量均通过；内置浏览器重载后实际显示登录页，未登录，不冒称已验证登录后 UI。未改网络、代理、DNS、账户或服务配置。
- Eval `f00fcb5e-fc7e-4eab-9b97-0f6ebe4ce789` 对原 suite v1 为 6/6 passed；六次请求全部 succeeded，服务报告 26570 tokens，未放宽断言或清洗输出。r2 `a5cb3511-2bc7-47d1-b394-c1c2103fcbb1` 已经正常 API 本人确认/激活，说明仍明确“代理代表所有者，不是独立第二人审核”；r1 和三次历史 Eval 不变。
- 活动「Ubuntu 内测 0921｜r2｜发布前复核清单」`89e656b4-5a42-4830-a7cb-99ce57496db0`，工作流 `4f88bc01-e79b-475f-9a98-bbad60e01520` 已 awaiting_review/human_review。内容 `85abe2a3-2547-4724-907b-9f9565748ccf`《AI 文案发布前，先过这三道关：事实、承诺、适配》为 needs_review，正文 1169 字符（954 个基本汉字），人工批准字段均 null。规则通过，模型质量分 8.2/10；这是模型评价，不是人类质量签收。
- 本次生成实际 1 次 Embedding 查询（102 reported tokens）及 plan/generate/review 三次文本请求（21605 reported tokens），未触发定向改写。1 个 AI image Asset 仍 planned、没有 storage_uri，发布任务和 runnable Job 均为 0；没有生成图片、微信素材/草稿或公开发布。累计调用账本 20 succeeded / 2 outcome_unknown，旧人工核对任务未变。
- 应用镜像仍 `146655ac…4944c`、源码仍 `1502460`，本轮无应用代码部署或数据库迁移；真实候选是数据库中的 r2。下一步由用户在「2 审核内容」体验现有稿件，不重跑 Eval 或生成重复稿，也不代替用户批准内容。待明确内容审核后再测试 AI 封面/手动上传和微信链路。
- 已记录内容示例缺少改后对照、过程用语外露；检索只取近邻且缺少相关性拒绝，返回了无关合成资料；source_chunk_ids 是召回列表而非实际事实引用；质量分达标时不因高优先级 revision_instructions 自动改写。详见 CF-20260921-01，均未在本轮冒称解决。单次六例/单稿成功不代表完整抗注入、跨网、灾备或企业交付签收。

### 21.55.21 全面重新复审：已确认新缺陷，尚未实施修复

- 用户本轮要求重新审视项目，范围为审计和报告，不是批准继续真实生成/素材/发布。完整结果见 `docs/project_audit_20260921.md`，24 个编号区分复现、代码确认和待验收；不要把它们混成 24 个已利用安全漏洞。
- 基于源码 `3a88f54`：隔离复现人工来源被转成 AI、素材重试绕过未知结果门禁、字符串 false 误判通过、改稿保留旧审核分、重复请求产生不同 run；双旧 Cookie 刷新也复现会话撤销，但尚未做双标签页 E2E。前端未保存修改直接审批旧稿、Worker 丢租约后的逐调用保护缺口等为代码审查结果。
- 原有定向测试 `42 passed, 9 subtests passed`；本轮探针使用临时 SQLite，不调用真实服务、不修改现有稿件。测试通过不表示上述问题已经修复；下一轮实现应首先把失败场景变成正式回归。
- 仅新增报告和交接/台账索引；应用代码、运行配置、真实数据库及受保护知识文件不变。没有提交、推送、连接远端或重新验收在线状态；同日实际部署事实仍以 21.55.20 为记录依据。
- 优先顺序：统一素材未知门禁和来源 → 严格模型输出/审核版本/未保存审批 → 生成幂等与租约 → 质量、E2E、异机恢复及真实告警。公网仍暂停；后续测试不要重复生成既有待审稿或假填供应商已核对。

### 21.55.22 复审后恢复实施：素材门禁、来源、输出契约和扫描边界

- 2026-09-23 用户要求全面重新审视，复审更新见 `docs/project_audit_20260923.md`，新增 A25 发布素材快照、A26 发布操作 ID、A27 下载锁序、A28 旧领域语义；持续目标随后恢复实施，不能停留在报告或声称全计划完成。
- 已实现 A01 共享素材操作门禁及审核者发起核对入口，A02 API/Worker 保留来源与配置漂移拒绝，A27 下载写回内容→素材一致锁序；新增临时数据库与真实 PostgreSQL 竞争测试。PG/MinIO 不能以本机跳过当作通过，最终结果见进度页。
- A03 新增三阶段结构契约，严格 bool/有限评分/引用结构与三平台布局，拒绝坏结果但保留已返回调用的哈希和用量；错误不回显模型原文。Eval 记录输出契约版本，旧 Eval 不自动成为新版通过证据，详见 `docs/model_output_contract.md`。未部署，不得擅自用旧一次性授权重跑 r2。
- 全量回归还发现 A29 存储时钟边界误判，经四种确定性失败测试确认并修复，存储专项 18 项通过。当前完整测试与 CI 状态统一记录在进度页，未取得证据前不要写成通过。
- 下一阶段：A25 最终发布物快照、A04/A06 所见即所批和审核证据版本，再做生成/发布操作幂等、租约、质量与运维验收。真实稿件/图片/微信、Ubuntu 配置、密钥/代理及受保护知识文件均未触碰。阶段同步按既有授权普通 commit/push，不 force，不自动合并主分支。

### 21.55.23 CI 签收与 A25 服务端发布物边界

- `942faa5` 已普通推送，CI `35844426448` 四项全部通过：480 passed / 218 subtests，覆盖率 83.61%，包括真实 PostgreSQL/MinIO、新锁序竞争、前端构建、依赖扫描及来源证据。不替代 Ubuntu 部署或真实供应商验收。
- 后续实现 A25 的服务端清单：正文/渠道摘要、素材 ID/URI/SHA-256；排期必须素材 ready 且具备完整性记录；执行/安全重试/脚本切换拒绝清单变化，适配器读取实际发送字节时校验。Worker 使用脱离 Session 的副本，不在提交后加载新稿/新封面。旧待执行任务缺清单拒绝，终态/已提交优先返回，不能重放平台调用。详见 `docs/publish_manifest_contract.md`。
- A25 的前端最终预览、确认指纹与 E2E **仍未实现**，不能把后端绑定称为完整所见即发布。下一步先完成这一部分，再按进度页做 A04/A06 与幂等；本批最终测试/CI 以 `docs/IMPLEMENTATION_PROGRESS.md` 为准，不把上一个提交的绿色复用到新源码。
- 最终源码 `69d27ac` 已推送，CI `35846656631` 四项全过：491 passed / 226 subtests，覆盖率 83.92%。`f399c0f` 的首次 CI 因旧测试误把合法清理任务算作重复生成而失败，修正后明确核对唯一生成与准确旧对象清理，原失败记录保留在进度页；不是 force 或权限问题。最后仅补文档签收。部署前须暂停新发布并协调 API/Worker 同版，不能让旧 Worker 消费新任务。
- 无部署、真实模型/媒体/微信调用、真实数据变化、代理/DNS/密钥修改；受保护知识文件未读改。全目标仍 active，不以阶段同步或局部测试代替整体完成。

### 21.55.24 再次全链路复审：新增安全缺口，保留在制预览

- 用户最新请求为重新审视和报告，本轮没有继续修改业务代码。HEAD 为 `771146d`，接手时已有未提交 A25/A26 预览、签名确认、操作 ID 和回执恢复；已保留，不能按上一节“前端尚无实现”忽略这些在制代码，也不能视为已验收/部署。
- 完整结果见 `docs/project_audit_20260923_followup.md`。新增 A30 平台 HTTP 异常 URL 中 token 被存进发布错误并向 viewer 返回；A31 渠道 config.api_base 未限制目标，HTTP 回环地址可进入客户端；A32 字符串 false 被当作公开提交，而页面严格 bool 提示为仅草稿；A33 快照累计量重复相加、样本数实际是快照数；A34 PATCH body=null 返回 500（事务回滚保留旧数据）。前三项优先整改，不是匿名 P0 或已发生真实平台攻击的结论。
- 本轮探针只用临时 SQLite、合成素材、虚构凭据与 MockTransport，没有真实微信/内网/模型调用。复现旧审核分保留、同键重复生成、地图领域硬编码及旧 Cookie 刷新撤销；未做真实浏览器双页、PostgreSQL 竞争或实机验收。
- 当前工作树本地后端 `471 passed, 20 skipped, 7 warnings, 226 subtests passed`，Ruff、前端 ESLint、无增量 TypeScript 与 diff 检查通过。跳过不算通过；在制预览仍缺专门 token/回执丢失/并发/E2E。未重新运行覆盖率、生产构建或依赖在线扫描。
- 仅新增审计及索引，未暂存、提交、推送、部署或改网络/密钥/真实数据。Ubuntu 源码 `1502460` 只是最后已记录事实，本轮未复核在线。后续先关 A30–A32，再收尾 A25/A26 与 A04/A06，然后费用/租约、内容质量与运行验收；不能重复消费旧一次性真实调用授权。
- 继续核对新增 A35：Embedding 接口两条 1024 维结果的 index 为 `[0,0]` 或 `[-1,2]` 时仍被接受；知识索引按位置配对，存在供应商异常结果静默错配的风险。仅 MockTransport，未调用真实接口、未修改真实向量；应与 A11 一起增加严格对应关系和原子更新回归，不能说真实索引已经损坏。
- 同日复核后的新一轮全套仍为 471 passed / 20 skipped / 7 warnings / 226 subtests，186.44 秒；Ruff、前端 lint/无增量类型检查和 diff 检查再次通过。没有把通过测试当成 A30–A35 已修复，也没有生产构建、远程 CI、新在线漏洞扫描或实机签收；本次仍只补审计文档。

### 21.55.25 A30–A32 开始实施，保留 A25/A26 在制代码

- 持续目标恢复后先做三个新安全缺口，不是再次重复审计：新增 50 项失败回归后实现分平台闭合配置、官方 Origin/禁止重定向、严格发布 bool、连接器与 Worker 安全错误及日志边界。接口对历史错误只显示安全摘要，旧数据库证据不删改。契约和升级限制见 `docs/channel_security_contract.md`。
- 已覆盖微信、抖音、API→Worker→viewer、旧配置网络前拒绝、写入前安全重试/写入后未知门禁、旧错误保留、任务被删除时的日志兜底。首轮全套 556 passed / 20 skipped / 226 subtests；后续最终签收必须看进度页，不把中间结果或跳过当最终通过。
- 所有测试仍为隔离合成数据；无实机/真实费用/微信/网络/密钥操作，历史日志影响和真实平台签收尚未完成。A25/A26 的旧在制代码全部保留，尚缺专门 token/重放/PG 并发/浏览器验收；下一步先收尾，不以本轮局部安全修复关闭完整目标。仅按阶段授权普通同步依赖完整的在制功能分支快照，不 force、不自动合并 main、不部署。
- 最终本地源码 558 passed / 20 skipped / 7 warnings / 226 subtests，181.66 秒；安全专项＋原连接器/素材清单 97 passed。Ruff、前端 lint/TypeScript、Next.js 生产构建、diff 检查通过。Git 作者与登录账号已核对归属 heee000；提交/CI 的实际状态以进度页随后补记为准。
- 源码 `a1e828f1dedf9826dd0441c2d0332a1a441437c0` 已普通推送，包含依赖完整的 A25/A26 在制快照，不代表后两项已验收。CI `35854697647` 已实际创建且按 ID 复核 in_progress；不要再 dispatch 同一源码，只查询该 run 完成/失败证据。当前没有 PR、合并或部署；最后只补同步记录。下一步完成此 CI 签收，再继续 A25/A26 token/回执/并发/浏览器测试及审核一致性。受保护知识文件仍唯一未跟踪文件，未读取或纳入提交。

### 21.55.26 确认协议补齐专项与浏览器验收

- 上一安全源码 `a1e828f` 的同一次 CI `35854697647` 已四项 success：578 passed / 226 subtests，覆盖率 84.33%。没有重复 dispatch，不把此结果用于本批新源码。
- 新增 41 项确认测试，修复签名未绑定操作 ID（换编号可复用凭证）和版本号 bool/float；统一预览/确认必填编号，验证脚本同步。并发等待内容锁后先重查回执，避免已成功的原请求因内容被改而误拒绝。四种新 PG 竞争待本批 CI，具体结果看实施进度。
- 本地最终后端 599 passed / 24 skipped / 7 warnings / 226 subtests；确认＋渠道专项 128 passed。六条真实前端/API/临时 SQLite 浏览器旅程 6 passed（Edge 无头），包括丢回执、刷新、精确重放和跨身份隔离。无 Worker，不访问真实账号。新增 Playwright/隔离服务/CI 入口，详见 `docs/browser_acceptance.md`。
- 夹具 `put()` 新 URI、测试标签选择器和 Node/原生可选依赖环境问题分别记录，不误报产品漏洞，不删除锁文件。后续最终构建/SSR、源码同步/CI 结果须看进度页；不把中间结果冒称最后签收。
- 未部署、未运行真实模型/媒体/微信、未修改网络/密钥或保护知识文件。Ubuntu 最后记录仍是 `1502460`，本轮未刷新在线状态；不能再次用旧一次性授权重跑真实稿。下一步本批 CI 后继续 A04/A06、生成幂等、费用/租约与剩余完整计划，不以发布专项阶段结束完整目标。
- 最终源码 **`0d6608f`** 已普通推送；[CI 35858597117](https://github.com/heee000/ContentFlow/actions/runs/35858597117) 四项全过：**623 passed / 226 subtests，84.56% 覆盖率**，含四种 PG 确认竞争和 MinIO。生产 standalone 浏览器本地 Edge 6/6（27.2 秒）、CI Chromium 6/6（32.9 秒）；SSR 2/2、构建和当次安全/来源校验通过。最后只补文档签收，勿重发同一 CI。Git 作者仍为 John Wang/heee000 noreply，没有 PR/合并/部署。
- 下一阶段入口已重读：`ReviewView.decide()` 只发送当前已保存版本，独立非受控表单没有 dirty 门禁；`update_content()` 升版仍保留 `model_review/quality_score`，`review_content()` 尚无新版规则复查。先补失败的 API/浏览器回归，再实施 A04/A06；不要暗中收费重跑模型，不以旧评分代替新稿证据。

### 21.55.27 当前工作树重新复审，新增指标与测试隔离疏漏

- 2026-09-24 用户再次要求全项目审计。HEAD 仍为 `0ca01d3`，保留全部未提交审核/迁移/文本传输及测试代码；本轮仅新增 [完整复审](project_audit_20260924.md) 和修正进度入口，没有业务修复、暂存、提交、推送、远程 CI 或部署。
- 上一节“审核尚无 dirty 门禁/版本复核”只代表当时状态。当前 A04/A06/A38 已有未提交实现，A36 已禁止文本重定向；本轮相关后端/迁移/PG 入口 70 passed / 25 skipped / 7 warnings。PG 跳过不算通过，不能把局部测试当完整候选版本验收。
- 当前 17 条浏览器旅程为 9 passed / 1 failed / 7 未执行。失败发生于测试精确匹配 `2 审核内容`，页面实际按钮名带计数 `2 审核内容 11`；不是证明远端改稿保护失效，也没有通过反复重跑刷绿。本轮未直接修测试。
- 隔离重现 A05/A08/A33/A34/A35/A37/A39/A40/A41；新增 A42：编辑权限录入字符串 Infinity 返回 201，自己的工作区指标汇总随后 500。新增 A43：默认模块导入先加载 Settings/.env，测试随后 `_env_file=None` 不构成导入前隔离，浏览器脚本仅过滤环境变量亦不够。没有打开/输出真实秘密，未发现真实业务库改写或外部调用；但不再声称 Python 测试导入阶段绝不读取本地配置。
- Ruff、前端 lint/无增量类型检查和 diff 检查通过；本轮没有全量后端、当前真实 PG/MinIO、远程依赖扫描、实机恢复/容量或真实模型/媒体/微信签收。最初沙箱临时文件权限错误与产品问题分开记录，不改 ACL。
- 完整改进目标仍未完成；优先收尾在制审核与测试隔离，再做错投/会话、生成幂等、执行权/预算和指标污染，随后内容质量及目标环境验收。公网计划仍冻结，旧一次性真实调用授权不复用。受保护知识文件仍不读取、哈希、改动或暂存。

### 21.55.28 审核收尾、指标污染与导入隔离整改

- 持续目标恢复实施，收尾 A04/A06/A38/A36，修复 A42/A43 与 A15 测试定位。四份契约为 `content_review_contract.md`、`text_transport_security.md`、`metrics_validation_contract.md`、`runtime_initialization_contract.md`；最新测试/源码 CI 以实施进度末节为准，不沿用 `0d6608f` 的绿色。
- 审核按当前版本/hash、本地规则和明确警告确认；保存失败/丢回执/远端冲突保留输入、禁止误批，旧证据归档且回滚原子。禁止文本 HTTP 跳转，不更改全局代理。新指标只允许有限范围数字；Worker/DB 共防，旧异常保留原值隔离，页面显示不完整，指标服务失败不再阻断其他队列。
- API 入口改为 `uvicorn contentflow.api:create_app --factory`，原 `api:app` 命令失效（本文件早期历史示例不代表当前启动方式）。导入 db/api 不再读默认配置；测试在收集/应用导入前隔离 dotenv/业务环境。独立 Worker 用显式 Settings 的数据库，自有 pool 必须在一次性调用后 with/close；永久循环退出自动释放，不关闭外部注入 pool。
- 本地浏览器完整 19/19（Edge/生产 standalone），后端中间专项 86 通过。全套首轮 719 passed / 1 failed / 34 skipped：新 Worker pool 泄漏使临时 SQLite 无法清理，已补资源生命周期测试后重验，不忽略或反复重跑刷绿。PG 的审核竞争与指标 NaN/边界/迁移必须本批 CI 签收；最终状态看实施进度。
- 迁移顺序 a5 → b6 审核证据 → c7 指标隔离；当前唯一 head `c7d8e9f0a1b2`，至少 34 表，恢复校验同步。迁移不自动改旧审核/原指标；生产回退不能直接删证据或抹隔离状态。API/Web/Worker 必须同版协调升级，本轮没有部署或操作真实 DB。
- 剩余优先 A37/A08 工作区上下文/会话刷新、A05 生成幂等、A07 执行权、A09/A12 快照/预算。A33 累计指标语义、A35 索引对应、A39 图像字节、A40 最终字段规则、A41 schema readiness/生产 Worker create_schema 仍未解决。无真实模型/媒体/微信/网络/密钥操作，受保护知识文件保持未读改/未暂存；完整目标仍 active。
- 生命周期修复后 45 项专项通过，最终本地全套 **721 passed / 34 skipped / 226 subtests，246.56 秒**；19 条浏览器、2 项 SSR、lint/类型/Ruff 均通过。待同步与本批 PG/MinIO/Chromium CI，不以本机跳过签收真实服务；具体 SHA/run 随后只追加到进度页，勿重复 dispatch。
- 最终源码 **`d6596d5a4a5a5cbd5172b833f40387a7897abdbb`** 已普通推送。[CI 35892709913](https://github.com/heee000/ContentFlow/actions/runs/35892709913) 四项全部 success：**755 passed / 226 subtests、84.87% 覆盖率**，包括真实 PG/MinIO、审核竞争与指标 NaN/迁移；19 条 Chromium（46.2 秒）、2 项 SSR、构建及当次依赖/来源验证通过。最后只补文档签收，不重复 dispatch、不合并/部署。下一阶段预检已记在进度页：A37/A08 必须同时处理 Cookie 上下文预期、迟到响应/分页和跨页刷新，不能仅广播切换或放宽旧令牌重放。

### 21.55.29 当前工作树全链路复审，保留在制会话改动

- 用户再次要求全项目审计，HEAD `3444609`；当前已有未提交 A37/A08 会话/工作区上下文、跨页续期、旧响应隔离与保留输入代码。全部保留，本轮不继续实现或提交。见 `docs/project_audit_20260924_followup.md` 和实施进度末节。
- 本地全套 **735 passed / 34 skipped / 226 subtests，250.22 秒**，7 warnings；生产 standalone Edge 浏览器 **25 passed，54.0 秒**；Ruff、lint、无增量类型及 diff 检查通过。当前候选 PG/MinIO、远程 CI、Ubuntu/真实平台均未验收，不借用旧 SHA 的绿色。会话专项原运行另为 6 passed/21.6 秒，非 25 次之外新增覆盖。
- 新 A44 队列冲突的整事务回滚风险（受控查重 miss+真实 SQLite 唯一冲突，尚无 PG 业务并发签收）；A45 活动外平台重生成假成功（202→awaiting_review、0 内容）；A46 禁用 Mock 的生产配置被 Provider override 绕过（合成已校验配置构造器证据，不是匿名越权）。A16 晚提交更新漏增量已由两个隔离会话复现。
- A05/A33/A34/A35/A39/A40/A41 仍复现；审核/指标非有限数值/文本跳转/导入隔离的上批修复不重复当作未修。会话迟到响应测试的异步完成屏障、真实 PG 刷新/切区竞争、逐入口负向矩阵与协议升级说明仍待补齐。
- 仅补报告/索引，未触碰真实数据库、稿件、网络、密钥、受保护知识文件；无暂存、提交、推送、CI 或部署。公网仍冻结，旧一次性真实调用授权不复用。继续时先收尾既有在制会话候选，再依报告顺序整改，不以完整测试通过声称产品已经成熟可交付。

### 21.55.30 会话收尾过程中再次全项目审计，当前候选尚未签收

- 用户最新要求复审，故本轮只检查/记录，保留已有未提交会话与测试实现。当前优先阅读 `docs/project_audit_20260924_current.md` 和实施进度末节；HEAD 仍 `3444609`，不要以 21.55.29 的 735/25 条绿色代替后来扩展过的候选。
- 接回后端全套 **761 passed / 38 skipped / 226 subtests，324.40 秒**。前端 npm 测试 **7 passed / 1 failed**，失败为重构后旧源码位置断言；会话浏览器 **2 passed / 1 failed / 5 未执行**，迟到响应完成屏障仍超时，删除 Content-Length 未解决。另补两条此前未执行专项 **2 passed / 12.8 秒**；真实 PG、完整 27 条、新 CI 均未签收。
- 当前矩阵与契约已补入在制文件，不再说完全未实现；但不能因六项模块测试通过就删除浏览器等待或关闭会话风险。先定位并修复失败，再签收相同候选，API/Web 上下文协议需协调升级。
- 新 A47 已由实际 Worker+Mock 故障注入证实：第一平台生成后第二平台失败，前者没有持久保存，run failed/0 内容/空结果。先做阶段检查点和安全续作，不能自动重放结果未知的供应商调用。A05/A16/A28/A33/A34/A35/A39/A40/A41/A44/A45/A46 仍复现；A42 继续正确拒绝。
- 只追加报告和索引，不修改业务/测试代码，不暂存/提交/推送/触发 CI/部署；真实秘密、业务数据、Ubuntu/网络和受保护知识文件未触碰。完整目标未完成，公网仍冻结。后续普通阶段同步必须先处理当前失败，不能把文档或旧 CI 视为该候选验收。

### 21.55.31 A37/A08 本地收尾，待本批真实服务 CI

- 持续目标恢复后完成在制会话协议，详见 `docs/browser_session_contract.md` 与实施进度末节。服务端 Cookie 上下文强制校验、锁后复核；前端请求/分页代次、跨页续期/切区协调、失回执保守停止并保留输入。没有放宽历史 refresh 重放撤销或用广播代替服务端门禁。
- 原源码位置断言已修正且增加正式模式行为回归；迟到响应根因为本机 Edge 的 no-store 未读响应生命周期，显式释放 body 后单项及完整浏览器通过。清理异常仍拒绝旧结果；测试等待成功/取消终态和模块 Promise 终结，不删等待刷绿。历史失败记录不改成已通过。
- 本地 **27/27 浏览器（58.2 秒）、14/14 模块/SSR**，lint/无增量类型/构建/Ruff/diff 检查通过；会话后端 **50 passed / 4 skipped（45.47 秒）**。后端代码未改动，上一轮全套 761/38 仍是该后端本地证据。四项新 PG 竞争、本批 MinIO/Chromium 和源码 CI 待签收，不借旧绿色。同步 SHA/run 和最终结果随后追加进度页，避免重复 dispatch。
- 旧 Cookie Web 与新 API 不透明兼容，缺头 428；API/Web 需同版升级，单一规范来源，未部署、无新迁移。无真实调用/费用/稿件/网络/密钥/受保护知识文件操作；后续仍 A05、A07/A12、A47 与事务/目标/Mock 策略、内容可信和运维门禁，完整目标未完成。只按阶段授权普通同步功能分支，不 force、不合并/PR/部署。
- 最终源码 **`12eaf9a4bd3135da7ddea4079004d49b41225e9f`** 已普通推送；[CI 35904373071](https://github.com/heee000/ContentFlow/actions/runs/35904373071) **四项全过：799 passed / 226 subtests、84.96% 覆盖率，27 条 Chromium（1.2 分钟）、14 项模块/SSR**。包含真实 PG/MinIO、新 4 项会话行锁竞争、当次依赖/来源签名验证。只补文档签收，不重复 dispatch 或部署。下一阶段 A05 的真实调用链和设计边界已追加实施进度末节，尚未实现；接手先核对该断点，不再重复旧会话失败定位。

### 21.55.32 A05/A45 生成意图与丢回执恢复

- 持续目标开始下一阶段，见 `docs/generation_intent_contract.md` 和实施进度末节。先取得 13 项失败回归，再实现强制操作编号、Brief 时间前置条件、工作区内唯一接受回执、事务原子性与只读查询；同编号原样重放原 Run，不再接受新任务，不因活动/Prompt 后来变化丢失原回执。
- 页面发送前保存，失回执/截止时间保留编号；刷新不自动重发、不同工作区不串，离页后相同回执乱序到达可收尾。生成记录/审计显示编号。不是费用上限、供应商 exactly-once 或跨设备持久草稿；A07/A09/A12/A17/A44/A46/A47 仍开放。
- A45 已在 API 与 Worker 外部调用前校验非空活动平台/重生成子集，旧队列不能先规划后零内容假成功。新迁移 `d8e9f0a1b2c3` / 至少 35 表；旧 Run 保留不伪造回执，采用无版本库时核对新表约束。旧客户端缺编号/预期时间会 422，API/Web/Worker/schema 要协调更新；未部署、未迁移真实库。
- 本地中间完整后端 784 passed / 43 skipped / 226 subtests；32 条 Edge 完整浏览器、24 项模块/SSR、静态检查/构建通过。随后补只读核对/约束和编号展示，最终专项与本批 PG/MinIO/Chromium CI 结果请看实施进度，不借上批绿色。阶段同步只普通 push 现有分支，不 force、不合并/PR/部署；受保护知识文件继续未读改/哈希/暂存。

### 21.55.33 生成候选全项目复审：新增发布原子性与回执退路问题

- 用户最新请求是全项目审计，本轮不继续实施或同步。HEAD `16c26bf`，完整保留 A05/A45 未提交代码；最新入口为 `docs/project_audit_20260924_generation_review.md`。后端重新全套 **785 passed / 43 skipped / 226 subtests（340.88 秒）**，Edge 完整 **32/32（1.2 分钟）**，模块/SSR **24/24**，构建/静态检查通过；当前候选真实 PG/MinIO/新 CI 仍未签收。
- 新 A48：SQLite 发布确认在审计失败后返回 500，却留下 queued 发布记录和 0 分发 Job；原样重放返回 202，依然 0 Job/0 审计。首次 SAVEPOINT 写/外层事务边界问题，不能直接推断 PG 也如此。A05 已处理自身事务不代表发布路径已修。
- 新 A49：发布回执实际 TS 模块对损坏/拒读返回 null，不完整意图也可接受；缺严格状态和身份匹配清理。服务端同编号幂等仍有效，本轮没有实际重复平台发布。继续实施时先补故障回归，不能因 32 条现有浏览器绿色关闭新缺口。
- A07 由 Mock Worker 故障注入确认失租后仍继续 generate/review；A47 已完成第一平台后第二平台失败仍 0 内容。其余媒体/规则/指标/向量/增量/队列/Mock/就绪问题复验见报告；A37/A08 已上版签收、A05/A45 在制、A42 继续拒绝，状态必须区分。
- npm 当次审计 0 已知漏洞；Python 在线审计被自动审批拒绝依赖元数据外发，未绕过、未执行。旧 CI 35904373071 只读复核 success/head=12eaf9a，不复用到当前候选。243 个源码/测试/交付候选前后哈希 0 变化，仅追加审计/索引；没有暂存/提交/推送/CI/部署/真实调用或网络/秘密操作，受保护知识文件仍不读取/哈希/暂存。
- 下一实施入口：完成 A05/A45 同版验收并优先补 A48/A49/A44，再执行权/预算/检查点及内容质量/运维。整体目标尚未完成，公网仍冻结，不能复用旧一次性真实验收授权。

### 21.55.34 发布事务候选重新复审：A50 时间契约失败，A49 尚未收尾

- 用户最新要求全项目审计，HEAD `16c26bf`；接手时 A05/A45、A44/A48 已有未提交实现，全部保留。最新报告 `docs/project_audit_20260924_acceptance_review.md`，本轮只审计记录，不继续修改业务或测试代码。
- 全套 **796 passed / 1 failed / 46 skipped / 226 subtests，352.81 秒**。原子性 12 项本地回归通过，但立即发布返回的 SQLite datetime 无时区，现有用例 TypeError。新 A50 经独立 API 与本机日期解析验证：首次/重放同任务无 offset，Asia/Shanghai 解释差 -8 小时；未证明 PG 同样受影响或实际调度提前。
- 32 条 Edge 浏览器、默认 24 项模块/SSR、构建/静态检查通过；单独 `publication-intents.test.mjs` 三项真实断言失败，默认 npm test 尚未包含。A49 要区分损坏/拒读、读回保存、身份匹配清理与只读恢复，不以现有浏览器绿色关闭。
- A07/A39/A40/A33/A35/A34/A16/A46 继续复现，A45/A42 正确拒绝。A47 本轮第二平台 generate 故障后保留 ai_provenance 诊断，但没有 plan/contents 或可编辑稿，勿说所有证据均丢失。A44/A48 进入已有本地候选/待完整签收，不再称完全未修。
- 247 个源码/测试/交付候选前后哈希 0 变化，仅报告/索引/进度/交接更新；无暂存、提交、推送、远程 CI、部署、真实账号/模型/媒体/微信或网络/密钥操作。受保护知识文件仍未读取/哈希/改动/暂存；当前 PG/MinIO 未签收，Python 在线依赖审计授权仍待明确。
- 下一步先修 A50 时间契约与 A49 回执、纳入正式测试，收尾 A44/A48/A05/A45 PG/同版验收，再执行权/预算/检查点与内容/运维计划。公网继续冻结，旧一次性真实授权不复用，完整目标仍 active。

### 21.55.35 发布恢复与 UTC 契约已本地收尾，真实服务/交付仍待验

- 持续目标实施已处理 A49/A50，保留并验证 A05/A45、A44/A48；参见 `docs/publication_acceptance_contract.md` 与实施进度末节。不要再引用上一节的后端 1 失败/模块 3 失败作为当前状态，也不要删除原始失败证据。
- API 所有类型化日期响应规范为 UTC/Z；发布原样重放和新只读核对返回原编号与任务，孤立回执拒绝、不补发。浏览器严格意图状态、保存读回、回执身份与删除身份核对、在途门禁及 30 秒截止；手动清除需明确风险确认，不能当作服务器取消或供应商无副作用的证明。
- 最新本地完整后端 **806 passed / 46 skipped / 226 subtests（330.63 秒）**，完整生产 Edge **43/43（1.8 分钟）**、默认模块/SSR **40/40**。全套后增加一项响应模型覆盖及 PG 断言，最终专项 **10 passed / 3 skipped（3.76 秒）**；Ruff/lint/无增量类型通过。具体中间失败、测试组成及边界见实施进度，不拼成一次未实际运行的 807 项全套。
- 本机 Docker Linux engine 不可用，未启动服务或变更网络；46 项服务用例仍未签收。Python 在线依赖元数据查询此前被拒，已询问一次授权但尚无答复，不运行审计或通过 CI 绕过。当前源码未提交/推送，HEAD 仍 `16c26bf`；不复用 `12eaf9a` 的 CI、不部署 Ubuntu、不迁移真实库。
- 接手先核对在制源码和本节断点，完成 PG/MinIO/迁移与同版候选验收后继续 A07 执行代次、A12 预算、A46 Mock 策略、A47 检查点，以及媒体/规则/向量/就绪、内容质量和运维。真实稿件待用户审核、旧一次性真实调用授权耗尽、受保护知识文件不读不改/不哈希/不暂存、公网冻结等边界不变。全目标尚未完成。

### 21.55.36 A07 执行权候选已完成本地回归，服务级验收仍待签收

- 持续目标实施已加入 `execution_fence.py`，最初九条失败回归确认真实 Worker 失权仍调用/写业务；现已逐调用检查、事务内条件写入门禁、主/独立进度 Session 保护、完成/错误传播隔离。原审计 A07 不再是“完全未做”，但 PG/交付未签收。详见 `docs/worker_execution_contract.md`。
- 人工重试会重置尝试次数，已新增每次领取独立令牌，旧 Worker/同次数不能复用。新迁移 head **e9f0a1b2c3d4** 接生成回执 d8e9f0a1b2c3，35 表；旧数据不伪造令牌，采用/回退只在隔离测试库执行。API/Worker/Web 要备份后协调升级，禁止混跑旧 Worker，未部署/迁移真实库。
- 当前最终执行权/迁移专项 **40 passed（22.76 秒）**，新 API/迁移下完整 Edge **43/43（1.9 分钟）**；新四项 PG 用例本机跳过。完整后端已实际终结：**847 passed / 50 skipped / 7 warnings / 226 subtests，383.04 秒**，不是沿用旧 806/46 全套或旧 CI。没有活动的后端/浏览器测试句柄需要重启；最终前端与阶段同步事实见实施进度追加记录。
- 调用账本保留迟到成功 hash/用量与未知结果，不自动写业务或补发；不可撤回已发送请求、不能承诺供应商 exactly-once。A12 预算、A47 成果检查点、A46 Mock 策略与剩余完整计划继续开放。
- 已只读确认当前远端和本地 HEAD 仍 `16c26bf`、作者/登录归属 heee000。依阶段同步授权可在本地验收后普通推送现有分支；不强推/合并/PR/部署。功能分支 push 不触发 CI，含在线依赖审计的手动 CI 仍等待外发授权；不要重复问或通过其他路径绕过。受保护知识文件未读改/哈希/暂存，真实账号/费用/平台/网络均未操作。

### 21.55.37 fe6d0e3 全项目再审：新增慢存储与续租冲突

- 用户要求重新审视项目，本轮只审计、隔离验证和记录。实际本地 HEAD 已为 `fe6d0e314be5f1edbcb840e226cadd58bafa1a3d`，不是上一节同步前的 `16c26bf`；初始仅受保护知识文件未跟踪，没有遗留源码修改。最新报告为 `docs/project_audit_20260924_execution_review.md`。
- 相关正式回归 **72 passed / 4 skipped / 1 warning，59.25 秒**，覆盖执行权/迁移/生成意图/接受原子性；四项 PG 未配置而跳过。未重跑完整 847/50 后端或 43 条浏览器，已有阶段记录不算本轮新执行。正常权限临时 SQLite 通过，沙箱错误未改 ACL。
- 新 A51：真实 `asset.generate` Worker + 真实心跳，3 秒租约/4 秒存储延迟后 Job running、Asset queued、URI null、账本 0，但临时对象 8094 字节存在。相同延迟放在生成阶段、存储正常时 succeeded/ready。预留写事务持有 Job 锁跨存储 I/O、独立心跳无法续租；SQLite 已复现，PG/S3 未实测，不声称默认 300 秒或实机已故障。不得通过取消旧 Worker 门禁来修复，应收敛短事务和外部 I/O/补偿协议。
- A47 后一平台失败仍丢失可编辑稿但保留 ai_provenance；A39 无效图片 ready、A40 标签漏审、A33 累计量相加、A35 非法向量 index、A41 空库 ready、A46 Mock 策略、A34 平台校验、A16 晚提交增量漏同步及 A28 领域硬编码均重新复验。先前 A05/A45/A44/A48/A49/A50 已实现，不能继续归为完全未修。
- 本轮未改业务/测试代码，未暂存/提交/推送/CI/部署，没有真实费用、平台、网络或凭据操作；受保护知识文件仍不读/改/哈希/暂存。原始组合探针的配置遗漏/清理 Job 抢先领取已修正夹具后复验，不列作产品缺陷。下一实施优先 A51/A41 与同版服务门禁，再预算/检查点/配置快照、内容与运行验收。完整目标未完成，公网冻结与真实调用边界不变。

### 21.55.38 存储候选全项目复审：新增旧素材复活竞争，未继续实施

- 最新请求为审计，不是继续修复。HEAD `fe6d0e3` + 接手已有 A51 暂存/短事务/回收 UI 候选、迁移 `f0a1b2c3d4e5`（接 e9，35 表）与新测试；全部保留。唯一最新审计见 `docs/project_audit_20260924_full_reassessment.md`，汇总入口已将旧“最新”划入历史区；请勿再将修复前 A05/A07/A44/A48/A49/A50/A51 原始失败直接套用到当前候选。
- A51 已实现外部 I/O 前短事务记录 charged staging、失权/回滚保留证据、受限管理员精确回收；本地慢上传续租等专项通过。当前 PG/MinIO/进程中止/响应丢失/回收竞争、回收 UI 和真实迁移恢复未签收，不因本地绿色关闭问题。
- 全套本轮 **859 passed / 50 skipped / 7 warnings / 226 subtests（456.24 秒）**；接回专项 **68 passed / 8 subtests（48.66 秒）**；完整 Edge **43/43（1.9 分钟）**，默认模块/SSR **40/40**，构建/Ruff/lint/无增量类型/diff 通过。所有测试进程已结束；50 跳过不算通过，当前无真实服务/同版 CI/部署证据。本机 Docker engine 管道缺失，未启动或改网络。
- 新 A52 用实际 Worker 和独立线程真实 API 复现：生成时改稿 v1→v2，旧 Asset stale 后被迟到结果写成 ready，已存 8094 字节。发布版本门禁仍挡旧素材，不声称实际误发；需按 content→asset 锁序在外部返回/激活前复核版本并补 PG 交错回归。A39/A40/A47/A33/A35/A41/A34/A46/A16/A28 再验及预算/RAG/质量/运维边界见报告。
- 本轮只补审计记录，238 个源码/测试/交付文件哈希无变化，无暂存/提交/推送/CI/部署、真实账号/收费/平台/网络/秘密操作。受保护知识文件继续未读/哈希/修改/暂存；Python 在线审计外发权限仍不通过其他路径绕过。下一实施从当前候选收尾和 A41/A52 开始，不再重新推倒既有幂等/执行权保护；公网冻结，完整产品目标未完成。

### 21.55.39 A52 已本地修复，与 A51 存储候选共同等待服务级验收

- 持续目标恢复实施：先取得 **13 failed / 3 passed** 的实际 Worker/独立线程真实改稿 API 回归，再修复生成、轮询、搜索、下载成功/processing/失败覆盖旧状态。操作输入快照 + 外部阶段间无锁重读 + 最终 content→asset 锁后重读，与执行令牌保护共同生效；不持有域行锁跨对象 I/O。混合候选 selected 变化保留，不无故取消生成。
- 过期结果在 Worker 事务边界回滚，保存 superseded 回执和审计，旧结果不关联新稿；已 PUT 对象仍 charged staging，不自动删/返配额/重发。未知 Provider 错误保持原人工核对/失败门禁，只是不覆盖 stale 或替换后的 ready。队列显示“旧结果未采用”与存储核对说明。详见 `docs/asset_work_contract.md`；组合候选 head 仍 f0，A52 无额外迁移。
- 最终全套 **880 passed / 53 skipped / 7 warnings / 226 subtests（397.24 秒）**；Edge 完整 **44/44（1.8 分钟）**，模块/SSR **40/40**，构建/lint/无增量 TypeScript/Ruff/diff 通过。新增 3 项真实 PG 等待/竞争已编写但跳过，不能视为签收；所有本地验证句柄结束。中间 Mock 夹具与桌面导航定位失败及处理详见进度，未删保护断言。
- 作者 John Wang/heee000 noreply、远端现有分支 fe6d0e3 已只读核对；阶段按用户历史授权普通 commit/push 现有分支，不 force/PR/合并/部署，不触发在线审计或 CI。阶段 SHA 随签收记录。真实数据/账号/费用/平台/网络未动，保护知识文件未读取/哈希/修改/暂存。
- 下一实施优先 A41：启动和 readiness 必须验证迁移兼容版本，Worker 生产入口不能靠 create_all 猜 schema；保留开发/测试初始化路径并补独立失败用例。A51/A52 仍需当前 PG/MinIO/kill/回收 UI 与迁移恢复，随后 A12/A47/A46/A09、A39/A40/RAG/质量及运维门槛。整体目标未完成，公网冻结，不能因当前本地全绿宣布产品完成。
- 本批源码提交 **9a1da9994567a888e91d465f31029b4111474871** 已形成，John Wang/heee000 noreply；880/53、44/44、40/40 对应此实现，本条追加仅文档。Git 同步只普通 push 现有分支，接手按远端 HEAD/实际回执核验，不能据此假定 CI 或部署已运行。实现提交后仅受保护知识文件未跟踪，全部业务候选已有版本记录。
