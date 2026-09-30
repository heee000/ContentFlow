from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..dependencies import CurrentPrincipal
from ..entities import (
    Asset,
    Campaign,
    ContentItem,
    Job,
    PublishJob,
    WorkflowRun,
)


router = APIRouter(prefix="/dashboard", tags=["dashboard"])
Db = Annotated[Session, Depends(get_db)]


@router.get("/summary")
def dashboard_summary(principal: CurrentPrincipal, session: Db):
    workspace_id = principal.workspace_id

    def count(model, *conditions) -> int:
        return int(
            session.scalar(
                select(func.count())
                .select_from(model)
                .where(model.workspace_id == workspace_id, *conditions)
            )
            or 0
        )

    return {
        "campaigns": count(Campaign, Campaign.status != "archived"),
        "runs_active": count(
            WorkflowRun, WorkflowRun.status.in_(["queued", "running"])
        ),
        "contents_needing_review": count(
            ContentItem, ContentItem.status == "needs_review"
        ),
        "assets_processing": count(
            Asset, Asset.status.in_(["pending", "processing"])
        ),
        "publishes_scheduled": count(
            PublishJob, PublishJob.status == "scheduled"
        ),
        "jobs_manual_review": count(Job, Job.status == "manual_review"),
        "jobs_failed": count(Job, Job.status == "failed"),
    }
