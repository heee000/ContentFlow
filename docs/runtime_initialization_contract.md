# 显式启动与测试配置隔离

对应 A43：过去 `import contentflow.db/api` 会加载默认 Settings/.env、构造数据库引擎或应用；后续测试 `_env_file=None` 来不及隔离导入副作用。

## 新入口

```sh
uvicorn contentflow.api:create_app --factory --host 0.0.0.0 --port 8000
```

原 `contentflow.api:app` 不再存在。Dockerfile、私有测试 Compose 和 README 已同步；外部进程管理器若固定旧命令，也需在协调部署时修改。生产服务仍在显式创建时读取部署配置，并未关闭正常 `.env` 支持。

- db 导入不构造 engine；没有明确配置的 Session 不能隐式连接默认库。API 创建与 Worker CLI 明确配置数据库；独立 Worker 实例使用传入 Settings 建立自己的 session factory，显式传入 factory 时保持兼容。
- 独立一次性 Worker 使用 `with Worker(settings=...)` 或在执行结束后 `close()` 释放自有连接池；`run_forever()` 退出时自动释放。只释放自己创建的池，借用调用方 factory 时不处置其连接。不能在另一个线程仍执行任务时直接 close，应先 request_stop 并等待退出。
- `create_schema()` 未绑定数据库时明确报错。此变更不等于已落实 A41 的生产迁移就绪门禁：现有 ready 检查和 Worker 生产 create_schema 路径仍需下一阶段整改。
- Alembic 已由调用方提供 connection 时不再额外读取 Settings；正常独立迁移 CLI 仍读取显式部署配置。

## 测试隔离

- pytest 的 `tests/conftest.py` 在收集前运行 `tests/isolation.py`；清除继承的 ContentFlow 业务环境变量并关闭 Settings 默认 dotenv，仅保留明确的 `CONTENTFLOW_TEST_*` 测试目标。
- 浏览器专用 API 在导入任何 ContentFlow 应用/测试夹具前执行同一隔离；前端测试启动器仍清除自己的业务环境变量。迟于 db/api 导入时隔离函数直接失败，不给出错误的安全承诺。
- 这是测试进程专用设置，不改变生产配置文件或系统环境，也不是生产可启用的绕过开关。测试仍可用合成路径显式测试 dotenv 行为。
- `tests/test_runtime_initialization.py` 的子进程分别验证：导入时 dotenv/目录/engine 三个探针绝不触发；合成 .env 和继承环境毒化被隔离；显式 Settings 下 API/Worker 不调用隐式配置读取。
- 该保证覆盖上述受控入口，不表示任意第三方脚本、裸 unittest 调用或独立 CLI 都自动处于测试模式。真实 PG/MinIO 只通过专用 TEST 目标启用；没有目标时明确 skip。

未读取/修改真实配置、密钥或业务数据；未改代理/DNS/Tailscale。具体测试与源码版本见 [实施进度](IMPLEMENTATION_PROGRESS.md)。
