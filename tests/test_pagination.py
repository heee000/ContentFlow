from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select

from contentflow import db
from contentflow.api import create_app
from contentflow.entities import Campaign
from contentflow.pagination import (
    decode_cursor,
    decode_sequence_cursor,
    encode_cursor,
    encode_sequence_cursor,
)
from contentflow.settings import Settings


class PaginationTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.settings = Settings(
            environment="development",
            database_url=f"sqlite:///{(root / 'pagination.db').as_posix()}",
            secret_key="pagination-test-secret",
            local_storage_dir=root / "storage",
            storage_backend="local",
            require_governed_prompts=False,
            metrics_enabled=False,
            embedding_provider="hash",
            text_provider="mock",
            image_provider="mock",
            video_provider="mock",
            cors_origins=["http://localhost:3001"],
        )
        self.client = TestClient(create_app(self.settings))
        self.client.__enter__()
        registered = self._register("owner@example.com", "Owner Workspace")
        self.headers = {"Authorization": f"Bearer {registered['access_token']}"}
        session_response = self.client.get(
            "/api/v1/auth/session",
            headers=self.headers,
        )
        self.workspace_id = session_response.json()["workspace"]["id"]

    def tearDown(self):
        self.client.__exit__(None, None, None)
        db.engine.dispose()
        self.temp_dir.cleanup()

    def _register(self, email: str, workspace_name: str) -> dict[str, object]:
        response = self.client.post(
            "/api/v1/auth/register",
            json={
                "email": email,
                "password": "a-secure-password",
                "display_name": email.split("@", 1)[0],
                "workspace_name": workspace_name,
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def _create_campaign(self, name: str, headers: dict[str, str] | None = None) -> str:
        response = self.client.post(
            "/api/v1/campaigns",
            headers=headers or self.headers,
            json={
                "name": name,
                "product_name": "分页测试产品",
                "objective": "验证稳定且有界的列表读取",
                "audience": "测试用户",
                "platforms": ["wechat"],
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def test_cursor_pages_are_stable_and_tenant_scoped(self):
        campaign_ids = [self._create_campaign(f"活动 {index}") for index in range(4)]
        same_time = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        with db.SessionLocal() as session:
            campaigns = list(
                session.scalars(
                    select(Campaign).where(Campaign.id.in_(campaign_ids))
                )
            )
            for campaign in campaigns:
                campaign.updated_at = same_time
            session.commit()

        outsider = self._register("outsider@example.com", "Other Workspace")
        outsider_headers = {
            "Authorization": f"Bearer {outsider['access_token']}"
        }
        outsider_id = self._create_campaign("不应泄漏", outsider_headers)

        first = self.client.get(
            "/api/v1/campaigns?limit=2",
            headers={**self.headers, "Origin": "http://localhost:3001"},
        )
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.headers["x-contentflow-page-limit"], "2")
        self.assertIn("x-contentflow-sync-time", first.headers)
        exposed_headers = first.headers.get("access-control-expose-headers", "").lower()
        self.assertIn("x-contentflow-next-cursor", exposed_headers)
        self.assertIn("x-contentflow-sync-time", exposed_headers)
        cursor = first.headers.get("x-contentflow-next-cursor")
        self.assertIsNotNone(cursor)
        first_ids = [item["id"] for item in first.json()]

        second = self.client.get(
            "/api/v1/campaigns",
            params={"limit": 2, "cursor": cursor},
            headers=self.headers,
        )
        self.assertEqual(second.status_code, 200, second.text)
        second_ids = [item["id"] for item in second.json()]
        self.assertFalse(set(first_ids) & set(second_ids))
        self.assertEqual(set(first_ids + second_ids), set(campaign_ids))
        self.assertNotIn(outsider_id, first_ids + second_ids)
        self.assertIsNone(second.headers.get("x-contentflow-next-cursor"))

    def test_updated_after_and_invalid_inputs(self):
        older_id = self._create_campaign("旧活动")
        newer_id = self._create_campaign("新活动")
        cutoff = datetime(2026, 2, 1, tzinfo=timezone.utc)
        with db.SessionLocal() as session:
            older = session.get(Campaign, older_id)
            newer = session.get(Campaign, newer_id)
            assert older is not None and newer is not None
            older.updated_at = cutoff - timedelta(seconds=1)
            newer.updated_at = cutoff + timedelta(seconds=1)
            session.commit()

        incremental = self.client.get(
            "/api/v1/campaigns",
            params={"updated_after": cutoff.isoformat()},
            headers=self.headers,
        )
        self.assertEqual(incremental.status_code, 200, incremental.text)
        self.assertEqual([item["id"] for item in incremental.json()], [newer_id])

        invalid_cursor = self.client.get(
            "/api/v1/campaigns?cursor=not-a-cursor",
            headers=self.headers,
        )
        self.assertEqual(invalid_cursor.status_code, 422, invalid_cursor.text)
        self.assertIn("分页游标无效", invalid_cursor.text)

        invalid_limit = self.client.get(
            "/api/v1/campaigns?limit=201",
            headers=self.headers,
        )
        self.assertEqual(invalid_limit.status_code, 422, invalid_limit.text)

        naive_time = self.client.get(
            "/api/v1/campaigns?updated_after=2026-02-01T00:00:00",
            headers=self.headers,
        )
        self.assertEqual(naive_time.status_code, 422, naive_time.text)

    def test_cursor_codec_rejects_tampering(self):
        timestamp = datetime(2026, 3, 4, 5, 6, 7, tzinfo=timezone.utc)
        cursor = encode_cursor(timestamp, "row-id")
        decoded = decode_cursor(cursor)
        self.assertEqual(decoded.updated_at, timestamp)
        self.assertEqual(decoded.row_id, "row-id")

        with self.assertRaises(HTTPException) as context:
            decode_cursor(f"{cursor}x")
        self.assertEqual(context.exception.status_code, 422)

        sequence_cursor = encode_sequence_cursor(42, "sequence-row")
        sequence_position = decode_sequence_cursor(sequence_cursor)
        self.assertEqual(sequence_position.sequence, 42)
        self.assertEqual(sequence_position.row_id, "sequence-row")

        with self.assertRaises(HTTPException) as sequence_context:
            decode_sequence_cursor(f"{sequence_cursor}x")
        self.assertEqual(sequence_context.exception.status_code, 422)

        with self.assertRaises(HTTPException):
            decode_sequence_cursor(encode_sequence_cursor(True, "sequence-row"))

    def test_all_operational_lists_expose_the_bounded_contract(self):
        paths = (
            "/api/v1/campaigns",
            "/api/v1/runs",
            "/api/v1/contents",
            "/api/v1/assets",
            "/api/v1/publishing/jobs",
            "/api/v1/knowledge/documents",
            "/api/v1/jobs",
            "/api/v1/channels",
            "/api/v1/auth/workspaces",
            "/api/v1/admin/members",
            "/api/v1/admin/audit-logs",
            "/api/v1/style-skills",
            "/api/v1/admin/prompt-releases/history",
            "/api/v1/admin/prompt-eval/suites",
            "/api/v1/admin/prompt-eval/runs",
        )
        for path in paths:
            with self.subTest(path=path):
                response = self.client.get(
                    path,
                    params={"limit": 1},
                    headers=self.headers,
                )
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(
                    response.headers["x-contentflow-page-limit"],
                    "1",
                )
                self.assertIn("x-contentflow-sync-time", response.headers)

    def test_audit_sequence_pages_are_stable(self):
        for index in range(3):
            self._create_campaign(f"审计游标活动 {index}")

        first = self.client.get(
            "/api/v1/admin/audit-logs",
            params={"limit": 2},
            headers=self.headers,
        )
        self.assertEqual(first.status_code, 200, first.text)
        cursor = first.headers.get("x-contentflow-next-cursor")
        self.assertIsNotNone(cursor)
        first_items = first.json()
        self.assertEqual(len(first_items), 2)

        second = self.client.get(
            "/api/v1/admin/audit-logs",
            params={"limit": 2, "cursor": cursor},
            headers=self.headers,
        )
        self.assertEqual(second.status_code, 200, second.text)
        second_items = second.json()
        self.assertTrue(second_items)
        self.assertFalse(
            {item["id"] for item in first_items}
            & {item["id"] for item in second_items}
        )
        combined_sequences = [
            item["chain_sequence"] for item in [*first_items, *second_items]
        ]
        self.assertEqual(combined_sequences, sorted(combined_sequences, reverse=True))

        invalid = self.client.get(
            "/api/v1/admin/audit-logs",
            params={"limit": 2, "cursor": f"{cursor}x"},
            headers=self.headers,
        )
        self.assertEqual(invalid.status_code, 422, invalid.text)
