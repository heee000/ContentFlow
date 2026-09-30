"""Actual HTTP parsing -> Worker -> storage must reject invalid media bytes."""

import base64
import hashlib
import json
from io import BytesIO
import shutil
import subprocess
from unittest.mock import patch

import httpx
import pytest
from PIL import Image
from sqlalchemy import select

from contentflow import db
from contentflow.entities import Asset, Campaign, ContentItem, Job, StorageObjectAllocation
from contentflow.media_providers import HTTPMediaProvider, media_provider_profile_fingerprint
from contentflow.worker import Worker
from contentflow.media_validation import MediaValidationError, validate_media
from contentflow.publish_manifest import ManifestObjectStorage, PublishManifestConflict
from media_fixtures import png_bytes
from test_media_contract_v1 import CONTRACT_HEADERS
import test_worker_storage_transactions as storage_tests

application = storage_tests.application


def test_http_generation_rejects_fake_image_before_storage(application):
    asset_id, job_id = storage_tests.queued_asset(application)
    application.settings.image_provider = "http"
    application.settings.media_api_base = "https://media.example/v1"
    application.settings.media_api_key = "TEST-ONLY"
    application.settings.image_model = "test-image"
    application.settings.media_download_allowed_hosts = ["media.example"]
    with db.SessionLocal() as session:
        asset = session.get(Asset, asset_id)
        asset.provider = "http"
        asset.prompt = "TEST-ONLY cover"
        session.commit()
    requests = []
    def reply(request):
        requests.append(request)
        return httpx.Response(200, headers=CONTRACT_HEADERS, json={"data": {
            "b64_json": base64.b64encode(b"not an image").decode(),
            "mime_type": "image/png", "filename": "test.png"}})
    with httpx.Client(transport=httpx.MockTransport(reply)) as client:
        provider = HTTPMediaProvider(application.settings, client=client)
        with patch("contentflow.worker.build_media_provider", return_value=provider):
            with Worker(settings=application.settings, session_factory=db.SessionLocal) as worker:
                assert worker.run_once()
    assert len(requests) == 1, "the actual HTTP parse/storage path must be exercised"
    with db.SessionLocal() as session:
        asset, job = session.get(Asset, asset_id), session.get(Job, job_id)
        assert (asset.status, job.status) == ("failed", "failed")
        assert asset.storage_uri is None
        assert not list(session.scalars(select(StorageObjectAllocation)))


def validate(data, **changes):
    args = dict(kind="image", mime_type="image/png", filename="TEST-ONLY.png",
        max_bytes=1024 * 1024, max_pixels=1024 * 1024)
    return validate_media(data, **{**args, **changes})


@pytest.mark.parametrize("raw,changes", [
    (b"not an image", {}), (png_bytes()[:40], {}),
    (png_bytes(), {"mime_type": "image/jpeg"}),
    (png_bytes(), {"max_bytes": 1}), (png_bytes(), {"max_pixels": 10}),
    (b'{"shots":[],"shots":[]}', {"kind": "video_storyboard", "mime_type": "application/json"}),
    (b'{"other":[]}', {"kind": "video_storyboard", "mime_type": "application/json"}),
    (b'{"shots":[]}', {"kind": "video", "mime_type": "application/json"}),
])
def test_invalid_media_never_validates(raw, changes):
    with pytest.raises(MediaValidationError) as error:
        validate(raw, **changes)
    assert error.value.retryable is False


def test_static_image_is_decoded_and_metadata_is_computed_from_bytes():
    result = validate(png_bytes())
    assert (result.mime_type, result.width, result.height) == ("image/png", 16, 16)
    assert result.source_sha256 == hashlib.sha256(png_bytes()).hexdigest()
    assert result.evidence["sha256"] == hashlib.sha256(result.data).hexdigest()


def test_animated_image_is_rejected_instead_of_silently_using_first_frame():
    output = BytesIO()
    Image.new("RGB", (16, 16), "red").save(output, format="PNG", save_all=True,
        append_images=[Image.new("RGB", (16, 16), "blue")], duration=100, loop=0)
    with pytest.raises(MediaValidationError):
        validate(output.getvalue())


def test_legacy_ready_bytes_are_checked_even_when_checksum_and_metadata_match():
    class Storage:
        def read(self, uri, *, max_bytes):
            return b"not an image"
    wrapper = ManifestObjectStorage(Storage(), {"assets": [{"uri": "test://asset",
        "kind": "image", "mime_type": "image/png", "size_bytes": 12,
        "sha256": hashlib.sha256(b"not an image").hexdigest()}]})
    with pytest.raises(PublishManifestConflict) as error:
        wrapper.read("test://asset")
    assert error.value.code == "publication_media_invalid"


@pytest.fixture(scope="module")
def valid_video():
    executable = shutil.which("ffmpeg")
    if not executable or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg/ffprobe required for real video decode acceptance")
    return subprocess.run([executable, "-v", "error", "-f", "lavfi", "-i",
        "color=c=blue:s=32x32:r=5:d=0.4", "-an", "-c:v", "mpeg4", "-threads", "1",
        "-f", "mp4", "-movflags", "frag_keyframe+empty_moov", "pipe:1"],
        capture_output=True, check=True, timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout


def test_video_has_real_probe_and_full_decode(valid_video):
    result = validate(valid_video, kind="video", mime_type="video/mp4")
    assert (result.width, result.height) == (32, 32)
    assert 0 < result.duration_seconds < 1
    assert result.data == valid_video


def test_truncated_video_and_missing_decoder_fail_closed(valid_video):
    with pytest.raises(MediaValidationError):
        validate(valid_video[:-35], kind="video", mime_type="video/mp4")
    with patch("contentflow.media_validation.shutil.which", return_value=None):
        with pytest.raises(MediaValidationError) as error:
            validate(valid_video, kind="video", mime_type="video/mp4")
        assert error.value.code == "media_decoder_unavailable"


def test_video_duration_limits_and_timeout_are_safe_errors(valid_video):
    with patch("contentflow.media_validation.MAX_VIDEO_SECONDS", 0.01):
        with pytest.raises(MediaValidationError) as error:
            validate(valid_video, kind="video", mime_type="video/mp4")
        assert error.value.code == "media_video_limit"
    with patch("contentflow.media_validation.subprocess.run", side_effect=subprocess.TimeoutExpired(
        "TEST_ONLY_PRIVATE_COMMAND", 20, stderr="TEST_ONLY_PRIVATE_STDERR")):
        with pytest.raises(MediaValidationError) as error:
            validate(valid_video, kind="video", mime_type="video/mp4")
        assert error.value.code == "media_decode_timeout"
        assert "TEST_ONLY_PRIVATE" not in str(error.value)


@pytest.mark.parametrize("phase", ["generate", "poll"])
def test_missing_decoder_stops_before_http_provider_invocation(application, phase):
    asset_id, job_id = storage_tests.queued_asset(application)
    application.settings.video_provider = "http"
    with db.SessionLocal() as session:
        asset = session.get(Asset, asset_id)
        asset.kind, asset.provider = "video", "http"
        asset.external_task_id = "TEST-ONLY-existing-task"
        asset.metadata_json = {**asset.metadata_json, "media_provider_profile_fingerprint":
            media_provider_profile_fingerprint(application.settings, "video")}
        session.get(Job, job_id).job_type = "asset." + phase
        session.commit()
    with patch("contentflow.media_validation.shutil.which", return_value=None), \
         patch("contentflow.worker.build_media_provider") as provider:
        assert application.worker.run_once()
        provider.assert_not_called()
    with db.SessionLocal() as session:
        job = session.get(Job, job_id)
        assert job.status == "failed" and "media_decoder_unavailable" in job.last_error
        assert not list(session.scalars(select(StorageObjectAllocation)))


@pytest.mark.parametrize("valid", [False, True])
def test_http_poll_validates_completed_download_before_storage(application, valid_video, valid):
    asset_id, job_id = storage_tests.queued_asset(application)
    settings = application.settings
    settings.video_provider = "http"
    settings.media_api_base, settings.media_api_key = "https://media.example/v1", "TEST-ONLY"
    settings.video_model, settings.media_download_allowed_hosts = "test-video", ["media.example"]
    with db.SessionLocal() as session:
        asset = session.get(Asset, asset_id)
        asset.kind, asset.provider, asset.status = "video", "http", "processing"
        asset.external_task_id = "TEST-ONLY-existing-task"
        asset.metadata_json = {**asset.metadata_json, "media_provider_profile_fingerprint":
            media_provider_profile_fingerprint(settings, "video")}
        session.get(Job, job_id).job_type = "asset.poll"
        session.commit()
    requests = []
    def reply(request):
        requests.append(request)
        return httpx.Response(200, headers=CONTRACT_HEADERS, json={"data": {
            "status": "completed", "url": "https://media.example/result.mp4"}})
    with httpx.Client(transport=httpx.MockTransport(reply)) as client, \
         patch("contentflow.worker.download_generated_media",
             return_value=valid_video if valid else b"not a video") as download, \
         patch("contentflow.worker.build_media_provider", return_value=HTTPMediaProvider(settings, client=client)):
        assert application.worker.run_once()
        download.assert_called_once()
    assert len(requests) == 1 and requests[0].method == "GET"
    with db.SessionLocal() as session:
        asset, job = session.get(Asset, asset_id), session.get(Job, job_id)
        assert (asset.status, job.status) == (("ready", "succeeded") if valid else ("failed", "failed"))
        if valid:
            assert asset.metadata_json["media_validation"]["width"] == 32
        else:
            assert asset.storage_uri is None
            assert not list(session.scalars(select(StorageObjectAllocation)))


@pytest.mark.parametrize("kind,mime,raw", [
    ("video", "video/mp4", b"not a video"),
    ("video_storyboard", "application/json", json.dumps({"shots": [{"narration": "绝对安全"}]}).encode()),
])
def test_manual_upload_rejects_invalid_video_and_unreviewed_storyboard(application, kind, mime, raw):
    fixture = application._create_publish_fixture(status="cancelled")
    with db.SessionLocal() as session:
        content = session.get(ContentItem, fixture["content_id"])
        campaign = session.get(Campaign, content.campaign_id)
        campaign.brief = {**(campaign.brief or {}), "forbidden_phrases": ["绝对安全"]}
        asset = session.scalar(select(Asset).where(Asset.content_item_id == content.id))
        asset.kind, asset.provider, asset.status = kind, "manual", "awaiting_upload"
        asset.storage_uri = None
        session.commit()
        asset_id = asset.id
    response = application.client.post("/api/v1/assets/upload", headers=application.headers,
        data={"asset_id": asset_id}, files={"file": ("test.bin", raw, mime)})
    assert response.status_code == 415, response.text
    if kind == "video_storyboard":
        assert "media_text_review_required" in response.text
    with db.SessionLocal() as session:
        assert session.get(Asset, asset_id).status == "awaiting_upload"
        assert not list(session.scalars(select(StorageObjectAllocation)))


def test_legacy_storyboard_text_is_reviewed_at_actual_read():
    raw = json.dumps({"shots": [{"绝对安全": "普通文字"}]}).encode()
    class Storage:
        def read(self, uri, *, max_bytes):
            return raw
    wrapper = ManifestObjectStorage(Storage(), {"assets": [{"uri": "test://asset",
        "kind": "video_storyboard", "mime_type": "application/json", "size_bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest()}]}, forbidden_phrases=("绝对安全",))
    with pytest.raises(PublishManifestConflict) as error:
        wrapper.read("test://asset")
    assert error.value.code == "publication_review_required"


def test_generated_storyboard_cannot_add_forbidden_text_after_content_approval(application):
    asset_id, job_id = storage_tests.queued_asset(application)
    with db.SessionLocal() as session:
        asset = session.get(Asset, asset_id)
        asset.kind = "video_storyboard"
        asset.metadata_json = {**asset.metadata_json, "shots": [{"narration": "绝对安全"}]}
        content = session.get(ContentItem, asset.content_item_id)
        campaign = session.get(Campaign, content.campaign_id)
        campaign.brief = {**(campaign.brief or {}), "forbidden_phrases": ["绝对安全"]}
        session.commit()
    assert application.worker.run_once()
    with db.SessionLocal() as session:
        assert session.get(Asset, asset_id).storage_uri is None
        job = session.get(Job, job_id)
        assert job.status == "failed" and "media_text_review_required" in job.last_error
        assert not list(session.scalars(select(StorageObjectAllocation)))
