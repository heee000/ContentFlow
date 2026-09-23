"""Generation acceptance identities, separate from provider retry semantics."""

from datetime import datetime, timezone
import hashlib
import json

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .entities import GenerationIntent, WorkflowRun


class GenerationIntentError(HTTPException):
    def __init__(self, message: str, *, stale_brief: bool = False):
        self.code = (
            "generation_precondition_failed"
            if stale_brief
            else "generation_intent_conflict"
        )
        super().__init__(status_code=409, detail=message)


def utc_timestamp(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def request_digest(campaign_id: str, payload) -> str:
    request = {
        "protocol": "generation-intent-v1",
        "campaign_id": campaign_id,
        "request": payload.model_dump(mode="json"),
    }
    return hashlib.sha256(
        json.dumps(
            request,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()


def accepted_run(
    session: Session, workspace_id: str, request_id: str, digest: str
) -> WorkflowRun | None:
    receipt = session.scalar(
        select(GenerationIntent).where(
            GenerationIntent.workspace_id == workspace_id,
            GenerationIntent.request_id == request_id,
        )
    )
    if receipt is None:
        return None
    if receipt.request_sha256 != digest:
        raise GenerationIntentError(
            "该生成操作编号已用于不同请求，请核对原任务，不能覆盖或重复生成"
        )
    run = session.get(WorkflowRun, receipt.run_id)
    if run is None or run.workspace_id != workspace_id:
        raise GenerationIntentError("生成回执关联异常，已停止重试，请联系管理员核对")
    return run


def generation_targets(platforms: list[str], requested: list[str]) -> list[str]:
    if (
        not platforms
        or len(set(platforms)) != len(platforms)
        or not set(platforms) <= {"wechat", "douyin", "xiaohongshu"}
    ):
        raise ValueError("活动必须包含非空、不重复的受支持平台")
    if len(set(requested)) != len(requested) or not set(requested) <= set(platforms):
        raise ValueError("重新生成的平台必须是不重复的活动平台子集")
    return [
        platform for platform in platforms if not requested or platform in requested
    ]
