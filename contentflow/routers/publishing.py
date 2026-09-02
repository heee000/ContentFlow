from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
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
from ..object_storage import build_object_storage
from ..pagination import (
    DEFAULT_PAGE_LIMIT,
    PageCursor,
    PageLimit,
    UpdatedAfter,
    paginate,
)
from ..schemas import (
    PublishJobResponse,
    PublishReconcileRequest,
    PublishScheduleRequest,
)
from ..storage_ledger import request_storage_deletion


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
def list_publish_jobs(
    principal: CurrentPrincipal,
    session: Db,
    response: Response,
    limit: PageLimit = DEFAULT_PAGE_LIMIT,
    cursor: PageCursor = None,
    updated_after: UpdatedAfter = None,
):
    query = select(PublishJob).where(
        PublishJob.workspace_id == principal.workspace_id
    )
    if updated_after is not None:
        query = query.where(PublishJob.updated_at > updated_after)
    return paginate(
        session,
        query,
        timestamp_column=PublishJob.updated_at,
        id_column=PublishJob.id,
        limit=limit,
        cursor=cursor,
        response=response,
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
    requested_at = datetime.now(timezone.utc)
    if payload.publish_now:
        if payload.scheduled_at is not None:
            raise HTTPException(
                status_code=422,
                detail="立即发布不能同时填写计划时间",
            )
        scheduled_at = requested_at
        publish_timing = "immediate"
    else:
        if payload.scheduled_at is None:
            raise HTTPException(status_code=422, detail="定时发布必须填写计划时间")
        if payload.scheduled_at.tzinfo is None:
            raise HTTPException(status_code=422, detail="计划时间必须包含时区")
        scheduled_at = payload.scheduled_at.astimezone(timezone.utc)
        if scheduled_at < requested_at:
            raise HTTPException(status_code=422, detail="发布时间不能早于当前时间")
        publish_timing = "scheduled"
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
        f"{channel.id}:{delivery_mode}:"
        f"{payload.request_id or scheduled_at.isoformat()}"
    )
    idempotency_key = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    existing = session.scalar(
        select(PublishJob).where(PublishJob.idempotency_key == idempotency_key)
    )
    if existing:
        return existing
    if delivery_mode == "connector" and channel.status != "connected":
        raise HTTPException(
            status_code=409,
            detail="官方 API 发布要求先通过平台连接测试",
        )
    publish_job = PublishJob(
        workspace_id=principal.workspace_id,
        content_item_id=content.id,
        channel_id=channel.id,
        scheduled_at=scheduled_at,
        idempotency_key=idempotency_key,
        status="queued" if publish_timing == "immediate" else "scheduled",
        request_json={
            "content_version": content.version,
            "delivery_mode": delivery_mode,
            "publish_timing": publish_timing,
            "request_id": payload.request_id,
            "script_requested_by": principal.user_id,
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
        run_at=scheduled_at,
    )
    record_audit(
        session,
        action=(
            "publish.immediate"
            if publish_timing == "immediate"
            else "publish.schedule"
        ),
        entity_type="publish_job",
        entity_id=publish_job.id,
        workspace_id=principal.workspace_id,
        actor_user_id=principal.user_id,
        metadata={
            "content_id": content.id,
            "channel_id": channel.id,
            "delivery_mode": delivery_mode,
            "scheduled_at": scheduled_at.isoformat(),
            "publish_timing": publish_timing,
        },
    )
    return publish_job


@router.post(
    "/jobs/{publish_job_id}/retry",
    response_model=PublishJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_publish_safely(
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
    if not job.retry_safe:
        raise HTTPException(
            status_code=409,
            detail="只有明确停在外部写入前的失败任务才能安全重试",
        )

    content = session.get(ContentItem, job.content_item_id)
    channel = session.get(ChannelConnection, job.channel_id)
    if content is None or channel is None:
        raise HTTPException(status_code=409, detail="内容或连接器已不存在")
    if content.status != "approved":
        raise HTTPException(status_code=409, detail="内容必须保持人工审核通过")
    if content.version != int((job.request_json or {}).get("content_version", 0)):
        raise HTTPException(status_code=409, detail="内容版本已变化，请重新审核并创建发布任务")
    if job.delivery_mode == "connector" and channel.status != "connected":
        raise HTTPException(
            status_code=409,
            detail="请先重新测试平台连接，确认恢复 connected 后再安全重试",
        )

    queue_job = get_publish_queue_job_for_update(session, job.id)
    if queue_job is not None and queue_job.status == "running":
        raise HTTPException(status_code=409, detail="分发任务正在执行，不能重复重试")

    now = datetime.now(timezone.utc)
    response_json = dict(job.response_json or {})
    failure = response_json.pop("dispatch_failure", None)
    history = list(response_json.get("dispatch_failure_history") or [])
    if isinstance(failure, dict):
        history.append(failure)
    if history:
        response_json["dispatch_failure_history"] = history[-20:]
    request_json = dict(job.request_json or {})
    request_json.pop("dispatch_token", None)
    request_json.pop("dispatch_started_at", None)
    request_json["publish_timing"] = "immediate"
    request_json["safe_retry_count"] = int(
        request_json.get("safe_retry_count") or 0
    ) + 1
    request_json["safe_retry_requested_at"] = now.isoformat()
    request_json["safe_retry_requested_by"] = principal.user_id

    job.request_json = request_json
    job.response_json = response_json
    job.status = "queued"
    job.scheduled_at = now
    job.error = None
    job.external_id = None
    job.external_url = None
    job.published_at = None
    if queue_job is None:
        queue_job = enqueue_job(
            session,
            job_type="publish.dispatch",
            payload={"publish_job_id": job.id},
            workspace_id=principal.workspace_id,
            idempotency_key=f"publish.dispatch:{job.id}",
            run_at=now,
        )
    else:
        queue_job.status = "retry"
        queue_job.attempts = 0
        queue_job.run_at = now
        queue_job.result_json = {}
        queue_job.last_error = None
        queue_job.locked_by = None
        queue_job.locked_at = None

    record_audit(
        session,
        action="publish.retry_safe",
        entity_type="publish_job",
        entity_id=job.id,
        workspace_id=principal.workspace_id,
        actor_user_id=principal.user_id,
        metadata={
            "queue_job_id": queue_job.id,
            "failure_stage": failure.get("stage")
            if isinstance(failure, dict)
            else None,
            "safe_retry_count": request_json["safe_retry_count"],
        },
    )
    return job


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
    queue_job = get_publish_queue_job_for_update(session, job.id)
    if queue_job is not None and queue_job.status == "running":
        raise HTTPException(status_code=409, detail="分发任务已开始执行，不能取消")
    job.status = "cancelled"
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
            "queue_job_id": (dispatch_queue_job.id if dispatch_queue_job else None),
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
    settings: AppSettings,
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
    response_json = dict(job.response_json or {})
    expired_attempt = job.script_confirmation_expired
    if previous_status in {"publishing", "submitted", "reconciliation_required"}:
        raise HTTPException(
            status_code=409,
            detail="平台结果可能已产生，必须先人工对账，禁止切换脚本以免重复发布",
        )
    if previous_status in {
        "published",
        "script_published",
        "cancelled",
    }:
        raise HTTPException(status_code=409, detail="当前状态不能切换为脚本发布")
    if (
        previous_status in {"script_ready", "script_confirmation_pending"}
        and not expired_attempt
    ):
        return job
    if previous_status not in {
        "scheduled",
        "queued",
        "failed",
        "exported",
        "script_ready",
        "script_confirmation_pending",
    }:
        raise HTTPException(status_code=409, detail="当前状态不能生成脚本发布包")

    queue_job = get_publish_queue_job_for_update(session, job.id)
    if queue_job is not None and queue_job.status == "running":
        raise HTTPException(
            status_code=409, detail="分发任务正在执行，不能切换发布方式"
        )
    expired_package_uri = response_json.get("package_uri") if expired_attempt else None
    request_json = dict(job.request_json or {})
    request_json["delivery_mode"] = "script"
    request_json["script_requested_by"] = principal.user_id
    request_json["script_requested_at"] = datetime.now(timezone.utc).isoformat()
    if expired_attempt:
        request_json["previous_script_attempt_id"] = response_json.get(
            "script_attempt_id"
        )
        request_json["previous_script_attempt_expired_at"] = response_json.get(
            "script_confirmation_expires_at"
        )
    job.request_json = request_json
    job.status = "scheduled"
    job.response_json = {}
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
    if expired_attempt:
        record_audit(
            session,
            action="publish.script_attempt_expired",
            entity_type="publish_job",
            entity_id=job.id,
            workspace_id=principal.workspace_id,
            actor_user_id=principal.user_id,
            metadata={
                "script_attempt_id": response_json.get("script_attempt_id"),
                "package_sha256": response_json.get("package_sha256"),
                "expired_at": response_json.get("script_confirmation_expires_at"),
            },
        )
    record_audit(
        session,
        action="publish.script_requested",
        entity_type="publish_job",
        entity_id=job.id,
        workspace_id=principal.workspace_id,
        actor_user_id=principal.user_id,
        metadata={
            "queue_job_id": queue_job.id,
            "previous_status": previous_status,
            "expired_attempt_replaced": expired_attempt,
        },
    )
    if isinstance(expired_package_uri, str):
        _allocation, cleanup_job = request_storage_deletion(
            session,
            settings=settings,
            workspace_id=principal.workspace_id,
            storage_uri=expired_package_uri,
            owner_type="publish_job",
            owner_id=(
                f"{job.id}:{response_json.get('script_attempt_id') or 'legacy-attempt'}"
            ),
            category="script-publish",
            filename=expired_package_uri.rsplit("/", 1)[-1] or "script-package.zip",
            size_bytes=response_json.get("size_bytes"),
            checksum=response_json.get("package_sha256"),
            mime_type="application/zip",
        )
        record_audit(
            session,
            action="publish.script_package_cleanup_requested",
            entity_type="publish_job",
            entity_id=job.id,
            workspace_id=principal.workspace_id,
            actor_user_id=principal.user_id,
            metadata={
                "cleanup_job_id": cleanup_job.id if cleanup_job is not None else None,
                "shared_legacy_object_retained": cleanup_job is None,
            },
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
    response_json = dict(job.response_json or {})
    if job.delivery_mode == "script":
        if job.script_confirmation_expired:
            raise HTTPException(
                status_code=409,
                detail="脚本发布包已过期，请生成新的脚本尝试",
            )
        artifact_uri = response_json.get("package_uri")
        allowed_statuses = {
            "script_ready",
            "script_confirmation_pending",
            "script_published",
            "failed",
        }
    else:
        artifact_uri = job.external_url
        allowed_statuses = {"exported"}
    if job.status not in allowed_statuses or not isinstance(artifact_uri, str):
        raise HTTPException(status_code=409, detail="该任务没有可下载的发布包")
    headers = {
        "Content-Disposition": f'attachment; filename="contentflow-publish-{job.id}.zip"'
    }
    package_checksum = str(response_json.get("package_sha256") or "")
    if len(package_checksum) == 64 and all(
        character in "0123456789abcdef" for character in package_checksum
    ):
        headers["X-ContentFlow-Artifact-SHA256"] = package_checksum
    try:
        data = build_object_storage(settings).read(
            artifact_uri, max_bytes=settings.max_upload_bytes
        )
    except (FileNotFoundError, OSError, ValueError) as error:
        raise HTTPException(status_code=404, detail="发布包文件不存在") from error
    if package_checksum and hashlib.sha256(data).hexdigest() != package_checksum:
        raise HTTPException(status_code=409, detail="发布包完整性校验失败")
    return Response(content=data, media_type="application/zip", headers=headers)
