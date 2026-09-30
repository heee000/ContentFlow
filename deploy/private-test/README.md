# 自有 Ubuntu 私人测试基座

本目录提供 PostgreSQL/pgvector、MinIO 和可选的 API/单 Worker/Web/Caddy 私人测试栈。面向 Ubuntu 24.04、linux/amd64、低并发测试；公网部署保持暂停。配置存在不代表当前机器已经通过部署验收，实测进度见文末记录。

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

Docker archive 不一定保留 RepoDigests。Docker 27 导出、Docker 29 加载还可能移除旧格式空字段而改变 config ID。本版本的 `compose.offline-pg.yml` 固定目标 Docker 29 经独立核验的 ID，且 `pull_policy: never`。只有源/目标全部 RootFS 层及非空运行配置匹配后才能接受这种变化，不能忽略任意 ID 不一致。核验通过后，在**每条**上述 Compose 命令中附加 `-f compose.offline-pg.yml`。镜像升级时必须重新验证，不能仅改标签。

## 应用配置与启动

1. 在开发机或 CI 构建后端（`INCLUDE_LOCAL_EMBEDDINGS=false`）和 Web；Web 构建参数 `NEXT_PUBLIC_CONTENTFLOW_API_BASE=http://localhost:3600/api/v1`。Caddy 使用本目录 `Dockerfile.caddy` 基于固定官方摘要构建：移除二进制不再需要的 `CAP_NET_BIND_SERVICE`，避免清空容器 capabilities 后启动报 EPERM。可用 `docker build --network none --build-arg CADDY_IMAGE=<已验真的本地ID> -t <本地标签> - < Dockerfile.caddy`，stdin 构建不发送含凭据的目录。所有镜像核验后再使用，不能通过增权绕过启动问题。
2. 仅在操作者明确授权复制 API 配置后，执行 `prepare-runtime.py export --container <已确认的源容器> --embedding-file <独立配置> --output <新的私有 providers.json>`。程序只导出白名单 Provider 参数，不复制旧数据库、用户账户、签名密钥或业务文件。将该文件经 SSH 传到私人目录。
3. 在 Ubuntu 执行 `python3 prepare-runtime.py prepare --infra-env .env --providers providers.json --output runtime.env`。独占创建 600 文件，生成独立应用签名、凭据加密和指标密钥；只复制 S3 应用权限，不复制 MinIO 管理权限。已有文件不会被覆盖。Compose 需支持 `env_file.format: raw`（实测 2.40.3），防止 Key 中 `$` 被插值。
4. 在该私人目录创建 `images.env`，包含 `CONTENTFLOW_RELEASE_SHA`（实际应用源码的完整 40 位 SHA）以及 `CONTENTFLOW_BACKEND_IMAGE`、`CONTENTFLOW_WEB_IMAGE`、`CONTENTFLOW_CADDY_IMAGE`（逐一验证的 `sha256:` image ID）。不要使用浮动标签。密钥不写入该文件。
5. 在 Bash 中设置本次 Compose 参数并逐步验证：

```sh
cf_compose=(docker compose --env-file .env --env-file images.env \
  -f compose.infra.yml -f compose.offline-pg.yml -f compose.app.yml)
"${cf_compose[@]}" config --quiet
"${cf_compose[@]}" run --rm --no-deps api python -c \
  'from contentflow.settings import Settings; Settings(_env_file=None).validate_runtime(); print("Runtime validation passed")'
# 已有实例升级须先确认任务/未知副作用、停止同库的全部写入者，完成并验证备份。
"${cf_compose[@]}" stop worker api
# 只有备份确认成功后，才执行下面的显式迁移；不能照抄跳过已有数据备份。
"${cf_compose[@]}" run --rm --no-deps api contentflow-migrate
"${cf_compose[@]}" up -d --wait --wait-timeout 180 api worker web caddy
```

迁移是显式维护步骤，生产 API/Worker/管理员 CLI 不自动迁移；错版/缺表启动拒绝，运行就绪必须包含 `schema=ok`。第一次管理员使用 `contentflow-bootstrap-admin bootstrap-workspace` 离线创建，只允许已迁移但无账户的数据库且注册关闭；先验证 schema 再提示密码，密码不作为命令参数。不要复制旧用户表或用开放注册绕过。内容生产仍需 Prompt 评测和独立审核激活，不因私人测试自动批准。升级失败不要重启不兼容旧 Worker，先按 [数据库契约](../../docs/database_schema_contract.md) 核对实际版本与备份；禁止 `down -v`。

## 私人访问入口

仅 Caddy 映射 Ubuntu `127.0.0.1:3800`，其余服务无宿主端口。Windows 使用严格主机验证的专用 SSH 密钥，将本机 `127.0.0.1:3600` 转发至 Ubuntu `127.0.0.1:3800`，随后访问 **http://localhost:3600/**。`127.0.0.1` URL、Ubuntu IP URL 和公网域名不是本配置的替代入口；Caddy 拒绝非 localhost Host。

局域网链路由 SSH 加密，HTTP 只出现在两端回环/容器私网。这不是公网 HTTP 部署。生产 Secure/HttpOnly Cookie 保持开启，使用独立 Cookie 名，避免与 Windows 既有 localhost 测试实例冲突。浏览器对 localhost 有 Secure Cookie 特例，仍需实测登录、刷新和授权请求；不支持时应配置私有 HTTPS，而不是关闭 Secure。[MDN Cookie 说明](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Set-Cookie)

SSH 隧道只负责访问页面，关闭 Windows 隧道不会停止 Ubuntu Worker。电脑重启、网络隔离、Ubuntu 地址变化可能要求重连；不是自动跨网络远程访问方案。不得把此隧道的私网地址当作微信公网白名单地址。

Windows 隧道命令不要加 `-6`：它不仅限制远端地址族，也会导致 `127.0.0.1` 本地监听无法解析。直接使用远端 IPv6 字面量即可，保留本地 IPv4 回环绑定。后台启动进程不等于隧道成功，必须实际请求 localhost:3600 的 readiness，再检查浏览器。

## 可选：Tailscale 私有 HTTPS

这是一层可回退覆盖配置，不是公网发布。Windows/手机必须安装并登录同一授权私网；只启动 Serve，不启动 Funnel、出口节点、子网路由或 Tailscale SSH。服务器仍只发布回环 Caddy，数据库/对象存储不开放宿主端口。

1. 从官方源安装客户端，Ubuntu 可使用 `sudo tailscale up --accept-dns=false --accept-routes=false --hostname=contentflow-test` 保留原 DNS/默认出口；按设备链接由操作者登录并确认加入正确账号。已有配置先检查，不使用 reset 或强制重新认证覆盖其他用途。
2. 从实际 `tailscale status --json` 的 Self.DNSName 取得设备完整域名（去掉末尾点）。在管理台确认 MagicDNS/HTTPS；申请公有 CA 证书会将设备域名永久写入 CT 日志，事先确认并避免使用含个人信息的设备名。
3. 使用相同 Web 源码构建新镜像，`NEXT_PUBLIC_CONTENTFLOW_API_BASE=https://<实际设备域名>/api/v1`。保留旧 localhost Web 镜像；传输后核对 archive 哈希、全部镜像层和运行配置，取得目标机不可变 image ID。
4. 在私人运行目录执行 `python3 prepare-tailnet.py --hostname <实际设备域名> --web-image sha256:<已验证目标ID> --output tailnet.env`。脚本拒绝 URL、通配符、端口、换行、非法 DNS 标签和浮动镜像，并拒绝覆盖已存在的文件。配置没有账号或密钥。
5. 每条 Compose 命令同时加 `--env-file tailnet.env` 和最后的 `-f compose.tailnet.yml`。先 `config --quiet` 并核对合并后的生产保护/回环映射，再在无 runnable Job 时执行 `up -d --wait --wait-timeout 180 api worker web caddy`。不迁移数据库、不清卷、不重生成运行密钥。
6. 用 `sudo tailscale serve --bg http://127.0.0.1:3800` 启动持久私有 HTTPS 代理。确认 `tailscale serve status --json` 只有 HTTPS 和预期代理、没有 AllowFunnel。该配置只接受确切设备 Host；localhost 仅保留健康检查，旧 `localhost:3600` 工作台地址不再适用。
7. 在服务器运行 `python3 verify-tailnet.py --account-file <私人测试登录JSON>`。脚本将目标绑定到本机实际 Tailscale 节点，正常验证 CA/主机名，检查匿名拒绝、非法 Host、同源 Web、Cookie 登录/刷新/退出与治理；不上传资料、调用 AI 或发布内容。凭据不进命令参数/输出，临时 Cookie 文件位于私人临时目录并在结束时清理。此脚本是服务器自检，**不能代替 Windows/手机真实浏览器和异网验收**。

回退：先确认队列无运行任务，使用原来不带 `tailnet.env/compose.tailnet.yml` 的完整 Compose 参数重建四个应用服务；核验 localhost SSH 登录后，执行 `sudo tailscale serve --https=443 off` 关闭本次 Serve。保留数据卷、runtime.env、images.env 和原 Caddyfile，不使用 `down -v`。Tailscale 客户端本身不因回退而被卸载。

访问策略由 tailnet 管理，ContentFlow 登录为第二层认证；未做额外 ACL 最小化前不声称“只开放了 443 给全部已邀请用户”。当前反向代理不信任上游转发头，客户端 IP 限流可能归并为同一代理出口；不要为取真实 IP 直接信任所有私网 X-Forwarded-For。设备凭据到期、客户端更新、主机休眠/断电、异机备份与微信固定出口仍需单独管理。

### Windows 同时运行 Clash/Mihomo 时

先区分三条链路：已入网的 Tailscale peer、保留证书验证但通过 `curl --resolve` 指定该 peer 的 HTTPS、普通域名的系统直连/实际浏览器代理。前两者正常而普通域名返回 fake-IP 或代理失败时，不重装服务器、不关闭 TLS，也不把同网段或自检成功当作浏览器验收。

经操作者允许后，备份 Clash 配置，只为**实际服务的确切域名**增加 hosts 映射、fake-IP 排除和优先路由；通过全局扩展脚本保存，避免只改生成 YAML。若普通 DIRECT 超时而系统直连成功，可以用仅该规则引用的 `type: direct` 出站，将 `interface-name` 设为实测的 Tailscale 网卡名；不得修改全局默认出口或替换现有代理组。固定映射使用已验证的 Tailscale 节点 IP，不使用校园网/家庭网络 IP；节点删除重建、网卡改名或切换 fake-IP 模式时需要复核。

应用前应比较完整配置，确认其他字段不变，校验脚本幂等与 Mihomo `-t`；通过既有本地控制接口重载，不额外公开控制端口。应用后须分别验证普通域名直连、显式代理请求、真实浏览器登录/刷新和原代理的正常 HTTPS。保留回退副本；不能将脚本已落盘等同于订阅更新、代理重启或主机重启已测试。私人订阅、节点配置与认证字段不进入公开仓库。

依据：[Mihomo DNS](https://wiki.metacubex.one/config/dns/)、[定向 DIRECT 出站](https://wiki.metacubex.one/config/proxies/direct/)、[Clash Verge 扩展脚本](https://www.clashverge.dev/guide/script.html)。

## 资源与边界

单人私人内测如需自审批，必须显式授权并在最后追加 `compose.single-operator.yml` 和指定工作区的 `single-operator.env`，不能关闭治理或伪造第二人。默认仍为双人策略；完整约束、维护命令与回退见 [单人内测审批](../../docs/private_single_operator_policy.md)。

- 基础服务无宿主端口；数据和前端网络均为 `internal: true`。API/Worker 另有出口网络以调用真实 API；Web 仅在内部前端网络。Docker 29 对仅接 internal 网络的容器不建立宿主端口映射，因此只有 Caddy 另接普通 ingress bridge（默认绑定回环），仍显式只映射 `127.0.0.1:3800`。该入口网络不是出口隔离，不能声称 Caddy 没有出站能力；它不持有业务密钥，也不接数据网络。
- PostgreSQL 内存上限 512 MiB，MinIO 384 MiB，初始化进程 128 MiB；日志轮转、PID 上限、持久卷和 `no-new-privileges` 均显式配置。上限不是容量签收，仍须实机测量。
- API/Worker 各 512 MiB，Web 256 MiB，Caddy 96 MiB；Caddy 非 root、只读根文件系统、清空 capabilities、临时目录限额。总上限不代表实际常驻内存，旧电脑必须监测 swap、OOM 和队列积压。
- 当前 PostgreSQL 用户仍是数据库容器初始化管理员，**不是**已完成的运行时最小权限角色拆分。不得把内部隔离称为企业级数据库权限治理。
- 重启/备份/恢复验证未通过前，不放入唯一副本的业务数据。不要执行 `down -v`、清卷或覆盖 `.env` 来解决问题。
- 用户已选择 Embedding API。后端可使用 `--build-arg INCLUDE_LOCAL_EMBEDDINGS=false` 构建，去掉本地 PyTorch/模型运行库；默认仍保留本地模型支持，不改变既有部署方式。API 模式不能调用本地 BGE，且真实服务必须返回 1024 维。
- 对原生固定维度、不接受可选 `dimensions` 请求字段的服务，设置 `CONTENTFLOW_EMBEDDING_SEND_DIMENSIONS=false`；这只省略请求字段，不会放宽返回向量长度校验。默认 true 保持既有服务行为，不能根据一次失败静默重试或自动改模式。
- API/Worker 的生产安全、治理、注册控制与 Cookie 保护不能为“跑起来”关闭。现有 API 密钥已获准按需安全迁移，但旧数据库、知识库和素材未获本轮迁移选择，不自动复制。

实时进度与尚未完成的验收见 `docs/ubuntu_private_test_setup.md`。
