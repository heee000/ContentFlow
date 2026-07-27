from __future__ import annotations

import io
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..audit import record_audit
from ..dependencies import (
    AppSettings,
    CurrentPrincipal,
    Principal,
    require_role,
)
from ..entities import Asset, ContentItem
from ..job_queue import enqueue_job
from ..knowledge_service import local_path_from_uri
from ..object_storage import build_object_storage
from ..schemas import AssetResponse, JobResponse


router = APIRouter(prefix="/assets", tags=["assets"])
Db = Annotated[Session, Depends(get_db)]
Editor = Annotated[Principal, Depends(require_role("editor"))]
MAX_ASSET_BYTES = 100 * 1024 * 1024
ALLOWED_ASSET_MIME_PREFIXES = ("image/", "video/", "application/json")


def get_asset(session: Session, workspace_id: str, asset_id: str) -> Asset:
    asset = session.scalar(
        select(Asset).where(
            Asset.id == asset_id,
            Asset.workspace_id == workspace_id,
        )
    )
    if asset is None:
        raise HTTPException(status_code=404, detail="素材不存在")
    return asset


@router.get("", response_model=list[AssetResponse])
def list_assets(
    principal: CurrentPrincipal,
    session: Db,
    content_item_id: str | None = None,
    status_filter: str | None = None,
):
    query = select(Asset).where(Asset.workspace_id == principal.workspace_id)
    if content_item_id:
        query = query.where(Asset.content_item_id == content_item_id)
    if status_filter:
        query = query.where(Asset.status == status_filter)
    return list(session.scalars(query.order_by(Asset.created_at.desc())))


@router.get("/{asset_id}", response_model=AssetResponse)
def get_asset_detail(asset_id: str, principal: CurrentPrincipal, session: Db):
    return get_asset(session, principal.workspace_id, asset_id)


@router.get("/{asset_id}/download")
def download_asset(
    asset_id: str,
    principal: CurrentPrincipal,
    session: Db,
    settings: AppSettings,
):
    asset = get_asset(session, principal.workspace_id, asset_id)
    if asset.status != "ready" or not asset.storage_uri:
        raise HTTPException(status_code=409, detail="素材尚未生成完成")
    if asset.storage_uri.startswith("file:"):
        path = local_path_from_uri(asset.storage_uri)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="素材文件不存在")
        return FileResponse(
            path,
            media_type=asset.mime_type or "application/octet-stream",
            filename=path.name,
        )
    try:
        data = build_object_storage(settings).read(asset.storage_uri)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="素材文件不存在") from error
    return Response(
        content=data,
        media_type=asset.mime_type or "application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{asset.id}"',
        },
    )


@router.post(
    "/{asset_id}/retry",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_asset(
    asset_id: str,
    principal: Editor,
    session: Db,
):
    asset = get_asset(session, principal.workspace_id, asset_id)
    if asset.status not in {"failed", "planned", "stale"}:
        raise HTTPException(status_code=409, detail="当前素材状态不能重新生成")
    asset.status = "queued"
    asset.error = None
    job = enqueue_job(
        session,
        job_type="asset.generate",
        payload={"asset_id": asset.id},
        workspace_id=principal.workspace_id,
        idempotency_key=f"asset.generate:{asset.id}:retry",
    )
    record_audit(
        session,
        action="asset.retry",
        entity_type="asset",
        entity_id=asset.id,
        workspace_id=principal.workspace_id,
        actor_user_id=principal.user_id,
    )
    return job


@router.post(
    "/upload",
    response_model=AssetResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_asset(
    principal: Editor,
    session: Db,
    settings: AppSettings,
    content_item_id: str = Form(...),
    kind: str = Form(...),
    file: UploadFile = File(...),
):
    content = session.scalar(
        select(ContentItem).where(
            ContentItem.id == content_item_id,
            ContentItem.workspace_id == principal.workspace_id,
        )
    )
    if content is None:
        raise HTTPException(status_code=404, detail="关联内容不存在")
    content_type = file.content_type or "application/octet-stream"
    if not any(
        content_type.startswith(prefix)
        for prefix in ALLOWED_ASSET_MIME_PREFIXES
    ):
        raise HTTPException(status_code=415, detail="仅支持图片、视频或分镜 JSON")
    data = await file.read(MAX_ASSET_BYTES + 1)
    if not data:
        raise HTTPException(status_code=400, detail="上传素材为空")
    if len(data) > MAX_ASSET_BYTES:
        raise HTTPException(status_code=413, detail="素材不能超过 100MB")
    filename = file.filename or (
        "asset.json" if content_type == "application/json" else "asset.bin"
    )
    stored = build_object_storage(settings).put(
        workspace_id=principal.workspace_id,
        category="assets",
        filename=filename,
        stream=io.BytesIO(data),
        content_type=content_type,
    )
    asset = Asset(
        workspace_id=principal.workspace_id,
        content_item_id=content.id,
        kind=kind,
        provider="upload",
        status="ready",
        storage_uri=stored.uri,
        mime_type=stored.mime_type,
        size_bytes=stored.size_bytes,
        metadata_json={
            "content_version": content.version,
            "checksum": stored.checksum,
            "original_filename": filename,
        },
    )
    session.add(asset)
    session.flush()
    record_audit(
        session,
        action="asset.upload",
        entity_type="asset",
        entity_id=asset.id,
        workspace_id=principal.workspace_id,
        actor_user_id=principal.user_id,
        metadata={
            "content_item_id": content.id,
            "size_bytes": stored.size_bytes,
            "mime_type": stored.mime_type,
        },
    )
    return asset
