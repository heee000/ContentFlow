"""Real row-lock tests; no default database, credentials, or external requests."""

from concurrent.futures import ThreadPoolExecutor
from http.cookies import SimpleCookie
from threading import Barrier, Event
import uuid

from fastapi import HTTPException, Request, Response
import pytest
from sqlalchemy import event, func, select, text

from contentflow.dependencies import Principal
from contentflow.entities import (
    AuthSession,
    AuthRefreshTokenHistory,
    Membership,
    User,
    Workspace,
)
from contentflow.routers.auth import (
    create_auth_session,
    refresh_session,
    rotate_auth_session,
    switch_workspace,
)
from contentflow.session_context import browser_context
import test_postgres_integration as pg_tests

postgres_harness = pg_tests.postgres_harness
TEST_DATABASE_URL = pg_tests.TEST_DATABASE_URL

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="CONTENTFLOW_TEST_POSTGRES_URL is required for PostgreSQL tests",
)


def request(settings, *, refresh=None, context=None):
    headers = [
        (b"origin", b"http://testserver"),
        (b"x-contentflow-session-mode", b"cookie"),
    ]
    if refresh:
        headers.append(
            (b"cookie", f"{settings.refresh_cookie_name}={refresh}".encode())
        )
    if context:
        headers.append((b"x-contentflow-context", context.encode()))
    return Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "http",
            "path": "/api/v1/auth/refresh",
            "query_string": b"",
            "headers": headers,
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 12345),
        }
    )


def fixture(harness):
    suffix = uuid.uuid4().hex
    with harness.sessions() as session:
        user = User(
            email=f"session-{suffix}@example.com",
            password_hash="TEST-ONLY",
            display_name="TEST-ONLY",
        )
        session.add(user)
        session.flush()
        workspaces = [
            Workspace(
                name=f"TEST-ONLY {i}", slug=f"session-{suffix}-{i}", created_by=user.id
            )
            for i in range(3)
        ]
        session.add_all(workspaces)
        session.flush()
        session.add_all(
            [
                Membership(user_id=user.id, workspace_id=w.id, role="admin")
                for w in workspaces
            ]
        )
        auth, token = create_auth_session(
            session,
            user=user,
            workspace=workspaces[0],
            request=request(harness.settings),
            settings=harness.settings,
        )
        session.commit()
        return (
            auth.id,
            user.id,
            [w.id for w in workspaces],
            token,
            browser_context(auth, harness.settings),
        )


def principal(session, sid, uid, wid):
    return Principal(
        user=session.get(User, uid),
        workspace=session.get(Workspace, wid),
        membership=session.scalar(
            select(Membership).where(
                Membership.user_id == uid, Membership.workspace_id == wid
            )
        ),
        auth_session=session.get(AuthSession, sid),
    )


def test_postgres_simultaneous_old_refresh_preserves_replay_revocation(
    postgres_harness,
):
    harness = postgres_harness
    sid, _uid, _workspaces, token, context = fixture(harness)
    barrier = Barrier(2)

    def renew(_):
        with harness.sessions() as session:
            session.execute(text("SET statement_timeout = '10s'"))
            session.get(AuthSession, sid)  # exercise the identity map, too
            barrier.wait(timeout=10)
            try:
                refresh_session(
                    request(harness.settings, refresh=token, context=context),
                    Response(),
                    session,
                    harness.settings,
                )
                return 200
            except HTTPException as error:
                session.rollback()
                return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(renew, range(2)))
    assert sorted(results) == [200, 401]
    with harness.sessions() as session:
        assert session.get(AuthSession, sid).revoke_reason == "refresh_token_reuse"
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuthRefreshTokenHistory)
                .where(AuthRefreshTokenHistory.auth_session_id == sid)
            )
            == 1
        )


@pytest.mark.parametrize("operation", ["refresh", "switch"])
def test_postgres_cached_context_is_rechecked_after_session_row_lock(
    postgres_harness, operation
):
    harness = postgres_harness
    sid, uid, workspaces, _token, old_context = fixture(harness)
    preloaded = Event()
    rotated = Event()
    lock_attempted = Event()
    latest = {}

    def observe_lock(_conn, _cursor, statement, _parameters, _context, _many):
        if "auth_sessions" in statement and "FOR UPDATE" in statement:
            lock_attempted.set()

    def stale_request():
        with harness.sessions() as session:
            session.execute(text("SET statement_timeout = '10s'"))
            old_principal = principal(session, sid, uid, workspaces[0])
            preloaded.set()
            assert rotated.wait(10)
            response = Response()
            try:
                if operation == "refresh":
                    refresh_session(
                        request(
                            harness.settings,
                            refresh=latest["token"],
                            context=old_context,
                        ),
                        response,
                        session,
                        harness.settings,
                    )
                else:
                    switch_workspace(
                        workspaces[2],
                        request(harness.settings, context=old_context),
                        response,
                        old_principal,
                        session,
                        harness.settings,
                    )
                pytest.fail("Stale request passed the session row lock")
            except HTTPException as error:
                session.rollback()
                assert "set-cookie" not in response.headers
                return error.status_code

    with harness.sessions() as owner, ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(stale_request)
        try:
            assert preloaded.wait(10)
            auth = owner.scalar(
                select(AuthSession).where(AuthSession.id == sid).with_for_update()
            )
            latest["token"] = rotate_auth_session(
                owner,
                auth,
                workspace=owner.get(Workspace, workspaces[1]),
                request=request(harness.settings),
                settings=harness.settings,
            )
            owner.flush()
            event.listen(harness.engine, "before_cursor_execute", observe_lock)
            rotated.set()
            assert lock_attempted.wait(10)
            owner.commit()
            assert future.result(timeout=15) == (409 if operation == "refresh" else 401)
        finally:
            owner.rollback()
            rotated.set()
            if event.contains(harness.engine, "before_cursor_execute", observe_lock):
                event.remove(harness.engine, "before_cursor_execute", observe_lock)
    with harness.sessions() as session:
        auth = session.get(AuthSession, sid)
        assert auth.workspace_id == workspaces[1]
        assert auth.revoked_at is None
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuthRefreshTokenHistory)
                .where(AuthRefreshTokenHistory.auth_session_id == sid)
            )
            == 1
        )


def test_postgres_refresh_then_workspace_switch_uses_current_cookie_without_revocation(
    postgres_harness,
):
    harness = postgres_harness
    sid, uid, workspaces, token, context = fixture(harness)
    with harness.sessions() as session:
        response = Response()
        refresh_session(
            request(harness.settings, refresh=token, context=context),
            response,
            session,
            harness.settings,
        )
        cookies = SimpleCookie()
        for header in response.headers.getlist("set-cookie"):
            cookies.load(header)
        renewed = cookies[harness.settings.refresh_cookie_name].value
        assert renewed != token
    with harness.sessions() as session:
        current = principal(session, sid, uid, workspaces[0])
        result = switch_workspace(
            workspaces[1],
            request(harness.settings, refresh=renewed, context=context),
            Response(),
            current,
            session,
            harness.settings,
        )
        assert result.workspace_id == workspaces[1]
        assert result.context != context
        assert session.get(AuthSession, sid).revoked_at is None
