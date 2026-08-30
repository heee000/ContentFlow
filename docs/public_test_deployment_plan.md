# ContentFlow 公网测试部署实现计划

> 记录日期：2026-08-31  
> 目标性质：个人、非商业、受控公网测试  
> 当前状态：规划已确认，基础设施和部署代码尚未实施  
> 适用分支：`codex/enterprise-media-runtime`

## 1. 目标与非目标

本阶段把 ContentFlow 从“本机真实链路可用”推进到“可以通过公网 HTTPS 地址进行受控测试”，并解决本地换网后微信公众号出口 IP 变化的问题。

本阶段必须达到：

1. 使用固定公网 IPv4 运行发布 Worker，微信公众号白名单只配置一次。
2. 通过 HTTPS 域名访问 Web，API、Worker、PostgreSQL、pgvector 和对象存储持续运行。
3. 关闭公开注册，仅允许已建立的测试账户使用。
4. 保留真实 DeepSeek/OpenAI-compatible 文本、BGE-M3、人工上传/开放图库和微信公众号草稿链路。
5. 代码通过 GitHub 门禁后形成可重复部署制品，部署、迁移、备份、验收和回滚都有命令及记录。
6. 从不同本地网络访问时，微信公众号看到的仍是云端 Worker 的固定出口 IP。

本阶段不做：

- 不开放匿名注册，不招募不受控外部用户，不做商业收费。
- 不承诺中国大陆访问质量，不做 ICP 备案、国内 CDN 或国内短信/实名能力。
- 不把单机测试环境描述成高可用生产环境、公开 Beta 或企业 SaaS。
- 不在本阶段补齐计费、企业 SSO/SCIM、数据库 RLS、跨可用区 HA、异地灾备和商业合规。
- 不自动开启微信公众号公开发布；初始仍保持 `auto_publish=false`，先验收素材和草稿。

## 2. 平台选择结论

| 平台 | 本阶段用途 | 结论 |
|---|---|---|
| GitHub 仓库 | 源码、Pull Request、CI、发布记录 | 使用 |
| GitHub Actions | 测试、构建、审计、生成容器制品、受控部署 | 使用；不是长期运行服务器 |
| GitHub Container Registry | 保存 API/Worker/Web OCI 镜像并按 digest 拉取 | 计划使用 |
| GitHub Pages | 项目说明或静态文档 | 可选；不能运行 FastAPI、Worker、PostgreSQL 或 MinIO |
| Vercel | Next.js 前端 | 可选第二阶段；不承载长期 Worker 和数据库 |
| 固定 IP 云主机 | Caddy、API、Worker、PostgreSQL、MinIO，初期也承载 Web | 本阶段推荐主方案 |
| 托管 PostgreSQL/S3 | 降低单机数据风险 | 公网测试稳定后再拆分，不是首次上线前置条件 |

Vercel Function 具有执行时长、内存、请求体和临时文件系统限制，默认出口还不是固定 IP。当前 ContentFlow 有常驻数据库队列 Worker、BGE-M3 模型缓存、最长 100 MiB 上传和外部平台发布副作用，因此不能把现有后端直接迁成 Vercel Functions 而仍声称语义等价。

Vercel 的正确定位是后续托管 `web/`。如果启用，必须给 Web 与 API 配置同一注册域下的 HTTPS 子域，例如 `app.example.com` 和 `api.example.com`；不能直接使用 `*.vercel.app` 搭配另一个站点的 API，因为当前认证 Cookie 为 `HttpOnly + Secure + SameSite=Lax`，跨站 Fetch 会破坏会话语义。

## 3. 推荐目标拓扑

首次公网测试采用一个域名、一个固定公网 IP、一个云主机：

```text
用户浏览器
    |
    | HTTPS https://contentflow.example.com
    v
Caddy（唯一公网入口，80/443）
    |-- /api/v1/* --> FastAPI :8000
    |-- /health/* --> FastAPI :8000
    `-- 其他路径 ----> Next.js :3000

Docker 内部网络
    |-- API ---------> PostgreSQL/pgvector
    |             `--> MinIO
    |-- Worker ------> PostgreSQL/pgvector
    |             |--> MinIO
    |             |--> 文本/媒体/图库 Provider
    |             `--> 微信公众号 API
    `-- Web

云主机固定公网 IPv4
    `--> 加入微信公众号 IP 白名单
```

关键约束：

- 公网安全组只开放 `80/443`；`22` 只允许受控管理来源或使用云厂商会话管理。不得公开 `5432/9000/9001/8000/3000/9090/3301`。
- PostgreSQL、MinIO、API、Worker 和 Web 只在 Docker 内部网络互通。
- Caddy 是唯一可信反向代理时，设置 `CONTENTFLOW_TRUSTED_PROXY_HOPS=1`；若以后接入 Cloudflare 代理，必须重新验证转发头清洗和跳数，不能直接照搬。
- 使用同源 API：`NEXT_PUBLIC_CONTENTFLOW_API_BASE=https://contentflow.example.com/api/v1`，`CONTENTFLOW_CORS_ORIGINS=["https://contentflow.example.com"]`，Cookie Domain 保持空值以使用 host-only Cookie。
- 微信白名单填写从 Worker 容器内测得的固定公网 IPv4，不以浏览器查询结果代替。

## 4. 资源与环境选择

### 4.1 推荐资源档位

继续在 Worker 内运行本地 BGE-M3 时，初始建议使用：

- Linux x86_64 云主机；
- 4 vCPU；
- 8 GiB RAM；
- 80 GiB 以上 SSD；
- 固定公网 IPv4；
- 与主要模型 API、Openverse/Wikimedia 和微信 API 网络连通的境外区域。

这是测试起点，不是容量承诺。上线后必须记录 BGE 冷启动内存、模型缓存大小、生成峰值内存、磁盘增长和 Worker 队列等待，再决定是否降配或扩容。若改用真实 OpenAI-compatible Embedding 服务，可以评估 2 vCPU/4 GiB，但必须重新索引知识库并重新验收质量和成本。

### 4.2 云平台选择原则

平台保持中立，只要求：

1. 固定公网 IPv4 在实例重启后不变化。
2. 支持安全组、防火墙、快照或磁盘备份。
3. 能运行 Docker Compose 和持久数据卷。
4. 能保存或安全注入运行密钥。
5. 能从 GitHub Container Registry 拉取按 digest 固定的镜像。

可从 AWS Lightsail/EC2、DigitalOcean、Hetzner、Vultr 或同类海外云主机中选择。首次实现不绑定某一家云厂商；供应商专有 Terraform 可在选定平台后补充。

### 4.3 公网测试最低运行配置

目标环境至少显式设置并通过现有 `Settings.validate_runtime()`：

- `CONTENTFLOW_ENVIRONMENT=production`
- PostgreSQL/pgvector 内网连接地址，禁止 SQLite
- `CONTENTFLOW_STORAGE_BACKEND=s3` 和 MinIO/S3 内网地址、Bucket、凭据
- 三个互不复用且至少 32 位的应用签名、凭据加密和指标 Token
- `CONTENTFLOW_ALLOW_MOCK_PROVIDERS=false`
- `CONTENTFLOW_REQUIRE_GOVERNED_PROMPTS=true`
- `CONTENTFLOW_METRICS_ENABLED=true`
- `CONTENTFLOW_ALLOW_REGISTRATION=false`（初始化受控窗口除外）
- `CONTENTFLOW_PUBLIC_BASE_URL=https://contentflow.example.com`
- `CONTENTFLOW_CORS_ORIGINS=["https://contentflow.example.com"]`
- `CONTENTFLOW_AUTH_COOKIE_DOMAIN=` 和 `CONTENTFLOW_TRUSTED_PROXY_HOPS=1`
- 真实文本 Provider、本地 BGE-M3、`manual` 媒体和 Openverse 精确下载域名

真实值保存在目标主机的受限密钥文件或云密钥服务中。Compose 渲染、CI 日志、部署日志、备份 manifest 和错误报告都不能输出这些值。

## 5. 完整实施阶段

### M0：计划与边界固化

状态：本文件完成即达到。

交付物：

- 公网测试目标、非目标、拓扑和验收标准；
- Vercel、GitHub Pages、GitHub Actions 和固定 IP 云主机的职责边界；
- 当前工作区私有文件、`.env`、账号资料和运行数据继续排除在 Git 外。

完成判据：计划进入 README、生产门禁、工程台账和交接文档；不把规划误写为已上线证据。

### M1：建立公网测试部署资产

计划新增：

```text
deploy/public-test/
├─ compose.yml
├─ Caddyfile
├─ env.example
├─ README.md
└─ backup-policy.md

.github/workflows/
├─ build-images.yml
└─ deploy-public-test.yml
```

实现要求：

1. `compose.yml` 使用已构建镜像而不是在服务器临时编译，API 与 Worker 共用同一后端镜像 digest。
2. 生产覆盖不映射数据库和对象存储端口；只让 Caddy 暴露 80/443。
3. Web 镜像以真实 HTTPS API Base 构建，避免当前本地 Compose 的 localhost 默认值进入公网制品。
4. 数据卷使用明确名称和挂载点；容器配置日志轮转、健康检查、重启策略和合理资源限制。
5. Caddy 自动申请 TLS，添加必要安全头；Grafana、Prometheus 和 MinIO Console 默认不暴露公网。
6. `env.example` 只保存变量名和生成说明，不保存真实 API Key、微信公众号凭据或可用密钥。
7. 在生产 Compose 上增加配置静态检查，验证没有敏感端口、弱默认密钥、Mock/Hash Provider、通配 CORS 或 HTTP 外部 Provider。

完成判据：在临时 Linux 主机上可以从空目录使用文档启动全部服务，`/health/ready` 返回数据库和存储 `ok`，Worker 心跳在线。

### M2：建立镜像和受控部署流水线

实现要求：

1. 现有 CI 继续作为部署前置门禁。
2. CI 通过后构建后端和 Web OCI 镜像，推送到 GHCR；标签同时包含 Git SHA，部署文件记录不可变 digest。
3. 对镜像做漏洞扫描并生成容器 SBOM；逐步补充签名和部署时验签，不能继续只用源码归档证明替代镜像证明。
4. 公网测试部署只允许 `workflow_dispatch` 或受保护 Environment 手动批准，不对每次功能分支 push 自动上线。
5. 部署顺序为：远端备份与空间检查 → 拉取新 digest → 数据库迁移 → 启动 API/Worker/Web → readiness/Worker/版本 smoke → 成功后标记版本。
6. 任何一步失败都停止晋级；数据库迁移不可逆时必须提前声明，不能伪装成可一键回滚。

完成判据：同一提交可以重复部署；运行环境能显示对应 Git SHA/镜像 digest；失败部署不会清空数据库、对象或覆盖密钥。

### M3：创建固定 IP 云环境与 HTTPS 入口

需要部署者提供：

- 一个可以创建境外云主机和固定公网 IPv4 的云账号；
- 一个自有域名及 DNS 修改权限，或明确接受仅用于首轮联调的临时域名；
- 是否继续在云端运行 BGE-M3，以确定 8 GiB 或更低资源档位。

实施步骤：

1. 创建 Ubuntu LTS 主机和固定公网 IPv4，记录实例、磁盘、区域和 IP。
2. 设置安全组与主机防火墙；创建非 root 部署账户，禁用 SSH 密码登录，限制 sudo。
3. 安装固定主版本的 Docker Engine/Compose，设置磁盘和 Docker 日志轮转。
4. DNS A 记录指向固定 IP；先使用 DNS-only 路径，减少代理跳数变量。
5. 生成互不相同的应用签名、凭据加密、指标和数据库/对象存储密钥；运行文件权限限制为部署账户可读。
6. 启动 Caddy 与 ContentFlow，验证 TLS、HSTS/CSP、同源 Cookie、CORS、上传大小和反向代理客户端 IP。
7. 从 Worker 容器查询公网 IPv4，将它加入微信公众号白名单并执行渠道连接测试。

完成判据：在家庭网络、手机热点等两个不同客户端网络访问同一 HTTPS 地址时登录和生成正常；两次从 Worker 查询的出口 IP 相同；微信公众号渠道保持 `connected`。

### M4：初始化测试数据与真实业务验收

默认选择“新建干净公网测试环境”，不直接复制完整本地数据库。这样可以避免把历史失败任务、测试账号、对象和本机审计数据无差别搬到公网。

初始化顺序：

1. 在公网路由尚未开放时，通过 SSH 隧道或仅管理员来源可达的初始化窗口创建两个管理账户并建立 reviewer/editor 职责；随后立刻设置 `CONTENTFLOW_ALLOW_REGISTRATION=false`，重启并确认注册接口拒绝新用户后才开放 Web。
2. 配置真实文本 Provider、本地 BGE-M3、`manual` 图片/视频、Openverse 搜索和治理 Prompt；不得开启 Mock。
3. 通过 Web 重新录入微信公众号 AppID/AppSecret，让凭据以公网环境的新加密密钥保存，不把本机 `.env` 或账号文本文档上传到服务器。
4. 建立小型测试知识库、Style Skill、单平台公众号活动和人工封面。
5. 先跑渠道连接和草稿链路，保持 `auto_publish=false`；只有单独获得本轮发布授权后才测试公开发布。

若必须迁移本地环境，则必须把 PostgreSQL dump、MinIO 对象和同一凭据解密密钥作为一个原子迁移单元，先在隔离实例恢复验真，再切换域名。不得只复制数据库而丢失对象或密钥。

核心验收用例：

1. 新用户注册已关闭；现有账户登录、刷新、退出和限流正常。
2. 上传知识文件后 Worker 完成 BGE 索引，重启 Worker 后数据仍在。
3. 创建活动，真实内容 Agent 完成生成并显示阶段进度；内容进入人工审核而不是自动发布。
4. 人工上传、开放图库和未配置 AI 图片三个入口分别表现正确；未配置能力失败关闭。
5. 审核后创建微信公众号立即任务，先成功创建草稿并保存外部 ID/响应证据。
6. Worker 在调用前失败时允许安全重试；写入开始后的不确定结果进入对账，不重复提交。
7. 更换访问端网络后再次创建草稿，Worker 出口 IP 不变，微信白名单无需修改。
8. 重启 API/Worker、模拟一个 Worker 租约中断，任务可按现有租约与幂等规则恢复。

完成判据：一条新的公网测试活动完整走过知识 → Agent 生成 → 审核 → 素材 → 微信草稿；数据库、对象、审计和任务状态一致，并记录测试时间、提交 SHA、镜像 digest、出口 IP 和微信结果。

### M5：备份、监控与回滚演练

最低测试运维要求：

1. 每日 PostgreSQL 与 MinIO 联合备份到与实例磁盘不同的位置，保留最近 7 个日备份和 4 个周备份。
2. 每次部署和迁移前创建备份；每月至少一次恢复到随机临时数据库/bucket 验真。
3. 监控 HTTPS 可用性、API readiness、Worker 心跳、队列最长等待、发布待对账、磁盘、内存和证书过期。
4. 容器日志设置大小和文件数量上限；应用日志、部署日志和备份报告不得包含密钥、Token、正文或平台凭据。
5. 编写并真实执行一次应用版本回滚：回到上一镜像 digest，数据库保持兼容，Web/API/Worker 均通过 smoke。
6. 编写主机丢失恢复步骤：新主机 + 固定 IP/DNS + 密钥 + 数据库/对象联合恢复；未演练前不宣称灾备完成。

完成判据：备份恢复、版本回滚和告警至少各有一次带时间戳的成功证据；单机故障仍可能造成停机，这一风险在测试说明中保持显式。

### M6：可选拆分 Vercel 前端

只有 M3-M5 稳定后再评估，目的主要是获得前端 Preview 和简化 Web 发布，不是解决后端可靠性。

步骤：

1. Vercel 导入同一 GitHub 仓库，Root Directory 设置为 `web`。
2. 为生产构建设置 `NEXT_PUBLIC_CONTENTFLOW_API_BASE=https://api.example.com/api/v1`。
3. 使用自有域名 `app.example.com`；API 使用同一注册域的 `api.example.com`，CORS 只允许精确 Web Origin。
4. 重新验收 `HttpOnly + Secure + SameSite=Lax` Cookie、刷新会话、CSP `connect-src`、上传和下载。
5. API、Worker、PostgreSQL、对象存储和微信发布继续位于固定 IP 云环境；不迁入 Vercel Functions。
6. Vercel Preview 默认不得连接真实生产 API/数据库；使用隔离测试 API 或部署保护。

如果不需要 Preview 和全球前端 CDN，就继续同机部署 Web。对当前个人测试目标，同机方案更简单、同源认证更稳、故障点更少。

## 6. GitHub 在本方案中的完整职责

GitHub 用来“管理和交付”，不直接“运行产品”：

1. 仓库保存源码、迁移、部署模板和运维文档。
2. Actions 执行现有 PostgreSQL/MinIO 测试、安全审计、前端构建和供应链门禁。
3. 新流水线生成后端/Web 镜像并推送 GHCR。
4. GitHub Environment 保存最小部署凭据并提供人工批准；应用运行密钥默认保留在目标主机或云密钥服务，不批量复制进 GitHub。
5. Release 记录提交、镜像 digest、迁移版本、变更说明和回滚目标。
6. GitHub Pages 如启用，只发布项目介绍或文档，不接触应用 Cookie、API Key、数据库或社媒凭据。

## 7. 风险与接受方式

| 风险 | 测试阶段处理 | 何时必须升级 |
|---|---|---|
| 单机故障导致整体停机 | 接受；远端联合备份和恢复演练 | 公开 Beta 前拆分数据库/对象存储并设计 HA |
| PostgreSQL/MinIO 与应用争用资源 | 资源限制、磁盘/内存监控 | 持续负载或外部用户增加前拆分 |
| BGE-M3 冷启动和内存占用 | 8 GiB 起步、持久模型缓存、实测后调优 | 出现 OOM 或队列 SLO 不达标时 |
| 云主机密钥文件 | root/部署用户最小权限、禁止进 Git、轮换记录 | 公开 Beta 前接入 Secret Manager/KMS |
| 固定 IP 云主机被扫描 | 只开放 80/443、注册关闭、强密码、限流、补丁 | 公开 Beta 前增加 WAF/托管防护和安全扫描 |
| 第三方平台副作用重复 | 继续使用现有幂等、对账和人工接管 | 每个平台真实异常矩阵未通过前不扩大使用 |
| Vercel 跨站 Cookie/动态出口 | 首阶段不拆前端；拆分时使用同一注册域并重验 | 启用 Vercel 前必须关闭 |

## 8. 开始实施前需要用户提供或选择的内容

只有进入 M3 实际创建外部资源时才需要暂停确认：

1. 选择一个海外云平台并提供可用账号，或由用户先创建主机后提供受限部署入口。
2. 提供/选择一个域名及 DNS 控制方式；没有域名时可先用临时域名联调，但不作为最终验收地址。
3. 确认云端继续本地 BGE-M3，还是改用外部 Embedding API。
4. 确认可接受的月度测试资源预算。

除此之外，部署模板、CI、镜像、配置校验、测试脚本和文档均可先在仓库内完成，不应因尚未选云厂商而停滞。

## 9. 当前完成度口径

本计划被记录不代表公网部署已经完成。当前仍维持：个人本地部署约 85%-90%，个人公开部署约 60%-65%。

只有 M1-M5 全部取得目标环境证据后，才能把状态更新为“个人受控公网测试可用”；这仍不等于公开 Beta、商业上线或企业生产签收。

## 10. 参考资料

- [GitHub Pages 是静态站点托管服务](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages)
- [GitHub Container Registry](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)
- [Vercel 上的 Next.js](https://vercel.com/docs/frameworks/full-stack/nextjs)
- [Vercel Functions 限制](https://vercel.com/docs/functions/limitations)
- [Vercel 固定出口 IP 说明](https://vercel.com/kb/guide/can-i-get-a-fixed-ip-address)
- [AWS Lightsail 固定 IP 示例](https://docs.aws.amazon.com/lightsail/latest/userguide/lightsail-create-static-ip.html)
