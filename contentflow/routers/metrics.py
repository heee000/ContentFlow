from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..db import get_db
from ..dependencies import CurrentPrincipal, Principal, require_role
from ..entities import MetricSnapshot, PublishJob
from ..job_queue import enqueue_job
from ..schemas import JobResponse, MetricInput


router = APIRouter(prefix="/metrics", tags=["metrics"])
Db = Annotated[Session, Depends(get_db)]
Editor = Annotated[Principal, Depends(require_role("editor"))]


@router.post("/snapshots", status_code=status.HTTP_201_CREATED)
def ingest_metric(payload: MetricInput, principal: Editor, session: Db):
    publish_job = session.scalar(
        select(PublishJob).where(
            PublishJob.id == payload.publish_job_id,
            PublishJob.workspace_id == principal.workspace_id,
        )
    )
    if publish_job is None:
        raise HTTPException(status_code=404, detail="发布任务不存在")
    snapshot = MetricSnapshot(
        workspace_id=principal.workspace_id,
        publish_job_id=publish_job.id,
        captured_at=payload.captured_at or datetime.now(timezone.utc),
        impressions=payload.impressions,
        clicks=payload.clicks,
        likes=payload.likes,
        comments=payload.comments,
        shares=payload.shares,
        raw_json=payload.raw,
    )
    session.add(snapshot)
    session.flush()
    record_audit(
        session,
        action="metrics.ingest",
        entity_type="metric_snapshot",
        entity_id=snapshot.id,
        workspace_id=principal.workspace_id,
        actor_user_id=principal.user_id,
        metadata={"publish_job_id": publish_job.id},
    )
    return {"id": snapshot.id}


@router.get("/summary")
def metrics_summary(principal: CurrentPrincipal, session: Db):
    rows = session.execute(
        select(
            func.count(MetricSnapshot.id),
            func.sum(MetricSnapshot.impressions),
            func.sum(MetricSnapshot.clicks),
            func.sum(MetricSnapshot.likes),
            func.sum(MetricSnapshot.comments),
            func.sum(MetricSnapshot.shares),
        ).where(MetricSnapshot.workspace_id == principal.workspace_id)
    ).one()
    sample_count = int(rows[0] or 0)
    impressions = float(rows[1] or 0)
    clicks = float(rows[2] or 0)
    engagement = float((rows[3] or 0) + (rows[4] or 0) + (rows[5] or 0))
    click_through_rate = round(clicks / impressions, 4) if impressions else 0
    engagement_rate = round(engagement / impressions, 4) if impressions else 0
    recommendations: list[str] = []
    if sample_count:
        if sample_count < 3:
            recommendations.append(
                "当前样本量较少，先持续回收至少 3 条已发布内容，再比较平台与选题差异。"
            )
        if click_through_rate < 0.03:
            recommendations.append(
                "点击率偏低，下一轮优先做封面、标题和首屏信息的单变量对照。"
            )
        if engagement_rate < 0.05:
            recommendations.append(
                "互动率偏低，可在正文中增加明确问题、收藏理由和更具体的场景信息。"
            )
        if click_through_rate >= 0.03 and engagement_rate >= 0.05:
            recommendations.append(
                "当前点击与互动信号较稳定，保留核心表达，只对选题或素材做单变量迭代。"
            )
    return {
        "sample_count": sample_count,
        "impressions": impressions,
        "clicks": clicks,
        "engagements": engagement,
        "click_through_rate": click_through_rate,
        "engagement_rate": engagement_rate,
        "recommendations": recommendations,
    }


@router.post(
    "/pull/{publish_job_id}",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def pull_platform_metrics(
    publish_job_id: str,
    principal: Editor,
    session: Db,
):
    publish_job = session.scalar(
        select(PublishJob).where(
            PublishJob.id == publish_job_id,
            PublishJob.workspace_id == principal.workspace_id,
        )
    )
    if publish_job is None:
        raise HTTPException(status_code=404, detail="发布任务不存在")
    if not publish_job.external_id:
        raise HTTPException(status_code=409, detail="发布任务没有平台作品 ID")
    captured_bucket = datetime.now(timezone.utc).strftime("%Y%m%d%H")
    return enqueue_job(
        session,
        job_type="metrics.pull",
        payload={"publish_job_id": publish_job.id},
        workspace_id=principal.workspace_id,
        idempotency_key=f"metrics.pull:{publish_job.id}:{captured_bucket}",
    )
