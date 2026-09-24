# 数据库运行准入与受控升级契约

适用：A41 schema 门禁、A53 升级/管理员职责、A54 Embedding 部署模式。当前唯一迁移 head 为 `f0a1b2c3d4e5`；本批没有新增迁移，也没有执行实机迁移。

## 运行不承担生产迁移

- `contentflow-migrate` 是显式维护命令。生产 API、Worker、管理员 CLI 不调用迁移、create_all、adopt 或 stamp，不尝试自动修复不兼容库。
- 开发入口仍显式跑既有迁移再验证，方便创建隔离开发库；不再用 create_all 掩盖迁移遗漏。
- 容器默认 CMD 也只执行 uvicorn，不再包含启动前 Alembic；根 Compose 首次启动需遵循 README 的显式维护步骤，不能只 `up` 后期待运行进程顺手建表。
- API lifespan、生产 Worker 构造/每次领取前、管理员 CLI 提示密码前检查；后者必须先有迁移完成的 schema。“首次空库建管理员”指没有业务账户，不是允许 CLI 顺手建表。
- 生产 Worker 检查失败不领取下一项任务；真正的 schema 不兼容终止运行。数据库短暂不可用仍由已有有界退避识别，包装异常不会泄露连接/参数，也不丢失故障分类。
- Worker 构造失败释放自己创建的连接池，不处置调用者提供的共享池。

## 检查范围与健康响应

`database_schema.verify_database_schema` 使用独立短期只读 Session；不读取业务内容或写入。要求：

1. 有且只有一个 Alembic revision，等于当前 head；旧版、未来版、多 head、无版本拒绝。
2. 所有 ORM 必需表和列存在。
3. Job 领取令牌及存储来源身份列符合必要长度/可空形状；staging 身份约束存在。
4. PG 的 `knowledge_vectors.embedding` 是非空 `vector(1024)`；向量 SELECT/余弦操作可解析执行。无数据 SELECT 不加载用户向量。

它不是完整 schema 校验器：不证明所有索引/约束语义、业务数据完整性、全部 DML 权限、模型语义一致性或恢复能力。生产仍需要最小权限运行角色与专用迁移身份；本批不创建或变更目标数据库角色。

PG 检查事务局部设置 statement timeout 3 秒、lock timeout 1.5 秒，关闭时回滚，不污染业务连接。逐语句超时不是整个探针总 SLA，连接建立/池等待及真实负载仍需验收。每次生产领取前做新检查，不用无限期缓存绕过漂移检测。

`GET /health/live` 仅证明 API 进程响应。`GET /health/ready` 不缓存：

- 成功：200，`status=ready / database=ok / schema=ok / storage=ok`。
- 结构不兼容：503，`database=ok / schema=incompatible / storage=not_checked`。
- 数据库不可用：503，`database=unavailable / schema=unknown`。
- 存储不可用：503，`schema=ok / storage=unavailable`。

响应保留 release_sha；日志只写稳定原因/异常类型，启动日志只写数据库方言，不再输出 DSN 片段。旧部署代码只检测 HTTP 200 不够，应核对 schema 和期望 SHA。

## 公网测试脚本的维护顺序

公网部署仍冻结；以下是已实现资产，不是已在用户服务器运行。

1. 校验参数、共享环境文件权限/磁盘；用共享环境目录的 `.contentflow-deploy.lock` 非阻塞锁防止并行维护，CI 并发限制不是唯一保护。
2. 在内存中渲染并验证 Compose，不 source 密钥文件、不打印配置；只输出有效 Embedding 模式。API/Worker 模式及 release SHA 必须一致。
3. 拉取不可变镜像。仅本地 BGE 模式校验/按需准备固定缓存；API 模式不运行 embedding-bootstrap，也不要求 PyTorch/本地模型缓存。模式未知直接拒绝。
4. 停止旧 Worker/API（沿用 Compose 的优雅停止期限）；等待 PostgreSQL 健康，查询已有表。查询失败/结果非法拒绝迁移。旧任务终态/未知副作用仍需按既有人工核对契约处理，停止进程不能撤销已发送的外部请求。
5. 有既有表就备份，与应用原先是否在线无关；空白库跳过。已有库的备份仓库必须预先初始化；备份失败不继续迁移。
6. 显式运行候选镜像的 `contentflow-migrate`，成功后启动同版服务。
7. 在 API 容器内验证 readiness 全字段和候选 SHA；在候选 Worker 容器内验证匹配 hostname/SHA、近期 online 且非 stopped 的心跳，不能用其他容器/旧版本/未来异常心跳冒充。
8. 全部通过才写成功 release 坐标；工作流随后更新 current。候选启动/验证失败时再次停止业务写入者，不晋级，不自动复活可能与新库不兼容的旧代码。

迁移前失败时旧业务进程已停；候选启动后的失败清理是尽力停止，若 Docker 停止本身失败会明确要求人工介入，不能声称绝对没有进程运行。Web/Caddy 可保留用于错误页/诊断，但 API/Worker 不应继续处理业务。数据库、卷、对象和共享密钥不被清除。

宿主需要 Docker Compose、Python 3 标准库和 `flock`（Linux util-linux），不应为运行预检安装整个应用。发布包包含 `contentflow/schema_contract.py`，它是无第三方依赖的唯一 schema 坐标源；`migrate.py` 保留兼容导出，避免复制 head 常量而漂移。

## 升级、回退和验收边界

- 必须维护窗口，API/Worker/Web/schema 同版更新；这不是零停机升级协议。停止其他主机上连接同一库的写入者是部署者责任，单机脚本不能隔离未知远程进程。
- 不自动 downgrade。失败后先核对实际 Alembic head、备份与候选日志，决定使用兼容旧版本还是恢复到经过验证的备份；不得用 create_all、删库或重置卷“修复”。数据库 major 升级另走专门方案。
- Worker 心跳 SHA 是受控镜像内的运行证据，不是二进制来源签名；仍需镜像 digest、构建来源、同 SHA CI 与部署权限保护。
- 新部署 shell 测试执行脚本副本，所有 Docker/备份工具为空操作；验证顺序、故障停止、不晋级、模式分流和旧成功记录保留，不能证明 Docker 或真实备份已经通过。
- 本批真实 PG/schema、MinIO/进程中止、迁移恢复、生产最小权限与服务容量仍待签收；局部 SQLite 绿色不能关闭整个企业交付门槛。

本地与阶段证据见 [实施进度](IMPLEMENTATION_PROGRESS.md) 末节；历史失败保留在 [本批修复前审计](project_audit_20260924_schema_candidate_review.md)。
