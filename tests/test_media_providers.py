from __future__ import annotations

import base64
import json
import unittest

import httpx

from contentflow.media_providers import (
    HTTPMediaProvider,
    MediaGeneration,
    build_media_provider,
    download_generated_media,
)
from contentflow.settings import Settings


def http_settings(**overrides) -> Settings:
    values = {
        "database_url": "sqlite:///contentflow-test.db",
        "image_provider": "http",
        "video_provider": "http",
        "media_api_base": "https://media.example/v1",
        "media_api_key": "test-media-key",
        "image_model": "configured-image-model",
        "video_model": "configured-video-model",
    }
    values.update(overrides)
    return Settings(**values)


class HTTPMediaProviderTest(unittest.TestCase):
    def test_image_generation_accepts_bounded_base64_result(self):
        expected = b"generated-image"

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/v1/images/generations")
            self.assertEqual(request.headers["authorization"], "Bearer test-media-key")
            payload = json.loads(request.content)
            self.assertEqual(payload["model"], "configured-image-model")
            self.assertEqual(payload["prompt"], "create a cover")
            self.assertEqual(payload["size"], "720x1280")
            return httpx.Response(
                200,
                json={
                    "request_id": "request-1",
                    "data": [
                        {
                            "b64_json": base64.b64encode(expected).decode("ascii"),
                            "mime_type": "image/png",
                        }
                    ],
                },
            )

        provider = HTTPMediaProvider(
            http_settings(),
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        result = provider.generate(
            kind="image",
            prompt="create a cover",
            metadata={"ratio": "9:16"},
        )

        self.assertEqual(result.status, "ready")
        self.assertEqual(result.content, expected)
        self.assertEqual(result.metadata, {"request_id": "request-1"})

    def test_video_generation_and_poll_follow_neutral_contract(self):
        requests: list[tuple[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append((request.method, request.url.path))
            if request.method == "POST":
                payload = json.loads(request.content)
                self.assertEqual(payload["model"], "configured-video-model")
                return httpx.Response(
                    202,
                    json={"data": {"id": "job-1", "status": "processing"}},
                )
            return httpx.Response(
                200,
                json={
                    "requestId": "request-2",
                    "data": {
                        "id": "job-1",
                        "status": "completed",
                        "url": "https://assets.example/generated.mp4",
                    },
                },
            )

        provider = HTTPMediaProvider(
            http_settings(),
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        submitted = provider.generate(
            kind="video",
            prompt="create a short video",
            metadata={"ratio": "16:9"},
        )
        completed = provider.poll(submitted.external_task_id or "")

        self.assertEqual(submitted.status, "processing")
        self.assertEqual(submitted.external_task_id, "job-1")
        self.assertEqual(completed.status, "ready")
        self.assertEqual(completed.download_url, "https://assets.example/generated.mp4")
        self.assertEqual(completed.metadata, {"request_id": "request-2"})
        self.assertEqual(
            requests,
            [
                ("POST", "/v1/videos/generations"),
                ("GET", "/v1/videos/generations/job-1"),
            ],
        )

    def test_non_object_response_has_stable_error(self):
        provider = HTTPMediaProvider(
            http_settings(),
            client=httpx.Client(
                transport=httpx.MockTransport(
                    lambda _request: httpx.Response(200, json=["unexpected"])
                )
            ),
        )

        with self.assertRaisesRegex(RuntimeError, "顶层必须是对象"):
            provider.generate(kind="image", prompt="cover", metadata={})

    def test_malformed_image_response_fails_without_copying_response_body(self):
        provider = HTTPMediaProvider(
            http_settings(),
            client=httpx.Client(
                transport=httpx.MockTransport(
                    lambda _request: httpx.Response(
                        200,
                        json={"internal_detail": "do-not-copy"},
                    )
                )
            ),
        )

        with self.assertRaisesRegex(RuntimeError, "缺少") as captured:
            provider.generate(kind="image", prompt="cover", metadata={})
        self.assertNotIn("do-not-copy", str(captured.exception))

    def test_inline_media_obeys_download_size_limit(self):
        with self.assertRaisesRegex(ValueError, "大小限制"):
            download_generated_media(
                MediaGeneration(status="ready", content=b"12345"),
                max_bytes=4,
            )

    def test_download_rejects_unlisted_host_before_request(self):
        generation = MediaGeneration(
            status="ready",
            download_url="https://untrusted.example/generated.png",
        )
        with self.assertRaisesRegex(ValueError, "允许"):
            download_generated_media(
                generation,
                allowed_hosts=("assets.example",),
            )

    def test_download_rejects_redirect_outside_allowlist(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "assets.example":
                return httpx.Response(
                    302,
                    headers={"location": "https://internal.example/secret"},
                )
            return httpx.Response(200, content=b"not-accepted")

        client = httpx.Client(
            transport=httpx.MockTransport(handler),
            follow_redirects=True,
        )
        generation = MediaGeneration(
            status="ready",
            download_url="https://assets.example/generated.png",
        )
        with self.assertRaisesRegex(ValueError, "允许"):
            download_generated_media(
                generation,
                client=client,
                allowed_hosts=("assets.example",),
            )

    def test_factory_selects_http_provider(self):
        provider = build_media_provider(http_settings(), "image")
        self.assertIsInstance(provider, HTTPMediaProvider)


if __name__ == "__main__":
    unittest.main()
