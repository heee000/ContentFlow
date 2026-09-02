from __future__ import annotations

import io
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from fastapi.responses import Response
from sqlalchemy import func, select
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
from ..media_providers import MediaGeneration, MediaProviderError, download_generated_media
from ..object_storage import build_object_storage
from ..pagination import (
    DEFAULT_PAGE_LIMIT,
    PageCursor,
    PageLimit,
    UpdatedAfter,
    paginate,
)
from ..publish_evidence import PublishEvidenceError, normalize_publish_evidence
from ..schemas import (
    AssetCapabilitiesResponse,
    AssetResponse,
    AssetSelectionRequest,
    AssetSourceChangeRequest,
    JobResponse,
)


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
        version = int(asset.content_version)
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=409, detail="素材版本无效") from error
    if version < 1:
        raise HTTPException(status_code=409, detail="素材版本无效")
    return version


@router.get("", response_model=list[AssetResponse])
def list_assets(
    principal: CurrentPrincipal,
    session: Db,
    response: Response,
    content_item_id: str | None = None,
    status_filter: str | None = None,
    limit: PageLimit = DEFAULT_PAGE_LIMIT,
    cursor: PageCursor = None,
    updated_after: UpdatedAfter = None,
):
    query = select(Asset).where(Asset.workspace_id == principal.workspace_id)
    if content_item_id:
        query = query.where(Asset.content_item_id == content_item_id)
    if status_filter:
        query = query.where(Asset.status == status_filter)
    if updated_after is not None:
        query = query.where(Asset.updated_at > updated_after)
    return paginate(
        session,
        query,
        timestamp_column=Asset.updated_at,
        id_column=Asset.id,
        limit=limit,
        cursor=cursor,
        response=response,
    )


@router.get("/capabilities", response_model=AssetCapabilitiesResponse)
def get_asset_capabilities(
    _principal: CurrentPrincipal,
    settings: AppSettings,
):
    return AssetCapabilitiesResponse(
        image_generation_available=settings.image_provider in {"http", "mock"},
        image_search_available=settings.image_search_provider == "openverse",
        video_generation_available=settings.video_provider in {"http", "mock"},
    )


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


@router.post("/{asset_id}/source", response_model=AssetResponse)
def change_asset_source(
    asset_id: str,
    payload: AssetSourceChangeRequest,
    principal: Editor,
    session: Db,
    settings: AppSettings,
):
    asset = get_asset(
        session,
        principal.workspace_id,
        asset_id,
        for_update=True,
    )
    if asset.kind != "image":
        raise HTTPException(status_code=409, detail="当前只支持切换封面图片来源")
    if asset.content_item_id is None:
        raise HTTPException(status_code=409, detail="素材未关联内容")
    content = session.scalar(
        select(ContentItem)
        .where(
            ContentItem.id == asset.content_item_id,
            ContentItem.workspace_id == principal.workspace_id,
        )
        .with_for_update()
    )
    if content is None or content.status != "approved":
        raise HTTPException(status_code=409, detail="内容审核通过后才能切换封面来源")
    if asset_content_version(asset) != content.version:
        raise HTTPException(status_code=409, detail="不能修改旧内容版本的素材")
    if asset.status not in {
        "planned",
        "failed",
        "awaiting_upload",
        "awaiting_selection",
    }:
        raise HTTPException(
            status_code=409,
            detail="素材正在处理或已经就绪，不能并发切换来源",
        )

    metadata = dict(asset.metadata_json or {})
    if metadata.get("candidate_group"):
        raise HTTPException(
            status_code=409,
            detail="混合候选已经分别生成，请直接选择候选素材",
        )
    previous_source = str(metadata.get("media_source") or asset.provider)
    if payload.source == "generate" and settings.image_provider not in {
        "http",
        "mock",
    }:
        raise HTTPException(
            status_code=409,
            detail="AI 图片生成服务尚未配置，可先选择人工上传或开放图库",
        )
    if payload.source == "search" and settings.image_search_provider != "openverse":
        raise HTTPException(status_code=409, detail="开放图库搜索服务尚未配置")

    try:
        source_revision = int(metadata.get("source_revision") or 0) + 1
    except (TypeError, ValueError):
        source_revision = 1
    for key in (
        "candidate_count",
        "license_checked_at",
        "license_checked_by_user_id",
        "license_review_required",
        "provider_configuration_required",
        "search_candidates",
        "search_provider",
        "selected_candidate",
        "source_checksum",
    ):
        metadata.pop(key, None)
    metadata = {
        **metadata,
        "media_source": payload.source,
        "source_revision": source_revision,
        "selected": True,
        "manual_upload_required": payload.source == "manual",
    }
    asset.storage_uri = None
    asset.mime_type = None
    asset.size_bytes = None
    asset.external_task_id = None
    asset.error = None
    asset.metadata_json = metadata

    job_type = None
    if payload.source == "manual":
        asset.provider = "manual"
        asset.status = "awaiting_upload"
    elif payload.source == "search":
        asset.provider = "openverse"
        asset.status = "queued"
        job_type = "asset.search"
    else:
        asset.provider = settings.image_provider
        asset.status = "queued"
        job_type = "asset.generate"

    if job_type is not None:
        enqueue_job(
            session,
            job_type=job_type,
            payload={"asset_id": asset.id},
            workspace_id=principal.workspace_id,
            idempotency_key=(
                f"{job_type}:{asset.id}:source-r{source_revision}:"
                f"content-v{content.version}"
            ),
        )
    record_audit(
        session,
        action="asset.source_change",
        entity_type="asset",
        entity_id=asset.id,
        workspace_id=principal.workspace_id,
        actor_user_id=principal.user_id,
        metadata={
            "content_item_id": content.id,
            "content_version": content.version,
            "from": previous_source,
            "to": payload.source,
            "source_revision": source_revision,
        },
    )
    session.flush()
    return asset


@router.post("/{asset_id}/select", response_model=AssetResponse)
def select_asset_candidate(
    asset_id: str,
    payload: AssetSelectionRequest,
    principal: Editor,
    session: Db,
    settings: AppSettings,
):
    asset = get_asset(
        session,
        principal.workspace_id,
        asset_id,
        for_update=True,
    )
    if asset.content_item_id is None:
        raise HTTPException(status_code=409, detail="素材未关联内容")
    content = session.scalar(
        select(ContentItem)
        .where(
            ContentItem.id == asset.content_item_id,
            ContentItem.workspace_id == principal.workspace_id,
        )
        .with_for_update()
    )
    if content is None or content.status != "approved":
        raise HTTPException(status_code=409, detail="内容必须保持审核通过状态")
    if asset_content_version(asset) != content.version:
        raise HTTPException(status_code=409, detail="不能选择旧内容版本的素材")
    metadata = dict(asset.metadata_json or {})
    selected_storage = None
    selected_storage_uri = None

    if asset.provider == "openverse" and asset.status == "awaiting_selection":
        if not payload.candidate_id:
            raise HTTPException(status_code=422, detail="请选择搜索结果")
        if not payload.acknowledge_license_check:
            raise HTTPException(
                status_code=422,
                detail="使用开放图库前必须核验原始落地页并确认许可",
            )
        candidates = metadata.get("search_candidates")
        if not isinstance(candidates, list):
            raise HTTPException(status_code=409, detail="图片搜索候选元数据无效")
        selected = next(
            (
                candidate
                for candidate in candidates
                if isinstance(candidate, dict)
                and candidate.get("id") == payload.candidate_id
            ),
            None,
        )
        if selected is None:
            raise HTTPException(status_code=404, detail="图片搜索候选不存在")
        generation = MediaGeneration(
            status="ready",
            download_url=str(selected.get("download_url") or ""),
            mime_type="image/jpeg",
            filename="searched-image.jpg",
        )
        try:
            raw = download_generated_media(
                generation,
                max_bytes=settings.max_upload_bytes,
                allowed_hosts=tuple(
                    settings.image_search_download_allowed_hosts
                ),
                require_https=True,
            )
            normalized = normalize_publish_evidence(
                raw,
                filename="searched-image.jpg",
                kind="screenshot",
                max_bytes=settings.max_upload_bytes,
                max_pixels=settings.publish_evidence_max_pixels,
            )
        except (MediaProviderError, PublishEvidenceError, ValueError) as error:
            raise HTTPException(
                status_code=422,
                detail="所选图片无法通过安全下载或图片规范校验",
            ) from error
        storage = build_object_storage(settings)
        stored = storage.put(
            workspace_id=principal.workspace_id,
            category="assets",
            filename=f"openverse-cover.{normalized.extension}",
            stream=io.BytesIO(normalized.data),
            content_type=normalized.mime_type,
        )
        selected_storage = storage
        selected_storage_uri = stored.uri
        asset.status = "ready"
        asset.storage_uri = stored.uri
        asset.mime_type = stored.mime_type
        asset.size_bytes = stored.size_bytes
        asset.error = None
        metadata = {
            **metadata,
            "selected": True,
            "selected_candidate": {
                key: value
                for key, value in selected.items()
                if key != "download_url"
            },
            "license_checked_by_user_id": principal.user_id,
            "license_checked_at": datetime.now(timezone.utc).isoformat(),
            "checksum": stored.checksum,
            "source_checksum": normalized.source_sha256,
        }
        asset.metadata_json = metadata
    elif asset.status == "ready":
        if payload.candidate_id:
            raise HTTPException(
                status_code=422,
                detail="已生成素材不接受搜索候选 ID",
            )
        metadata["selected"] = True
        asset.metadata_json = metadata
    else:
        raise HTTPException(status_code=409, detail="当前素材还不能被选用")

    candidate_group = metadata.get("candidate_group")
    if candidate_group:
        siblings = list(
            session.scalars(
                select(Asset).where(
                    Asset.content_item_id == content.id,
                    Asset.workspace_id == principal.workspace_id,
                    Asset.content_version == content.version,
                    Asset.id != asset.id,
                ).limit(settings.asset_max_items_per_content_version + 1)
            )
        )
        if len(siblings) > settings.asset_max_items_per_content_version:
            raise HTTPException(
                status_code=409,
                detail="当前内容版本素材数量超过配置上限，请先由管理员处理异常数据",
            )
        for sibling in siblings:
            sibling_metadata = dict(sibling.metadata_json or {})
            if sibling_metadata.get("candidate_group") == candidate_group:
                sibling_metadata["selected"] = False
                sibling.metadata_json = sibling_metadata
    record_audit(
        session,
        action="asset.select",
        entity_type="asset",
        entity_id=asset.id,
        workspace_id=principal.workspace_id,
        actor_user_id=principal.user_id,
        metadata={
            "content_item_id": content.id,
            "candidate_group": candidate_group,
            "provider": asset.provider,
        },
    )
    try:
        session.flush()
    except Exception:
        if selected_storage is not None and selected_storage_uri is not None:
            try:
                selected_storage.delete(selected_storage_uri)
            except Exception:
                pass
        raise
    return asset


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
    if asset.status not in {"failed", "planned", "stale"}:
        raise HTTPException(status_code=409, detail="当前素材状态不能重新执行")
    if asset.provider == "openverse":
        job_type = "asset.search"
    else:
        configured_provider = (
            settings.image_provider
            if asset.kind == "image"
            else settings.video_provider
        )
        if asset.provider == "configured-image-generation":
            configured_provider = settings.image_provider
        elif asset.provider == "configured-video-generation":
            configured_provider = settings.video_provider
        if asset.provider in {"manual", "manual-upload"}:
            raise HTTPException(
                status_code=409,
                detail="该素材使用人工上传模式，请上传真实素材而不是重新生成",
            )
        if configured_provider == "manual":
            raise HTTPException(
                status_code=409,
                detail="活动要求 AI 生成素材，但当前环境仍未配置对应 Provider",
            )
        asset.provider = configured_provider
        job_type = "asset.generate"
    asset.status = "queued"
    asset.error = None
    job = enqueue_job(
        session,
        job_type=job_type,
        payload={"asset_id": asset.id},
        workspace_id=principal.workspace_id,
        idempotency_key=f"{job_type}:{asset.id}:retry",
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
                    Asset.content_version == content.version,
                    Asset.kind == target_kind,
                    Asset.status.in_(["awaiting_upload", "planned", "failed"]),
                )
                .limit(settings.asset_max_items_per_content_version + 1)
                .with_for_update()
            )
        )
        if len(candidates) > settings.asset_max_items_per_content_version:
            raise HTTPException(
                status_code=409,
                detail="当前内容版本素材数量超过配置上限，请先由管理员处理异常数据",
            )
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
    if asset is None:
        current_asset_count = session.scalar(
            select(func.count(Asset.id)).where(
                Asset.workspace_id == principal.workspace_id,
                Asset.content_item_id == content.id,
                Asset.content_version == content.version,
            )
        )
        if int(current_asset_count or 0) >= settings.asset_max_items_per_content_version:
            raise HTTPException(
                status_code=409,
                detail=(
                    "当前内容版本素材数量已达到配置上限 "
                    f"({settings.asset_max_items_per_content_version})"
                ),
            )
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
            content_version=content.version,
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
