from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from contentflow import db
from contentflow.api import create_app
from contentflow.entities import AuthRateLimit, AuthRefreshTokenHistory, AuthSession
from contentflow.settings import Settings


class AuthSessionTest(unittest.TestCase):
    origin = "http://localhost:3000"

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.settings = Settings(
            database_url=f"sqlite:///{(root / 'auth-sessions.db').as_posix()}",
            secret_key="auth-session-test-secret",
            local_storage_dir=root / "storage",
            allow_registration=True,
            cors_origins=[self.origin],
            auth_rate_limit_window_seconds=60,
            auth_rate_limit_block_seconds=120,
            auth_login_account_attempts=2,
            auth_login_ip_attempts=100,
            auth_registration_ip_attempts=2,
            auth_refresh_session_attempts=100,
            auth_refresh_ip_attempts=100,
        )
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        db.engine.dispose()
        self.temp_dir.cleanup()

    def cookie_headers(self) -> dict[str, str]:
        return {
            "Origin": self.origin,
            "X-ContentFlow-Session-Mode": "cookie",
        }

    def register_cookie(self, email: str = "cookie-owner@example.com"):
        response = self.client.post(
            "/api/v1/auth/register",
            headers=self.cookie_headers(),
            json={
                "email": email,
                "password": "session-password",
                "display_name": "Cookie Owner",
                "workspace_name": "Cookie Workspace",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response

    def register_bearer(self, email: str = "cli-owner@example.com"):
        response = self.client.post(
            "/api/v1/auth/register",
            json={
                "email": email,
                "password": "session-password",
                "display_name": "CLI Owner",
                "workspace_name": "CLI Workspace",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response

    def test_cookie_session_never_exposes_access_token_to_javascript(self):
        denied = self.client.post(
            "/api/v1/auth/register",
            headers={"X-ContentFlow-Session-Mode": "cookie"},
            json={
                "email": "untrusted@example.com",
                "password": "session-password",
                "display_name": "Untrusted",
                "workspace_name": "Untrusted Workspace",
            },
        )
        self.assertEqual(denied.status_code, 403, denied.text)

        registered = self.register_cookie()
        self.assertNotIn("access_token", registered.json())
        set_cookie_headers = [
            value.lower() for value in registered.headers.get_list("set-cookie")
        ]
        self.assertTrue(
            any(
                f"{self.settings.access_cookie_name}=" in value
                and "httponly" in value
                and "samesite=lax" in value
                and "path=/api/v1" in value
                for value in set_cookie_headers
            )
        )
        self.assertTrue(
            any(
                f"{self.settings.refresh_cookie_name}=" in value
                and "httponly" in value
                and "samesite=lax" in value
                and "path=/api/v1/auth" in value
                for value in set_cookie_headers
            )
        )

        current = self.client.get("/api/v1/auth/session")
        self.assertEqual(current.status_code, 200, current.text)

        rejected_write = self.client.post(
            "/api/v1/auth/workspaces",
            json={"name": "Rejected Workspace"},
        )
        self.assertEqual(rejected_write.status_code, 403, rejected_write.text)

        created = self.client.post(
            "/api/v1/auth/workspaces",
            headers=self.cookie_headers(),
            json={"name": "Second Workspace"},
        )
        self.assertEqual(created.status_code, 201, created.text)
        self.assertNotIn("access_token", created.json())
        current = self.client.get("/api/v1/auth/session")
        self.assertEqual(current.status_code, 200, current.text)
        self.assertEqual(current.json()["workspace"]["name"], "Second Workspace")

    def test_refresh_rotation_detects_reuse_and_revokes_session(self):
        self.register_cookie()
        old_refresh = self.client.cookies.get(self.settings.refresh_cookie_name)
        self.assertTrue(old_refresh)

        missing_origin = self.client.post(
            "/api/v1/auth/refresh",
            headers={"X-ContentFlow-Session-Mode": "cookie"},
        )
        self.assertEqual(missing_origin.status_code, 403, missing_origin.text)

        refreshed = self.client.post(
            "/api/v1/auth/refresh",
            headers=self.cookie_headers(),
        )
        self.assertEqual(refreshed.status_code, 200, refreshed.text)
        self.assertNotIn("access_token", refreshed.json())
        second_refresh = self.client.cookies.get(
            self.settings.refresh_cookie_name
        )
        self.assertTrue(second_refresh)
        self.assertNotEqual(old_refresh, second_refresh)

        refreshed_again = self.client.post(
            "/api/v1/auth/refresh",
            headers=self.cookie_headers(),
        )
        self.assertEqual(refreshed_again.status_code, 200, refreshed_again.text)
        latest_refresh = self.client.cookies.get(
            self.settings.refresh_cookie_name
        )
        self.assertTrue(latest_refresh)
        self.assertNotEqual(second_refresh, latest_refresh)

        reused = self.client.post(
            "/api/v1/auth/refresh",
            headers={
                **self.cookie_headers(),
                "Cookie": (
                    f"{self.settings.refresh_cookie_name}={old_refresh}"
                ),
            },
        )
        self.assertEqual(reused.status_code, 401, reused.text)
        self.assertIn("会话已撤销", reused.json()["error"]["message"])

        latest_rejected = self.client.post(
            "/api/v1/auth/refresh",
            headers=self.cookie_headers(),
        )
        self.assertEqual(latest_rejected.status_code, 401, latest_rejected.text)
        current = self.client.get("/api/v1/auth/session")
        self.assertEqual(current.status_code, 401, current.text)

        with db.SessionLocal() as session:
            auth_session = session.scalar(select(AuthSession))
            self.assertIsNotNone(auth_session)
            self.assertIsNotNone(auth_session.revoked_at)
            self.assertEqual(auth_session.revoke_reason, "refresh_token_reuse")
            history = session.scalars(select(AuthRefreshTokenHistory)).all()
            self.assertEqual(len(history), 2)

    def test_logout_revokes_cookie_session_and_clears_cookies(self):
        self.register_cookie()
        logged_out = self.client.post(
            "/api/v1/auth/logout",
            headers=self.cookie_headers(),
        )
        self.assertEqual(logged_out.status_code, 204, logged_out.text)
        deletion_headers = [
            value.lower() for value in logged_out.headers.get_list("set-cookie")
        ]
        self.assertGreaterEqual(
            sum("max-age=0" in value for value in deletion_headers),
            2,
        )
        current = self.client.get("/api/v1/auth/session")
        self.assertEqual(current.status_code, 401, current.text)

        with db.SessionLocal() as session:
            auth_session = session.scalar(select(AuthSession))
            self.assertIsNotNone(auth_session)
            self.assertIsNotNone(auth_session.revoked_at)
            self.assertEqual(auth_session.revoke_reason, "logout")

    def test_bearer_compatibility_and_logout_with_bad_refresh_cookie(self):
        registered = self.register_bearer()
        token = registered.json().get("access_token")
        self.assertTrue(token)
        headers = {"Authorization": f"Bearer {token}"}

        current = self.client.get("/api/v1/auth/session", headers=headers)
        self.assertEqual(current.status_code, 200, current.text)

        bogus_refresh = (
            "33333333-3333-3333-3333-333333333333."
            + ("x" * 64)
        )
        logged_out = self.client.post(
            "/api/v1/auth/logout",
            headers={
                **headers,
                "Origin": self.origin,
                "Cookie": (
                    f"{self.settings.refresh_cookie_name}={bogus_refresh}"
                ),
            },
        )
        self.assertEqual(logged_out.status_code, 204, logged_out.text)
        rejected = self.client.get("/api/v1/auth/session", headers=headers)
        self.assertEqual(rejected.status_code, 401, rejected.text)

    def test_logout_all_revokes_every_session_for_user(self):
        registered = self.register_bearer("all-sessions@example.com")
        first_token = registered.json()["access_token"]
        logged_in = self.client.post(
            "/api/v1/auth/login",
            json={
                "email": "all-sessions@example.com",
                "password": "session-password",
            },
        )
        self.assertEqual(logged_in.status_code, 200, logged_in.text)
        second_token = logged_in.json()["access_token"]

        logged_out = self.client.post(
            "/api/v1/auth/logout-all",
            headers={"Authorization": f"Bearer {first_token}"},
        )
        self.assertEqual(logged_out.status_code, 204, logged_out.text)
        for token in (first_token, second_token):
            rejected = self.client.get(
                "/api/v1/auth/session",
                headers={"Authorization": f"Bearer {token}"},
            )
            self.assertEqual(rejected.status_code, 401, rejected.text)


    def test_login_rate_limit_is_shared_and_hides_identifiers(self):
        self.register_bearer("limited-login@example.com")
        payload = {
            "email": "limited-login@example.com",
            "password": "wrong-password",
        }
        for _ in range(2):
            rejected = self.client.post("/api/v1/auth/login", json=payload)
            self.assertEqual(rejected.status_code, 401, rejected.text)

        blocked = self.client.post("/api/v1/auth/login", json=payload)
        self.assertEqual(blocked.status_code, 429, blocked.text)
        retry_after = int(blocked.headers["retry-after"])
        self.assertGreater(retry_after, 0)
        self.assertLessEqual(
            retry_after,
            self.settings.auth_rate_limit_block_seconds,
        )

        correct_but_blocked = self.client.post(
            "/api/v1/auth/login",
            json={
                "email": "limited-login@example.com",
                "password": "session-password",
            },
        )
        self.assertEqual(
            correct_but_blocked.status_code,
            429,
            correct_but_blocked.text,
        )

        with db.SessionLocal() as session:
            rows = session.scalars(
                select(AuthRateLimit).where(
                    AuthRateLimit.scope.in_(["login-account", "login-ip"])
                )
            ).all()
            self.assertEqual({row.scope for row in rows}, {"login-account", "login-ip"})
            for row in rows:
                self.assertEqual(len(row.key_hash), 64)
                self.assertNotIn("limited-login@example.com", row.key_hash)

    def test_successful_login_clears_account_limit_only(self):
        self.register_bearer("cleared-login@example.com")
        rejected = self.client.post(
            "/api/v1/auth/login",
            json={
                "email": "cleared-login@example.com",
                "password": "wrong-password",
            },
        )
        self.assertEqual(rejected.status_code, 401, rejected.text)
        logged_in = self.client.post(
            "/api/v1/auth/login",
            json={
                "email": "cleared-login@example.com",
                "password": "session-password",
            },
        )
        self.assertEqual(logged_in.status_code, 200, logged_in.text)

        with db.SessionLocal() as session:
            account_rows = session.scalars(
                select(AuthRateLimit).where(
                    AuthRateLimit.scope == "login-account"
                )
            ).all()
            ip_rows = session.scalars(
                select(AuthRateLimit).where(AuthRateLimit.scope == "login-ip")
            ).all()
            self.assertEqual(account_rows, [])
            self.assertEqual(len(ip_rows), 1)

    def test_registration_and_refresh_limits_return_retry_after(self):
        self.settings.auth_refresh_session_attempts = 2
        self.register_bearer("registration-one@example.com")
        self.register_bearer("registration-two@example.com")
        registration_blocked = self.client.post(
            "/api/v1/auth/register",
            json={
                "email": "registration-three@example.com",
                "password": "session-password",
                "display_name": "Third",
                "workspace_name": "Third Workspace",
            },
        )
        self.assertEqual(registration_blocked.status_code, 429)
        self.assertIn("retry-after", registration_blocked.headers)

        self.client.cookies.clear()
        logged_in = self.client.post(
            "/api/v1/auth/login",
            headers=self.cookie_headers(),
            json={
                "email": "registration-one@example.com",
                "password": "session-password",
            },
        )
        self.assertEqual(logged_in.status_code, 200, logged_in.text)
        for _ in range(2):
            refreshed = self.client.post(
                "/api/v1/auth/refresh",
                headers=self.cookie_headers(),
            )
            self.assertEqual(refreshed.status_code, 200, refreshed.text)
        refresh_blocked = self.client.post(
            "/api/v1/auth/refresh",
            headers=self.cookie_headers(),
        )
        self.assertEqual(refresh_blocked.status_code, 429, refresh_blocked.text)
        self.assertIn("retry-after", refresh_blocked.headers)
        current = self.client.get("/api/v1/auth/session")
        self.assertEqual(current.status_code, 200, current.text)


    def test_forwarded_for_is_ignored_without_trusted_proxy_config(self):
        self.settings.auth_registration_ip_attempts = 1
        first = self.client.post(
            "/api/v1/auth/register",
            headers={"X-Forwarded-For": "203.0.113.20"},
            json={
                "email": "untrusted-proxy-one@example.com",
                "password": "session-password",
                "display_name": "Untrusted Proxy One",
                "workspace_name": "Untrusted Proxy One",
            },
        )
        self.assertEqual(first.status_code, 201, first.text)
        blocked = self.client.post(
            "/api/v1/auth/register",
            headers={"X-Forwarded-For": "203.0.113.21"},
            json={
                "email": "untrusted-proxy-two@example.com",
                "password": "session-password",
                "display_name": "Untrusted Proxy Two",
                "workspace_name": "Untrusted Proxy Two",
            },
        )
        self.assertEqual(blocked.status_code, 429, blocked.text)


    def test_forwarded_client_ip_requires_explicit_trusted_proxy_hop(self):
        self.settings.trusted_proxy_hops = 1
        self.settings.auth_registration_ip_attempts = 1
        for index, client_ip in enumerate(("203.0.113.10", "203.0.113.11"), start=1):
            registered = self.client.post(
                "/api/v1/auth/register",
                headers={"X-Forwarded-For": client_ip},
                json={
                    "email": f"proxy-client-{index}@example.com",
                    "password": "session-password",
                    "display_name": f"Proxy Client {index}",
                    "workspace_name": f"Proxy Workspace {index}",
                },
            )
            self.assertEqual(registered.status_code, 201, registered.text)

        with db.SessionLocal() as session:
            rows = session.scalars(
                select(AuthRateLimit).where(
                    AuthRateLimit.scope == "registration-ip"
                )
            ).all()
            self.assertEqual(len(rows), 2)


if __name__ == "__main__":
    unittest.main()
