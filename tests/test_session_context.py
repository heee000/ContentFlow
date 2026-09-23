"""Browser context preconditions are not an optional UI hint."""

from copy import deepcopy

import pytest
from sqlalchemy import func, select

from contentflow import db
from contentflow.entities import (
    AuthSession,
    Campaign,
    Job,
    KnowledgeDocument,
    Asset,
    PublishJob,
    AuthRefreshTokenHistory,
)
import test_auth_sessions as auth_tests


@pytest.fixture
def application():
    fixture = auth_tests.AuthSessionTest()
    fixture.setUp()
    fixture.register_cookie()
    try:
        yield fixture
    finally:
        fixture.tearDown()


def current_context(fixture):
    return (
        fixture.client.get("/api/v1/auth/session")
        .json()
        .get("context", "TEST-ONLY-missing-context")
    )


def headers(fixture, context):
    return {
        "Origin": fixture.origin,
        "X-ContentFlow-Session-Mode": "cookie",
        "X-ContentFlow-Context": context,
    }


BRIEF = {
    "name": "TEST-ONLY context-bound",
    "product_name": "Test product",
    "objective": "Verify context",
    "audience": "Test users",
    "platforms": ["wechat"],
}


def test_cookie_write_requires_context_even_without_session_mode_header(application):
    response = application.client.post(
        "/api/v1/campaigns", headers={"Origin": application.origin}, json=BRIEF
    )
    assert response.status_code == 428
    with db.SessionLocal() as session:
        assert session.scalar(select(func.count(Campaign.id))) == 0


def test_stale_tab_cannot_create_in_new_workspace(application):
    original = current_context(application)
    created = application.client.post(
        "/api/v1/auth/workspaces",
        headers=headers(application, original),
        json={"name": "TEST-ONLY second workspace"},
    )
    assert created.status_code == 201
    response = application.client.post(
        "/api/v1/campaigns", headers=headers(application, original), json=BRIEF
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "session_context_changed"
    with db.SessionLocal() as session:
        assert session.scalar(select(func.count(Campaign.id))) == 0


@pytest.mark.parametrize(
    "path",
    [
        "/campaigns",
        "/contents",
        "/assets",
        "/jobs",
        "/metrics/summary",
        "/auth/workspaces",
    ],
)
def test_stale_context_cannot_read_other_workspace_collections(application, path):
    original = current_context(application)
    assert (
        application.client.post(
            "/api/v1/auth/workspaces",
            headers=headers(application, original),
            json={"name": "TEST-ONLY another workspace"},
        ).status_code
        == 201
    )
    response = application.client.get(
        f"/api/v1{path}", headers=headers(application, original)
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "session_context_changed"


@pytest.mark.parametrize("path", ["/auth/refresh", "/auth/logout", "/auth/logout-all"])
def test_stale_tab_cannot_rotate_or_revoke_new_context(application, path):
    original = current_context(application)
    assert (
        application.client.post(
            "/api/v1/auth/workspaces",
            headers=headers(application, original),
            json={"name": "TEST-ONLY current workspace"},
        ).status_code
        == 201
    )
    cookies_before = deepcopy(dict(application.client.cookies))
    response = application.client.post(
        f"/api/v1{path}", headers=headers(application, original)
    )
    assert response.status_code == 409
    assert dict(application.client.cookies) == cookies_before
    assert application.client.get("/api/v1/auth/session").status_code == 200
    with db.SessionLocal() as session:
        assert session.scalar(select(AuthSession.revoked_at)) is None


def test_context_is_bound_to_login_session_and_cannot_authenticate_on_its_own(
    application,
):
    original = current_context(application)
    login = application.client.post(
        "/api/v1/auth/login",
        headers=application.cookie_headers(),
        json={"email": "cookie-owner@example.com", "password": "session-password"},
    )
    assert login.status_code == 200
    new = current_context(application)
    assert original != new
    assert len(new) == 64
    assert (
        application.client.post(
            "/api/v1/campaigns", headers=headers(application, original), json=BRIEF
        ).status_code
        == 409
    )
    application.client.cookies.clear()
    assert (
        application.client.get(
            "/api/v1/campaigns", headers=headers(application, new)
        ).status_code
        == 401
    )


def test_read_only_bootstrap_recovers_context_without_rotating_refresh(application):
    original = current_context(application)
    refresh = application.client.cookies.get(application.settings.refresh_cookie_name)
    application.client.cookies.delete(application.settings.access_cookie_name)
    response = application.client.post(
        "/api/v1/auth/bootstrap", headers={"Origin": application.origin}
    )
    assert response.status_code == 200
    assert response.json()["context"] == original
    assert "access_token" not in response.json()
    assert "set-cookie" not in response.headers
    assert (
        application.client.cookies.get(application.settings.refresh_cookie_name)
        == refresh
    )
    refreshed = application.client.post(
        "/api/v1/auth/refresh", headers=headers(application, original)
    )
    assert refreshed.status_code == 200
    assert current_context(application) == original


def test_wrong_context_is_rejected_without_disclosing_current_value(application):
    actual = current_context(application)
    response = application.client.get(
        "/api/v1/campaigns", headers=headers(application, "wrong-context")
    )
    assert response.status_code == 409
    assert actual not in response.text


PROTECTED_REQUESTS = [
    ("GET", "/assets/TEST-ONLY/download", {}),
    ("GET", "/publishing/preview-assets/TEST-ONLY?checksum=" + "a" * 64, {}),
    ("GET", "/publishing/jobs/TEST-ONLY/artifact", {}),
    (
        "POST",
        "/knowledge/documents",
        {"files": {"file": ("test.txt", b"TEST-ONLY", "text/plain")}},
    ),
    (
        "POST",
        "/assets/upload",
        {
            "data": {"asset_id": "TEST-ONLY"},
            "files": {"file": ("test.png", b"TEST-ONLY", "image/png")},
        },
    ),
    (
        "POST",
        "/publishing/preview",
        {
            "json": {
                "content_item_id": "TEST-ONLY",
                "channel_id": "TEST-ONLY",
                "request_id": "TEST-ONLY-intent",
                "publish_now": True,
            }
        },
    ),
    (
        "POST",
        "/publishing/jobs",
        {
            "json": {
                "content_item_id": "TEST-ONLY",
                "channel_id": "TEST-ONLY",
                "request_id": "TEST-ONLY-intent",
                "publish_now": True,
                "preview_token": "a" * 64,
            }
        },
    ),
    ("POST", "/campaigns/TEST-ONLY/runs", {"json": {}}),
    ("POST", "/auth/refresh", {}),
    ("POST", "/auth/logout", {}),
    ("POST", "/auth/logout-all", {}),
]


@pytest.mark.parametrize("missing", [False, True])
@pytest.mark.parametrize("method,path,options", PROTECTED_REQUESTS)
def test_context_gate_covers_media_publication_and_auth_before_side_effects(
    application, missing, method, path, options
):
    original = current_context(application)
    assert (
        application.client.post(
            "/api/v1/auth/workspaces",
            headers=headers(application, original),
            json={"name": "TEST-ONLY current workspace"},
        ).status_code
        == 201
    )
    current = current_context(application)
    cookies = dict(application.client.cookies)
    supplied = (
        {"Origin": application.origin} if missing else headers(application, original)
    )
    response = application.client.request(
        method, "/api/v1" + path, headers=supplied, **options
    )
    assert response.status_code == (428 if missing else 409)
    assert response.json()["error"]["code"] == (
        "session_context_required" if missing else "session_context_changed"
    )
    assert "set-cookie" not in response.headers
    assert dict(application.client.cookies) == cookies
    assert current_context(application) == current
    with db.SessionLocal() as session:
        for model in (Campaign, Job, KnowledgeDocument, Asset, PublishJob):
            assert session.scalar(select(func.count()).select_from(model)) == 0
        assert session.scalar(select(AuthSession.revoked_at)) is None
    assert not any(
        path.is_file() for path in application.settings.local_storage_dir.rglob("*")
    )


@pytest.mark.parametrize("origin", [None, "https://untrusted.invalid"])
def test_bootstrap_requires_trusted_origin_and_does_not_rotate(application, origin):
    original = current_context(application)
    cookies = dict(application.client.cookies)
    response = application.client.post(
        "/api/v1/auth/bootstrap", headers={} if origin is None else {"Origin": origin}
    )
    assert response.status_code == 403
    assert dict(application.client.cookies) == cookies
    assert current_context(application) == original
    with db.SessionLocal() as session:
        assert (
            session.scalar(select(func.count()).select_from(AuthRefreshTokenHistory))
            == 0
        )


def test_historical_refresh_cannot_use_bootstrap_to_evade_replay_revocation(
    application,
):
    context = current_context(application)
    old = application.client.cookies.get(application.settings.refresh_cookie_name)
    assert (
        application.client.post(
            "/api/v1/auth/refresh", headers=headers(application, context)
        ).status_code
        == 200
    )
    response = application.client.post(
        "/api/v1/auth/bootstrap",
        headers={
            "Origin": application.origin,
            "Cookie": f"{application.settings.refresh_cookie_name}={old}",
        },
    )
    assert response.status_code == 401
    assert "context" not in response.json()
    assert application.client.get("/api/v1/auth/session").status_code == 401
    with db.SessionLocal() as session:
        assert (
            session.scalar(select(AuthSession.revoke_reason)) == "refresh_token_reuse"
        )


def test_other_account_login_rejects_old_context_without_revoking_current_account(
    application,
):
    context = current_context(application)
    application.register_cookie(email="TEST-ONLY-second@example.com")
    response = application.client.post(
        "/api/v1/auth/logout", headers=headers(application, context)
    )
    assert response.status_code == 409
    current = application.client.get("/api/v1/auth/session")
    assert current.status_code == 200
    assert current.json()["user"]["email"] == "test-only-second@example.com"
