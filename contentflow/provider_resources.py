"""Durable per-workspace request admission; units are calls/bytes, not money."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .entities import ProviderInvocation, ProviderInvocationAttempt, Workspace


_MESSAGES = {
    "provider_daily_calls": "工作区当日外部调用额度已用尽，请核对调用账本后等待下一 UTC 日或调整额度",
    "provider_daily_input_bytes": "工作区当日外部请求输入额度已用尽，请核对调用账本并缩小任务",
    "provider_concurrency": "工作区外部请求并发已满，请核对仍在执行的任务后人工重试",
    "provider_request_too_large": "模型请求超过输入大小上限，请缩小 Brief、知识片段或脚本",
    "provider_response_too_large": "模型响应超过大小上限，已停止读取；请核对原调用结果，不要自动重发",
    "embedding_batch_limit": "Embedding 输入超过批次或单条文本限制，请分批或缩短文本",
    "knowledge_chunk_limit": "知识文档分块数超过限制，请拆分文档后重新索引",
}


class ProviderResourceLimitError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(_MESSAGES[code])


def resource_limit_receipt(error: Exception) -> str | None:
    if type(error) is ProviderResourceLimitError and type(error.code) is str and error.code in _MESSAGES:
        return f"[{error.code}] {_MESSAGES[error.code]}"
    return None


@dataclass(frozen=True)
class ProviderResourceLimits:
    daily_calls: int = 1000
    daily_input_bytes: int = 64 * 1024 * 1024
    concurrent_requests: int = 4

    def __post_init__(self):
        if any(type(value) is not int or value < 1 for value in
               (self.daily_calls, self.daily_input_bytes, self.concurrent_requests)):
            raise ValueError("Provider resource limits must be positive integers")

    @classmethod
    def from_settings(cls, settings):
        return cls(settings.workspace_provider_daily_calls,
            settings.workspace_provider_daily_input_bytes, settings.workspace_provider_concurrent_requests)


def provider_resource_usage(session: Session, workspace_id: str, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    start = now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    daily = select(func.count(ProviderInvocationAttempt.id),
        func.coalesce(func.sum(ProviderInvocation.request_bytes), 0)).join(
            ProviderInvocation, ProviderInvocation.id == ProviderInvocationAttempt.invocation_id).where(
                ProviderInvocation.workspace_id == workspace_id,
                ProviderInvocationAttempt.started_at >= start, ProviderInvocationAttempt.started_at < end)
    calls, size = session.execute(daily).one()
    active = session.scalar(select(func.count(ProviderInvocationAttempt.id)).join(
        ProviderInvocation, ProviderInvocation.id == ProviderInvocationAttempt.invocation_id).where(
            ProviderInvocation.workspace_id == workspace_id, ProviderInvocationAttempt.status == "started"))
    return {"period_start": start, "period_end": end, "calls": int(calls),
        "input_bytes": int(size), "active_requests": int(active or 0)}


def admit_provider_request(session: Session, workspace_id: str, request_bytes: int,
                           limits: ProviderResourceLimits) -> datetime:
    # A no-op UPDATE acquires a cross-process write lock on SQLite and a row lock
    # on PostgreSQL. Keep updated_at unchanged: admission isn't a workspace edit.
    # The count, attempt insert, audit and commit share this transaction/lock.
    locked = session.execute(update(Workspace).where(Workspace.id == workspace_id).values(
        id=Workspace.id, updated_at=Workspace.updated_at).execution_options(synchronize_session=False))
    if locked.rowcount != 1:
        raise ValueError("Provider workspace does not exist")
    admitted_at = datetime.now(timezone.utc)
    usage = provider_resource_usage(session, workspace_id, now=admitted_at)
    if usage["calls"] >= limits.daily_calls:
        raise ProviderResourceLimitError("provider_daily_calls")
    if usage["input_bytes"] + request_bytes > limits.daily_input_bytes:
        raise ProviderResourceLimitError("provider_daily_input_bytes")
    if usage["active_requests"] >= limits.concurrent_requests:
        raise ProviderResourceLimitError("provider_concurrency")
    return admitted_at
