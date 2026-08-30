# 公网测试备份策略

## 范围与加密

- PostgreSQL：每日 custom-format 逻辑 dump，`pg_restore --list` 通过后进入 restic。
- 业务对象：直接位于独立 R2 Bucket，必须启用版本保留/生命周期；数据库恢复后按对象 URI 与应用 SHA-256 Metadata 交叉核对。
- restic 在客户端完成加密、认证和分块。R2 备份 Bucket、Token、repository password 都不得与业务对象配置复用。
- `CONTENTFLOW_BACKUP_REPOSITORY_PASSWORD` 是灾难恢复密钥。至少保存一份不在云主机和 R2 中的离线副本；丢失后备份不可恢复。

## 周期与保留

- 每日：`backup.sh`，保留最近 7 个日快照。
- 每周：由同一快照序列保留最近 4 个周快照。
- 每次迁移/部署前：`deploy.sh` 在检测到现有 API/Worker 时先强制执行一次备份；备份失败则停止部署。
- 每月：`verify-backup.sh` 恢复到随机临时数据库，验证表数和 Alembic revision，再精确清理。

## 失败处理

1. dump、restic 上传、抽样检查或 retention 任一步失败都返回非零；不得把失败定时任务记为成功。
2. 明文 dump 仅存在于命名 Docker volume 的固定文件，脚本通过 trap 删除；异常退出后再次运行前应检查 staging volume。
3. 不对当前数据库执行 `pg_restore --clean`。真正灾难恢复先创建新数据库，验收后再切换应用连接。
4. R2 版本保留、Object Lock、跨区域副本和账号恢复能力属于外部配置，未取得控制台/恢复证据前不能宣称已经具备。
5. restic `check --read-data-subset` 是日常抽检，不等于全量介质读取；应按月/季度安排更高比例或全量检查，并监控费用。

## 最低证据

每次演练记录时间、release SHA、snapshot ID、dump 大小、restic check 结果、恢复表数、Alembic revision、业务对象抽检、执行人和异常。日志不得包含 repository password、R2 Key、模型 Key、平台凭据或正文。
