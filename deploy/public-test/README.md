# ContentFlow 受控公网测试部署

本目录把 `docs/public_test_deployment_plan.md` 的 M1/M2 落成可执行资产。目标是单台固定 IPv4 Linux 主机上的个人、非商业、受控测试，不是高可用企业生产。

## 已固化的边界

- 只有 Caddy 发布 80/443；PostgreSQL、API、Worker、Web、指标和维护工具都不发布主机端口。
- API 与 Worker 使用同一后端镜像 digest；所有运行镜像必须写成 `name@sha256:...`。
- Caddy 同源代理 Web、`/api/*` 和健康检查，自动申请 TLS；`/metrics` 不对公网代理。
- 注册始终关闭。首个工作区和两位管理员由交互式离线 CLI 创建，密码不进入命令行、环境文件或日志。
- 文本模型使用供应商中立的 OpenAI-compatible HTTPS 端点；Embedding 默认使用固定提交的本地 BGE-M3，也支持独立 Embedding Base/Key。
- 业务对象与加密数据库备份使用两个 R2 Bucket 和两组互不越权的 Token。公网栈不运行 MinIO。
- PostgreSQL 备份先进入临时卷，再由 restic 客户端加密后上传；服务器和 R2 都不保存明文 dump 的长期副本。

## 外部资源准备

部署者需要先准备：

1. Ubuntu LTS x86 主机、固定公网 IPv4、非 root 部署用户，以及仅开放 22（限制来源）、80、443 的防火墙。
2. 一个域名，DNS A 记录指向固定 IPv4。首次签发证书前保持 DNS-only。
3. 两个 Cloudflare R2 Bucket：业务对象、PostgreSQL 备份；各自创建只能访问对应 Bucket 的 Token。
4. GitHub 仓库的 `public-test-build` 和 `public-test` Environment。`public-test` 应配置人工批准。
5. 真实文本模型的兼容 Base URL、API Key 和模型名。不要上传本机 `.env` 或平台账密文档。

服务器建议路径为 `/opt/contentflow`，共享密钥文件为 `/opt/contentflow/shared/.env`，权限必须为 `600`。从 `env.example` 复制后逐项替换；用 `openssl rand -hex 32` 分别生成 PostgreSQL、签名、凭据加密、指标和 restic 密码，禁止复用。镜像坐标由每次成功部署写入当前 release 的 `release.env`，维护脚本会同时读取共享密钥与该只读 release 坐标，不改写共享 `.env`。

若 GHCR Package 不是公开可读，先由部署用户执行一次 `docker login ghcr.io`，只使用具备 `read:packages` 的细粒度 Token。

## 仓库工作流

1. 对准备部署的完整 SHA 手工运行 `ContentFlow CI`，等待四个 Job 全部成功。
2. 运行 `Build public-test images`，输入同一 SHA 和 `https://你的域名`。工作流会构建 amd64 后端/Web 镜像、推送 GHCR、生成 BuildKit provenance/SBOM、用 Trivy 阻断存在可修复 Critical 漏洞的制品，并上传 `release.env` 与扫描报告。
3. 记录成功构建的 Actions run ID。
4. 在服务器创建 `/opt/contentflow/shared/.env` 并完成 R2/模型配置。
5. 运行 `Deploy public test`，只输入 SHA 和上一步 build run ID。部署工作流从该成功构建的 Artifact 读取 digest，不接受手工拼接的镜像坐标；受保护环境批准后才经严格 SSH host key 校验部署。

目标服务器的 GitHub Secrets：

- `PUBLIC_TEST_SSH_HOST`
- `PUBLIC_TEST_SSH_USER`
- `PUBLIC_TEST_SSH_PRIVATE_KEY`
- `PUBLIC_TEST_SSH_KNOWN_HOSTS`：提前在可信通道核对的完整 known_hosts 行，不能在工作流里临时 `ssh-keyscan` 后盲信
- `PUBLIC_TEST_DEPLOY_PATH`：建议 `/opt/contentflow`

应用密钥、模型 Key、R2 Token 和平台凭据不保存到 GitHub Secrets；它们只在目标主机的 `shared/.env` 或后续 Secret Manager 中维护。

## 首次初始化顺序

部署脚本会校验至少 8 GiB 空闲空间、Compose 安全配置、不可变镜像、BGE offline 缓存、数据库迁移、API readiness 和 Worker 心跳。首次没有 BGE 缓存时会下载固定 revision，并立即用 offline 模式复验。

部署成功后，在服务器终端交互创建两位管理员；`getpass` 读取的密码不会回显：

```sh
cd /opt/contentflow/current/deploy/public-test
docker compose --env-file /opt/contentflow/shared/.env -f compose.yml run --rm --no-deps -it api \
  contentflow-bootstrap-admin bootstrap-workspace \
  --email owner@example.com --display-name Owner --workspace-name "ContentFlow Public Test"

# 使用上一条输出的 workspace slug；第二个账号用于 Prompt/Eval 双人治理。
docker compose --env-file /opt/contentflow/shared/.env -f compose.yml run --rm --no-deps -it api \
  contentflow-bootstrap-admin add-admin \
  --workspace-slug contentflow-public-test-xxxxxxxx \
  --email reviewer@example.com --display-name Reviewer
```

第一条命令只允许空数据库；第二条拒绝复用已有邮箱。两条命令都要求 `CONTENTFLOW_ALLOW_REGISTRATION=false` 并写入系统审计。创建完成后从公网登录，建立 Eval 套件、双人激活、Prompt 评测/审批/激活，直到管理页显示“可生成”。

## R2 真实兼容性签收

在上传任何正式业务对象前运行完整矩阵。探针只创建一个随机 `contentflow-conformance-*` 前缀，覆盖 HeadBucket、256 KiB 单段、9 MiB 分段、100 MiB 上限、SHA-256 Metadata、读取和精确删除；不会列出或清空 Bucket：

```sh
docker compose --env-file /opt/contentflow/shared/.env -f compose.yml run --rm --no-deps api \
  contentflow-s3-conformance
```

对备份 Bucket 复验时，临时把备份 Bucket/Token 映射为 `CONTENTFLOW_S3_*` 运行同一命令。两组完整结果都成功前，R2 只能记为“待验收”。

## 加密备份与隔离恢复

首次只执行一次：

```sh
./init-backup.sh /opt/contentflow/shared/.env
```

每日定时执行：

```sh
./backup.sh /opt/contentflow/shared/.env
```

脚本生成 PostgreSQL custom dump、用 `pg_restore --list` 本地验真、restic 加密上传、抽样读取校验，并保留 7 个日备份和 4 个周备份。每月至少执行一次：

```sh
./verify-backup.sh /opt/contentflow/shared/.env
```

恢复验证只创建 `contentflow_verify_*` 随机临时数据库，核对表数和 Alembic revision 后删除；不会对当前 `contentflow` 执行 `--clean`。完整边界见 `backup-policy.md`。

## 上线后验收

1. `https://域名/health/ready` 返回 database/storage `ok` 和当前 release SHA。
2. 注册接口保持 403；两位管理员可登录、刷新和退出。
3. 上传小型知识文件，Worker 完成 BGE 索引；重启 Worker 后检索仍有效。
4. 新活动完成 Agent 生成、人工审核、封面人工上传/图库选择，保持微信 `auto_publish=false`。
5. 从 Worker 容器查询出口 IPv4，加入微信公众号白名单；家庭网络和手机热点访问时该 Worker 出口不变。
6. 先完成公众号连接测试，再创建一份“不公开发布”的草稿并保存外部 ID/证据。
7. 记录 SHA、后端/Web digest、域名、固定出口 IP、R2 探针、备份恢复、微信草稿和异常重试结果。

部署失败时工作流不会更新 `/opt/contentflow/current`。数据库迁移不被伪装成自动可逆；需要回退时先检查迁移兼容性，再显式使用上一 release 目录和 digest。
