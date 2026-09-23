from __future__ import annotations

from .publication_payload import douyin_text, export_markdown, wechat_article

import io
import json
import math
import zipfile
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from .entities import Asset, ChannelConnection, ContentItem, PublishJob
from .object_storage import ObjectStorage
from .security import decrypt_credentials_with_keys
from .settings import Settings
from .channel_config import OFFICIAL_ORIGINS, validate_channel_config
from .connector_errors import (
    ConnectorPublishError as ConnectorPublishError,
    connector_request,
)


@dataclass(slots=True)
class ConnectorResult:
    status: str
    external_id: str | None = None
    external_url: str | None = None
    response: dict[str, Any] = field(default_factory=dict)


def _required_id(value, *, stage: str, retry_safe: bool = False) -> str:
    # Preserve the existing scalar-ID compatibility without accepting bool,
    # arbitrary objects or numeric credentials as identifiers.
    if (
        stage in {"submit_publish", "create_video"}
        and type(value) is int
        and 0 < value < 2**64
    ):
        return str(value)
    if not isinstance(value, str) or not value.strip() or len(value) > 2048:
        raise ConnectorPublishError(
            stage=stage,
            retry_safe=retry_safe,
            invalidate_channel=stage == "authenticate",
            code="invalid_response",
        )
    return value


class ChannelConnector(Protocol):
    reconciliation_supported: bool

    def test(self) -> ConnectorResult: ...

    def publish(
        self,
        *,
        publish_job: PublishJob,
        content: ContentItem,
        assets: list[Asset],
    ) -> ConnectorResult: ...

    def reconcile(self, publish_job: PublishJob) -> ConnectorResult: ...

    def pull_metrics(self, publish_job: PublishJob) -> dict[str, float]: ...


def _object_name(uri: str) -> str:
    return uri.rsplit("/", 1)[-1] or "asset.bin"


class XiaohongshuExportConnector:
    reconciliation_supported = False

    def __init__(
        self,
        *,
        channel: ChannelConnection,
        storage: ObjectStorage,
    ):
        self.channel = channel
        self.storage = storage

    def test(self) -> ConnectorResult:
        return ConnectorResult(
            status="export_only",
            response={
                "message": "未声明不存在的公开发布能力；审核后生成可下载投放包。",
            },
        )

    def publish(
        self,
        *,
        publish_job: PublishJob,
        content: ContentItem,
        assets: list[Asset],
    ) -> ConnectorResult:
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "content.md",
                export_markdown(content),
            )
            archive.writestr(
                "manifest.json",
                json.dumps(
                    {
                        "platform": "xiaohongshu",
                        "content_item_id": content.id,
                        "content_version": content.version,
                        "human_approved": content.status == "approved",
                        "publish_mode": "manual_export",
                        "asset_count": len(assets),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )
            archive.writestr(
                "layout.json",
                json.dumps(
                    content.layout_json or {},
                    ensure_ascii=False,
                    indent=2,
                ),
            )
            for index, asset in enumerate(assets, start=1):
                if not asset.storage_uri:
                    continue
                name = _object_name(asset.storage_uri)
                data = self.storage.read(asset.storage_uri)
                archive.writestr(f"assets/{index:02d}-{name}", data)
        output.seek(0)
        stored = self.storage.put(
            workspace_id=content.workspace_id,
            category="exports",
            filename=f"xiaohongshu-{publish_job.id}.zip",
            stream=output,
            content_type="application/zip",
        )
        return ConnectorResult(
            status="exported",
            external_id=stored.checksum[:16],
            external_url=stored.uri,
            response={
                "mode": "manual_export",
                "storage_uri": stored.uri,
                "size_bytes": stored.size_bytes,
            },
        )

    def reconcile(self, publish_job: PublishJob) -> ConnectorResult:
        raise NotImplementedError("小红书导出模式没有远端发布状态")

    def pull_metrics(self, publish_job: PublishJob) -> dict[str, float]:
        raise NotImplementedError("小红书导出模式需要人工回填数据")


class DouyinConnector:
    reconciliation_supported = False

    def __init__(
        self,
        *,
        channel: ChannelConnection,
        credentials: dict[str, Any],
        storage: ObjectStorage,
        client: httpx.Client | None = None,
    ):
        validate_channel_config(
            "douyin", channel.config_json if channel.config_json is not None else {}
        )
        self.channel = channel
        self.credentials = credentials
        self.storage = storage
        self.client = client or httpx.Client(timeout=60)
        self.base_url = OFFICIAL_ORIGINS["douyin"]

    def _identity(self) -> tuple[str, str]:
        config = validate_channel_config(
            "douyin",
            self.channel.config_json if self.channel.config_json is not None else {},
        )
        token = str(self.credentials.get("access_token") or "")
        open_id = str(self.credentials.get("open_id") or config.open_id or "")
        if not token or not open_id:
            raise ConnectorPublishError(
                stage="authenticate",
                retry_safe=True,
                invalidate_channel=True,
                code="missing_credentials",
            )
        return token, open_id

    def test(self) -> ConnectorResult:
        token, open_id = self._identity()
        connector_request(
            self.client,
            "POST",
            f"{self.base_url}/oauth/userinfo/",
            stage="test_connection",
            retry_safe=True,
            invalidate_channel=True,
            params={"open_id": open_id, "access_token": token},
        )
        return ConnectorResult(status="connected")

    def publish(
        self,
        *,
        publish_job: PublishJob,
        content: ContentItem,
        assets: list[Asset],
    ) -> ConnectorResult:
        token, open_id = self._identity()
        video = next(
            (
                asset
                for asset in assets
                if asset.mime_type
                and asset.mime_type.startswith("video/")
                and asset.storage_uri
            ),
            None,
        )
        if video is None:
            raise ConnectorPublishError(
                stage="validate_assets", retry_safe=True, code="assets_unavailable"
            )
        filename = _object_name(video.storage_uri or "")
        try:
            data = self.storage.read(video.storage_uri or "")
        except Exception:
            raise ConnectorPublishError(
                stage="read_assets", retry_safe=True, code="assets_unavailable"
            ) from None
        upload_body = connector_request(
            self.client,
            "POST",
            f"{self.base_url}/api/douyin/v1/video/upload/",
            stage="upload_media",
            params={"open_id": open_id, "access_token": token},
            files={"video": (filename, data, video.mime_type)},
        )
        upload_data = upload_body.get("data")
        upload_video = (
            upload_data.get("video") if isinstance(upload_data, dict) else None
        )
        video_id = _required_id(
            upload_video.get("video_id") if isinstance(upload_video, dict) else None,
            stage="upload_media",
        )
        body = connector_request(
            self.client,
            "POST",
            f"{self.base_url}/api/douyin/v1/video/create/",
            stage="create_video",
            params={"open_id": open_id, "access_token": token},
            json={
                "video_id": video_id,
                "text": douyin_text(content),
            },
        )
        created_data = body.get("data")
        item_id = _required_id(
            created_data.get("item_id") if isinstance(created_data, dict) else None,
            stage="create_video",
        )
        return ConnectorResult(
            status="published",
            external_id=str(item_id),
            response={"item_id": item_id},
        )

    def reconcile(self, publish_job: PublishJob) -> ConnectorResult:
        raise NotImplementedError(
            "抖音结果不确定且没有 item_id 时，不支持可靠的自动对账"
        )

    def pull_metrics(self, publish_job: PublishJob) -> dict[str, float]:
        token, open_id = self._identity()
        body = connector_request(
            self.client,
            "POST",
            f"{self.base_url}/api/douyin/v1/video/video_data/",
            stage="pull_metrics",
            params={"open_id": open_id, "access_token": token},
            json={"item_ids": [publish_job.external_id]},
        )
        data = body.get("data")
        rows = data.get("list", []) if isinstance(data, dict) else None
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ConnectorPublishError(
                stage="pull_metrics", retry_safe=False, code="invalid_response"
            )
        row = rows[0] if rows else {}
        values = {
            name: row.get(name, 0)
            for name in ("play_count", "digg_count", "comment_count", "share_count")
        }
        if any(
            type(value) not in {int, float} or not math.isfinite(value) or value < 0
            for value in values.values()
        ):
            raise ConnectorPublishError(
                stage="pull_metrics", retry_safe=False, code="invalid_response"
            )
        return {
            "impressions": float(values["play_count"]),
            "clicks": 0.0,
            "likes": float(values["digg_count"]),
            "comments": float(values["comment_count"]),
            "shares": float(values["share_count"]),
        }


class WechatConnector:
    reconciliation_supported = True

    def __init__(
        self,
        *,
        channel: ChannelConnection,
        credentials: dict[str, Any],
        storage: ObjectStorage,
        client: httpx.Client | None = None,
    ):
        validate_channel_config(
            "wechat", channel.config_json if channel.config_json is not None else {}
        )
        self.channel = channel
        self.credentials = credentials
        self.storage = storage
        self.client = client or httpx.Client(timeout=60)
        self.base_url = OFFICIAL_ORIGINS["wechat"]

    def _access_token(self) -> str:
        validate_channel_config(
            "wechat",
            self.channel.config_json if self.channel.config_json is not None else {},
        )
        body = connector_request(
            self.client,
            "GET",
            f"{self.base_url}/cgi-bin/token",
            stage="authenticate",
            retry_safe=True,
            invalidate_channel=True,
            params={
                "grant_type": "client_credential",
                "appid": self.credentials.get("app_id"),
                "secret": self.credentials.get("app_secret"),
            },
        )
        return _required_id(
            body.get("access_token"), stage="authenticate", retry_safe=True
        )

    def test(self) -> ConnectorResult:
        self._access_token()
        return ConnectorResult(status="connected")

    def publish(
        self,
        *,
        publish_job: PublishJob,
        content: ContentItem,
        assets: list[Asset],
    ) -> ConnectorResult:
        config = validate_channel_config(
            "wechat",
            self.channel.config_json if self.channel.config_json is not None else {},
        )
        token = self._access_token()
        cover = next(
            (
                asset
                for asset in assets
                if asset.mime_type
                and asset.mime_type.startswith("image/")
                and asset.storage_uri
            ),
            None,
        )
        if cover is None:
            raise ConnectorPublishError(
                "公众号草稿需要一张已就绪封面图",
                stage="validate_assets",
                retry_safe=True,
            )
        filename = _object_name(cover.storage_uri or "")
        try:
            data = self.storage.read(cover.storage_uri or "")
        except Exception:
            raise ConnectorPublishError(
                "读取公众号封面失败，尚未执行任何平台写入",
                stage="read_assets",
                retry_safe=True,
            ) from None
        uploaded = connector_request(
            self.client,
            "POST",
            f"{self.base_url}/cgi-bin/material/add_material",
            stage="upload_media",
            params={"access_token": token, "type": "image"},
            files={"media": (filename, data, cover.mime_type)},
        )
        media_id = _required_id(uploaded.get("media_id"), stage="upload_media")
        draft_body = connector_request(
            self.client,
            "POST",
            f"{self.base_url}/cgi-bin/draft/add",
            stage="create_draft",
            params={"access_token": token},
            json={
                "articles": [
                    {
                        **wechat_article(content, self.channel),
                        "thumb_media_id": media_id,
                    }
                ]
            },
        )
        draft_media_id = _required_id(draft_body.get("media_id"), stage="create_draft")
        if config.auto_publish is not True:
            return ConnectorResult(
                status="draft_created",
                external_id=str(draft_media_id),
                response={"media_id": draft_media_id},
            )
        body = connector_request(
            self.client,
            "POST",
            f"{self.base_url}/cgi-bin/freepublish/submit",
            stage="submit_publish",
            params={"access_token": token},
            json={"media_id": draft_media_id},
        )
        publish_id = _required_id(body.get("publish_id"), stage="submit_publish")
        return ConnectorResult(
            status="submitted",
            external_id=str(publish_id),
            response={"publish_id": publish_id},
        )

    def reconcile(self, publish_job: PublishJob) -> ConnectorResult:
        if not publish_job.external_id:
            raise ValueError("公众号自动对账需要 freepublish publish_id")
        token = self._access_token()
        body = connector_request(
            self.client,
            "POST",
            f"{self.base_url}/cgi-bin/freepublish/get",
            stage="query_publish",
            params={"access_token": token},
            json={"publish_id": publish_job.external_id},
        )
        article_id = body.get("article_id")
        publish_status = body.get("publish_status")
        if publish_status is not None and type(publish_status) is not int:
            raise ConnectorPublishError(
                stage="query_publish", retry_safe=False, code="invalid_response"
            )
        safe_response = {"publish_status": publish_status}
        if article_id:
            article_id = _required_id(article_id, stage="query_publish")
            detail = body.get("article_detail") or {}
            if not isinstance(detail, dict):
                raise ConnectorPublishError(
                    stage="query_publish", retry_safe=False, code="invalid_response"
                )
            items = detail.get("item") or []
            if not isinstance(items, list) or any(
                not isinstance(item, dict) for item in items
            ):
                raise ConnectorPublishError(
                    stage="query_publish", retry_safe=False, code="invalid_response"
                )
            first_item = items[0] if items else {}
            external_url = (
                body.get("article_url")
                or detail.get("article_url")
                or first_item.get("article_url")
                or first_item.get("url")
            )
            return ConnectorResult(
                status="published",
                external_id=str(article_id),
                external_url=str(external_url) if external_url else None,
                response={**safe_response, "article_id": article_id},
            )
        return ConnectorResult(
            status="pending",
            external_id=publish_job.external_id,
            response=safe_response,
        )

    def pull_metrics(self, publish_job: PublishJob) -> dict[str, float]:
        raise NotImplementedError("公众号数据能力需按账号权限单独配置")


def build_connector(
    *,
    channel: ChannelConnection,
    settings: Settings,
    storage: ObjectStorage,
) -> ChannelConnector:
    validate_channel_config(
        channel.platform, channel.config_json if channel.config_json is not None else {}
    )
    credentials = (
        decrypt_credentials_with_keys(
            channel.credential_ciphertext,
            settings.credential_decryption_keys,
        )
        if channel.credential_ciphertext
        else {}
    )
    if channel.platform == "xiaohongshu":
        return XiaohongshuExportConnector(channel=channel, storage=storage)
    if channel.platform == "douyin":
        return DouyinConnector(
            channel=channel,
            credentials=credentials,
            storage=storage,
        )
    if channel.platform == "wechat":
        return WechatConnector(
            channel=channel,
            credentials=credentials,
            storage=storage,
        )
    raise ValueError(f"不支持的平台连接器: {channel.platform}")
