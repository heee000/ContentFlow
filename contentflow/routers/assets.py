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
from ..filenames import safe_filename
from ..job_queue import enqueue_job
from ..knowledge_service import local_path_from_uri
from ..object_storage import build_object_storage
from ..publish_evidence import PublishEvidenceError, normalize_publish_evidence
from ..schemas import AssetResponse, JobResponse


router = APIRouter(prefix="/assets", tags=["assets"])
Db = Annotated[Session, Depends(get_db)]
Editor = Annotated[Principal, Depends(require_role("editor"))]


def get_asset(
    session: Session,
    workspace_id: str,
    asset_id: str,
    *,
    for_update: bool = False,
) -> Asset:
    query = select(Asset).where(
        Asset.id == asset_id,
        Asset.workspace_id == workspace_id,
    )
    if for_update:
        query = query.with_for_update()
    asset = session.scalar(query)
    if asset is None:
        raise HTTPException(status_code=404, detail="素材不存在")
    return asset


def asset_content_version(asset: Asset) -> int:
    try:
        version = int((asset.metadata_json or {}).get("content_version") or 1)
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=409, detail="素材版本元数据无效") from error
    if version < 1:
        raise HTTPException(status_code=409, detail="素材版本元数据无效")
    return version


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
    settings: AppSettings,
):
    asset = get_asset(session, principal.workspace_id, asset_id)
    configured_provider = (
        settings.image_provider if asset.kind == "image" else settings.video_provider
    )
    if asset.provider in {"manual", "manual-upload"} or configured_provider == "manual":
        raise HTTPException(
            status_code=409,
            detail="该素材使用人工上传模式，请上传真实素材而不是重新生成",
        )
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
    asset_id: str | None = Form(None),
    content_item_id: str | None = Form(None),
    kind: str | None = Form(None),
    file: UploadFile = File(...),
):
    asset: Asset | None = None
    content: ContentItem | None = None
    if asset_id:
        asset = get_asset(session, principal.workspace_id, asset_id, for_update=True)
        if asset.content_item_id is None:
            raise HTTPException(status_code=409, detail="素材任务未关联内容")
        if content_item_id and content_item_id != asset.content_item_id:
            raise HTTPException(status_code=409, detail="素材任务与关联内容不一致")
        if kind and kind != asset.kind:
            raise HTTPException(status_code=409, detail="素材类型与目标任务不一致")
        content = session.scalar(
            select(ContentItem)
            .where(
                ContentItem.id == asset.content_item_id,
                ContentItem.workspace_id == principal.workspace_id,
            )
            .with_for_update()
        )
    else:
        if not content_item_id or not kind:
            raise HTTPException(
                status_code=422,
                detail="请选择待上传素材任务，或同时提供关联内容和素材类型",
            )
        content = session.scalar(
            select(ContentItem)
            .where(
                ContentItem.id == content_item_id,
                ContentItem.workspace_id == principal.workspace_id,
            )
            .with_for_update()
        )
    if content is None:
        raise HTTPException(status_code=404, detail="关联内容不存在")
    if content.status != "approved":
        raise HTTPException(status_code=409, detail="内容必须先通过审核再上传正式素材")

    target_kind = asset.kind if asset is not None else str(kind)
    if target_kind not in {"image", "video", "video_storyboard"}:
        raise HTTPException(status_code=422, detail="不支持的素材类型")
    if asset is None:
        candidates = list(
            session.scalars(
                select(Asset)
                .where(
                    Asset.content_item_id == content.id,
                    Asset.workspace_id == principal.workspace_id,
                    Asset.kind == target_kind,
                    Asset.status.in_(["awaiting_upload", "planned", "failed"]),
                )
                .with_for_update()
            )
        )
        candidates = [
            candidate
            for candidate in candidates
            if asset_content_version(candidate) == content.version
        ]
        if len(candidates) > 1:
            raise HTTPException(
                status_code=409,
                detail="当前版本有多个待上传素材任务，请选择具体任务",
            )
        if candidates:
            asset = candidates[0]
    if asset is not None:
        if asset.status not in {"awaiting_upload", "planned", "failed"}:
            raise HTTPException(status_code=409, detail="当前素材状态不能人工替换")
        asset_version = asset_content_version(asset)
        if asset_version != content.version:
            raise HTTPException(status_code=409, detail="素材任务属于旧内容版本")

    filled_existing_task = asset is not None
    claimed_type = file.content_type or "application/octet-stream"
    data = await file.read(settings.max_upload_bytes + 1)
    if not data:
        raise HTTPException(status_code=400, detail="上传素材为空")
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"素材不能超过 {settings.max_upload_bytes} 字节",
        )
    filename = file.filename or (
        "asset.json" if claimed_type == "application/json" else "asset.bin"
    )
    original_filename = filename
    source_checksum: str | None = None
    if target_kind == "image":
        if not claimed_type.startswith("image/"):
            raise HTTPException(status_code=415, detail="封面任务只接受图片文件")
        try:
            normalized = normalize_publish_evidence(
                data,
                filename=filename,
                kind="screenshot",
                max_bytes=settings.max_upload_bytes,
                max_pixels=settings.publish_evidence_max_pixels,
            )
        except PublishEvidenceError as error:
            raise HTTPException(
                status_code=415,
                detail=f"封面图片无法安全处理: {error}",
            ) from error
        data = normalized.data
        claimed_type = normalized.mime_type
        original_filename = normalized.original_filename
        filename = f"cover.{normalized.extension}"
        source_checksum = normalized.source_sha256
    elif target_kind == "video_storyboard":
        if claimed_type != "application/json":
            raise HTTPException(status_code=415, detail="分镜任务只接受 JSON 文件")
        try:
            normalized = normalize_publish_evidence(
                data,
                filename=filename,
                kind="platform_export",
                max_bytes=settings.max_upload_bytes,
                max_pixels=settings.publish_evidence_max_pixels,
            )
        except PublishEvidenceError as error:
            raise HTTPException(status_code=415, detail="分镜 JSON 无效") from error
        data = normalized.data
        claimed_type = normalized.mime_type
        original_filename = normalized.original_filename
        filename = f"storyboard.{normalized.extension}"
        source_checksum = normalized.source_sha256
    elif target_kind == "video":
        if not claimed_type.startswith("video/"):
            raise HTTPException(status_code=415, detail="视频任务只接受视频文件")
        try:
            filename = safe_filename(filename)
            original_filename = filename
        except ValueError as error:
            raise HTTPException(status_code=422, detail="素材文件名无效") from error
    else:
        raise HTTPException(status_code=415, detail="仅支持图片、视频或分镜 JSON")

    storage = build_object_storage(settings)
    stored = storage.put(
        workspace_id=principal.workspace_id,
        category="assets",
        filename=filename,
        stream=io.BytesIO(data),
        content_type=claimed_type,
    )
    if asset is None:
        asset = Asset(
            workspace_id=principal.workspace_id,
            content_item_id=content.id,
            kind=target_kind,
        )
        session.add(asset)
    asset.provider = "manual-upload"
    asset.status = "ready"
    asset.storage_uri = stored.uri
    asset.mime_type = stored.mime_type
    asset.size_bytes = stored.size_bytes
    asset.external_task_id = None
    asset.error = None
    asset.metadata_json = {
        **(asset.metadata_json or {}),
        "content_version": content.version,
        "checksum": stored.checksum,
        "source_checksum": source_checksum or stored.checksum,
        "original_filename": original_filename,
        "manual_upload_required": False,
    }
    try:
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
                "content_version": content.version,
                "size_bytes": stored.size_bytes,
                "mime_type": stored.mime_type,
                "filled_existing_task": filled_existing_task,
            },
        )
    except Exception:
        try:
            storage.delete(stored.uri)
        except Exception:
            pass
        raise
    return asset
