from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..db import get_db
from ..dependencies import AppSettings, CurrentPrincipal, Principal, require_role
from ..entities import (
    Asset,
    Campaign,
    ContentItem,
    Job,
    JobManualReview,
    ProviderInvocation,
    ProviderInvocationAttempt,
    PublishJob,
    WorkflowRun,
)
from ..job_queue import utcnow
from ..job_recovery import manual_review_job_types
from ..pagination import (
    DEFAULT_PAGE_LIMIT,
    PageCursor,
    PageLimit,
    UpdatedAfter,
    paginate,
)
from ..schemas import (
    JobContextResponse,
    JobManualReviewAction,
    JobManualReviewResponse,
    JobResponse,
    ProviderInvocationAttemptResponse,
)


router = APIRouter(prefix="/jobs", tags=["jobs"])
Db = Annotated[Session, Depends(get_db)]
Editor = Annotated[Principal, Depends(require_role("editor"))]
Reviewer = Annotated[Principal, Depends(require_role("reviewer"))]


def latest_manual_reviews(
    session: Session,
    jobs: list[Job],
) -> dict[str, JobManualReview]:
    job_ids = [job.id for job in jobs]
    if not job_ids:
        return {}
    latest_requested = (
        select(
            JobManualReview.job_id.label("job_id"),
            func.max(JobManualReview.requested_at).label("requested_at"),
        )
        .where(JobManualReview.job_id.in_(job_ids))
        .group_by(JobManualReview.job_id)
        .subquery()
    )
    reviews: dict[str, JobManualReview] = {}
    for review in session.scalars(
        select(JobManualReview)
        .join(
            latest_requested,
            and_(
                JobManualReview.job_id == latest_requested.c.job_id,
                JobManualReview.requested_at == latest_requested.c.requested_at,
            ),
        )
        .order_by(JobManualReview.id.desc())
    ):
        reviews.setdefault(review.job_id, review)
    return reviews


def job_response(
    job: Job,
    *,
    context: JobContextResponse | None = None,
    manual_review: JobManualReview | None = None,
) -> JobResponse:
    return JobResponse.model_validate(job).model_copy(
        update={
            "context": context or JobContextResponse(),
            "manual_review": (
                JobManualReviewResponse.model_validate(manual_review)
                if manual_review is not None
                else None
            ),
        }
    )


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
    reviews = latest_manual_reviews(session, jobs)

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
            job_response(
                job,
                context=context,
                manual_review=reviews.get(job.id),
            )
        )
    return responses


@router.get(
    "/{job_id}/provider-invocations",
    response_model=list[ProviderInvocationAttemptResponse],
)
def list_job_provider_invocations(
    job_id: str,
    principal: Reviewer,
    session: Db,
    response: Response,
    limit: PageLimit = DEFAULT_PAGE_LIMIT,
    cursor: PageCursor = None,
):
    job = session.scalar(
        select(Job).where(
            Job.id == job_id,
            Job.workspace_id == principal.workspace_id,
        )
    )
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    rows = paginate(
        session,
        select(ProviderInvocationAttempt, ProviderInvocation)
        .join(
            ProviderInvocation,
            ProviderInvocation.id == ProviderInvocationAttempt.invocation_id,
        )
        .where(
            ProviderInvocation.job_id == job.id,
            ProviderInvocation.workspace_id == principal.workspace_id,
        ),
        timestamp_column=ProviderInvocationAttempt.started_at,
        id_column=ProviderInvocationAttempt.id,
        limit=limit,
        cursor=cursor,
        response=response,
        scalar=False,
    )
    return [
        ProviderInvocationAttemptResponse(
            id=attempt.id,
            invocation_id=invocation.id,
            request_key=invocation.request_key,
            entity_type=invocation.entity_type,
            entity_id=invocation.entity_id,
            provider_kind=invocation.provider_kind,
            provider_name=invocation.provider_name,
            model_name=invocation.model_name,
            operation=invocation.operation,
            request_sha256=invocation.request_sha256,
            request_bytes=invocation.request_bytes,
            attempt_number=attempt.attempt_number,
            status=attempt.status,
            idempotency_key_sent=attempt.idempotency_key_sent,
            provider_request_id=attempt.provider_request_id,
            provider_request_id_source=attempt.provider_request_id_source,
            response_sha256=attempt.response_sha256,
            response_bytes=attempt.response_bytes,
            response_model=attempt.response_model,
            usage_source=attempt.usage_source,
            input_tokens=attempt.input_tokens,
            output_tokens=attempt.output_tokens,
            total_tokens=attempt.total_tokens,
            error_type=attempt.error_type,
            started_at=attempt.started_at,
            completed_at=attempt.completed_at,
        )
        for attempt, invocation in rows
    ]


@router.post("/{job_id}/retry", response_model=JobResponse)
def retry_job(
    job_id: str,
    principal: Editor,
    session: Db,
    settings: AppSettings,
):
    job = session.scalar(
        select(Job).where(
            Job.id == job_id,
            Job.workspace_id == principal.workspace_id,
        )
    )
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job.status == "manual_review":
        raise HTTPException(
            status_code=409,
            detail="该任务必须由审核者核对供应商活动后处置",
        )
    if job.status != "failed":
        raise HTTPException(status_code=409, detail="只有失败任务可以重试")
    if job.job_type in manual_review_job_types(settings):
        raise HTTPException(
            status_code=409,
            detail="该任务的供应商结果无法自动确认，不能通过通用入口重试",
        )
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
    return job_response(
        job,
        manual_review=latest_manual_reviews(session, [job]).get(job.id),
    )


@router.post("/{job_id}/manual-review", response_model=JobResponse)
def resolve_manual_review(
    job_id: str,
    payload: JobManualReviewAction,
    principal: Reviewer,
    session: Db,
):
    job_query = select(Job).where(
        Job.id == job_id,
        Job.workspace_id == principal.workspace_id,
    )
    if session.bind and session.bind.dialect.name == "postgresql":
        job_query = job_query.with_for_update()
    job = session.scalar(job_query)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job.status != "manual_review":
        raise HTTPException(status_code=409, detail="该任务当前不在人工核对状态")

    review_query = select(JobManualReview).where(
        JobManualReview.job_id == job.id,
        JobManualReview.workspace_id == principal.workspace_id,
        JobManualReview.resolved_at.is_(None),
    )
    if session.bind and session.bind.dialect.name == "postgresql":
        review_query = review_query.with_for_update()
    review = session.scalar(review_query)
    if review is None:
        raise HTTPException(status_code=409, detail="人工核对记录缺失，禁止处置")

    review.resolved_at = utcnow()
    review.resolved_by_user_id = principal.user_id
    review.provider_checked = True
    review.decision = payload.decision
    review.note = payload.note
    job.locked_by = None
    job.locked_at = None
    if payload.decision == "retry":
        job.status = "retry"
        job.attempts = 0
        job.run_at = utcnow()
        job.last_error = None
    else:
        job.status = "failed"

    record_audit(
        session,
        action="job.manual_review_resolved",
        entity_type="job",
        entity_id=job.id,
        workspace_id=principal.workspace_id,
        actor_user_id=principal.user_id,
        metadata={
            "job_type": job.job_type,
            "reason_code": review.reason_code,
            "decision": payload.decision,
            "provider_checked": True,
        },
    )
    session.flush()
    return job_response(job, manual_review=review)
