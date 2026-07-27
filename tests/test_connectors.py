from __future__ import annotations

import io
import json
import unittest
import zipfile

import httpx

from contentflow.connectors import (
    DouyinConnector,
    WechatConnector,
    XiaohongshuExportConnector,
)
from contentflow.entities import Asset, ChannelConnection, ContentItem, PublishJob
from contentflow.object_storage import StoredObject


class MemoryStorage:
    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def put(
        self,
        *,
        workspace_id: str,
        category: str,
        filename: str,
        stream,
        content_type: str | None = None,
    ) -> StoredObject:
        uri = f"memory://{workspace_id}/{category}/{filename}"
        data = stream.read()
        self.objects[uri] = data
        return StoredObject(
            uri=uri,
            checksum="a" * 64,
            size_bytes=len(data),
            mime_type=content_type or "application/octet-stream",
        )

    def read(self, uri: str, *, max_bytes: int = 100 * 1024 * 1024) -> bytes:
        data = self.objects[uri]
        if len(data) > max_bytes:
            raise ValueError("too large")
        return data


def content() -> ContentItem:
    return ContentItem(
        id="content-1",
        workspace_id="workspace-1",
        campaign_id="campaign-1",
        run_id="run-1",
        platform="douyin",
        title="北京夜游路线",
        body="先整理候选地点，再确认路线。",
        hashtags=["北京出行"],
        call_to_action="打开地图确认路线",
        layout_json={
            "aspect_ratio": "9:16",
            "shots": [{"time": "0-3秒", "visual": "路线选择"}],
        },
        status="approved",
        version=1,
    )


def publish_job(channel_id: str) -> PublishJob:
    from datetime import datetime, timezone

    return PublishJob(
        id="publish-1",
        workspace_id="workspace-1",
        content_item_id="content-1",
        channel_id=channel_id,
        scheduled_at=datetime.now(timezone.utc),
        idempotency_key=f"key-{channel_id}",
    )


class ConnectorContractTest(unittest.TestCase):
    def test_xiaohongshu_creates_manual_export_package(self):
        storage = MemoryStorage()
        storage.objects["memory://asset/cover.png"] = b"png"
        channel = ChannelConnection(
            id="channel-xhs",
            workspace_id="workspace-1",
            platform="xiaohongshu",
            display_name="人工投放",
            status="export_only",
        )
        connector = XiaohongshuExportConnector(channel=channel, storage=storage)
        item = content()
        item.platform = "xiaohongshu"
        result = connector.publish(
            publish_job=publish_job(channel.id),
            content=item,
            assets=[
                Asset(
                    id="asset-1",
                    workspace_id="workspace-1",
                    content_item_id=item.id,
                    kind="image",
                    status="ready",
                    storage_uri="memory://asset/cover.png",
                    mime_type="image/png",
                )
            ],
        )
        self.assertEqual(result.status, "exported")
        package = storage.objects[result.external_url or ""]
        with zipfile.ZipFile(io.BytesIO(package)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            layout = json.loads(archive.read("layout.json"))
            self.assertEqual(manifest["publish_mode"], "manual_export")
            self.assertTrue(manifest["human_approved"])
            self.assertEqual(layout["aspect_ratio"], "9:16")

    def test_douyin_upload_create_and_metrics_contract(self):
        storage = MemoryStorage()
        storage.objects["memory://asset/video.mp4"] = b"video"

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/oauth/userinfo/":
                return httpx.Response(200, json={"data": {"error_code": 0}})
            if request.url.path.endswith("/video/upload/"):
                return httpx.Response(
                    200,
                    json={"data": {"video": {"video_id": "video-1"}}},
                )
            if request.url.path.endswith("/video/create/"):
                return httpx.Response(200, json={"data": {"item_id": "item-1"}})
            if request.url.path.endswith("/video/video_data/"):
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "list": [
                                {
                                    "play_count": 100,
                                    "digg_count": 8,
                                    "comment_count": 2,
                                    "share_count": 1,
                                }
                            ]
                        }
                    },
                )
            return httpx.Response(404)

        channel = ChannelConnection(
            id="channel-douyin",
            workspace_id="workspace-1",
            platform="douyin",
            display_name="抖音官方账号",
            config_json={"api_base": "https://douyin.test"},
        )
        connector = DouyinConnector(
            channel=channel,
            credentials={"access_token": "token", "open_id": "open-id"},
            storage=storage,
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        self.assertEqual(connector.test().status, "connected")
        job = publish_job(channel.id)
        result = connector.publish(
            publish_job=job,
            content=content(),
            assets=[
                Asset(
                    id="video",
                    workspace_id="workspace-1",
                    content_item_id="content-1",
                    kind="video_storyboard",
                    status="ready",
                    storage_uri="memory://asset/video.mp4",
                    mime_type="video/mp4",
                )
            ],
        )
        self.assertEqual(result.external_id, "item-1")
        job.external_id = result.external_id
        metrics = connector.pull_metrics(job)
        self.assertEqual(metrics["impressions"], 100)
        self.assertEqual(metrics["likes"], 8)

    def test_wechat_creates_draft_by_default(self):
        storage = MemoryStorage()
        storage.objects["memory://asset/cover.png"] = b"image"

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/cgi-bin/token":
                return httpx.Response(200, json={"access_token": "wechat-token"})
            if request.url.path == "/cgi-bin/material/add_material":
                return httpx.Response(200, json={"media_id": "cover-media"})
            if request.url.path == "/cgi-bin/draft/add":
                return httpx.Response(200, json={"media_id": "draft-media"})
            return httpx.Response(404)

        channel = ChannelConnection(
            id="channel-wechat",
            workspace_id="workspace-1",
            platform="wechat",
            display_name="公众号",
            config_json={"api_base": "https://wechat.test"},
        )
        connector = WechatConnector(
            channel=channel,
            credentials={"app_id": "app", "app_secret": "secret"},
            storage=storage,
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        item = content()
        item.platform = "wechat"
        result = connector.publish(
            publish_job=publish_job(channel.id),
            content=item,
            assets=[
                Asset(
                    id="cover",
                    workspace_id="workspace-1",
                    content_item_id=item.id,
                    kind="image",
                    status="ready",
                    storage_uri="memory://asset/cover.png",
                    mime_type="image/png",
                )
            ],
        )
        self.assertEqual(result.status, "draft_created")
        self.assertEqual(result.external_id, "draft-media")


if __name__ == "__main__":
    unittest.main()
