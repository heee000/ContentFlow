from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..dependencies import CurrentPrincipal, Principal, require_role
from ..entities import Job, PublishJob
from ..schemas import JobResponse


router = APIRouter(prefix="/jobs", tags=["jobs"])
Db = Annotated[Session, Depends(get_db)]
Editor = Annotated[Principal, Depends(require_role("editor"))]


@router.get("", response_model=list[JobResponse])
def list_jobs(principal: CurrentPrincipal, session: Db, status: str | None = None):
    query = select(Job).where(Job.workspace_id == principal.workspace_id)
    if status:
        query = query.where(Job.status == status)
    return list(session.scalars(query.order_by(Job.created_at.desc()).limit(200)))


@router.post("/{job_id}/retry", response_model=JobResponse)
def retry_job(job_id: str, principal: Editor, session: Db):
    job = session.scalar(
        select(Job).where(
            Job.id == job_id,
            Job.workspace_id == principal.workspace_id,
        )
    )
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job.status != "failed":
        raise HTTPException(status_code=409, detail="只有失败任务可以重试")
    if job.job_type == "publish.dispatch":
        publish_job_id = dict(job.payload_json or {}).get("publish_job_id")
        publish_job = session.scalar(
            select(PublishJob).where(
                PublishJob.id == publish_job_id,
                PublishJob.workspace_id == principal.workspace_id,
            )
        )
        if publish_job and publish_job.status == "reconciliation_required":
            raise HTTPException(
                status_code=409,
                detail="发布结果不确定，请先在发布管理中完成人工对账",
            )

    job.status = "retry"
    job.attempts = 0
    job.last_error = None
    return job

