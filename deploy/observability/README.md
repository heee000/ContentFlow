# ContentFlow 可观测性部署资产

本目录提供版本化的 Prometheus 抓取/记录/告警规则和 Grafana provisioning。它是单机 Compose 的可验收运维基线，不等于已经完成生产高可用监控、通知路由或 7x24 值班签收。

## 启动

在 `.env` 中至少设置：

```dotenv
CONTENTFLOW_METRICS_ENABLED=true
CONTENTFLOW_METRICS_BEARER_TOKEN=<至少 32 字符的独立随机值>
CONTENTFLOW_GRAFANA_ADMIN_PASSWORD=<至少 32 字符且不同于指标 Token>
CONTENTFLOW_GRAFANA_ROOT_URL=https://grafana.example.com
CONTENTFLOW_GRAFANA_COOKIE_SECURE=true
CONTENTFLOW_GRAFANA_BIND_ADDRESS=127.0.0.1
```

然后启动可观测性 profile：

```powershell
docker compose --profile observability up --build -d
docker compose --profile observability ps
```

本地默认从 `http://127.0.0.1:3301` 访问 Grafana。Prometheus 只在 Compose 内部网络暴露 `9090`，不会发布到宿主机。Grafana secret preflight 会在启动前确认管理员密码至少 32 字符且不与指标 Token 相同；失败时 Grafana 不启动，也不会输出秘密内容。

## 配置与规则校验

```powershell
docker compose exec prometheus /bin/promtool check config /etc/prometheus/prometheus.yml
docker compose exec prometheus /bin/promtool check rules /etc/prometheus/contentflow.rules.yml
docker compose exec prometheus /bin/promtool test rules /etc/prometheus/contentflow.rules.test.yml
```

CI 使用固定摘要的 Prometheus 3.13.1 distroless 镜像执行相同的三层校验：完整配置语法、规则/PromQL 语法和持续故障告警行为。Grafana 数据源与看板由只读文件 provision，UI 修改不会成为事实来源。

## 已覆盖信号

- API 抓取存活、请求速率、5xx 比例、模板路由 P95；
- 活跃/陈旧 Worker、队列各状态与最长就绪等待；
- Workflow、Prompt Eval 状态和待人工发布对账；
- Prometheus 自身规则计算失败。

数据库 Gauge 在每个 API 副本上是同一数据库全局视图，看板与规则必须使用 `max` 去重；HTTP Counter/Histogram 才按实例 `sum(rate(...))` 聚合。不要加入 workspace、用户、活动或发布任务 ID 标签。

## 仍需生产集成

当前没有内置 Alertmanager receiver，以免把占位地址伪装成通知闭环。目标环境仍需把规则接入企业 Alertmanager/托管告警平台，配置通知升级、静默权限、值班日历和演练；还应为 Prometheus 配置高可用/remote-write/长期保留，并通过 TLS 网关或 VPN 访问 Grafana。仓库中的 15 天本地 Prometheus retention 只适合参考拓扑。
