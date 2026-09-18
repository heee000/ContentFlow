# 单人私人内测审批

用户于 2026-09-18 明确授权此例外。它是本人确认，不是独立第二人审核，也不构成企业合规证明。

## 默认与范围

- 默认 `CONTENTFLOW_PROMPT_APPROVAL_POLICY=dual_control`，现有企业行为不变。
- 显式选择 `single_operator_private`，并配置一个有效 UUID 的 `CONTENTFLOW_SINGLE_OPERATOR_WORKSPACE_ID`，才对该工作区生效；其他工作区仍为双人规则。
- 启动验证还要求治理开启、注册关闭、单一 HTTPS Tailscale 设备域名和同源 CORS。不会切换成 development，不放宽生产密钥、Secure Cookie、RBAC、限流或审计。
- 域名检查不能证明网络隔离。部署者必须另外验证 Serve 仅限 tailnet、没有 Funnel，且不公开宿主入口；本版本仅支持此私人部署方式，不提供网页开关。

## 决策与证据

Eval 套件创建者可以填写至少 3 字符的本人确认说明后激活套件。Prompt 创建者仍须通过当前套件、当前模型和当前 Prompt 哈希对应的评测，再填写确认说明审批，然后单独激活。

管理页显示当前模式和本人确认提示。审计记录实际操作者、`approval_policy`、`self_review` / `self_activation`；不冒充另一个人。Prompt 的说明保存在版本复核记录，Eval 的说明保存在激活审计中。

完整性校验、租户边界、管理员权限、模型评测和内容人工审核均不豁免。审批 Prompt 不等于批准任何内容、生成素材或允许公开发布。

## 私人服务器启用与回退

先通过测试、备份当前镜像引用/配置、确认队列无运行任务并核验 Serve 未启用 Funnel。建立单独的 `single-operator.env`，仅含已确认的测试工作区 UUID（无秘密），保留原配置。使用新的已验证 API/Worker/Web 镜像；后端和前端必须同时更新。

在私人目录的每条 Compose 维护命令中使用完整覆盖顺序：

```bash
cf_compose=(docker compose --env-file .env --env-file images.env \
  --env-file tailnet.env --env-file single-operator.env \
  -f compose.infra.yml -f compose.offline-pg.yml -f compose.app.yml \
  -f compose.tailnet.yml -f compose.single-operator.yml)
"${cf_compose[@]}" config --quiet
```

先验证合并后的运行设置，再更新应用服务，不清理数据库/对象卷、不重新生成密钥。启用后读取两个治理接口，确认仅目标工作区为 `single_operator_private`；保持 production/governance/mock/注册保护不变。

回到双人模式时移除最后的单人环境文件和覆盖文件并重建 API/Worker。此后单人激活的套件、单人批准的 Prompt 不再满足门禁，已有激活版本也会阻止新生成；须建立由另一名管理员激活的套件和独立审批的新 Prompt 版本。不能只改界面文案或把旧单人记录重标为双人。

## 真实验收

`deploy/private-test/acceptance_fixture.py` 提供不含凭据、不请求网络的合成测试输入。6 个用例覆盖三阶段结构、风格提示注入、禁止无依据效果承诺和定向修订；确定性断言只代表初始回归，不等于中文长度、文风、事实完整性或对抗安全的全面评估。

部署后只创建本次合成活动。运行真实 Eval 和内容生成，记录用量与实际产物，停在内容人工审核节点；模型自评分不是独立质量评估。图片优先设为 AI 生成，但必须在用户批准内容后才执行。微信仅限已授权素材/草稿测试，公开发布另行确认。
