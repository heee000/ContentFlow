"""Confirmation proofs and receipt recovery; no Worker or platform is executed."""

import base64
import copy
import hashlib
import hmac
import json
import time
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from contentflow import db
from contentflow.entities import (
    Asset,
    ChannelConnection,
    ContentItem,
    Job,
    Membership,
    PublishJob,
    User,
)
from contentflow.knowledge_service import local_path_from_uri
from contentflow.publish_manifest import (
    PREVIEW_TTL_SECONDS,
    PublishManifestConflict,
    sign_publication_preview,
    verify_publication_preview,
)
import test_worker_v2 as worker_tests


SECRET = "isolated-preview-signing-secret"
IDENTITY = {"workspace_id": "workspace-one", "user_id": "user-one", "secret": SECRET}


def test_confirmation_token_accepts_exact_identity_until_expiry():
    with patch("contentflow.publish_manifest.time.time", return_value=1000):
        token = sign_publication_preview("a" * 64, **IDENTITY)
    with patch(
        "contentflow.publish_manifest.time.time",
        return_value=1000 + PREVIEW_TTL_SECONDS - 0.01,
    ):
        verify_publication_preview(token, "a" * 64, **IDENTITY)
    with patch(
        "contentflow.publish_manifest.time.time",
        return_value=1000 + PREVIEW_TTL_SECONDS,
    ):
        with pytest.raises(PublishManifestConflict):
            verify_publication_preview(token, "a" * 64, **IDENTITY)


@pytest.mark.parametrize(
    "field", ["user_id", "workspace_id", "secret", "fingerprint", "signature"]
)
def test_confirmation_proof_rejects_changed_authority_or_payload(field):
    token = sign_publication_preview("a" * 64, **IDENTITY)
    identity = dict(IDENTITY)
    fingerprint = "a" * 64
    if field in identity:
        identity[field] += "-other"
    elif field == "fingerprint":
        fingerprint = "b" * 64
    else:
        token = token[:-1] + ("0" if token[-1] != "0" else "1")
    with pytest.raises(PublishManifestConflict):
        verify_publication_preview(token, fingerprint, **identity)


@pytest.mark.parametrize("token", ["", "not-a-token", "...", "☃.☃", "a." + "0" * 64])
def test_invalid_confirmation_encoding_is_a_controlled_conflict(token):
    with pytest.raises(PublishManifestConflict):
        verify_publication_preview(token, "a" * 64, **IDENTITY)


@pytest.mark.parametrize(
    "field,value",
    [("v", True), ("v", 1.0), ("expires", True), ("expires", "9999999999")],
)
def test_signed_confirmation_record_requires_strict_schema(field, value):
    record = {
        "v": 1,
        "expires": int(time.time()) + 1000,
        "workspace": IDENTITY["workspace_id"],
        "user": IDENTITY["user_id"],
        "fingerprint": "a" * 64,
    }
    record[field] = value
    encoded = base64.urlsafe_b64encode(json.dumps(record).encode()).rstrip(b"=")
    signature = hmac.new(
        SECRET.encode(), b"publish-preview-v1:" + encoded, hashlib.sha256
    ).hexdigest()
    with pytest.raises(PublishManifestConflict):
        verify_publication_preview(
            encoded.decode() + "." + signature, "a" * 64, **IDENTITY
        )


@pytest.fixture
def application():
    fixture = worker_tests.WorkerIntegrationTest()
    fixture.setUp()
    try:
        fixture.publication_fixture = fixture._create_publish_fixture(
            status="cancelled"
        )
        yield fixture
    finally:
        fixture.tearDown()


def new_intent(application):
    fixture = application.publication_fixture
    return {
        "content_item_id": fixture["content_id"],
        "channel_id": fixture["channel_id"],
        "delivery_mode": "connector",
        "publish_now": True,
        "request_id": str(uuid.uuid4()),
    }


def preview(application, intent, headers=None):
    response = application.client.post(
        "/api/v1/publishing/preview",
        headers=headers or application.headers,
        json=intent,
    )
    assert response.status_code == 200, response.text
    return response.json()


def counts():
    with db.SessionLocal() as session:
        return (
            session.scalar(select(func.count(PublishJob.id))),
            session.scalar(select(func.count(Job.id))),
        )


def test_preview_is_read_only_and_returns_the_bound_asset_bytes(application):
    before = counts()
    result = preview(application, new_intent(application))
    assert counts() == before
    assert result["content_version"] == 1
    assert result["expires_in_seconds"] == PREVIEW_TTL_SECONDS
    assert result["document"]["format"] == "wechat_plain_text"
    assert "提交公开发布" in result["document"]["behavior"]
    assert len(result["assets"]) == 1
    asset = result["assets"][0]
    response = application.client.get(
        f"/api/v1/publishing/preview-assets/{asset['id']}?checksum={asset['checksum']}",
        headers=application.headers,
    )
    assert response.status_code == 200
    assert hashlib.sha256(response.content).hexdigest() == asset["checksum"]
    assert len(response.content) == asset["size_bytes"]
    assert counts() == before


@pytest.mark.parametrize("missing", ["request_id", "preview_token"])
def test_confirmation_requires_operation_id_and_proof(application, missing):
    intent = new_intent(application)
    intent["preview_token"] = preview(application, intent)["preview_token"]
    intent.pop(missing)
    before = counts()
    result = application.client.post(
        "/api/v1/publishing/jobs", headers=application.headers, json=intent
    )
    assert result.status_code == 422
    assert counts() == before


@pytest.mark.parametrize(
    "changed", ["body", "cover_metadata", "channel", "mode", "request_id"]
)
def test_confirmation_rejects_changes_after_preview(application, changed):
    intent = new_intent(application)
    result = preview(application, intent)
    intent["preview_token"] = result["preview_token"]
    with db.SessionLocal() as session:
        if changed == "body":
            session.get(
                ContentItem, intent["content_item_id"]
            ).body = "A different saved text, even at the same version."
        elif changed == "channel":
            session.get(ChannelConnection, intent["channel_id"]).config_json = {
                "auto_publish": False
            }
        elif changed == "cover_metadata":
            asset = session.get(Asset, result["assets"][0]["id"])
            asset.metadata_json = {**asset.metadata_json, "checksum": "0" * 64}
        elif changed == "request_id":
            intent["request_id"] = str(uuid.uuid4())
        else:
            intent["delivery_mode"] = "script"
        session.commit()
    before = counts()
    response = application.client.post(
        "/api/v1/publishing/jobs", headers=application.headers, json=intent
    )
    assert response.status_code == 409, response.text
    assert counts() == before


@pytest.mark.parametrize("damage", ["same_size", "larger", "missing"])
def test_preview_rejects_corrupted_object_not_just_metadata(application, damage):
    result = preview(application, new_intent(application))
    asset = result["assets"][0]
    with db.SessionLocal() as session:
        stored = session.get(Asset, asset["id"])
        path = local_path_from_uri(stored.storage_uri).resolve()
        # Deliberately simulate object corruption only inside this disposable
        # fixture, not via put(), whose content-addressed URI correctly changes.
        assert application.settings.local_storage_dir.resolve() in path.parents
        if damage == "missing":
            path.unlink()
        else:
            path.write_bytes(
                b"X" * (asset["size_bytes"] + (1 if damage == "larger" else 0))
            )
    before = counts()
    response = application.client.get(
        f"/api/v1/publishing/preview-assets/{asset['id']}?checksum={asset['checksum']}",
        headers=application.headers,
    )
    assert response.status_code == 409, response.text
    assert counts() == before


def test_preview_requires_explicit_operation_id(application):
    intent = new_intent(application)
    intent.pop("request_id")
    before = counts()
    response = application.client.post(
        "/api/v1/publishing/preview", headers=application.headers, json=intent
    )
    assert response.status_code == 422
    assert counts() == before


def test_one_confirmation_cannot_be_reused_with_a_new_operation_id(application):
    intent = new_intent(application)
    intent["preview_token"] = preview(application, intent)["preview_token"]
    first = application.client.post(
        "/api/v1/publishing/jobs", headers=application.headers, json=intent
    )
    assert first.status_code == 202, first.text
    before = counts()
    intent["request_id"] = str(uuid.uuid4())
    response = application.client.post(
        "/api/v1/publishing/jobs", headers=application.headers, json=intent
    )
    assert response.status_code == 409, response.text
    assert counts() == before


def test_expired_preview_cannot_create_a_new_job(application):
    intent = new_intent(application)
    with patch(
        "contentflow.publish_manifest.time.time",
        return_value=time.time() - PREVIEW_TTL_SECONDS - 10,
    ):
        intent["preview_token"] = preview(application, intent)["preview_token"]
    before = counts()
    response = application.client.post(
        "/api/v1/publishing/jobs", headers=application.headers, json=intent
    )
    assert response.status_code == 409
    assert counts() == before


@pytest.mark.parametrize("changed", ["none", "body", "channel", "expiry", "cancelled"])
def test_lost_receipt_exact_replay_returns_original_without_new_work(
    application, changed
):
    intent = new_intent(application)
    intent["preview_token"] = preview(application, intent)["preview_token"]
    created = application.client.post(
        "/api/v1/publishing/jobs", headers=application.headers, json=intent
    )
    assert created.status_code == 202, created.text
    publication_id = created.json()["id"]
    with db.SessionLocal() as session:
        if changed == "body":
            content = session.get(ContentItem, intent["content_item_id"])
            content.body = "Edited after queueing"
            content.version += 1
            content.status = "needs_review"
        if changed == "channel":
            session.get(ChannelConnection, intent["channel_id"]).config_json = {
                "auto_publish": "false"
            }
        if changed == "cancelled":
            session.get(PublishJob, publication_id).status = "cancelled"
        session.commit()
    before = counts()
    # A receipt replay must not re-validate expired proof/current content or
    # enqueue a replacement; it is not a second authorization to publish.
    with patch(
        "contentflow.routers.publishing.prepare_publication",
        side_effect=AssertionError("receipt must not create work"),
    ):
        with patch(
            "contentflow.publish_manifest.time.time",
            return_value=time.time() + (10000 if changed == "expiry" else 0),
        ):
            replayed = application.client.post(
                "/api/v1/publishing/jobs",
                headers=application.headers,
                json=copy.deepcopy(intent),
            )
    assert replayed.status_code == 202, replayed.text
    assert replayed.json()["id"] == publication_id
    assert replayed.json()["status"] == (
        "cancelled" if changed == "cancelled" else "queued"
    )
    assert counts() == before


@pytest.mark.parametrize(
    "changed", ["delivery_mode", "preview_token", "content_item_id"]
)
def test_same_operation_id_with_different_request_is_conflict(application, changed):
    intent = new_intent(application)
    intent["preview_token"] = preview(application, intent)["preview_token"]
    first = application.client.post(
        "/api/v1/publishing/jobs", headers=application.headers, json=intent
    )
    assert first.status_code == 202, first.text
    before = counts()
    intent[changed] = {
        "delivery_mode": "script",
        "preview_token": "invalid-signature-" * 6,
        "content_item_id": str(uuid.uuid4()),
    }[changed]
    response = application.client.post(
        "/api/v1/publishing/jobs", headers=application.headers, json=intent
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "publish_intent_conflict"
    assert counts() == before


def test_legacy_operation_id_is_not_silently_replaced(application):
    intent = new_intent(application)
    intent["preview_token"] = preview(application, intent)["preview_token"]
    with db.SessionLocal() as session:
        original = session.get(
            PublishJob, application.publication_fixture["publish_job_id"]
        )
        original.request_json = {
            **original.request_json,
            "request_id": intent["request_id"],
        }
        session.commit()
    before = counts()
    response = application.client.post(
        "/api/v1/publishing/jobs", headers=application.headers, json=intent
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "publish_intent_conflict"
    assert counts() == before


def test_preview_bytes_are_scoped_to_reviewer_workspace(application):
    intent = new_intent(application)
    proof = preview(application, intent)
    asset = proof["assets"][0]
    asset_path = (
        f"/api/v1/publishing/preview-assets/{asset['id']}?checksum={asset['checksum']}"
    )
    other = application.client.post(
        "/api/v1/auth/register",
        json={
            "email": "other-preview@example.com",
            "password": "isolated-safe-password",
            "display_name": "Other",
            "workspace_name": "Other workspace",
        },
    )
    assert other.status_code == 201
    other_headers = {"Authorization": f"Bearer {other.json()['access_token']}"}
    assert application.client.get(asset_path, headers=other_headers).status_code == 404
    assert (
        application.client.post(
            "/api/v1/publishing/preview", headers=other_headers, json=intent
        ).status_code
        == 404
    )
    with db.SessionLocal() as session:
        member = session.scalar(
            select(Membership).where(
                Membership.workspace_id == application.workspace_id
            )
        )
        member.role = "viewer"
        session.commit()
    assert (
        application.client.get(asset_path, headers=application.headers).status_code
        == 403
    )


def test_other_reviewer_cannot_use_original_confirmation_proof(application):
    intent = new_intent(application)
    intent["preview_token"] = preview(application, intent)["preview_token"]
    registered = application.client.post(
        "/api/v1/auth/register",
        json={
            "email": "reviewer-preview@example.com",
            "password": "isolated-safe-password",
            "display_name": "Reviewer",
            "workspace_name": "Reviewer home",
        },
    )
    assert registered.status_code == 201
    with db.SessionLocal() as session:
        user = session.scalar(
            select(User).where(User.email == "reviewer-preview@example.com")
        )
        session.add(
            Membership(
                user_id=user.id, workspace_id=application.workspace_id, role="reviewer"
            )
        )
        session.commit()
    switched = application.client.post(
        f"/api/v1/auth/switch/{application.workspace_id}",
        headers={"Authorization": f"Bearer {registered.json()['access_token']}"},
    )
    assert switched.status_code == 200, switched.text
    headers = {"Authorization": f"Bearer {switched.json()['access_token']}"}
    before = counts()
    response = application.client.post(
        "/api/v1/publishing/jobs", headers=headers, json=intent
    )
    assert response.status_code == 409
    assert counts() == before


def test_preview_html_escapes_plain_content_without_changing_displayed_text(
    application,
):
    with db.SessionLocal() as session:
        item = session.get(ContentItem, application.publication_fixture["content_id"])
        item.body = '<script>alert("not executable")</script>\nA & B'
        session.commit()
    document = preview(application, new_intent(application))["document"]
    assert document["text"] == '<script>alert("not executable")</script>\nA & B'
    assert "<script>" not in document["fields"]["content"]
    assert "&lt;script&gt;" in document["fields"]["content"]
