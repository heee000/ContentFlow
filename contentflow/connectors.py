from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from .entities import Asset, ChannelConnection, ContentItem, PublishJob
from .object_storage import ObjectStorage
from .security import decrypt_credentials_with_keys
from .settings import Settings


@dataclass(slots=True)
class ConnectorResult:
    status: str
    external_id: str | None = None
    external_url: str | None = None
    response: dict[str, Any] = field(default_factory=dict)


class ConnectorPublishError(RuntimeError):
    """A connector failure with an explicit external side-effect boundary."""

    def __init__(
        self,
        message: str,
        *,
        stage: str,
        retry_safe: bool,
        invalidate_channel: bool = False,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.retry_safe = retry_safe
        self.invalidate_channel = invalidate_channel


class ChannelConnector(Protocol):
    reconciliation_supported: bool

    def test(self) -> ConnectorResult:
        ...

    def publish(
        self,
        *,
        publish_job: PublishJob,
        content: ContentItem,
        assets: list[Asset],
    ) -> ConnectorResult:
        ...

    def reconcile(self, publish_job: PublishJob) -> ConnectorResult:
        ...

    def pull_metrics(self, publish_job: PublishJob) -> dict[str, float]:
        ...


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
                "\n".join(
                    [
                        f"# {content.title}",
                        "",
                        content.body,
                        "",
                        " ".join(f"#{tag.lstrip('#')}" for tag in content.hashtags),
                        "",
                        content.call_to_action,
                    ]
                ),
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
        self.channel = channel
        self.credentials = credentials
        self.storage = storage
        self.client = client or httpx.Client(timeout=60)
        self.base_url = str(
            channel.config_json.get("api_base") or "https://open.douyin.com"
        ).rstrip("/")

    def _identity(self) -> tuple[str, str]:
        token = str(self.credentials.get("access_token") or "")
        open_id = str(
            self.credentials.get("open_id")
            or self.channel.config_json.get("open_id")
            or ""
        )
        if not token or not open_id:
            raise ValueError("抖音发布需要 access_token 和 open_id")
        return token, open_id

    def test(self) -> ConnectorResult:
        token, open_id = self._identity()
        response = self.client.post(
            f"{self.base_url}/oauth/userinfo/",
            params={"open_id": open_id, "access_token": token},
        )
        response.raise_for_status()
        body = response.json()
        error_code = (body.get("data") or {}).get("error_code", body.get("extra", {}).get("error_code", 0))
        if error_code:
            raise RuntimeError(f"抖音连接测试失败: {body}")
        return ConnectorResult(status="connected", response=body)

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
            raise ValueError("抖音发布需要已生成的视频素材")
        filename = _object_name(video.storage_uri or "")
        data = self.storage.read(video.storage_uri or "")
        uploaded = self.client.post(
            f"{self.base_url}/api/douyin/v1/video/upload/",
            params={"open_id": open_id, "access_token": token},
            files={"video": (filename, data, video.mime_type)},
        )
        uploaded.raise_for_status()
        upload_body = uploaded.json()
        video_id = ((upload_body.get("data") or {}).get("video") or {}).get(
            "video_id"
        )
        if not video_id:
            raise RuntimeError(f"抖音视频上传未返回 video_id: {upload_body}")
        created = self.client.post(
            f"{self.base_url}/api/douyin/v1/video/create/",
            params={"open_id": open_id, "access_token": token},
            json={
                "video_id": video_id,
                "text": "\n".join(
                    [
                        content.title,
                        content.body,
                        " ".join(f"#{tag.lstrip('#')}" for tag in content.hashtags),
                    ]
                )[:2200],
            },
        )
        created.raise_for_status()
        body = created.json()
        item_id = (body.get("data") or {}).get("item_id")
        if not item_id:
            raise RuntimeError(f"抖音创建作品未返回 item_id: {body}")
        return ConnectorResult(
            status="published",
            external_id=str(item_id),
            response=body,
        )

    def reconcile(self, publish_job: PublishJob) -> ConnectorResult:
        raise NotImplementedError(
            "抖音结果不确定且没有 item_id 时，不支持可靠的自动对账"
        )

    def pull_metrics(self, publish_job: PublishJob) -> dict[str, float]:
        token, open_id = self._identity()
        response = self.client.post(
            f"{self.base_url}/api/douyin/v1/video/video_data/",
            params={"open_id": open_id, "access_token": token},
            json={"item_ids": [publish_job.external_id]},
        )
        response.raise_for_status()
        rows = (response.json().get("data") or {}).get("list") or []
        row = rows[0] if rows else {}
        return {
            "impressions": float(row.get("play_count") or 0),
            "clicks": 0.0,
            "likes": float(row.get("digg_count") or 0),
            "comments": float(row.get("comment_count") or 0),
            "shares": float(row.get("share_count") or 0),
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
        self.channel = channel
        self.credentials = credentials
        self.storage = storage
        self.client = client or httpx.Client(timeout=60)
        self.base_url = str(
            channel.config_json.get("api_base") or "https://api.weixin.qq.com"
        ).rstrip("/")

    def _access_token(self) -> str:
        try:
            response = self.client.get(
                f"{self.base_url}/cgi-bin/token",
                params={
                    "grant_type": "client_credential",
                    "appid": self.credentials.get("app_id"),
                    "secret": self.credentials.get("app_secret"),
                },
            )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise ConnectorPublishError(
                "公众号鉴权请求失败，尚未执行任何平台写入",
                stage="authenticate",
                retry_safe=True,
                invalidate_channel=True,
            ) from error
        token = body.get("access_token")
        if not token:
            errcode = body.get("errcode", "unknown")
            errmsg = body.get("errmsg", "unknown error")
            raise ConnectorPublishError(
                f"公众号鉴权失败（{errcode}）：{errmsg}",
                stage="authenticate",
                retry_safe=True,
                invalidate_channel=True,
            )
        return str(token)

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
        except Exception as error:
            raise ConnectorPublishError(
                "读取公众号封面失败，尚未执行任何平台写入",
                stage="read_assets",
                retry_safe=True,
            ) from error
        uploaded = self.client.post(
            f"{self.base_url}/cgi-bin/material/add_material",
            params={"access_token": token, "type": "image"},
            files={"media": (filename, data, cover.mime_type)},
        )
        uploaded.raise_for_status()
        media_id = uploaded.json().get("media_id")
        if not media_id:
            raise RuntimeError(f"公众号封面上传失败: {uploaded.json()}")
        draft = self.client.post(
            f"{self.base_url}/cgi-bin/draft/add",
            params={"access_token": token},
            json={
                "articles": [
                    {
                        "title": content.title,
                        "author": str(self.channel.config_json.get("author") or ""),
                        "digest": content.body[:120],
                        "content": "<p>"
                        + content.body.replace("\n", "</p><p>")
                        + "</p>",
                        "thumb_media_id": media_id,
                        "need_open_comment": 0,
                        "only_fans_can_comment": 0,
                    }
                ]
            },
        )
        draft.raise_for_status()
        draft_body = draft.json()
        draft_media_id = draft_body.get("media_id")
        if not draft_media_id:
            raise RuntimeError(f"公众号草稿创建失败: {draft_body}")
        if not self.channel.config_json.get("auto_publish", False):
            return ConnectorResult(
                status="draft_created",
                external_id=str(draft_media_id),
                response=draft_body,
            )
        submitted = self.client.post(
            f"{self.base_url}/cgi-bin/freepublish/submit",
            params={"access_token": token},
            json={"media_id": draft_media_id},
        )
        submitted.raise_for_status()
        body = submitted.json()
        publish_id = body.get("publish_id")
        if not publish_id:
            raise RuntimeError(f"公众号发布提交失败: {body}")
        return ConnectorResult(
            status="submitted",
            external_id=str(publish_id),
            response=body,
        )

    def reconcile(self, publish_job: PublishJob) -> ConnectorResult:
        if not publish_job.external_id:
            raise ValueError("公众号自动对账需要 freepublish publish_id")
        token = self._access_token()
        response = self.client.post(
            f"{self.base_url}/cgi-bin/freepublish/get",
            params={"access_token": token},
            json={"publish_id": publish_job.external_id},
        )
        response.raise_for_status()
        body = response.json()
        error_code = int(body.get("errcode") or 0)
        if error_code:
            raise RuntimeError(f"公众号发布状态查询失败: {body}")

        article_id = body.get("article_id")
        if article_id:
            detail = body.get("article_detail") or {}
            items = detail.get("item") or []
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
                response=body,
            )
        return ConnectorResult(
            status="pending",
            external_id=publish_job.external_id,
            response=body,
        )

    def pull_metrics(self, publish_job: PublishJob) -> dict[str, float]:
        raise NotImplementedError("公众号数据能力需按账号权限单独配置")


def build_connector(
    *,
    channel: ChannelConnection,
    settings: Settings,
    storage: ObjectStorage,
) -> ChannelConnector:
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
