from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..db import get_db
from ..dependencies import AppSettings, CurrentPrincipal, Principal, require_role
from ..entities import ChannelConnection
from ..job_queue import enqueue_job
from ..schemas import ChannelCreate, ChannelResponse, JobResponse
from ..security import encrypt_credentials


router = APIRouter(prefix="/channels", tags=["channels"])
Db = Annotated[Session, Depends(get_db)]
Admin = Annotated[Principal, Depends(require_role("admin"))]


def validate_channel_payload(payload: ChannelCreate) -> str:
    if payload.platform == "xiaohongshu":
        return "export_only"
    required = {
        "douyin": {"access_token"},
        "wechat": {"app_id", "app_secret"},
    }[payload.platform]
    missing = sorted(required - set(payload.credentials))
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"{payload.platform} 缺少凭据字段: {', '.join(missing)}",
        )
    return "pending_test"


@router.get("", response_model=list[ChannelResponse])
def list_channels(principal: CurrentPrincipal, session: Db):
    return list(
        session.scalars(
            select(ChannelConnection)
            .where(ChannelConnection.workspace_id == principal.workspace_id)
            .order_by(ChannelConnection.created_at.desc())
        )
    )


@router.post("", response_model=ChannelResponse, status_code=status.HTTP_201_CREATED)
def create_channel(
    payload: ChannelCreate,
    principal: Admin,
    session: Db,
    settings: AppSettings,
):
    initial_status = validate_channel_payload(payload)
    channel = ChannelConnection(
        workspace_id=principal.workspace_id,
        platform=payload.platform,
        display_name=payload.display_name,
        status=initial_status,
        credential_ciphertext=(
            encrypt_credentials(payload.credentials, settings.secret_key)
            if payload.credentials
            else None
        ),
        config_json=payload.config,
    )
    session.add(channel)
    session.flush()
    record_audit(
        session,
        action="channel.create",
        entity_type="channel_connection",
        entity_id=channel.id,
        workspace_id=principal.workspace_id,
        actor_user_id=principal.user_id,
        metadata={
            "platform": channel.platform,
            "display_name": channel.display_name,
        },
    )
    return channel


@router.post("/{channel_id}/test", response_model=JobResponse, status_code=202)
def test_channel(
    channel_id: str,
    principal: Admin,
    session: Db,
):
    channel = session.scalar(
        select(ChannelConnection).where(
            ChannelConnection.id == channel_id,
            ChannelConnection.workspace_id == principal.workspace_id,
        )
    )
    if channel is None:
        raise HTTPException(status_code=404, detail="连接器不存在")
    job = enqueue_job(
        session,
        job_type="connector.test",
        payload={"channel_id": channel.id},
        workspace_id=principal.workspace_id,
        idempotency_key=f"connector.test:{channel.id}:{channel.updated_at.isoformat()}",
    )
    return job

