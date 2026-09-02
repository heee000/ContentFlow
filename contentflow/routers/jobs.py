from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..dependencies import CurrentPrincipal, Principal, require_role
from ..entities import Asset, Campaign, ContentItem, Job, PublishJob, WorkflowRun
from ..pagination import (
    DEFAULT_PAGE_LIMIT,
    PageCursor,
    PageLimit,
    UpdatedAfter,
    paginate,
)
from ..schemas import JobContextResponse, JobResponse


router = APIRouter(prefix="/jobs", tags=["jobs"])
Db = Annotated[Session, Depends(get_db)]
Editor = Annotated[Principal, Depends(require_role("editor"))]


@router.get("", response_model=list[JobResponse])
def list_jobs(
    principal: CurrentPrincipal,
    session: Db,
    response: Response,
    status: str | None = None,
    limit: PageLimit = DEFAULT_PAGE_LIMIT,
    cursor: PageCursor = None,
    updated_after: UpdatedAfter = None,
):
    query = select(Job).where(Job.workspace_id == principal.workspace_id)
    if status:
        query = query.where(Job.status == status)
    if updated_after is not None:
        query = query.where(Job.updated_at > updated_after)
    jobs = paginate(
        session,
        query,
        timestamp_column=Job.updated_at,
        id_column=Job.id,
        limit=limit,
        cursor=cursor,
        response=response,
    )
    run_ids = {
        str(job.payload_json.get("run_id"))
        for job in jobs
        if job.job_type == "workflow.execute" and job.payload_json.get("run_id")
    }
    asset_ids = {
        str(job.payload_json.get("asset_id"))
        for job in jobs
        if job.job_type.startswith("asset.") and job.payload_json.get("asset_id")
    }
    publish_job_ids = {
        str(job.payload_json.get("publish_job_id"))
        for job in jobs
        if job.payload_json.get("publish_job_id")
    }
    runs = (
        {
            item.id: item
            for item in session.scalars(
                select(WorkflowRun).where(
                    WorkflowRun.workspace_id == principal.workspace_id,
                    WorkflowRun.id.in_(run_ids),
                )
            )
        }
        if run_ids
        else {}
    )
    assets = (
        {
            item.id: item
            for item in session.scalars(
                select(Asset).where(
                    Asset.workspace_id == principal.workspace_id,
                    Asset.id.in_(asset_ids),
                )
            )
        }
        if asset_ids
        else {}
    )
    publish_jobs = (
        {
            item.id: item
            for item in session.scalars(
                select(PublishJob).where(
                    PublishJob.workspace_id == principal.workspace_id,
                    PublishJob.id.in_(publish_job_ids),
                )
            )
        }
        if publish_job_ids
        else {}
    )
    content_ids = {
        item.content_item_id for item in assets.values() if item.content_item_id
    } | {item.content_item_id for item in publish_jobs.values()}
    contents = (
        {
            item.id: item
            for item in session.scalars(
                select(ContentItem).where(
                    ContentItem.workspace_id == principal.workspace_id,
                    ContentItem.id.in_(content_ids),
                )
            )
        }
        if content_ids
        else {}
    )
    campaign_ids = {item.campaign_id for item in runs.values()} | {
        item.campaign_id for item in contents.values()
    }
    campaigns = (
        {
            item.id: item
            for item in session.scalars(
                select(Campaign).where(
                    Campaign.workspace_id == principal.workspace_id,
                    Campaign.id.in_(campaign_ids),
                )
            )
        }
        if campaign_ids
        else {}
    )

    responses: list[JobResponse] = []
    for job in jobs:
        payload = dict(job.payload_json or {})
        content = None
        campaign = None
        if job.job_type == "workflow.execute":
            run = runs.get(str(payload.get("run_id") or ""))
            campaign = campaigns.get(run.campaign_id) if run else None
        elif job.job_type.startswith("asset."):
            asset = assets.get(str(payload.get("asset_id") or ""))
            content = contents.get(asset.content_item_id) if asset else None
        elif payload.get("publish_job_id"):
            publish_job = publish_jobs.get(str(payload["publish_job_id"]))
            content = contents.get(publish_job.content_item_id) if publish_job else None
        if content is not None:
            campaign = campaigns.get(content.campaign_id)
        context = JobContextResponse(
            campaign_id=campaign.id if campaign else None,
            campaign_name=campaign.name if campaign else None,
            product_name=campaign.product_name if campaign else None,
            content_item_id=content.id if content else None,
            content_title=content.title if content else None,
            platform=content.platform if content else None,
        )
        responses.append(
            JobResponse.model_validate(job).model_copy(update={"context": context})
        )
    return responses


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
        if publish_job and publish_job.retry_safe:
            raise HTTPException(
                status_code=409,
                detail="请在发布管理中复测渠道并使用安全重试",
            )

    job.status = "retry"
    job.attempts = 0
    job.last_error = None
    return job
