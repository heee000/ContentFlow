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

脚本已经通过目标 Ubuntu 的 `bash -n`、帮助输出、缺少参数拒绝（退出码 2）与非 root 拒绝（退出码 1）；上传前后 SHA-256 一致。仓库为 `.sh` 显式固定 LF，避免 Windows 签出引入 CRLF。已给操作者一次性 sudo 命令，当前等待其本机执行，**尚未安装 Docker**。

安装完成后用新 SSH 会话验证组权限、daemon 和 Compose，再进入 ContentFlow 的独立目录、配置与镜像准备。脚本存在不等于已安装，尚未复制业务数据库或密钥。

主机地址、用户名和凭据在操作者自己的连接配置中维护，不写入公开部署模板。

## 先完成 SSH 认证

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
2. **低资源内部栈**：单 Worker；Web/API/PostgreSQL/pgvector/MinIO；文本和媒体采用用户已选择的 API，本地 BGE-M3 小批量起步。构建尽量在开发机或 CI 完成，不在运行业务时同时构建。监控全套按需启动。
3. **独立部署配置**：使用独立目录/项目名/数据卷，数据库和对象存储不映射到整个局域网；对外入口采用私有 HTTPS 反向代理或 SSH 隧道。明确网页 API Base、Cookie、CORS、代理层数及注册策略，不能通过关闭生产保护来“修好登录”。根目录 Compose 是开发配置，不能不经调整直接作为共享服务器配置。
4. **凭据和数据**：先运行空测试数据库；用户现有知识库/素材/数据库是否迁移需明确选择后执行。密钥通过独立文件或 Secret 注入，不提交 Git；Windows `.venv`、`node_modules`、运行缓存不复制到 Linux。切换 Embedding 模型必须重建向量，不能混用旧 BGE 向量。
5. **主机运行保障**：确认 Docker/服务开机启动，服务器不自动休眠；使用持久数据卷，数据库和对象备份到不同物理设备。swap 仅为 OOM 缓冲；索引期间的可用内存、换页、队列等待和 API 响应才是判断是否要升级 RAM/切 Embedding API 的依据。
6. **逐级验收**：先完成健康检查、登录、空数据读写、重启持久化；再用受控样例验证索引、真实内容生成、素材和人工审核。微信先测试连接和草稿，实际公开发布保持单独明确操作，不能在部署验证脚本中自动发布。

公网部署资产 `deploy/public-test/` 当前要求固定公网域名、R2、不可变镜像，且部署脚本会启动 BGE 缓存初始化。内部测试需要独立适配，不能仅把主机地址替换成局域网 IP；也不能声称 Embedding API 模式已经跳过该初始化步骤。

## 网络边界

客户端换网络不会改变常驻 Worker 的出口；服务器所在网络改变出口或使用不同代理仍可能触发微信白名单问题。SSH/Tailscale 私网地址不是微信所见的公网出口。是否需要固定出口网关，等实际服务器出口和网络稳定性采样后再决定。
