"""Offline regressions for channel configuration and secret-bearing failures."""

import logging
import traceback
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx
import pytest
from pydantic import ValidationError

from contentflow.connectors import (
    ConnectorPublishError,
    DouyinConnector,
    WechatConnector,
)
from contentflow.entities import Asset, ChannelConnection
from contentflow.publication_payload import preview_document
from contentflow.routers.channels import validate_channel_payload
from contentflow.schemas import ChannelCreate
from test_connectors import MemoryStorage, content, publish_job
import test_worker_v2 as worker_tests
from contentflow import db
from contentflow.entities import Job, Membership, PublishJob
from sqlalchemy import select


SECRET = "synthetic-channel-secret-sentinel"


@pytest.mark.parametrize("value", ["false", "true", 0, 1, None, [], {}, ""])
def test_channel_input_rejects_non_boolean_publish_switch(value):
    with pytest.raises((ValidationError, ValueError)):
        payload = ChannelCreate(
            platform="wechat",
            display_name="isolated",
            credentials={
                "app_id": "test-id",
                "app_secret": SECRET,
            },
            config={"auto_publish": value},
        )
        validate_channel_payload(payload)


@pytest.mark.parametrize("platform", ["wechat", "douyin"])
@pytest.mark.parametrize(
    "target",
    [
        "http://127.0.0.1:18789",
        "https://localhost",
        "https://169.254.169.254",
        "https://api.weixin.qq.com.evil.invalid",
        "https://evil.invalid",
        "https://api.weixin.qq.com/?secret=value",
        "https://user@api.weixin.qq.com",
    ],
)
def test_runtime_rejects_non_official_channel_target_before_requests(platform, target):
    calls = []
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: calls.append(r))
    ) as client:
        channel = ChannelConnection(platform=platform, config_json={"api_base": target})
        connector_type = WechatConnector if platform == "wechat" else DouyinConnector
        with pytest.raises(ValueError):
            connector_type(
                channel=channel, credentials={}, storage=MemoryStorage(), client=client
            )
    assert calls == []


@pytest.mark.parametrize("value", ["false", "true", 0, 1, None, [], {}])
def test_preview_rejects_invalid_legacy_boolean(value):
    channel = ChannelConnection(platform="wechat", config_json={"auto_publish": value})
    with pytest.raises(ValueError):
        preview_document(content(), channel, "connector")


def wechat_fixture(auto_publish=True):
    storage = MemoryStorage()
    storage.objects["memory://audit/cover.png"] = b"synthetic-image"
    item = content()
    item.platform = "wechat"
    channel = ChannelConnection(
        id="audit-channel",
        platform="wechat",
        config_json={"auto_publish": auto_publish},
    )
    asset = Asset(
        id="audit-cover",
        kind="image",
        status="ready",
        mime_type="image/png",
        storage_uri="memory://audit/cover.png",
    )
    return storage, item, channel, asset


@pytest.mark.parametrize("stage", ["token", "add_material", "add", "submit", "get"])
@pytest.mark.parametrize("failure", ["http", "network", "json", "platform"])
def test_wechat_failures_never_expose_response_or_credential(stage, failure, caplog):
    storage, item, channel, asset = wechat_fixture()
    paths = []

    def handler(request):
        paths.append(request.url.path)
        if request.url.path.rsplit("/", 1)[-1] == stage:
            if failure == "network":
                raise httpx.ConnectError(SECRET, request=request)
            if failure == "json":
                return httpx.Response(200, text=SECRET)
            if failure == "platform":
                return httpx.Response(200, json={"errcode": 40164, "errmsg": SECRET})
            return httpx.Response(500, text=SECRET)
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": SECRET})
        return httpx.Response(
            200, json={"media_id": "audit-media", "publish_id": "audit-publish"}
        )

    caplog.set_level(logging.DEBUG)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        connector = WechatConnector(
            channel=channel,
            credentials={"app_id": "audit", "app_secret": SECRET},
            storage=storage,
            client=client,
        )
        job = publish_job(channel.id)
        job.external_id = "audit-publish"
        with pytest.raises(ConnectorPublishError) as caught:
            if stage == "get":
                connector.reconcile(job)
            else:
                connector.publish(publish_job=job, content=item, assets=[asset])
    assert caught.value.retry_safe is (stage == "token")
    assert SECRET not in str(caught.value)
    assert SECRET not in "".join(traceback.format_exception(caught.value))
    assert SECRET not in caplog.text
    assert (
        len(paths)
        == {"token": 1, "add_material": 2, "add": 3, "submit": 4, "get": 2}[stage]
    )


def test_official_connector_does_not_follow_redirect_even_with_injected_client():
    storage, _item, channel, _asset = wechat_fixture()
    hosts = []

    def handler(request):
        hosts.append(request.url.host)
        return httpx.Response(302, headers={"location": "https://evil.invalid/token"})

    with httpx.Client(
        transport=httpx.MockTransport(handler), follow_redirects=True
    ) as client:
        connector = WechatConnector(
            channel=channel, credentials={}, storage=storage, client=client
        )
        with pytest.raises(ConnectorPublishError):
            connector.test()
    assert hosts == ["api.weixin.qq.com"]


@pytest.fixture
def application():
    fixture = worker_tests.WorkerIntegrationTest()
    fixture.setUp()
    try:
        yield fixture
    finally:
        fixture.tearDown()


def test_channel_api_rejects_unsafe_config_without_echoing_credentials(application):
    configs = [
        {"auto_publish": value} for value in ["false", "true", 0, 1, None, [], {}]
    ]
    configs += [
        {"api_base": "https://evil.invalid"},
        {"unknown_key": SECRET},
        {"connection_mode": "script"},
        {"auto_publish": False, "author": [SECRET]},
    ]
    for config in configs:
        response = application.client.post(
            "/api/v1/channels",
            headers=application.headers,
            json={
                "platform": "wechat",
                "display_name": "audit",
                "config": config,
                "credentials": {"app_id": "audit-id", "app_secret": SECRET},
            },
        )
        assert response.status_code == 422, response.text
        assert SECRET not in response.text
    with db.SessionLocal() as session:
        assert session.scalar(select(ChannelConnection)) is None


def test_legacy_configuration_blocks_preview_and_test_without_queuing(application):
    fixture = application._create_publish_fixture(status="cancelled")
    with db.SessionLocal() as session:
        session.get(ChannelConnection, fixture["channel_id"]).config_json = {
            "auto_publish": "false"
        }
        session.commit()
    preview = application.client.post(
        "/api/v1/publishing/preview",
        headers=application.headers,
        json={
            "content_item_id": fixture["content_id"],
            "channel_id": fixture["channel_id"],
            "publish_now": True,
            "request_id": "invalid-legacy-config-preview",
        },
    )
    tested = application.client.post(
        f"/api/v1/channels/{fixture['channel_id']}/test", headers=application.headers
    )
    for response in (preview, tested):
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "channel_configuration_invalid"
    with db.SessionLocal() as session:
        assert session.scalar(select(Job)) is None


def queue_fixture(application, fixture, job_type):
    with db.SessionLocal() as session:
        job = Job(
            workspace_id=application.workspace_id,
            job_type=job_type,
            status="queued",
            payload_json={
                "publish_job_id": fixture["publish_job_id"],
                "channel_id": fixture["channel_id"],
            },
            run_at=datetime.now(timezone.utc) - timedelta(seconds=1),
            max_attempts=3,
            idempotency_key=f"isolated:{job_type}:{fixture['publish_job_id']}",
        )
        session.add(job)
        session.commit()
        return job.id


def test_worker_blocks_invalid_legacy_configuration_before_network(application):
    fixture = application._create_publish_fixture(status="queued")
    with db.SessionLocal() as session:
        session.get(ChannelConnection, fixture["channel_id"]).config_json = {
            "api_base": "http://127.0.0.1"
        }
        session.commit()
    job_id = queue_fixture(application, fixture, "publish.dispatch")
    with patch("contentflow.connectors.httpx.Client") as client:
        assert application.worker.run_once()
    client.assert_not_called()
    with db.SessionLocal() as session:
        job = session.get(Job, job_id)
        publication = session.get(PublishJob, fixture["publish_job_id"])
        assert job.status == "failed"
        assert publication.status == "failed"
        assert publication.attempts == 0


def test_upload_error_is_safe_in_database_viewer_api_and_logs(application, caplog):
    fixture = application._create_publish_fixture(status="cancelled")
    response = application.confirm_publish(
        headers=application.headers,
        json={
            "content_item_id": fixture["content_id"],
            "channel_id": fixture["channel_id"],
            "publish_now": True,
        },
    )
    assert response.status_code == 202, response.text
    publication_id = response.json()["id"]

    def handler(request):
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"access_token": SECRET})
        return httpx.Response(500, text=SECRET)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:

        def factory(*, channel, storage, settings):
            return WechatConnector(
                channel=channel,
                storage=storage,
                credentials={"app_id": "audit-id", "app_secret": SECRET},
                client=client,
            )

        caplog.set_level(logging.DEBUG)
        with patch("contentflow.worker.build_connector", side_effect=factory):
            assert application.worker.run_once()
    with db.SessionLocal() as session:
        publication = session.get(PublishJob, publication_id)
        job = session.scalar(select(Job))
        assert publication.status == "reconciliation_required"
        assert not publication.retry_safe
        assert SECRET not in publication.error
        assert "HTTP=500" in publication.error
        assert SECRET not in job.last_error
        member = session.scalar(
            select(Membership).where(
                Membership.workspace_id == application.workspace_id
            )
        )
        member.role = "viewer"
        session.commit()
    for path in ("/api/v1/publishing/jobs", "/api/v1/jobs"):
        listed = application.client.get(path, headers=application.headers)
        assert listed.status_code == 200
        assert SECRET not in listed.text
    assert SECRET not in caplog.text


@pytest.mark.parametrize(
    "job_type",
    ["connector.test", "publish.dispatch", "publish.reconcile", "metrics.pull"],
)
def test_unexpected_connector_exception_is_sanitized_at_worker_boundary(
    application, caplog, job_type
):
    fixture = application._create_publish_fixture(
        status="queued" if job_type == "publish.dispatch" else "submitted",
        external_id="known-publish-id",
    )
    job_id = queue_fixture(application, fixture, job_type)

    class BrokenConnector:
        reconciliation_supported = True

        def fail(self, *args, **kwargs):
            raise RuntimeError(SECRET)

        test = publish = reconcile = pull_metrics = fail

    caplog.set_level(logging.DEBUG)
    with patch("contentflow.worker.build_connector", return_value=BrokenConnector()):
        assert application.worker.run_once()
    with db.SessionLocal() as session:
        job = session.get(Job, job_id)
        assert job.last_error and SECRET not in job.last_error
        publication = session.get(PublishJob, fixture["publish_job_id"])
        assert SECRET not in (publication.error or "")
        if job_type == "publish.dispatch":
            assert publication.status == "reconciliation_required"
            assert not publication.retry_safe
    assert SECRET not in caplog.text


def test_historical_errors_are_not_returned_and_evidence_is_preserved(application):
    fixture = application._create_publish_fixture(status="reconciliation_required")
    job_id = queue_fixture(application, fixture, "publish.dispatch")
    with db.SessionLocal() as session:
        publication = session.get(PublishJob, fixture["publish_job_id"])
        publication.error = SECRET
        publication.response_json = {
            "dispatch_failure": {"retry_safe": False, "stage": SECRET}
        }
        session.get(Job, job_id).last_error = SECRET
        session.commit()
    for path in ("/api/v1/publishing/jobs", "/api/v1/jobs"):
        response = application.client.get(path, headers=application.headers)
        assert response.status_code == 200
        assert SECRET not in response.text
    with db.SessionLocal() as session:
        assert session.get(PublishJob, fixture["publish_job_id"]).error == SECRET
        assert session.get(Job, job_id).last_error == SECRET


@pytest.mark.parametrize("stage", ["userinfo", "upload", "create", "video_data"])
@pytest.mark.parametrize("failure", ["http", "network", "json", "platform"])
def test_douyin_failures_are_bounded_and_secret_free(stage, failure, caplog):
    storage, item, _channel, asset = wechat_fixture()
    item.platform = "douyin"
    asset.kind, asset.mime_type = "video", "video/mp4"
    channel = ChannelConnection(id="audit-douyin", platform="douyin", config_json={})
    paths = []

    def handler(request):
        operation = request.url.path.strip("/").rsplit("/", 1)[-1]
        paths.append(operation)
        if operation == stage:
            if failure == "network":
                raise httpx.ConnectError(SECRET, request=request)
            if failure == "json":
                return httpx.Response(200, text=SECRET)
            if failure == "platform":
                return httpx.Response(
                    200, json={"data": {"error_code": 40164, "description": SECRET}}
                )
            return httpx.Response(500, text=SECRET)
        return httpx.Response(
            200,
            json={
                "data": {
                    "video": {"video_id": "video"},
                    "item_id": "item",
                    "error_code": 0,
                }
            },
        )

    caplog.set_level(logging.DEBUG)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        connector = DouyinConnector(
            channel=channel,
            credentials={"access_token": SECRET, "open_id": "audit-open-id"},
            storage=storage,
            client=client,
        )
        job = publish_job(channel.id)
        job.external_id = "known-id"
        with pytest.raises(ConnectorPublishError) as caught:
            if stage == "userinfo":
                connector.test()
            elif stage == "video_data":
                connector.pull_metrics(job)
            else:
                connector.publish(publish_job=job, content=item, assets=[asset])
    assert caught.value.retry_safe is (stage == "userinfo")
    assert SECRET not in "".join(traceback.format_exception(caught.value))
    assert SECRET not in caplog.text
    assert len(paths) == (2 if stage == "create" else 1)


@pytest.mark.parametrize(
    "config,expected",
    [({}, False), ({"auto_publish": False}, False), ({"auto_publish": True}, True)],
)
def test_valid_publish_switch_preview_and_delivery_agree(config, expected):
    storage, item, channel, asset = wechat_fixture()
    channel.config_json = config
    paths = []

    def handler(request):
        paths.append(request.url.path)
        assert request.url.host == "api.weixin.qq.com"
        assert request.url.scheme == "https"
        return httpx.Response(
            200,
            json={"access_token": SECRET, "media_id": "media", "publish_id": "publish"},
        )

    document = preview_document(item, channel, "connector")
    assert ("提交公开发布" in document["behavior"]) is expected
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        connector = WechatConnector(
            channel=channel, credentials={}, storage=storage, client=client
        )
        result = connector.publish(
            publish_job=publish_job(channel.id), content=item, assets=[asset]
        )
    assert (result.status == "submitted") is expected
    assert any(path.endswith("/submit") for path in paths) is expected
    assert SECRET not in str(result.response)


@pytest.mark.parametrize("operation", ["test", "publish", "reconcile"])
def test_configuration_revalidated_if_connector_outlives_channel_change(operation):
    storage, item, channel, asset = wechat_fixture()
    calls = []
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: calls.append(request))
    ) as client:
        connector = WechatConnector(
            channel=channel, credentials={}, storage=storage, client=client
        )
        channel.config_json = {"auto_publish": "false"}
        job = publish_job(channel.id)
        job.external_id = "published-id"
        with pytest.raises(ValueError):
            if operation == "test":
                connector.test()
            elif operation == "reconcile":
                connector.reconcile(job)
            else:
                connector.publish(publish_job=job, content=item, assets=[asset])
    assert calls == []


@pytest.mark.parametrize(
    "bad_body", [[], {"errcode": SECRET}, {"access_token": {"key": SECRET}}]
)
def test_malformed_authentication_shape_does_not_echo_body(bad_body):
    storage, _item, channel, _asset = wechat_fixture()
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=bad_body))
    ) as client:
        connector = WechatConnector(
            channel=channel, credentials={}, storage=storage, client=client
        )
        with pytest.raises(ConnectorPublishError) as caught:
            connector.test()
    assert SECRET not in "".join(traceback.format_exception(caught.value))
    assert caught.value.retry_safe


def test_safe_error_wrapper_cannot_mark_raw_upstream_text_as_safe():
    from contentflow.connector_errors import (
        public_connector_error,
        safe_connector_failure,
    )

    error = ConnectorPublishError(SECRET, stage=SECRET, code=SECRET, retry_safe=True)
    error.args = (SECRET,)
    assert SECRET not in safe_connector_failure(error)
    assert SECRET not in public_connector_error(
        diagnostic={"stage": [SECRET], "code": {"key": SECRET}}
    )


def test_removed_job_still_cannot_expose_platform_exception_in_logs(
    application, caplog
):
    fixture = application._create_publish_fixture(status="cancelled")
    job_id = queue_fixture(application, fixture, "connector.test")

    def deleted_job_failure(session, payload, settings):
        session.delete(session.get(Job, job_id))
        session.commit()
        raise RuntimeError(SECRET)

    application.worker.handlers["connector.test"] = deleted_job_failure
    caplog.set_level(logging.DEBUG)
    assert application.worker.run_once()
    assert SECRET not in caplog.text
    with db.SessionLocal() as session:
        assert session.get(Job, job_id) is None


def test_positive_numeric_publish_id_retains_existing_scalar_compatibility():
    storage, item, channel, asset = wechat_fixture()

    def handler(request):
        return httpx.Response(
            200, json={"access_token": SECRET, "media_id": "media", "publish_id": 12345}
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        connector = WechatConnector(
            channel=channel, credentials={}, storage=storage, client=client
        )
        result = connector.publish(
            publish_job=publish_job(channel.id), content=item, assets=[asset]
        )
    assert result.status == "submitted"
    assert result.external_id == "12345"
