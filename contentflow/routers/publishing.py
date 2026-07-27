from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..db import get_db
from ..dependencies import (
    AppSettings,
    CurrentPrincipal,
    Principal,
    require_role,
)
from ..entities import ChannelConnection, ContentItem, PublishJob
from ..job_queue import enqueue_job
from ..knowledge_service import local_path_from_uri
from ..object_storage import build_object_storage
from ..schemas import PublishJobResponse, PublishScheduleRequest


router = APIRouter(prefix="/publishing", tags=["publishing"])
Db = Annotated[Session, Depends(get_db)]
Reviewer = Annotated[Principal, Depends(require_role("reviewer"))]


@router.get("/jobs", response_model=list[PublishJobResponse])
def list_publish_jobs(principal: CurrentPrincipal, session: Db):
    return list(
        session.scalars(
            select(PublishJob)
            .where(PublishJob.workspace_id == principal.workspace_id)
            .order_by(PublishJob.scheduled_at.desc())
        )
    )


@router.post(
    "/jobs",
    response_model=PublishJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def schedule_publish(
    payload: PublishScheduleRequest,
    principal: Reviewer,
    session: Db,
):
    content = session.scalar(
        select(ContentItem).where(
            ContentItem.id == payload.content_item_id,
            ContentItem.workspace_id == principal.workspace_id,
        )
    )
    channel = session.scalar(
        select(ChannelConnection).where(
            ChannelConnection.id == payload.channel_id,
            ChannelConnection.workspace_id == principal.workspace_id,
        )
    )
    if content is None or channel is None:
        raise HTTPException(status_code=404, detail="内容或连接器不存在")
    if content.status != "approved":
        raise HTTPException(status_code=409, detail="内容必须先通过人工审核")
    if channel.platform != content.platform:
        raise HTTPException(status_code=409, detail="内容平台与连接器不匹配")
    if payload.scheduled_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=422, detail="发布时间不能早于当前时间")

    raw_key = (
        f"{principal.workspace_id}:{content.id}:{content.version}:"
        f"{channel.id}:{payload.scheduled_at.isoformat()}"
    )
    idempotency_key = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    existing = session.scalar(
        select(PublishJob).where(PublishJob.idempotency_key == idempotency_key)
    )
    if existing:
        return existing
    publish_job = PublishJob(
        workspace_id=principal.workspace_id,
        content_item_id=content.id,
        channel_id=channel.id,
        scheduled_at=payload.scheduled_at,
        idempotency_key=idempotency_key,
        status="scheduled",
        request_json={"content_version": content.version},
    )
    session.add(publish_job)
    session.flush()
    enqueue_job(
        session,
        job_type="publish.dispatch",
        payload={"publish_job_id": publish_job.id},
        workspace_id=principal.workspace_id,
        idempotency_key=f"publish.dispatch:{publish_job.id}",
        run_at=payload.scheduled_at,
    )
    record_audit(
        session,
        action="publish.schedule",
        entity_type="publish_job",
        entity_id=publish_job.id,
        workspace_id=principal.workspace_id,
        actor_user_id=principal.user_id,
        metadata={
            "content_id": content.id,
            "channel_id": channel.id,
            "scheduled_at": payload.scheduled_at.isoformat(),
        },
    )
    return publish_job


@router.post("/jobs/{publish_job_id}/cancel", response_model=PublishJobResponse)
def cancel_publish(
    publish_job_id: str,
    principal: Reviewer,
    session: Db,
):
    job = session.scalar(
        select(PublishJob).where(
            PublishJob.id == publish_job_id,
            PublishJob.workspace_id == principal.workspace_id,
        )
    )
    if job is None:
        raise HTTPException(status_code=404, detail="发布任务不存在")
    if job.status not in {"scheduled", "queued"}:
        raise HTTPException(status_code=409, detail="当前状态不能取消")
    job.status = "cancelled"
    record_audit(
        session,
        action="publish.cancel",
        entity_type="publish_job",
        entity_id=job.id,
        workspace_id=principal.workspace_id,
        actor_user_id=principal.user_id,
    )
    return job


@router.get("/jobs/{publish_job_id}/artifact")
def download_publish_artifact(
    publish_job_id: str,
    principal: CurrentPrincipal,
    session: Db,
    settings: AppSettings,
):
    job = session.scalar(
        select(PublishJob).where(
            PublishJob.id == publish_job_id,
            PublishJob.workspace_id == principal.workspace_id,
        )
    )
    if job is None:
        raise HTTPException(status_code=404, detail="发布任务不存在")
    if job.status != "exported" or not job.external_url:
        raise HTTPException(status_code=409, detail="该任务没有可下载的导出包")
    if job.external_url.startswith("file:"):
        path = local_path_from_uri(job.external_url)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="导出文件不存在")
        return FileResponse(
            path,
            media_type="application/zip",
            filename=path.name,
        )
    try:
        data = build_object_storage(settings).read(job.external_url)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="导出文件不存在") from error
    return Response(
        content=data,
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f'attachment; filename="contentflow-export-{job.id}.zip"'
            )
        },
    )
