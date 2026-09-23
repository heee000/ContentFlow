import hashlib
from unittest.mock import Mock

import pytest
import httpx

from contentflow.connectors import ConnectorPublishError, WechatConnector
from contentflow.entities import Asset, ChannelConnection, ContentItem, PublishJob
from contentflow.publish_manifest import ManifestObjectStorage, PublishManifestConflict


@pytest.mark.parametrize(
    "payload", [b"confirmed", b"tampered!", b"short", b"too-long-data"]
)
def test_manifest_storage_checks_exact_bytes(payload):
    storage = Mock()
    storage.read.return_value = payload
    protected = ManifestObjectStorage(
        storage,
        {
            "assets": [
                {
                    "uri": "test://confirmed",
                    "size_bytes": 9,
                    "sha256": hashlib.sha256(b"confirmed").hexdigest(),
                }
            ]
        },
    )
    if payload == b"confirmed":
        assert protected.read("test://confirmed") == payload
    else:
        with pytest.raises(PublishManifestConflict):
            protected.read("test://confirmed")
    storage.read.assert_called_once_with("test://confirmed", max_bytes=9)
    storage.read.reset_mock()
    with pytest.raises(PublishManifestConflict):
        protected.read("test://not-approved")
    storage.read.assert_not_called()


def test_wechat_checksum_mismatch_stops_before_any_platform_write():
    storage = Mock()
    storage.read.return_value = b"tampered!"
    protected = ManifestObjectStorage(
        storage,
        {
            "assets": [
                {
                    "uri": "test://confirmed",
                    "size_bytes": 9,
                    "sha256": hashlib.sha256(b"confirmed").hexdigest(),
                }
            ]
        },
    )
    requests = []

    def transport(request):
        requests.append((request.method, request.url.path))
        assert request.method == "GET"
        assert request.url.path == "/cgi-bin/token"
        return httpx.Response(200, json={"access_token": "isolated-token"})

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        connector = WechatConnector(
            channel=ChannelConnection(config_json={"auto_publish": True}),
            credentials={"app_id": "isolated", "app_secret": "isolated"},
            storage=protected,
            client=client,
        )
        with pytest.raises(ConnectorPublishError) as captured:
            connector.publish(
                publish_job=PublishJob(),
                content=ContentItem(title="test", body="test"),
                assets=[Asset(storage_uri="test://confirmed", mime_type="image/png")],
            )
    assert captured.value.retry_safe is True
    assert captured.value.stage == "read_assets"
    assert requests == [("GET", "/cgi-bin/token")]
