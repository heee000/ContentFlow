"""Pin the approved publication payload, not just the text version.

The manifest contains hashes and object identifiers, never channel credentials.
It is an application consistency boundary, not a signature against a DB admin.
"""

from __future__ import annotations

import copy
import base64
import hashlib
import hmac
import json
import re
import time
from typing import Any

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from .entities import Asset, ChannelConnection, ContentItem, PublishJob
from .object_storage import ObjectStorage, is_workspace_storage_uri
from .settings import Settings
from .channel_config import validate_channel_config


MANIFEST_VERSION = 1
PREVIEW_TTL_SECONDS = 900


class PublishManifestConflict(ValueError):
    def __init__(self, message: str, *, code: str = "publish_manifest_conflict"):
        self.code = code
        super().__init__(message)


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def publication_fingerprint(manifest: dict, intent: dict, mode: str) -> str:
    return _digest({"manifest": manifest, "intent": intent, "delivery_mode": mode})


def confirmation_request_digest(payload: dict) -> str:
    return _digest(payload)


def sign_publication_preview(
    fingerprint: str, *, workspace_id: str, user_id: str, secret: str
) -> str:
    raw = json.dumps(
        {
            "v": 1,
            "fingerprint": fingerprint,
            "workspace": workspace_id,
            "user": user_id,
            "expires": int(time.time()) + PREVIEW_TTL_SECONDS,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    encoded = base64.urlsafe_b64encode(raw).rstrip(b"=")
    signature = hmac.new(
        secret.encode(), b"publish-preview-v1:" + encoded, hashlib.sha256
    ).hexdigest()
    return encoded.decode() + "." + signature


def verify_publication_preview(
    token: str, fingerprint: str, *, workspace_id: str, user_id: str, secret: str
):
    try:
        encoded, signature = token.rsplit(".", 1)
        expected = hmac.new(
            secret.encode(), b"publish-preview-v1:" + encoded.encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError()
        record = json.loads(
            base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        )
        if (
            type(record.get("v")) is not int
            or record["v"] != 1
            or record.get("workspace") != workspace_id
            or record.get("user") != user_id
            or type(record.get("expires")) is not int
            or record["expires"] <= time.time()
        ):
            raise ValueError()
        if record.get("fingerprint") != fingerprint:
            raise PublishManifestConflict(
                "预览后正文、素材、渠道或执行选项已变化，请重新预览确认"
            )
    except PublishManifestConflict:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError):
        raise PublishManifestConflict(
            "发布预览已过期或不属于当前账号，请重新预览确认"
        ) from None


def detached_copy(entity):
    """Do not let an ORM expiration reload a different payload after commit."""
    return type(entity)(
        **{
            field.key: copy.deepcopy(getattr(entity, field.key))
            for field in inspect(entity).mapper.column_attrs
        }
    )


def load_release_inputs(
    session: Session,
    *,
    workspace_id: str,
    content_id: str,
    channel_id: str,
    settings: Settings,
) -> tuple[ContentItem, ChannelConnection, list[Asset]]:
    # API content/media edits and candidate downloads lock ContentItem first.
    # A caller may hold its own PublishJob, but never Asset before Content.
    content = session.scalar(
        select(ContentItem)
        .where(ContentItem.id == content_id, ContentItem.workspace_id == workspace_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    channel = session.scalar(
        select(ChannelConnection)
        .where(
            ChannelConnection.id == channel_id,
            ChannelConnection.workspace_id == workspace_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if content is None or channel is None:
        raise PublishManifestConflict("发布内容或连接器不存在")
    validate_channel_config(channel.platform, channel.config_json)
    if content.status != "approved":
        raise PublishManifestConflict("内容必须保持人工审核通过状态")
    if content.platform != channel.platform:
        raise PublishManifestConflict("内容平台与连接器不匹配")
    current = list(
        session.scalars(
            select(Asset)
            .where(
                Asset.workspace_id == workspace_id,
                Asset.content_item_id == content.id,
                Asset.content_version == content.version,
            )
            .order_by(Asset.id)
            .limit(settings.asset_max_items_per_content_version + 1)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    if len(current) > settings.asset_max_items_per_content_version:
        raise PublishManifestConflict("当前内容版本素材数量超过配置上限")
    assets = [
        asset
        for asset in current
        if not (asset.metadata_json or {}).get("candidate_optional")
        or (asset.metadata_json or {}).get("selected")
    ]
    if not assets:
        raise PublishManifestConflict("请先选用并准备好发布素材，再创建发布任务")
    return content, channel, assets


def build_release_manifest(
    content: ContentItem,
    channel: ChannelConnection,
    assets: list[Asset],
    settings: Settings,
) -> dict[str, Any]:
    records = []
    for asset in sorted(assets, key=lambda item: item.id):
        checksum = (asset.metadata_json or {}).get("checksum")
        if (
            asset.status != "ready"
            or not asset.mime_type
            or not isinstance(checksum, str)
            or not re.fullmatch(r"[0-9a-f]{64}", checksum)
            or type(asset.size_bytes) is not int
            or asset.size_bytes <= 0
            or not is_workspace_storage_uri(
                settings, content.workspace_id, asset.storage_uri
            )
        ):
            raise PublishManifestConflict(
                "素材未就绪或缺少完整性记录，请先准备好素材再创建发布任务"
            )
        if (
            asset.content_item_id != content.id
            or asset.content_version != content.version
        ):
            raise PublishManifestConflict("素材与当前审核版本不一致")
        records.append(
            {
                "asset_id": asset.id,
                "kind": asset.kind,
                "uri": asset.storage_uri,
                "sha256": checksum,
                "size_bytes": asset.size_bytes,
                "mime_type": asset.mime_type,
                "provider": asset.provider,
                "metadata_sha256": _digest(asset.metadata_json or {}),
            }
        )
    if not records:
        raise PublishManifestConflict("没有已选用的发布素材")
    return {
        "schema_version": MANIFEST_VERSION,
        "content_id": content.id,
        "content_version": content.version,
        "content_sha256": _digest(
            {
                "workspace_id": content.workspace_id,
                "platform": content.platform,
                "title": content.title,
                "body": content.body,
                "hashtags": content.hashtags,
                "call_to_action": content.call_to_action,
                "layout": content.layout_json,
                "approved_by": content.approved_by,
            }
        ),
        "channel_id": channel.id,
        # Status changes from connection tests do not change the payload. All
        # configuration and credential rotations do require a new confirmation.
        "channel_sha256": _digest(
            {
                "workspace_id": channel.workspace_id,
                "platform": channel.platform,
                "display_name": channel.display_name,
                "config": channel.config_json,
                "credential_ciphertext": channel.credential_ciphertext,
            }
        ),
        "assets": records,
    }


def require_publish_manifest(
    job: PublishJob,
    content: ContentItem,
    channel: ChannelConnection,
    assets: list[Asset],
    settings: Settings,
) -> dict[str, Any]:
    manifest = (job.request_json or {}).get("release_manifest")
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != MANIFEST_VERSION
    ):
        raise PublishManifestConflict(
            "旧任务缺少最终发布物确认，请重新确认素材并创建发布任务"
        )
    if manifest != build_release_manifest(content, channel, assets, settings):
        raise PublishManifestConflict(
            "正文、选用素材或渠道配置已变化，请重新确认并创建发布任务"
        )
    return manifest


class ManifestObjectStorage:
    """Validate the exact bytes consumed by every API/export/script adapter."""

    def __init__(self, storage: ObjectStorage, manifest: dict[str, Any]):
        self.storage = storage
        self.records = {record["uri"]: record for record in manifest["assets"]}

    def read(self, uri: str, *, max_bytes: int = 100 * 1024 * 1024) -> bytes:
        record = self.records.get(uri)
        if record is None:
            raise PublishManifestConflict("分发试图读取未确认的素材")
        data = self.storage.read(uri, max_bytes=min(max_bytes, record["size_bytes"]))
        if (
            len(data) != record["size_bytes"]
            or hashlib.sha256(data).hexdigest() != record["sha256"]
        ):
            raise PublishManifestConflict(
                "素材文件与发布确认的校验和不一致，已阻止使用"
            )
        return data

    def __getattr__(self, name):
        return getattr(self.storage, name)
