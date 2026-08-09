from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..db import get_db
from ..dependencies import CurrentPrincipal, Principal, require_role
from ..entities import Campaign, WorkflowRun
from ..job_queue import enqueue_job
from ..schemas import WorkflowRunRequest, WorkflowRunResponse
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
):
    campaign: Campaign = get_campaign_or_404(
        session, principal.workspace_id, campaign_id
    )
    if campaign.status == "archived":
        raise HTTPException(status_code=409, detail="归档活动不能生成内容")
    run = WorkflowRun(
        workspace_id=principal.workspace_id,
        campaign_id=campaign.id,
        status="queued",
        current_stage="queued",
        provider=payload.provider or "configured",
        trace_id=uuid.uuid4().hex,
        request_json=payload.model_dump(),
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
        metadata={"campaign_id": campaign.id},
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

