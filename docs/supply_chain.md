# ContentFlow 软件供应链证据

ContentFlow 的 GitHub Actions 会为每个 Pull Request、`main` 推送和人工运行生成一组可下载、可离线检查的供应链材料；只有非 Pull Request 运行才会申请 OIDC 和证明写权限，并发布签名证明。

## 1. 证据包含什么

`contentflow-supply-chain-<commit>` Artifact 固定包含：

| 文件 | 含义 |
| --- | --- |
| `contentflow-source-<commit>.tar.gz` | 由指定 Git commit 生成的确定性源码归档；只含 Git 跟踪文件 |
| `python.cdx.json` | 锁定 Python 环境的 CycloneDX 清单，包含应用和传递依赖 |
| `frontend.cdx.json` | `web/package-lock.json` 对应的 CycloneDX 清单，包含开发、可选和传递依赖 |
| `SHA256SUMS` | 对上述三个文件的精确 SHA-256 清单 |

源码归档使用 `git archive`，再用时间戳为 0、无原始文件名的 gzip 包装。未跟踪文件、`.contentflow/` 运行数据和本地凭据不会进入归档。校验器会重新从声明的 commit 生成归档并比较摘要，同时要求归档文件集与 `git ls-files` 完全一致。

`npm sbom` 可能为不同安装路径的相同包生成重复 `bom-ref`。`scripts/supply_chain.py normalize` 只在名称、版本、类型或 purl 完全一致时归并这些记录，并保留全部安装路径；任何身份冲突都失败关闭。归并后会检查组件标识、版本、purl、依赖图、已知漏洞字段和构建机绝对路径泄漏。

## 2. CI 权限边界

`supply-chain` Job 在 Pull Request 和非 Pull Request 中都运行，仅有 `contents: read`：

1. 从锁文件重建 Python 和 npm 环境；
2. 生成并规范化两份 CycloneDX；
3. 创建源码归档和 SHA-256 清单；
4. 离线验证组件、依赖图、归档、敏感路径和哈希；
5. 上传保留 30 天的 Actions Artifact。

`attest-supply-chain` Job 只在 `github.event_name != 'pull_request'` 时运行，并且等待后端、前端和供应链三个低权限 Job 全部成功。它单独持有 `id-token: write`、`attestations: write` 和 `artifact-metadata: write`，下载后再次验真，再发布：

- 一份 SLSA build provenance；
- 一份绑定 Python CycloneDX 的 SBOM 证明；
- 一份绑定前端 CycloneDX 的 SBOM 证明。

所有第三方 Action 都固定到 40 位 commit SHA，checkout 不持久化工作流令牌。证明使用 GitHub OIDC 取得的短期 Sigstore 证书，由 GitHub Attestations API 关联到仓库。

## 3. 本地生成与验证

PowerShell 示例；输出目录应位于已忽略的 `.contentflow/`，不要把生成物提交到仓库：

```powershell
$env:PYTHONUTF8 = "1"
$commit = git rev-parse HEAD
$output = ".contentflow/supply-chain-local"
New-Item -ItemType Directory -Force $output | Out-Null

uv run --locked pip-audit --local --strict --format cyclonedx-json --output "$output/python.raw.cdx.json"
npm sbom --prefix web --package-lock-only --sbom-format cyclonedx --sbom-type application |
  Set-Content "$output/frontend.raw.cdx.json" -Encoding utf8

uv run --locked python scripts/supply_chain.py normalize --input "$output/python.raw.cdx.json" --output "$output/python.cdx.json"
uv run --locked python scripts/supply_chain.py normalize --input "$output/frontend.raw.cdx.json" --output "$output/frontend.cdx.json" --root-name contentflow-web
uv run --locked python scripts/supply_chain.py build --repository-root . --expected-commit $commit --output "$output/contentflow-source-$commit.tar.gz"
uv run --locked python scripts/supply_chain.py manifest --directory $output --file "contentflow-source-$commit.tar.gz" --file python.cdx.json --file frontend.cdx.json --output SHA256SUMS
uv run --locked python scripts/supply_chain.py verify --repository-root . --expected-commit $commit --archive "$output/contentflow-source-$commit.tar.gz" --python-sbom "$output/python.cdx.json" --frontend-sbom "$output/frontend.cdx.json" --manifest "$output/SHA256SUMS"
```

从 GitHub 下载 Artifact 后，应在同一个 commit 的干净 checkout 中运行最后一条 `verify` 命令。签名证明还要绑定仓库、签名工作流和源码 commit：

```powershell
gh attestation verify ".\contentflow-source-$commit.tar.gz" `
  --repo heee000/ContentFlow `
  --signer-workflow heee000/ContentFlow/.github/workflows/ci.yml `
  --source-digest $commit

gh attestation verify ".\contentflow-source-$commit.tar.gz" `
  --repo heee000/ContentFlow `
  --signer-workflow heee000/ContentFlow/.github/workflows/ci.yml `
  --source-digest $commit `
  --predicate-type https://cyclonedx.org/bom
```

默认 `gh attestation verify` 验证 SLSA provenance；CycloneDX 是另一种 predicate，必须显式给出 `--predicate-type`。

## 4. 证据边界

- 证明表示指定 GitHub 工作流为指定摘要生成并声明了材料，不表示所有依赖、业务逻辑或部署环境天然安全；仍要结合依赖审计、代码审查、测试和环境策略。
- Python 清单对应当前 `uv sync --all-extras` 的交付/测试环境，前端清单对应完整 lockfile；它们不是按某个容器层裁剪后的最小运行时清单。
- 当前签名对象是可复现源码归档，不是 OCI 镜像。镜像按 digest 构建、漏洞扫描、签名、注册表保留和部署时验签仍是未关闭的生产门禁。
- Actions Artifact 当前保留 30 天；签名证明存储在 GitHub Attestations API。正式发布还应把制品复制到受控、不可变、具有保留策略的制品仓库。
- 供应链证明不能替代受保护分支、环境审批、独立迁移、灰度发布和回滚演练。

实现和权限依据：[GitHub actions/attest](https://github.com/actions/attest)、[GitHub Artifact Attestations 文档](https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations)、[npm sbom](https://docs.npmjs.com/cli/commands/npm-sbom/)。
