# 自有 Ubuntu 私人测试基座

本目录目前只提供 PostgreSQL/pgvector 与 MinIO **基础服务**，不是已完成的 Web/API/Worker 部署。面向 Ubuntu 24.04、linux/amd64、低并发测试；公网部署保持暂停。

## 初始化

1. 由操作者在 Ubuntu 执行已审阅的 `install-docker.sh`，明确接受 docker 组的 root 等价权限，再新建 SSH 会话。
2. 在新的、当前用户持有且权限为 700 的目录中放入本目录配置。确认目标 `contentflow-private-test` 项目/数据卷没有既有业务；不要将其与根开发 Compose 混用。
3. 执行 `bash init-infra-secrets.sh`。脚本独占创建权限 600 的 `.env`，拒绝覆盖已有文件；数据库密码、MinIO 管理密码和应用密码相互独立。不要打印 `.env` 或完整的 `docker compose config`。
4. 从官方仓库获取 Compose 中的固定摘要镜像，执行：

```sh
docker compose --env-file .env -f compose.infra.yml --profile setup config --quiet
docker compose --env-file .env -f compose.infra.yml up -d --wait --wait-timeout 120 postgres minio
docker compose --env-file .env -f compose.infra.yml --profile setup run --rm minio-init
```

初始化程序将业务 bucket 设为非匿名，并生成仅限该 bucket 的应用权限；验证应用可列出业务 bucket、不能调用服务器管理 API。不能把根管理凭据交给后续 API/Worker。

## 网络不通时的离线镜像

只从有网络的机器拉取相同官方摘要，再使用 `docker save`、压缩和 SSH 传输。传输前后核对整个文件的 SHA-256，加载后核对镜像 config ID 和 linux/amd64 架构；不要使用不明代理镜像或关闭 TLS/SSH 验证。

Docker archive 不一定保留 RepoDigests。本版本的 `compose.offline-pg.yml` 固定了经源端核验的 pgvector config ID，且 `pull_policy: never`。核验通过后，在**每条**上述 Compose 命令中附加 `-f compose.offline-pg.yml`。镜像升级时必须同时重新验证该 ID，不能仅改标签。

## 资源与边界

- 无服务映射宿主端口；数据网络为 `internal: true`，数据库和对象存储不能直接访问公网。后续 API/Worker 要加入该网络和独立出口网络，前端入口使用受控 SSH 隧道/私有 HTTPS。
- PostgreSQL 内存上限 512 MiB，MinIO 384 MiB，初始化进程 128 MiB；日志轮转、PID 上限、持久卷和 `no-new-privileges` 均显式配置。上限不是容量签收，仍须实机测量。
- 当前 PostgreSQL 用户仍是数据库容器初始化管理员，**不是**已完成的运行时最小权限角色拆分。不得把内部隔离称为企业级数据库权限治理。
- 重启/备份/恢复验证未通过前，不放入唯一副本的业务数据。不要执行 `down -v`、清卷或覆盖 `.env` 来解决问题。
- 用户已选择 Embedding API。后端可使用 `--build-arg INCLUDE_LOCAL_EMBEDDINGS=false` 构建，去掉本地 PyTorch/模型运行库；默认仍保留本地模型支持，不改变既有部署方式。API 模式不能调用本地 BGE，且真实服务必须返回 1024 维。
- 对原生固定维度、不接受可选 `dimensions` 请求字段的服务，设置 `CONTENTFLOW_EMBEDDING_SEND_DIMENSIONS=false`；这只省略请求字段，不会放宽返回向量长度校验。默认 true 保持既有服务行为，不能根据一次失败静默重试或自动改模式。
- API/Worker 的生产安全、治理、注册控制与 Cookie 保护不能为“跑起来”关闭。现有 API 密钥已获准按需安全迁移，但旧数据库、知识库和素材未获本轮迁移选择，不自动复制。

实时进度与尚未完成的验收见 `docs/ubuntu_private_test_setup.md`。
