from __future__ import annotations

import unittest

import httpx

from contentflow.image_search import ImageSearchError, OpenverseImageSearchProvider
from contentflow.settings import Settings


class ImageSearchTest(unittest.TestCase):
    @staticmethod
    def settings() -> Settings:
        return Settings(
            environment="development",
            database_url="sqlite://",
            secret_key="image-search-test-secret",
            storage_backend="local",
            require_governed_prompts=False,
            metrics_enabled=False,
            embedding_provider="hash",
            text_provider="mock",
            image_provider="mock",
            video_provider="mock",
            image_search_provider="openverse",
            openverse_api_base="https://api.openverse.org/v1",
            image_search_download_allowed_hosts=["upload.wikimedia.org"],
        )

    def test_filters_and_normalizes_open_license_candidates(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.host, "api.openverse.org")
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "title": "Beijing street",
                            "creator": "Example creator",
                            "creator_url": "https://evil.invalid/profile",
                            "license": "by-sa",
                            "license_version": "4.0",
                            "license_url": (
                                "https://creativecommons.org/licenses/by-sa/4.0/"
                            ),
                            "source": "wikimedia",
                            "provider": "wikimedia",
                            "foreign_landing_url": (
                                "https://commons.wikimedia.org/wiki/File:Example.jpg"
                            ),
                            "url": (
                                "https://upload.wikimedia.org/wikipedia/"
                                "commons/example.jpg"
                            ),
                            "thumbnail": (
                                "https://upload.wikimedia.org/wikipedia/"
                                "commons/thumb/example.jpg"
                            ),
                            "width": 1600,
                            "height": 1200,
                        },
                        {
                            "title": "Disallowed host",
                            "creator": "Unknown",
                            "license": "cc0",
                            "url": "https://example.invalid/image.jpg",
                        },
                    ]
                },
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        try:
            provider = OpenverseImageSearchProvider(self.settings(), client=client)
            candidates = provider.search(query="北京 城市街景")
        finally:
            client.close()

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["license"], "by-sa")
        self.assertEqual(candidates[0]["provider"], "wikimedia")
        self.assertEqual(candidates[0]["creator_url"], "")
        self.assertEqual(
            candidates[0]["landing_url"],
            "https://commons.wikimedia.org/wiki/File:Example.jpg",
        )
        self.assertEqual(len(candidates[0]["id"]), 24)

    def test_rejects_when_no_candidate_meets_download_boundary(self):
        client = httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    json={
                        "results": [
                            {
                                "title": "Wrong host",
                                "creator": "Unknown",
                                "license": "cc0",
                                "url": "https://example.invalid/image.jpg",
                            }
                        ]
                    },
                )
            )
        )
        try:
            provider = OpenverseImageSearchProvider(self.settings(), client=client)
            with self.assertRaises(ImageSearchError):
                provider.search(query="北京")
        finally:
            client.close()


if __name__ == "__main__":
    unittest.main()
