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
from ..entities import ChannelConnection, ContentItem, Job, PublishJob
from ..job_queue import enqueue_job
from ..knowledge_service import local_path_from_uri
from ..object_storage import build_object_storage
from ..schemas import (
    PublishJobResponse,
    PublishReconcileRequest,
    PublishScheduleRequest,
    PublishScriptResultRequest,
)


router = APIRouter(prefix="/publishing", tags=["publishing"])
Db = Annotated[Session, Depends(get_db)]
Reviewer = Annotated[Principal, Depends(require_role("reviewer"))]


def get_publish_queue_job_for_update(
    session: Session, publish_job_id: str
) -> Job | None:
    query = select(Job).where(
        Job.idempotency_key == f"publish.dispatch:{publish_job_id}"
    )
    if session.bind and session.bind.dialect.name == "postgresql":
        query = query.with_for_update()
    return session.scalar(query)


def get_reconciliation_queue_job_for_update(
    session: Session, publish_job_id: str
) -> Job | None:
    query = select(Job).where(
        Job.idempotency_key == f"publish.reconcile:{publish_job_id}"
    )
    if session.bind and session.bind.dialect.name == "postgresql":
        query = query.with_for_update()
    return session.scalar(query)


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
    if payload.delivery_mode == "manual_export" and channel.platform != "xiaohongshu":
        raise HTTPException(status_code=422, detail="人工导出目前只适用于小红书")
    delivery_mode = payload.delivery_mode
    if delivery_mode == "connector" and channel.status == "export_only":
        delivery_mode = "manual_export"
    if delivery_mode == "connector" and channel.status == "script_only":
        raise HTTPException(
            status_code=409,
            detail="该连接器不支持官方 API，请选择脚本辅助或人工导出",
        )

    raw_key = (
        f"{principal.workspace_id}:{content.id}:{content.version}:"
        f"{channel.id}:{delivery_mode}:{payload.scheduled_at.isoformat()}"
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
        request_json={
            "content_version": content.version,
            "delivery_mode": delivery_mode,
        },
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
            "delivery_mode": delivery_mode,
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
    query = select(PublishJob).where(
        PublishJob.id == publish_job_id,
        PublishJob.workspace_id == principal.workspace_id,
    )
    if session.bind and session.bind.dialect.name == "postgresql":
        query = query.with_for_update()
    job = session.scalar(query)
    if job is None:
        raise HTTPException(status_code=404, detail="发布任务不存在")
    if job.status not in {"scheduled", "queued"}:
        raise HTTPException(status_code=409, detail="当前状态不能取消")
    job.status = "cancelled"
    queue_job = get_publish_queue_job_for_update(session, job.id)
    if queue_job is not None:
        queue_job.status = "succeeded"
        queue_job.result_json = {
            "publish_job_id": job.id,
            "status": "cancelled",
        }
        queue_job.last_error = None
        queue_job.locked_by = None
        queue_job.locked_at = None

    record_audit(
        session,
        action="publish.cancel",
        entity_type="publish_job",
        entity_id=job.id,
        workspace_id=principal.workspace_id,
        actor_user_id=principal.user_id,
        metadata={"queue_job_id": queue_job.id if queue_job else None},
    )
    return job


@router.post(
    "/jobs/{publish_job_id}/reconcile",
    response_model=PublishJobResponse,
)
def reconcile_publish(
    publish_job_id: str,
    payload: PublishReconcileRequest,
    principal: Reviewer,
    session: Db,
):
    query = select(PublishJob).where(
        PublishJob.id == publish_job_id,
        PublishJob.workspace_id == principal.workspace_id,
    )
    if session.bind and session.bind.dialect.name == "postgresql":
        query = query.with_for_update()
    job = session.scalar(query)
    if job is None:
        raise HTTPException(status_code=404, detail="发布任务不存在")
    if job.status not in {"reconciliation_required", "submitted"}:
        raise HTTPException(status_code=409, detail="当前发布任务不需要人工对账")

    dispatch_queue_job = get_publish_queue_job_for_update(session, job.id)
    reconciliation_queue_job = get_reconciliation_queue_job_for_update(
        session,
        job.id,
    )

    reconciled_at = datetime.now(timezone.utc)
    response_json = dict(job.response_json or {})
    response_json["manual_reconciliation"] = {
        "decision": payload.decision,
        "reason": payload.reason,
        "actor_user_id": principal.user_id,
        "reconciled_at": reconciled_at.isoformat(),
        "dispatch_queue_job_id": (
            dispatch_queue_job.id if dispatch_queue_job else None
        ),
        "reconciliation_queue_job_id": (
            reconciliation_queue_job.id if reconciliation_queue_job else None
        ),
    }
    job.response_json = response_json
    if payload.decision == "confirmed_published":
        job.status = "published"
        job.external_id = payload.external_id or job.external_id
        job.external_url = payload.external_url or job.external_url
        job.published_at = reconciled_at
        job.error = None
        if dispatch_queue_job is not None:
            dispatch_queue_job.status = "succeeded"
            dispatch_queue_job.result_json = {
                "publish_job_id": job.id,
                "status": "published",
                "reconciled": True,
            }
            dispatch_queue_job.last_error = None
            dispatch_queue_job.locked_by = None
            dispatch_queue_job.locked_at = None
    else:
        job.status = "failed"
        job.external_id = None
        job.external_url = None
        job.published_at = None
        job.error = f"人工确认平台未发布：{payload.reason}"[:8000]
        if dispatch_queue_job is not None:
            dispatch_queue_job.status = "failed"
            dispatch_queue_job.last_error = job.error
            dispatch_queue_job.locked_by = None
            dispatch_queue_job.locked_at = None
    if reconciliation_queue_job is not None:
        reconciliation_queue_job.status = "succeeded"
        reconciliation_queue_job.result_json = {
            "publish_job_id": job.id,
            "status": job.status,
            "decision": payload.decision,
            "reconciled": "manual",
        }
        reconciliation_queue_job.last_error = None
        reconciliation_queue_job.locked_by = None
        reconciliation_queue_job.locked_at = None

    record_audit(
        session,
        action="publish.reconcile",
        entity_type="publish_job",
        entity_id=job.id,
        workspace_id=principal.workspace_id,
        actor_user_id=principal.user_id,
        metadata={
            "decision": payload.decision,
            "reason": payload.reason,
            "queue_job_id": (
                dispatch_queue_job.id if dispatch_queue_job else None
            ),
            "reconciliation_queue_job_id": (
                reconciliation_queue_job.id if reconciliation_queue_job else None
            ),
        },
    )
    return job


@router.post(
    "/jobs/{publish_job_id}/script-package",
    response_model=PublishJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_script_package(
    publish_job_id: str,
    principal: Reviewer,
    session: Db,
):
    query = select(PublishJob).where(
        PublishJob.id == publish_job_id,
        PublishJob.workspace_id == principal.workspace_id,
    )
    if session.bind and session.bind.dialect.name == "postgresql":
        query = query.with_for_update()
    job = session.scalar(query)
    if job is None:
        raise HTTPException(status_code=404, detail="发布任务不存在")
    previous_status = job.status
    if previous_status in {"publishing", "submitted", "reconciliation_required"}:
        raise HTTPException(
            status_code=409,
            detail="平台结果可能已产生，必须先人工对账，禁止切换脚本以免重复发布",
        )
    if previous_status in {"published", "script_published", "cancelled"}:
        raise HTTPException(status_code=409, detail="当前状态不能切换为脚本发布")
    if previous_status == "script_ready":
        return job
    if previous_status not in {"scheduled", "queued", "failed", "exported"}:
        raise HTTPException(status_code=409, detail="当前状态不能生成脚本发布包")

    queue_job = get_publish_queue_job_for_update(session, job.id)
    if queue_job is not None and queue_job.status == "running":
        raise HTTPException(status_code=409, detail="分发任务正在执行，不能切换发布方式")
    request_json = dict(job.request_json or {})
    request_json["delivery_mode"] = "script"
    request_json["script_requested_by"] = principal.user_id
    request_json["script_requested_at"] = datetime.now(timezone.utc).isoformat()
    job.request_json = request_json
    job.status = "scheduled"
    job.error = None
    job.external_id = None
    job.external_url = None
    if queue_job is None:
        queue_job = enqueue_job(
            session,
            job_type="publish.dispatch",
            payload={"publish_job_id": job.id},
            workspace_id=principal.workspace_id,
            idempotency_key=f"publish.dispatch:{job.id}",
            run_at=datetime.now(timezone.utc),
        )
    else:
        queue_job.status = "retry"
        queue_job.attempts = 0
        queue_job.run_at = datetime.now(timezone.utc)
        queue_job.result_json = {}
        queue_job.last_error = None
        queue_job.locked_by = None
        queue_job.locked_at = None
    record_audit(
        session,
        action="publish.script_requested",
        entity_type="publish_job",
        entity_id=job.id,
        workspace_id=principal.workspace_id,
        actor_user_id=principal.user_id,
        metadata={"queue_job_id": queue_job.id, "previous_status": previous_status},
    )
    return job


@router.post("/jobs/{publish_job_id}/script-result", response_model=PublishJobResponse)
def confirm_script_result(
    publish_job_id: str,
    payload: PublishScriptResultRequest,
    principal: Reviewer,
    session: Db,
):
    query = select(PublishJob).where(
        PublishJob.id == publish_job_id,
        PublishJob.workspace_id == principal.workspace_id,
    )
    if session.bind and session.bind.dialect.name == "postgresql":
        query = query.with_for_update()
    job = session.scalar(query)
    if job is None:
        raise HTTPException(status_code=404, detail="发布任务不存在")
    if job.status != "script_ready" or job.delivery_mode != "script":
        raise HTTPException(status_code=409, detail="只有已生成任务包的脚本发布可以登记结果")
    confirmed_at = datetime.now(timezone.utc)
    response_json = dict(job.response_json or {})
    response_json["script_result"] = {
        "decision": payload.decision,
        "reason": payload.reason,
        "actor_user_id": principal.user_id,
        "confirmed_at": confirmed_at.isoformat(),
    }
    job.response_json = response_json
    if payload.decision == "confirmed_published":
        job.status = "script_published"
        job.external_id = payload.external_id
        job.external_url = payload.external_url
        job.published_at = confirmed_at
        job.error = None
    else:
        job.status = "failed"
        job.external_id = None
        job.external_url = None
        job.published_at = None
        job.error = f"人工确认脚本未发布：{payload.reason}"[:8000]
    record_audit(
        session,
        action="publish.script_result",
        entity_type="publish_job",
        entity_id=job.id,
        workspace_id=principal.workspace_id,
        actor_user_id=principal.user_id,
        metadata={"decision": payload.decision, "reason": payload.reason},
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
    if job.status not in {"exported", "script_ready"} or not job.external_url:
        raise HTTPException(status_code=409, detail="该任务没有可下载的发布包")
    headers = {
        "Content-Disposition": f'attachment; filename="contentflow-publish-{job.id}.zip"'
    }
    package_checksum = str((job.response_json or {}).get("package_sha256") or "")
    if len(package_checksum) == 64 and all(
        character in "0123456789abcdef" for character in package_checksum
    ):
        headers["X-ContentFlow-Artifact-SHA256"] = package_checksum
    if job.external_url.startswith("file:"):
        path = local_path_from_uri(job.external_url)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="发布包文件不存在")
        return FileResponse(path, media_type="application/zip", headers=headers)
    try:
        data = build_object_storage(settings).read(
            job.external_url, max_bytes=settings.max_upload_bytes
        )
    except (FileNotFoundError, ValueError) as error:
        raise HTTPException(status_code=404, detail="发布包文件不存在") from error
    return Response(content=data, media_type="application/zip", headers=headers)
