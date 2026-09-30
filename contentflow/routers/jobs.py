from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..asset_operations import lock_asset_for_mutation, require_new_asset_operation
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
from ..job_queue import request_job_manual_review, utcnow
from ..job_recovery import manual_review_job_types
from ..pagination import (
    DEFAULT_PAGE_LIMIT,
    PageCursor,
    PageLimit,
    UpdatedAfter,
    paginate,
)
from ..provider_invocations import provider_invocation_attempt_response_data
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
        provider_invocation_attempt_response_data(attempt, invocation)
        for attempt, invocation in rows
    ]


def lock_job_for_user_action(session: Session, workspace_id: str, job_id: str) -> Job:
    query = select(Job).where(Job.id == job_id, Job.workspace_id == workspace_id)
    observed = session.scalar(query)
    if observed is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if observed.job_type.startswith("asset."):
        asset_id = (observed.payload_json or {}).get("asset_id")
        if not isinstance(asset_id, str) or lock_asset_for_mutation(
            session, workspace_id, asset_id
        ) is None:
            raise HTTPException(status_code=409, detail="素材任务缺少有效的关联素材")
    return session.scalar(
        query.with_for_update().execution_options(populate_existing=True)
    )


@router.post("/{job_id}/request-manual-review", response_model=JobResponse)
def request_asset_manual_review(
    job_id: str, principal: Reviewer, session: Db,
):
    """Escalate a terminal generation/poll failure; never authorizes a retry."""
    job = lock_job_for_user_action(session, principal.workspace_id, job_id)
    if job.job_type not in {"asset.generate", "asset.poll"}:
        raise HTTPException(status_code=409, detail="仅素材生成或轮询失败可从此入口发起核对")
    if job.status == "manual_review":
        return job_response(job, manual_review=latest_manual_reviews(session, [job]).get(job.id))
    if job.status != "failed":
        raise HTTPException(status_code=409, detail="任务尚未终止，不能并发发起人工核对")
    review = request_job_manual_review(
        session, job,
        reason_code="asset_outcome_verification_requested",
        error="素材失败后主动核对供应商结果；尚未授权重新执行",
        source="reviewer_request",
    )
    record_audit(
        session, action="asset.manual_review_requested", entity_type="job",
        entity_id=job.id, workspace_id=principal.workspace_id,
        actor_user_id=principal.user_id,
    )
    return job_response(job, manual_review=review)


@router.post("/{job_id}/retry", response_model=JobResponse)
def retry_job(
    job_id: str,
    principal: Editor,
    session: Db,
    settings: AppSettings,
):
    job = lock_job_for_user_action(session, principal.workspace_id, job_id)
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

    if job.job_type.startswith("asset."):
        asset = session.get(Asset, job.payload_json["asset_id"])
        require_new_asset_operation(session, asset)
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
    job = lock_job_for_user_action(session, principal.workspace_id, job_id)
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
        if job.job_type.startswith("asset."):
            session.flush()
            require_new_asset_operation(
                session, session.get(Asset, job.payload_json["asset_id"]),
                resolving_job_id=job.id,
            )
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
