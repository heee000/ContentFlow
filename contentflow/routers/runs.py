from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..db import get_db
from ..dependencies import AppSettings, CurrentPrincipal, Principal, require_role
from ..entities import Campaign, PromptRelease, WorkflowRun
from ..job_queue import enqueue_job
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


@router.get("/campaigns/{campaign_id}/runs", response_model=list[WorkflowRunResponse])
def list_runs(
    campaign_id: str,
    principal: CurrentPrincipal,
    session: Db,
    limit: RunLimit = 20,
):
    get_campaign_or_404(session, principal.workspace_id, campaign_id)
    return list(
        session.scalars(
            select(WorkflowRun)
            .where(
                WorkflowRun.workspace_id == principal.workspace_id,
                WorkflowRun.campaign_id == campaign_id,
            )
            .order_by(WorkflowRun.created_at.desc())
            .limit(limit)
        )
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
):
    campaign: Campaign = get_campaign_or_404(
        session, principal.workspace_id, campaign_id
    )
    if campaign.status == "archived":
        raise HTTPException(status_code=409, detail="归档活动不能生成内容")
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
        **payload.model_dump(),
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
    session.flush()
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
            "style_skill_id": style_skill_snapshot["id"],
            "style_manifest_sha256": style_skill_snapshot["manifest_sha256"],
            "quality_profile": generation_preferences["quality_profile"],
            "image_source": generation_preferences["image_source"],
        },
    )
    return run


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
