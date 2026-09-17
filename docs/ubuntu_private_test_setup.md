# Ubuntu 内部测试机准备记录

更新：2026-09-17。范围是用户自有电脑上的私人测试；既有公网/云服务器部署仍暂停。

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
