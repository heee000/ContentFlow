# Ubuntu 内部测试机准备记录

更新：2026-09-18。范围是用户自有电脑上的私人测试；既有公网/云服务器部署仍暂停。

**当前结论**：私人六服务已切换 Tailscale HTTPS，服务器端真实 TLS、Cookie 登录/刷新/退出、既有知识读取与数据校验通过。Windows 客户端已安装并由用户授权入网，两端在线；Windows 固定已验证节点 IP、保留证书校验的 HTTPS readiness 返回 200。当前浏览器仍被本机 Clash/Mihomo 域名解析及代理路径阻断，等待用户同意仅调整该服务域名；浏览器跨设备/手机异网验收仍未通过。下方 localhost:3600 和“待安装/待 Key/未迁移”等保留为过程记录，不代表当前入口。应用源码仍为 `8a5e300`；部署配置提交 `75a473e` 的 CI 四个 Job 已全部成功，定向覆盖配置 18 项通过。详见台账 `CF-20260917-09`、`CF-20260918-01`。

## 现在怎样体验

现在应在已加入同一授权 Tailscale 网络的设备上打开 Serve 输出的 **HTTPS 网址**；实际地址在本机私人使用说明中，不把账号/授权地址写入公开模板。旧 localhost:3600 工作台入口已不适用，仅保留回环健康检查和可回退配置；旧 Windows localhost:3000 实例不受影响。新工作区为 `Ubuntu Private Test 20260917`，没有旧活动；登录资料保存在 `.contentflow/private-test-transfer-20260917/private-test-login.json`，已被 Git 忽略，不上传或复制到公开文档。

当前可以登录、浏览资源与系统、检查知识库和任务队列、创建活动。已有一个明确标记为合成测试的知识文件，真实索引成功；不是从旧知识文件拷贝。内容生成前仍需要在管理页完成 Prompt 版本、评测、独立审核与激活；生产保护没有关闭。微信渠道未迁移，本轮没有创建微信草稿或公开发布。

已验收：PostgreSQL/MinIO/API/Web/Caddy 就绪、Worker 真实执行索引、API 鉴权、Host 限制、浏览器 Secure/HttpOnly Cookie、重启前后对象哈希/向量/文档一致。只重启了私人容器，不是主机断电或异机恢复测试。六容器低负载采样约 550 MiB，系统 available 1357 MiB、swap 843 MiB；暂时足够低并发体验，不能据此承诺长期高负载。

入口配置特别注意：Caddy 需要去除不必要的二进制低端口 capability，并同时接内部 app 与单独 ingress 网络才能在当前 Docker 建立回环端口映射；后者不是出口隔离。Host 拒绝必须位于显式 route 的 proxy 之前。Windows 隧道使用 IPv6 远端字面量但不加 `-6`，否则 IPv4 回环绑定失败。均不需要修改 SSH 认证、系统 DNS、全局代理或关闭生产保护。

## 连接准备过程（最新状态见实机预检）

- 用户报告 Ubuntu 24.04.2 LTS、2 核 CPU、4 GB RAM、约 800 GB 可用存储；这些尚不是远程资源采样。
- 用户已启用 OpenSSH，并完成过 Windows 到 Ubuntu 的交互式登录。
- Windows 到该机器 TCP/22 已连通，SSH 主机密钥与 Windows 已保存的 known_hosts 匹配。
- 已在 Windows 用户的 `.ssh` 下新建专用 `contentflow_ubuntu_test` Ed25519 密钥。私钥未进入仓库、未上传或打印；只要求用户追加对应公钥。
- 用户反馈已执行公钥追加，但专用密钥仍被服务器拒绝。握手确认客户端发送了预期密钥；用户随后提供权限/指纹诊断，确认家目录 750、`.ssh` 700、`authorized_keys` 600 且所有者正确，但文件为 0 字节。已给出 Ubuntu 直接追加公钥的命令；仍须实际免密连接验证后才能标记成功。
- 尚未安装服务器软件、复制业务数据、修改服务器防火墙、配置真实 Provider 或执行平台测试。

后续诊断：用户直接追加公钥后指纹已与 Windows 匹配，sshd 默认配置为 `PubkeyAuthentication yes`、标准 authorized_keys 路径、`StrictModes yes`，服务器主机指纹一致。只读检查另外确认 Windows PowerShell 7 的命令引号使本轮新建私钥带上意外口令；私钥/公钥配对正确。自动审批拒绝直接移除口令，随后用户明确同意仅修正这把新密钥。完成结果见下节，原公钥落盘问题与本地口令问题分别记录，不混为服务器认证规则异常。

## 实机预检与一次手动安装

用户已明确同意仅修正新密钥，意外口令已移除并通过空口令读取验证。随后 IPv4 连接超时，但经用户已提供的 IPv6 成功免密登录；使用原已验证主机的 HostKeyAlias 保持 StrictHostKeyChecking。SSH 认证阻塞已解除。

2026-09-17 实测：AMD A6-9210、x86_64、2 个逻辑 CPU、含 AVX2；内存约 3.7 GiB，可用约 1.6 GiB，已有 swap 约 3.7 GiB/使用 465 MiB；931.5 GiB 机械磁盘，根文件系统空闲约 846 GiB。一个用户既有后台进程约占 576 MiB，桌面应用也占用内存；这些进程未被终止。AVX2 只是指令集信息，不是 PyTorch/BGE 实机推理签收。

Docker 未安装，sudo 要求密码。Docker CE 官方 APT 地址一次请求连接重置，GitHub HTTPS 返回 200；现有 Ubuntu APT 列表提供 docker.io 29.1.3 与 docker-compose-v2 2.40.3，因此私人测试先使用 Ubuntu 维护的包，不添加其他软件源或移除现有软件。

已准备 `deploy/private-test/install-docker.sh`。它仅接受 Ubuntu 24.04、现有非 root 用户及显式 `--grant-docker-access`，遇到已有冲突包会停止；从现有 APT 源安装 Docker/Compose，启用 Docker 并追加部署用户到 docker 组。操作者在自己的终端用 sudo 执行一次，密码不交给代理。

**docker 组具有 root 等价的容器管理权限**，只对该自有测试机的指定部署用户授予；没有修改 sudoers、SSH、桌面服务、应用数据或公开 Docker daemon TCP 端口。[Docker 官方说明](https://docs.docker.com/engine/install/linux-postinstall/)

Docker 启动会初始化自己的网络/防火墙规则，因此不能将安装描述为“防火墙完全不变”；当前脚本没有配置业务端口映射，也没有启动 ContentFlow 容器。

脚本已经通过目标 Ubuntu 的 `bash -n`、帮助输出、缺少参数拒绝（退出码 2）与非 root 拒绝（退出码 1）；上传前后 SHA-256 一致。仓库为 `.sh` 显式固定 LF，避免 Windows 签出引入 CRLF。操作者已执行安装；新 SSH 会话验证 Docker 29.1.3、Compose 2.40.3、docker 组权限及 daemon enabled/active，安装已签收。

安装完成后用新 SSH 会话验证组权限、daemon 和 Compose，再进入 ContentFlow 的独立目录、配置与镜像准备。脚本存在不等于已安装，尚未复制业务数据库或密钥。

主机地址、用户名和凭据在操作者自己的连接配置中维护，不写入公开部署模板。

## Docker 安装后的准备增量

- 实测当时约 1.8 GiB 可用内存、swap 使用约 690 MiB。用户已明确选择 **Embedding API**，并允许按需安全复制现有 API 配置；不复制旧数据库、知识库或素材。用户尚需注册/提供合适的 Embedding API Key，不能伪造占位 Key 或切换为 hash 冒充真实接入。
- MinIO Server/Client 官方 Quay 固定摘要镜像已在 Ubuntu 拉取成功。Docker Hub 对 pgvector 的请求超时；Windows 当前 Docker Engine 27.4.0 可用且能取得同一固定摘要，改为本机保存镜像、压缩后经 SSH 传输。没有修改服务器 DNS/代理，也没有引入非官方镜像源。
- pgvector 源端摘要为 `sha256:1d533553fefe4f12e5d80c7b80622ba0c382abb5758856f52983d8789179f0fb`，linux/amd64 config ID 为 `sha256:5fa1d4c74299c466a1a051ed66ce7a44b69cf27b66444a202f7e5592963ed596`。未压缩 archive 为 446161920 字节，压缩包为 152452401 字节，后者 SHA-256 为 `15629343a6437d72f7e3e74184fc6a23ffc816aa0e7a65ceb114eded7283fa61`。**传输完成、目标哈希、load 和启动仍待验证**，部分文件不能加载或当作完整包。
- 独立测试目录已创建为 700；初始化脚本生成独立随机数据库/对象存储口令，`.env` 为 600，未打印内容；没有复制已有 API 密钥。新基座配置无宿主端口，使用 internal 数据网络、资源上限、持久卷、日志轮转及独立 bucket 应用权限，另附按 config ID 锁定的离线 pgvector 覆盖文件。
- Windows 已成功构建 API-only 后端，约 412 MB；无网络/只读容器中 Worker、psycopg、boto3 导入通过，确认不存在 torch/sentence-transformers。现有约 2.47 GB 本地模型镜像和运行容器均未替换。镜像大小不是运行内存指标，默认含本地依赖的分支本轮未重新构建。
- `.dockerignore` 改为后端必要输入白名单，构建上下文约 722 kB；避免把业务知识、运行数据或密钥发送到构建器。私人/公网配置回归合计 9 项通过，目标机 shell/Compose 静态核验通过；**整栈、持久化、API 真实调用和用户登录尚未签收**。
- MinIO 已在独立 internal 网络启动并健康，业务 bucket 设为非匿名；真实初始化验证应用凭据可列出业务 bucket，调用管理 API 被拒绝。没有公开端口，也没有业务资料。PostgreSQL 压缩镜像仍在传输中，不能把 MinIO 就绪说成整栈就绪。
- Embedding 选型建议（2026-09-17）：硅基流动 `BAAI/bge-m3`，Base `https://api.siliconflow.cn/v1`，模型原生 1024 维，当前官方价格页标为免费。免费模型有实名认证和固定速率限制；不承诺永久价格或 SLA。服务端一次短 DNS 超时后，较长有界 HTTPS 探测返回 401，证明网络/认证端点可达，不是 Key 或向量调用通过。尚未注册、充值或发送任何知识内容。
- 依据：[价格](https://siliconflow.cn/pricing)、[免费模型限制](https://docs.siliconflow.cn/docs/userguide/faqs/rate-limit-and-upgradation)、[Embedding API](https://docs.siliconflow.cn/docs/api/embeddings-post)、[BGE-M3 模型卡](https://huggingface.co/BAAI/bge-m3)。原生 1024 维不等同于支持可选 `dimensions` 请求字段；接入时须按目标合同验证，不能仅凭模型同名复用旧向量。

## Embedding 真实接入验证

用户已直接提供并授权使用硅基流动 Key。不再要求用户另建文件；密钥仅存本轮 Git 忽略的本地运行配置，不写入本文或提交。既有文本/媒体密钥仍未迁移。

1. 一次最小协议探针发送 3 句合成文本，返回模型 `BAAI/bge-m3`、3 条 1024 维有限数值向量，服务报告 25 tokens。
2. 按官方合同补充通用 `CONTENTFLOW_EMBEDDING_SEND_DIMENSIONS`，默认 true，固定维度服务设置 false 后省略可选请求参数，但仍严格校验返回维度。该开关通过既有开发/公网 Compose 透传；未关闭生产门禁、未增加供应商专属默认值。
3. 通过 ContentFlow 的 `Settings → build_embedding_provider → encode_many` 实际链路再次调用同样合成文本，返回维度正确且相关句相似度高于无关句，服务报告 25 tokens；两次合计 50 tokens。该结果是连通性/格式/简单语义 smoke，不是完整 RAG 质量或账单审计。
4. 账本在省略字段模式加入受控证据标记，防止同一 Job/entity/ordinal 切换请求模式时误用旧逻辑身份；默认模式保留旧摘要计算。定向 26 项与 6 subtests 通过，覆盖默认发送、省略字段、仍拒绝错误维度、配置透传及账本请求分离。
5. 后续连接 Ubuntu 时已知 IPv6 与 IPv4 均超时，已请用户唤醒/核实最新地址，没有反复追加公钥或更改认证规则。压缩包传输进程已正常结束，但目标机的完整 SHA-256/load/数据库启动尚未验证。等待连接恢复期间只完成本地测试、镜像与记录，不把 Key 验证成功写成部署已完成。

用户随后确认电脑未休眠、地址不变。Windows 路由核查为 WLAN 同网段直连；使用较长连接超时且显式无 SSH ProxyCommand 的重连成功，主机身份与认证保持有效。不能据一次重连成功归因于代理或休眠，也没有修改系统网络配置。继续执行远端镜像完整性与数据库验证。

本轮 Windows 完整覆盖率测试在迁移测试期间发生 Python 原生 `access violation`，没有产生完整通过结论；堆栈涉及 Pydantic Settings/Alembic，根因尚未定位，不将其无证据归因于业务代码或环境。定向回归及真实适配器测试通过，更新的 API-only Docker 镜像构建与无网络导入通过。完整回归使用 Linux CI 核验，Windows 崩溃仍保留为诊断项。

## 2026-09-17 应用部署增量

SSH 已恢复，MinIO 与 PostgreSQL 已在独立私网健康运行。pgvector 压缩包完整 hash 匹配，Docker 27→29 的旧空字段归一导致加载 ID 变化，经全部层和运行字段匹配后确认；实测 PostgreSQL 16.14 / vector 0.8.5。无需操作者重新追加公钥、装 Docker 或提供 Embedding Key。

授权范围内的文本/媒体配置已从旧实例白名单导出，与独立 Embedding 配置一起经 SSH 存入新的 600 运行文件。应用签名、凭据加密、指标 Key 全部独立新建；旧数据库、旧账户、知识、素材和 MinIO 管理权限没有迁移到应用。

Ubuntu 真实 Embedding 最小探针通过（3×1024，有限数值，相关句优先，25 tokens），含 Windows 两次共报告 75 tokens。`f290824` 的 Linux CI 全绿，351 passed / 199 subtests；Windows 原生崩溃诊断仍保留。应用镜像压缩传输已完整校验，正在加载和启动；登录、索引、重启持久化不因此自动算通过。

访问方案为 Windows **http://localhost:3600/** → 严格验证的 SSH 隧道 → Ubuntu 回环 Caddy:3800。不是校园网直接开放，不是公网部署；页面及 API 使用同源地址，独立 Cookie 名，生产 Secure/HttpOnly 不关闭。详细可复用步骤见 `deploy/private-test/README.md`。

## SSH 认证故障时的历史排查（当前已解决）

已登录 Ubuntu 的操作者检查以下输出；只有路径权限和公钥指纹，不要求发送密码或私钥：

```sh
ls -ld ~ ~/.ssh ~/.ssh/authorized_keys
ssh-keygen -lf ~/.ssh/authorized_keys
```

Windows 检查预期公钥指纹：

```powershell
ssh-keygen -lf "$env:USERPROFILE\.ssh\contentflow_ubuntu_test.pub"
```

两个指纹必须匹配。`.ssh` 应由登录用户持有并为 `700`，`authorized_keys` 为 `600`；家目录不能让其他用户写入。若已符合仍拒绝，再核对 sshd 的实际 AuthorizedKeysFile、PubkeyAuthentication 和认证日志。保留现有 key 和密码登录，不清空 authorized_keys，不关闭 StrictHostKeyChecking，不授予无限制免密 sudo。

## 认证通过后的实施顺序

1. **只读预检**：核实 x86_64、CPU 型号/指令集、RAM/swap、物理磁盘、Docker/Compose、已有服务、睡眠配置和 sudo 权限。旧 AMD/Intel CPU 的实际依赖兼容性需要实测，不能只按“2 核”估算。
2. **低资源内部栈**：单 Worker；Web/API/PostgreSQL/pgvector/MinIO；文本、Embedding 和媒体采用用户已选择的 API，不在旧电脑加载本地 BGE。构建尽量在开发机或 CI 完成，不在运行业务时同时构建。监控全套按需启动。
3. **独立部署配置**：使用独立目录/项目名/数据卷，数据库和对象存储不映射到整个局域网；对外入口采用私有 HTTPS 反向代理或 SSH 隧道。明确网页 API Base、Cookie、CORS、代理层数及注册策略，不能通过关闭生产保护来“修好登录”。根目录 Compose 是开发配置，不能不经调整直接作为共享服务器配置。
4. **凭据和数据**：先运行空测试数据库；用户现有知识库/素材/数据库是否迁移需明确选择后执行。密钥通过独立文件或 Secret 注入，不提交 Git；Windows `.venv`、`node_modules`、运行缓存不复制到 Linux。切换 Embedding 模型必须重建向量，不能混用旧 BGE 向量。
5. **主机运行保障**：确认 Docker/服务开机启动，服务器不自动休眠；使用持久数据卷，数据库和对象备份到不同物理设备。swap 仅为 OOM 缓冲；索引期间的可用内存、换页、队列等待和 API 响应才是判断是否要升级 RAM/切 Embedding API 的依据。
6. **逐级验收**：先完成健康检查、登录、空数据读写、重启持久化；再用受控样例验证索引、真实内容生成、素材和人工审核。微信先测试连接和草稿，实际公开发布保持单独明确操作，不能在部署验证脚本中自动发布。

公网部署资产 `deploy/public-test/` 当前要求固定公网域名、R2、不可变镜像，且部署脚本会启动 BGE 缓存初始化。内部测试需要独立适配，不能仅把主机地址替换成局域网 IP；也不能声称 Embedding API 模式已经跳过该初始化步骤。

## 网络边界

客户端换网络不会改变常驻 Worker 的出口；服务器所在网络改变出口或使用不同代理仍可能触发微信白名单问题。SSH/Tailscale 私网地址不是微信所见的公网出口。是否需要固定出口网关，等实际服务器出口和网络稳定性采样后再决定。

## 2026-09-17 Tailscale 私网接入准备（尚未完成远程访问）

用户选择先用自有电脑私人测试，注册 Tailscale 后授权继续安装；这不是恢复公网部署，也不授权开启 Funnel、出口节点或校园网子网路由。

- 已实机安装官方 Tailscale 1.102.4，`tailscaled` 为 enabled/active。Ubuntu 对包站点 DNS 查询间歇超时，改由 Windows 下载官方 Ubuntu Noble 包，再经严格主机验证的 SSH 上传。目标 `gpgv` 验证官方 InRelease 签名，签名清单的 Packages SHA-256 和最终 deb SHA-256 均匹配；不关闭 TLS、SSH 或软件签名校验。
- 已确认依赖 iptables/iproute2 存在。`apt-get --no-download` 安装本地绝对路径出现内部 Pathname 错误且未安装；随后仅对已校验的 deb 执行 `dpkg --install` 成功，没有卸载、升级其他包。尚未添加 Tailscale APT 更新源，后续需要补充可用的签名更新路径，不能声称已配置自动更新。
- 首次连接使用 `--accept-dns=false --accept-routes=false --hostname=contentflow-test`，不启用 Tailscale SSH、出口节点或 Funnel。安装后 `/etc/resolv.conf` 哈希与基线相同，原六个 ContentFlow 容器继续运行，readiness 的 database/storage 均为 ok；应用源码仍是 `8a5e300`。
- 初次 `up --timeout=30s` 超时，但稍后的 daemon 完成控制端通信并返回设备登录地址；当前为 Logged out / 等待浏览器授权，不是已入网。管理台已登录不等于设备授权页沿用会话，实际设备页仍要求重新登录。授权地址、用户账号和凭据不写入本文。
- Windows 1.102.4 官方 MSI 已完整下载，Authenticode 为 Valid、发行者为 Tailscale Inc.。发起安装的 UAC 请求返回“用户取消”，客户端未安装；已询问是否重发，未绕过或自动反复弹窗。
- 浏览器控制故障已按用户要求交给“电脑相关”任务修复，本任务重置控制会话后实测重新读取现有 Edge 管理台成功。没有通过代理切换或绕过网址/请求头安全检查恢复。该工具修复不属于 ContentFlow 源码修改。

接续点：由用户完成设备登录/授权及 Windows 安装确认；HTTPS 确认页已打开但未提交。启用 HTTPS 会将证书设备域名永久写入公开 CT 日志，网站本身仍可保持私有，已就这一点请求确认。授权完成后才能签收设备列表、准确设备域名与权限，再调整 Web 构建期 API Base、API 公共地址/CORS 和入口 Host/代理配置，配置 Serve 并测试真实 HTTPS 登录、素材访问与手机跨网连接。现阶段**未启用 Serve、未开放公网、未改应用入口、未完成异机备份或固定微信出口**。

依据：[官方软件源](https://pkgs.tailscale.com/stable/)、[CLI 连接参数](https://tailscale.com/docs/reference/tailscale-cli/up)、[HTTPS 与证书透明度](https://tailscale.com/docs/how-to/set-up-https-certificates)。

### 后续签收：Ubuntu 已连接、Serve 已启用、服务器端 HTTPS 通过

用户随后完成设备登录并明确允许 HTTPS 的 CT 公示。浏览器管理台显示节点 Connected、HTTPS 已开启，服务器为 Running/Online、Health 空。Serve 在后台只代理回环 Caddy，配置无 AllowFunnel。

新增独立 `compose.tailnet.yml`、`Caddyfile.tailnet`、独占配置生成器和 HTTPS 验证脚本；原 SSH-only 配置/镜像不覆盖。Web 以实际 HTTPS API Base 重建，使用已验真 Node 摘要。压缩 archive 106919714 字节、SHA-256 `dfa443b2cccf642ad8cdb6eaf57336d88cd3ce6888b99f7a4908082aa9a854a9`；目标加载后全部 9 层和运行配置匹配。Docker 27/29 去除旧空字段导致 ID 变化，源 `8dd20e…`、目标 `4a126b…` 均已核验，未忽略未知差异。

合并配置验证生产治理/注册/Mock 限制不变、仍只映射回环 3800。确认队列为空后只重建四个应用服务，不重启 DB/MinIO 或迁移数据。服务器真实 HTTPS 自检通过：CA/主机名正常验证、readiness、匿名 401、错误 Host 403、Web HTTPS CSP、Secure/HttpOnly/Lax 双 Cookie、登录/刷新/退出、原有 1 份知识读取、治理仍强制。对象 checksum、1024 维向量和仅 1 次 succeeded Provider attempt 保持一致，runnable Job 为 0。

定向回归 18 项与 Ruff 通过；初次沙箱内测试无法创建临时目录，使用已批准的独立项目内临时目录在沙箱外执行后通过，未改系统 ACL。Caddy 配置由目标已加固镜像离线验证通过；本机旧标签仍指向带 capability 的原始镜像，不能将那个标签当作已验收加固镜像。

**剩余**：Windows 安装 UAC 仍待用户明确重发或手动安装，尚未完成本轮浏览器/手机异网测试；不要把服务器自检当作跨设备签收。精细 ACL、真实客户端 IP 限流、设备凭据续期、签名更新源、备份/长稳与固定微信出口未在本轮完成。

### 2026-09-18 Windows 已入网，发现单域名代理兼容问题

用户要求重发安装后，后台提权再次返回系统“用户取消”，用户实际没有看见弹窗；不能据此断言用户点击取消。没有安装日志或服务，故确认该次安装未开始。改由用户直接打开已验签的官方 MSI 安装成功，实测版本 1.102.4、服务 Running/Automatic。

客户端连接使用 `--accept-dns=false --accept-routes=false`，保留现有 DNS/代理配置；用户亲自完成同一 tailnet 的设备登录授权。两端 Running/Online、客户端 Health 无告警，Tailscale ping 直连成功。Windows 以单次 `curl --noproxy '*' --resolve <已验证服务域名>:443:<已验证节点IP>` 访问 readiness 返回 200，数据库/存储为 ok；保留 CA/主机名校验，没有使用 `-k` 或修改 hosts。

普通域名查询返回 Mihomo fake-IP 范围中的 `28.0.0.*` 及对应合成 IPv6，而非实际 Tailscale 节点地址；系统代理为本机 7890，TUN DNS 已启用。普通 HTTPS、显式同路径 HTTP 代理请求均 TLS 握手失败，Edge 返回 `ERR_CONNECTION_CLOSED`。这与此前已修复的浏览器工具初始化故障不同，也没有本次双核心冲突证据；不重复杀进程、关闭 TUN 或更改全局 DNS。

已请求仅为该 ContentFlow 域名添加可回退的正确解析及直连配置，用户确认前不修改代理设置。Windows 入网和直连 TLS 已签收，但真实浏览器登录、手机移动网和完整业务流程未因此自动签收。提交 `75a473e` 的 [CI #35229216117](https://github.com/heee000/ContentFlow/actions/runs/35229216117) 四个 Job 全部成功；本段为运行记录，不重建镜像或新增 AI 调用。
