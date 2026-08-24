from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..db import get_db
from ..dependencies import CurrentPrincipal, Principal, require_role
from ..entities import Campaign
from ..schemas import CampaignCreate, CampaignResponse, CampaignUpdate
from ..style_skills import resolve_style_skill


router = APIRouter(prefix="/campaigns", tags=["campaigns"])
Db = Annotated[Session, Depends(get_db)]
Editor = Annotated[Principal, Depends(require_role("editor"))]


def get_campaign_or_404(
    session: Session, workspace_id: str, campaign_id: str
) -> Campaign:
    campaign = session.scalar(
        select(Campaign).where(
            Campaign.id == campaign_id,
            Campaign.workspace_id == workspace_id,
        )
    )
    if campaign is None:
        raise HTTPException(status_code=404, detail="活动不存在")
    return campaign


@router.get("", response_model=list[CampaignResponse])
def list_campaigns(principal: CurrentPrincipal, session: Db):
    return list(
        session.scalars(
            select(Campaign)
            .where(Campaign.workspace_id == principal.workspace_id)
            .order_by(Campaign.updated_at.desc())
        )
    )


@router.post(
    "",
    response_model=CampaignResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_campaign(payload: CampaignCreate, principal: Editor, session: Db):
    brief = payload.model_dump()
    try:
        resolve_style_skill(session, principal.workspace_id, payload.style_skill_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    campaign = Campaign(
        workspace_id=principal.workspace_id,
        created_by=principal.user_id,
        name=payload.name,
        product_name=payload.product_name,
        objective=payload.objective,
        audience=payload.audience,
        platforms=list(payload.platforms),
        tone=payload.tone,
        brief=brief,
    )
    session.add(campaign)
    session.flush()
    record_audit(
        session,
        action="campaign.create",
        entity_type="campaign",
        entity_id=campaign.id,
        workspace_id=principal.workspace_id,
        actor_user_id=principal.user_id,
        metadata={"platforms": campaign.platforms},
    )
    return campaign


@router.get("/{campaign_id}", response_model=CampaignResponse)
def get_campaign(campaign_id: str, principal: CurrentPrincipal, session: Db):
    return get_campaign_or_404(session, principal.workspace_id, campaign_id)


@router.patch("/{campaign_id}", response_model=CampaignResponse)
def update_campaign(
    campaign_id: str,
    payload: CampaignUpdate,
    principal: Editor,
    session: Db,
):
    campaign = get_campaign_or_404(session, principal.workspace_id, campaign_id)
    updates = payload.model_dump(exclude_unset=True)
    brief_fields = {
        "name",
        "product_name",
        "objective",
        "audience",
        "platforms",
        "tone",
        "city",
        "must_include",
        "forbidden_phrases",
        "call_to_action",
        "product_facts",
        "style_skill_id",
        "style_notes",
        "quality_profile",
        "image_source",
        "image_search_query",
    }
    selected_style = updates.get(
        "style_skill_id", (campaign.brief or {}).get("style_skill_id")
    )
    try:
        resolve_style_skill(session, principal.workspace_id, selected_style)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    for field, value in updates.items():
        if field in {
            "name",
            "product_name",
            "objective",
            "audience",
            "platforms",
            "tone",
            "status",
        }:
            setattr(campaign, field, value)
    merged_brief = dict(campaign.brief or {})
    merged_brief.update(
        {key: value for key, value in updates.items() if key in brief_fields}
    )
    campaign.brief = merged_brief
    record_audit(
        session,
        action="campaign.update",
        entity_type="campaign",
        entity_id=campaign.id,
        workspace_id=principal.workspace_id,
        actor_user_id=principal.user_id,
        metadata={"changed_fields": sorted(updates)},
    )
    return campaign

