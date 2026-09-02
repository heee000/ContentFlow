from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..db import get_db
from ..dependencies import AppSettings, CurrentPrincipal, Principal, require_role
from ..entities import Asset, ContentItem, ContentRevision
from ..job_queue import enqueue_job
from ..pagination import (
    DEFAULT_PAGE_LIMIT,
    PageCursor,
    PageLimit,
    UpdatedAfter,
    paginate,
    paginate_sequence,
)
from ..schemas import (
    ContentResponse,
    ContentRevisionResponse,
    ContentUpdate,
    ReviewDecision,
)


router = APIRouter(prefix="/contents", tags=["contents"])
Db = Annotated[Session, Depends(get_db)]
Editor = Annotated[Principal, Depends(require_role("editor"))]
Reviewer = Annotated[Principal, Depends(require_role("reviewer"))]


def get_content_or_404(
    session: Session, workspace_id: str, content_id: str
) -> ContentItem:
    item = session.scalar(
        select(ContentItem).where(
            ContentItem.id == content_id,
            ContentItem.workspace_id == workspace_id,
        )
    )
    if item is None:
        raise HTTPException(status_code=404, detail="内容不存在")
    return item


def get_content_for_update_or_409(
    session: Session,
    workspace_id: str,
    content_id: str,
    expected_version: int,
) -> ContentItem:
    query = select(ContentItem).where(
        ContentItem.id == content_id,
        ContentItem.workspace_id == workspace_id,
    )
    if session.bind and session.bind.dialect.name == "postgresql":
        query = query.with_for_update()
    item = session.scalar(query)
    if item is None:
        raise HTTPException(status_code=404, detail="内容不存在")
    if item.version != expected_version:
        raise HTTPException(
            status_code=409,
            detail=f"内容版本已变化，当前版本为 {item.version}，请刷新后重试",
        )
    return item


@router.get("", response_model=list[ContentResponse])
def list_contents(
    principal: CurrentPrincipal,
    session: Db,
    response: Response,
    campaign_id: str | None = None,
    status: str | None = None,
    platform: str | None = None,
    limit: PageLimit = DEFAULT_PAGE_LIMIT,
    cursor: PageCursor = None,
    updated_after: UpdatedAfter = None,
):
    query = select(ContentItem).where(
        ContentItem.workspace_id == principal.workspace_id
    )
    if campaign_id:
        query = query.where(ContentItem.campaign_id == campaign_id)
    if status:
        query = query.where(ContentItem.status == status)
    if platform:
        query = query.where(ContentItem.platform == platform)
    if updated_after is not None:
        query = query.where(ContentItem.updated_at > updated_after)
    return paginate(
        session,
        query,
        timestamp_column=ContentItem.updated_at,
        id_column=ContentItem.id,
        limit=limit,
        cursor=cursor,
        response=response,
    )


@router.get("/{content_id}", response_model=ContentResponse)
def get_content(content_id: str, principal: CurrentPrincipal, session: Db):
    return get_content_or_404(session, principal.workspace_id, content_id)


@router.get(
    "/{content_id}/revisions",
    response_model=list[ContentRevisionResponse],
)
def list_content_revisions(
    content_id: str,
    principal: CurrentPrincipal,
    session: Db,
    response: Response,
    limit: PageLimit = DEFAULT_PAGE_LIMIT,
    cursor: PageCursor = None,
):
    get_content_or_404(session, principal.workspace_id, content_id)
    return paginate_sequence(
        session,
        select(ContentRevision).where(
            ContentRevision.content_item_id == content_id,
            ContentRevision.workspace_id == principal.workspace_id,
        ),
        sequence_column=ContentRevision.version,
        id_column=ContentRevision.id,
        limit=limit,
        cursor=cursor,
        response=response,
    )


@router.patch("/{content_id}", response_model=ContentResponse)
def update_content(
    content_id: str,
    payload: ContentUpdate,
    principal: Editor,
    session: Db,
):
    item = get_content_for_update_or_409(
        session,
        principal.workspace_id,
        content_id,
        payload.expected_version,
    )
    updates = payload.model_dump(exclude_unset=True, exclude={"expected_version"})
    if not updates:
        return item
    for field, value in updates.items():
        setattr(item, field, value)
    item.version += 1
    item.status = "needs_review"
    item.approved_by = None
    item.approved_at = None
    review = dict(item.review_json or {})
    review["last_human_edit_by"] = principal.user_id
    review["last_human_edit_at"] = datetime.now(timezone.utc).isoformat()
    item.review_json = review
    existing_assets = list(
        session.scalars(select(Asset).where(Asset.content_item_id == item.id))
    )
    for asset in existing_assets:
        if asset.status == "stale":
            continue
        asset.status = "stale"
        metadata = dict(asset.metadata_json or {})
        metadata["content_version"] = item.version
        session.add(
            Asset(
                workspace_id=item.workspace_id,
                content_item_id=item.id,
                kind=asset.kind,
                provider=asset.provider,
                status="planned",
                prompt=(
                    f"{item.title}\n{item.body}\n"
                    f"视觉要求：{metadata.get('model_input') or asset.prompt or ''}"
                ),
                metadata_json=metadata,
            )
        )
    session.add(
        ContentRevision(
            workspace_id=item.workspace_id,
            content_item_id=item.id,
            version=item.version,
            title=item.title,
            body=item.body,
            hashtags=list(item.hashtags),
            call_to_action=item.call_to_action,
            layout_json=dict(item.layout_json or {}),
            generation_json={
                **dict(item.generation_json or {}),
                "last_human_edit_by": principal.user_id,
                "last_human_edit_at": datetime.now(timezone.utc).isoformat(),
            },
            changed_by=principal.user_id,
            change_reason="human_edit",
        )
    )
    record_audit(
        session,
        action="content.update",
        entity_type="content_item",
        entity_id=item.id,
        workspace_id=principal.workspace_id,
        actor_user_id=principal.user_id,
        metadata={"changed_fields": sorted(updates), "version": item.version},
    )
    return item


@router.post("/{content_id}/review", response_model=ContentResponse)
def review_content(
    content_id: str,
    payload: ReviewDecision,
    principal: Reviewer,
    session: Db,
    settings: AppSettings,
):
    item = get_content_for_update_or_409(
        session,
        principal.workspace_id,
        content_id,
        payload.expected_version,
    )
    if item.status not in {"needs_review", "blocked"}:
        raise HTTPException(
            status_code=409,
            detail="只有待审核或规则拦截的内容可以审核",
        )
    now = datetime.now(timezone.utc)
    review = dict(item.review_json or {})
    review["human_decision"] = payload.decision
    review["human_reason"] = payload.reason
    review["human_reviewer_id"] = principal.user_id
    review["human_reviewed_at"] = now.isoformat()
    item.review_json = review
    if payload.decision == "approve":
        item.status = "approved"
        item.approved_by = principal.user_id
        item.approved_at = now
        assets = list(
            session.scalars(
                select(Asset).where(
                    Asset.content_item_id == item.id,
                    Asset.status.in_(["planned", "failed"]),
                )
            )
        )
        for asset in assets:
            requested_provider = asset.provider
            if requested_provider == "openverse":
                asset.status = "queued"
                asset.error = None
                enqueue_job(
                    session,
                    job_type="asset.search",
                    payload={"asset_id": asset.id},
                    workspace_id=principal.workspace_id,
                    idempotency_key=(
                        f"asset.search:{asset.id}:content-v{item.version}"
                    ),
                )
                continue
            if requested_provider == "configured-image-generation":
                provider = settings.image_provider
            elif requested_provider == "configured-video-generation":
                provider = settings.video_provider
            else:
                provider = (
                    settings.image_provider
                    if asset.kind == "image"
                    else settings.video_provider
                )
            asset.provider = provider
            asset.error = None
            if provider == "manual":
                if requested_provider in {
                    "configured-image-generation",
                    "configured-video-generation",
                }:
                    asset.provider = requested_provider
                    asset.status = "failed"
                    asset.error = (
                        "活动要求 AI 生成素材，但当前环境未配置对应生成 Provider"
                    )
                    asset.metadata_json = {
                        **(asset.metadata_json or {}),
                        "provider_configuration_required": True,
                    }
                else:
                    asset.status = "awaiting_upload"
                    asset.metadata_json = {
                        **(asset.metadata_json or {}),
                        "manual_upload_required": True,
                    }
                continue
            asset.status = "queued"
            enqueue_job(
                session,
                job_type="asset.generate",
                payload={"asset_id": asset.id},
                workspace_id=principal.workspace_id,
                idempotency_key=(f"asset.generate:{asset.id}:content-v{item.version}"),
            )
    else:
        item.status = "rejected"
        item.approved_by = None
        item.approved_at = None
    record_audit(
        session,
        action=f"content.{payload.decision}",
        entity_type="content_item",
        entity_id=item.id,
        workspace_id=principal.workspace_id,
        actor_user_id=principal.user_id,
        metadata={"reason": payload.reason, "version": item.version},
    )
    return item
