# 生产部署与运维

## 配置检查

生产环境至少确认：

- `CONTENTFLOW_ENVIRONMENT=production`
- 强随机 `CONTENTFLOW_SECRET_KEY`
- PostgreSQL 数据库地址
- S3/MinIO Endpoint、Bucket 和凭据
- 明确的 `CONTENTFLOW_CORS_ORIGINS`
- Web 映射端口与跨域来源一致；例如 `CONTENTFLOW_WEB_PORT=3300` 时，CORS 列表需包含 `http://localhost:3300`
- 选择的文本、Embedding、图片、视频 Provider 及其密钥

仓库不会在生产环境自动 `create_all`。API 容器启动前执行 `alembic upgrade head`；迁移失败时服务不会启动。

## Docker Compose

```powershell
Copy-Item .env.example .env
# 编辑 .env，替换所有 replace-me
docker compose config
docker compose up --build -d
docker compose ps
```

验收：

```powershell
Invoke-RestMethod http://localhost:8000/health/live
Invoke-RestMethod http://localhost:8000/health/ready
$contentFlowWebPort = if ($env:CONTENTFLOW_WEB_PORT) { $env:CONTENTFLOW_WEB_PORT } else { "3000" }
Invoke-WebRequest "http://localhost:$contentFlowWebPort"
docker compose logs api worker --tail 200
```

## 水平扩展

API 无本地会话状态，可增加副本。Worker 通过 PostgreSQL `SKIP LOCKED` 并发领取任务，也可横向扩展。使用多副本时：

- 所有实例使用同一数据库、对象存储和 `CONTENTFLOW_SECRET_KEY`
- 不要把 `local` 存储作为多副本共享存储
- 根据外部模型和平台限流设置 Worker 数量
- 数据库时间应统一为 UTC

## 数据库迁移

```powershell
python -m alembic current
python -m alembic upgrade head
python -m alembic history
```

初始 PostgreSQL 迁移会安装 `vector` 扩展并创建 1024 维 HNSW 索引。托管数据库需要允许 `CREATE EXTENSION vector`，否则由管理员预先安装。

## 备份

- PostgreSQL：每日逻辑备份并定期做恢复演练
- MinIO/S3：启用版本控制或对象生命周期策略
- 备份应用密钥：丢失后已加密平台凭据无法恢复
- 审计日志按合规周期归档，不要和普通应用日志一起随意清理

## 监控建议

当前健康检查覆盖进程和数据库连通性。生产环境应采集：

- HTTP 状态、P95/P99 延迟、请求 ID
- Job queued/running/retry/failed 数量与最长排队时间
- 外部模型耗时、错误率和费用
- 各平台限流、授权过期与发布失败率
- 对象存储容量和下载错误
- PostgreSQL 连接池、慢查询和向量查询耗时

## 故障处理

- 内容未生成：查看 `workflow.execute` Job 和关联 `WorkflowRun.error`
- 文档未索引：确认对象可读、编码与 `knowledge.index` 错误
- 素材长期 processing：查看 `asset.poll` 重试次数和外部 task ID
- 发布失败：确认内容版本、素材状态、渠道 scope 与外部响应
- 渠道 invalid：重新授权或更新凭据后执行连接测试
- Job 最终 failed：修复根因后在任务队列点击“重试”
