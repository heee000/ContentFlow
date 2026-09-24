# 安全异常诊断与日志配置契约

适用：A55 异常日志、A56 开发迁移日志配置，以及它们引入的 HTTP/素材错误回执回归。没有新增迁移；本契约不代表真实日志采集平台、部署或恢复已验收。

## HTTP 与事务

- 未处理的业务 API 异常在响应开始前返回 HTTP 500，固定 `internal_error` 和通用消息，保留 `request_id`、`X-Request-ID`、`Cache-Control: no-store`。正常异常处理不再把原始异常重抛给 ASGI 服务器日志。
- HTTP 错误边界不提交或修复数据库事务。生成接受的审计写入失败时，WorkflowRun、Job、GenerationIntent 和 workflow.enqueue 审计全部回滚；同键后续接受只创建一份任务、回执和审计。
- 响应流开始后不能替换为 JSON 500；记录安全诊断后抛出不带原异常链的 ResponseStreamFailure，不能将半截下载当成完整成功。进程退出、非 HTTP lifespan 和服务器自身错误不属于这个路由边界。

## 安全诊断

- 应用异常日志保留固定事件、内部请求/任务编号、异常类型、模块/函数/行号。最多记录 8 个异常、每个异常 32 个栈帧；不格式化异常消息、SQL、URL、局部变量、原始 traceback 或异常组标题。
- API、Worker、存储补偿、发布证据、调用账本及 AI provenance 的相关异常记录使用这一机制。调用者只传静态文本或内部标识，不传用户正文或外部响应。
- SQLAlchemy 运行引擎及独立 Alembic 引擎启用 hide_parameters，作为附加保护；它不能单独替代上述异常记录规则。
- 未知 Worker 错误仅保存异常类型和按任务编号排查的通用提示。已有数据库/发布人工核对与重试分类继续生效；脱敏不能将未知外部副作用改成可安全重试。

## 素材配置漂移的安全提示

固定本地异常 MediaConfigurationError 只接受已定义的错误码。Worker 仅对精确类型和白名单码生成回执，不输出异常 args、cause 或任意 MediaProviderError 的原始消息。

| 错误码 | 固定处理指引 |
| --- | --- |
| `media_source_configuration_changed` | 当前配置与已批准来源不一致；恢复对应配置并核对任务，不静默换来源 |
| `media_poll_configuration_changed` | 原异步任务的配置变化或缺指纹；人工核对原任务及配置，确认远端结果前不要重新生成 |

错误码和指引同时保存在 Job.last_error 与 Asset.error；两类均不可自动重试，检查发生在 Provider 构造/外部调用前。缺少指纹与指纹变化同样保守失败。回执不包含配置值、URL、模型标识或远端任务号。此提示不提供绕过现有人工核对门禁的快捷重放。

## 日志配置所有权

- 嵌入式 upgrade_database 不调用 fileConfig，不替换宿主 handlers/level、不禁用已有应用 logger。
- 独立 Alembic CLI 可以配置自己的 handlers，但使用 disable_existing_loggers=False。
- API/Worker 配置入口确保本身 logger 启用；开发/生产 API、Worker、管理员入口及独立 Alembic 均有隔离回归，验证应用日志仍可观察。
- 不通过关闭 logger 修复隐私问题，不打开生产 SQL echo/debug，不读取真实日志或凭据。

## 验收范围与余项

回归覆盖实际 SQL 唯一约束、网络异常、嵌套异常/异常组、流式中断、Worker 失败和存储补偿，并核对合成私密标记不进入响应/日志/回执。生产采集器、访问权限和日志保留周期仍需环境验收。

重复指标的业务冲突仍返回通用 500，其明确 409/幂等契约属于 A33，保留为下一阶段问题；本阶段关闭其异常日志复制业务参数的通道，不将 A33 算作已修复。

当前实施状态与验证结果见 [实施进度](IMPLEMENTATION_PROGRESS.md) 末节。历史失败保留于 [修复前审计](project_audit_20260924_product_delivery_review.md)。
