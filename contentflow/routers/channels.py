from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..db import get_db
from ..dependencies import AppSettings, CurrentPrincipal, Principal, require_role
from ..entities import ChannelConnection, Job
from ..job_queue import enqueue_job
from ..schemas import ChannelCreate, ChannelResponse, JobResponse
from ..security import encrypt_credentials


router = APIRouter(prefix="/channels", tags=["channels"])
Db = Annotated[Session, Depends(get_db)]
Admin = Annotated[Principal, Depends(require_role("admin"))]


def validate_channel_payload(payload: ChannelCreate) -> str:
    if payload.platform == "xiaohongshu":
        return "export_only"
    if payload.platform == "douyin":
        required_values = {
            "access_token": payload.credentials.get("access_token"),
            "open_id": payload.credentials.get("open_id")
            or payload.config.get("open_id"),
        }
    else:
        required_values = {
            "app_id": payload.credentials.get("app_id"),
            "app_secret": payload.credentials.get("app_secret"),
        }
    missing = sorted(
        key
        for key, value in required_values.items()
        if not isinstance(value, str) or not value.strip()
    )
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
            encrypt_credentials(
                payload.credentials,
                settings.credential_encryption_primary_key,
            )
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
    channel_query = select(ChannelConnection).where(
        ChannelConnection.id == channel_id,
        ChannelConnection.workspace_id == principal.workspace_id,
    )
    if session.bind and session.bind.dialect.name == "postgresql":
        channel_query = channel_query.with_for_update()
    channel = session.scalar(channel_query)
    if channel is None:
        raise HTTPException(status_code=404, detail="连接器不存在")
    if channel.status == "pending_test":
        active_job = session.scalar(
            select(Job)
            .where(
                Job.job_type == "connector.test",
                Job.workspace_id == principal.workspace_id,
                Job.status.in_(["queued", "retry", "running"]),
                Job.payload_json["channel_id"].as_string() == channel.id,
            )
            .order_by(Job.created_at.desc())
        )
        if active_job is not None:
            return active_job
    # A terminal connector test must be retryable. Moving the channel back to
    # pending_test updates its timestamp, which gives the new attempt a fresh
    # idempotency key. Repeated clicks while the same attempt is pending still
    # resolve to the existing queued job.
    channel.status = "pending_test"
    session.flush()
    job = enqueue_job(
        session,
        job_type="connector.test",
        payload={"channel_id": channel.id},
        workspace_id=principal.workspace_id,
        idempotency_key=f"connector.test:{channel.id}:{channel.updated_at.isoformat()}",
    )
    return job
