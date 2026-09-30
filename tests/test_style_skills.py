from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from contentflow import db
from contentflow.api import create_app
from contentflow.settings import Settings
from contentflow.style_skills import (
    BUILTIN_STYLE_SKILLS,
    normalize_style_manifest,
    style_manifest_hash,
)


class StyleSkillTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.settings = Settings(
            environment="development",
            database_url=f"sqlite:///{(root / 'style.db').as_posix()}",
            secret_key="style-test-secret",
            local_storage_dir=root / "storage",
            storage_backend="local",
            require_governed_prompts=False,
            metrics_enabled=False,
            embedding_provider="hash",
            text_provider="mock",
            image_provider="mock",
            video_provider="mock",
        )
        self.client = TestClient(create_app(self.settings))
        self.client.__enter__()
        registered = self.client.post(
            "/api/v1/auth/register",
            json={
                "email": "style@example.com",
                "password": "a-secure-password",
                "display_name": "Style Owner",
                "workspace_name": "Style Workspace",
            },
        )
        self.assertEqual(registered.status_code, 201, registered.text)
        self.headers = {
            "Authorization": f"Bearer {registered.json()['access_token']}"
        }

    def tearDown(self):
        self.client.__exit__(None, None, None)
        db.engine.dispose()
        self.temp_dir.cleanup()

    @staticmethod
    def manifest():
        return {
            "manifest_version": 1,
            "slug": "calm-editor",
            "name": "克制编辑",
            "version": "1.0.0",
            "description": "使用具体、克制且可核验的写作方式。",
            "instructions": ["先给具体判断，再给证据和适用边界"],
            "forbidden_patterns": ["无证据的效果承诺"],
            "platform_instructions": {
                "wechat": ["使用有观点的导语和清晰小标题"]
            },
            "examples": [],
        }

    def test_manifest_is_declarative_and_hash_stable(self):
        normalized = normalize_style_manifest(self.manifest())
        self.assertEqual(
            style_manifest_hash(normalized),
            style_manifest_hash(normalize_style_manifest(self.manifest())),
        )
        unsafe = {**self.manifest(), "python": "import os"}
        with self.assertRaisesRegex(ValueError, "未知字段"):
            normalize_style_manifest(unsafe)

    def test_documented_example_is_a_valid_manifest(self):
        example_path = (
            Path(__file__).parents[1]
            / "docs"
            / "examples"
            / "style-skills"
            / "warm-city-guide.json"
        )
        normalized = normalize_style_manifest(
            __import__("json").loads(example_path.read_text(encoding="utf-8"))
        )
        self.assertEqual(normalized["slug"], "warm-city-guide")

    def test_install_list_and_select_for_campaign(self):
        response = self.client.post(
            "/api/v1/style-skills",
            headers=self.headers,
            json={"manifest": self.manifest()},
        )
        self.assertEqual(response.status_code, 201, response.text)
        installed = response.json()
        self.assertEqual(installed["source"], "workspace")
        self.assertEqual(len(installed["manifest_sha256"]), 64)

        second_manifest = {
            **self.manifest(),
            "slug": "calm-editor-second",
            "name": "克制编辑二号",
        }
        second_response = self.client.post(
            "/api/v1/style-skills",
            headers=self.headers,
            json={"manifest": second_manifest},
        )
        self.assertEqual(second_response.status_code, 201, second_response.text)

        listed = self.client.get("/api/v1/style-skills", headers=self.headers)
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertGreaterEqual(len(listed.json()), len(BUILTIN_STYLE_SKILLS) + 2)

        first_page = self.client.get(
            "/api/v1/style-skills",
            headers=self.headers,
            params={"limit": 1},
        )
        self.assertEqual(first_page.status_code, 200, first_page.text)
        self.assertEqual(first_page.headers["x-contentflow-page-limit"], "1")
        cursor = first_page.headers.get("x-contentflow-next-cursor")
        self.assertIsNotNone(cursor)
        self.assertEqual(
            len([item for item in first_page.json() if item["source"] == "builtin"]),
            len(BUILTIN_STYLE_SKILLS),
        )
        self.assertEqual(
            len([item for item in first_page.json() if item["source"] == "workspace"]),
            1,
        )

        second_page = self.client.get(
            "/api/v1/style-skills",
            headers=self.headers,
            params={"limit": 1, "cursor": cursor},
        )
        self.assertEqual(second_page.status_code, 200, second_page.text)
        self.assertEqual(
            len([item for item in second_page.json() if item["source"] == "builtin"]),
            0,
        )
        self.assertEqual(
            len([item for item in second_page.json() if item["source"] == "workspace"]),
            1,
        )

        campaign = self.client.post(
            "/api/v1/campaigns",
            headers=self.headers,
            json={
                "name": "风格化公众号内容",
                "product_name": "ContentFlow",
                "objective": "验证声明式风格选择",
                "audience": "内容运营人员",
                "platforms": ["wechat"],
                "style_skill_id": installed["id"],
                "quality_profile": "deep",
                "image_source": "search",
            },
        )
        self.assertEqual(campaign.status_code, 201, campaign.text)
        self.assertEqual(
            campaign.json()["brief"]["style_skill_id"],
            installed["id"],
        )


if __name__ == "__main__":
    unittest.main()
