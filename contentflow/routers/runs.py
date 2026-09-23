from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..db import get_db
from ..dependencies import AppSettings, CurrentPrincipal, Principal, require_role
from ..entities import Campaign, GenerationIntent, PromptRelease, WorkflowRun
from ..generation_intents import GenerationIntentError, accepted_run, generation_targets, request_digest, utc_timestamp
from ..job_queue import enqueue_job
from ..pagination import (
    DEFAULT_PAGE_LIMIT,
    PageCursor,
    UpdatedAfter,
    paginate,
)
from ..prompt_eval import EvalIntegrityError, require_current_passed_eval
from ..prompt_governance import PromptIntegrityError, resolve_active_prompt_set
from ..schemas import WorkflowRunRequest, WorkflowRunResponse
from ..style_skills import resolve_style_skill
from ..workflow_service import campaign_generation_preferences, campaign_to_brief
from .campaigns import get_campaign_or_404


router = APIRouter(tags=["workflow-runs"])
Db = Annotated[Session, Depends(get_db)]
Editor = Annotated[Principal, Depends(require_role("editor"))]
RunLimit = Annotated[int, Query(ge=1, le=100)]
@router.get("/runs", response_model=list[WorkflowRunResponse])
def list_workspace_runs(
    principal: CurrentPrincipal,
    session: Db,
    response: Response,
    limit: RunLimit = DEFAULT_PAGE_LIMIT,
    cursor: PageCursor = None,
    updated_after: UpdatedAfter = None,
):
    query = select(WorkflowRun).where(
        WorkflowRun.workspace_id == principal.workspace_id
    )
    if updated_after is not None:
        query = query.where(WorkflowRun.updated_at > updated_after)
    return paginate(
        session,
        query,
        timestamp_column=WorkflowRun.updated_at,
        id_column=WorkflowRun.id,
        limit=limit,
        cursor=cursor,
        response=response,
    )


@router.get("/campaigns/{campaign_id}/runs", response_model=list[WorkflowRunResponse])
def list_runs(
    campaign_id: str,
    principal: CurrentPrincipal,
    session: Db,
    response: Response,
    limit: RunLimit = 20,
    cursor: PageCursor = None,
    updated_after: UpdatedAfter = None,
):
    get_campaign_or_404(session, principal.workspace_id, campaign_id)
    query = select(WorkflowRun).where(
        WorkflowRun.workspace_id == principal.workspace_id,
        WorkflowRun.campaign_id == campaign_id,
    )
    if updated_after is not None:
        query = query.where(WorkflowRun.updated_at > updated_after)
    return paginate(
        session,
        query,
        timestamp_column=WorkflowRun.updated_at,
        id_column=WorkflowRun.id,
        limit=limit,
        cursor=cursor,
        response=response,
    )


@router.post(
    "/campaigns/{campaign_id}/runs",
    response_model=WorkflowRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_run(
    campaign_id: str,
    payload: WorkflowRunRequest,
    principal: Editor,
    session: Db,
    settings: AppSettings,
    idempotency_key: Annotated[str, Header(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")],
):
    digest = request_digest(campaign_id, payload)
    existing = accepted_run(session, principal.workspace_id, idempotency_key, digest)
    if existing is not None:
        return existing
    campaign = session.scalar(select(Campaign).where(
        Campaign.id == campaign_id, Campaign.workspace_id == principal.workspace_id
    ).with_for_update().execution_options(populate_existing=True))
    # The same intent may have committed while this request waited for the row.
    existing = accepted_run(session, principal.workspace_id, idempotency_key, digest)
    if existing is not None:
        return existing
    if campaign is None:
        raise HTTPException(status_code=404, detail="活动不存在")
    if utc_timestamp(campaign.updated_at) != payload.expected_campaign_updated_at:
        raise GenerationIntentError("Brief 已改变，本次旧请求未创建任务；请刷新后重新确认生成", stale_brief=True)
    if campaign.status == "archived":
        raise HTTPException(status_code=409, detail="归档活动不能生成内容")
    try:
        generation_targets(campaign.platforms, payload.regenerate_platforms)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    try:
        prompt_set = resolve_active_prompt_set(session, principal.workspace_id)
        if not prompt_set.release_id and settings.require_governed_prompts:
            raise ValueError(
                "当前环境要求受治理 Prompt；请先完成 Eval 套件、"
                "Prompt 评测、双人审批与激活"
            )
        if prompt_set.release_id:
            release = session.get(PromptRelease, prompt_set.release_id)
            if release is None or release.workspace_id != principal.workspace_id:
                raise ValueError("工作流关联的 Prompt 版本不存在")
            require_current_passed_eval(
                session,
                release,
                settings,
                payload.provider,
            )
    except (EvalIntegrityError, PromptIntegrityError) as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="当前 Prompt 或 Eval 套件完整性校验失败，已禁止生成",
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    generation_preferences = campaign_generation_preferences(campaign)
    try:
        style_skill_snapshot = resolve_style_skill(
            session,
            principal.workspace_id,
            generation_preferences["style_skill_id"],
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    run_request = {
        **payload.model_dump(mode="json"),
        "generation_request_id": idempotency_key,
        "campaign_brief_snapshot": campaign_to_brief(campaign),
        "generation_preferences": generation_preferences,
        "style_skill_snapshot": style_skill_snapshot,
    }
    run = WorkflowRun(
        workspace_id=principal.workspace_id,
        campaign_id=campaign.id,
        status="queued",
        current_stage="queued",
        provider=payload.provider or "configured",
        trace_id=uuid.uuid4().hex,
        request_json=run_request,
    )
    session.add(run)
    # Flush the owned run before opening a savepoint. This also starts a real
    # outer SQLite write transaction, so releasing a savepoint cannot commit the
    # receipt independently of Job/audit creation.
    session.flush()
    try:
        with session.begin_nested():
            session.add(GenerationIntent(workspace_id=principal.workspace_id,
                request_id=idempotency_key, request_sha256=digest, run_id=run.id,
                requested_by=principal.user_id))
            session.flush()
    except IntegrityError:
        # Only discard this unaccepted run, never roll back the caller's whole
        # transaction. A different campaign can race for the same workspace key.
        session.delete(run)
        session.flush()
        existing = accepted_run(session, principal.workspace_id, idempotency_key, digest)
        if existing is not None:
            return existing
        raise
    enqueue_job(
        session,
        job_type="workflow.execute",
        payload={"run_id": run.id},
        workspace_id=principal.workspace_id,
        idempotency_key=f"workflow.execute:{run.id}",
    )
    record_audit(
        session,
        action="workflow.enqueue",
        entity_type="workflow_run",
        entity_id=run.id,
        workspace_id=principal.workspace_id,
        actor_user_id=principal.user_id,
        metadata={
            "campaign_id": campaign.id,
            "generation_request_id": idempotency_key,
            "style_skill_id": style_skill_snapshot["id"],
            "style_manifest_sha256": style_skill_snapshot["manifest_sha256"],
            "quality_profile": generation_preferences["quality_profile"],
            "image_source": generation_preferences["image_source"],
        },
    )
    return run


@router.get("/generation-intents/{request_id}", response_model=WorkflowRunResponse)
def get_generation_receipt(request_id: str, principal: CurrentPrincipal, session: Db):
    receipt = session.scalar(select(GenerationIntent).where(
        GenerationIntent.workspace_id == principal.workspace_id,
        GenerationIntent.request_id == request_id))
    if receipt is None:
        raise HTTPException(status_code=404, detail="当前工作区没有该生成接受回执")
    return get_run(receipt.run_id, principal, session)


@router.get("/runs/{run_id}", response_model=WorkflowRunResponse)
def get_run(run_id: str, principal: CurrentPrincipal, session: Db):
    run = session.scalar(
        select(WorkflowRun).where(
            WorkflowRun.id == run_id,
            WorkflowRun.workspace_id == principal.workspace_id,
        )
    )
    if run is None:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    return run
