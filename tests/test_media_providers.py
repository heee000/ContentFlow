from __future__ import annotations

import base64
import json
import unittest
from unittest.mock import patch

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
        "media_download_allowed_hosts": ["assets.example"],
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
                headers={"ContentFlow-Media-Version": "1"},
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
            idempotency_key="image-key-0001",
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
                    headers={"ContentFlow-Media-Version": "1"},
                    json={"data": {"id": "job-1", "status": "processing"}},
                )
            return httpx.Response(
                200,
                headers={"ContentFlow-Media-Version": "1"},
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
            idempotency_key="video-key-0001",
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
                    lambda _request: httpx.Response(
                        200,
                        headers={"ContentFlow-Media-Version": "1"},
                        json=["unexpected"],
                    )
                )
            ),
        )

        with self.assertRaisesRegex(RuntimeError, "顶层必须是对象"):
            provider.generate(
                kind="image",
                prompt="cover",
                metadata={},
                idempotency_key="image-key-0002",
            )

    def test_malformed_image_response_fails_without_copying_response_body(self):
        provider = HTTPMediaProvider(
            http_settings(),
            client=httpx.Client(
                transport=httpx.MockTransport(
                    lambda _request: httpx.Response(
                        200,
                        headers={"ContentFlow-Media-Version": "1"},
                        json={"internal_detail": "do-not-copy"},
                    )
                )
            ),
        )

        with self.assertRaisesRegex(RuntimeError, "字段不符合") as captured:
            provider.generate(
                kind="image",
                prompt="cover",
                metadata={},
                idempotency_key="image-key-0003",
            )
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
        requested_hosts: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested_hosts.append(request.url.host)
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
        self.assertEqual(requested_hosts, ["assets.example"])

    def test_download_validates_relative_redirect_before_each_request(self):
        requested_paths: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested_paths.append(request.url.path)
            if request.url.path == "/generated.png":
                return httpx.Response(302, headers={"location": "/final.png"})
            return httpx.Response(200, content=b"accepted")

        client = httpx.Client(
            transport=httpx.MockTransport(handler),
            follow_redirects=True,
        )
        result = download_generated_media(
            MediaGeneration(
                status="ready",
                download_url="https://assets.example/generated.png",
            ),
            client=client,
            allowed_hosts=("assets.example",),
            require_https=True,
        )
        self.assertEqual(result, b"accepted")
        self.assertEqual(requested_paths, ["/generated.png", "/final.png"])

    def test_production_download_rejects_http_before_request(self):
        client = httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: self.fail("HTTP download must not be requested")
            )
        )
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            download_generated_media(
                MediaGeneration(
                    status="ready",
                    download_url="http://assets.example/generated.png",
                ),
                client=client,
                allowed_hosts=("assets.example",),
                require_https=True,
            )

    def test_provider_rejects_unsafe_base_before_request(self):
        cases = (
            ("https://user:pass@media.example/v1", False),
            ("https://media.example/v1?token=private", False),
            ("http://media.example/v1", True),
        )
        for base_url, production in cases:
            with self.subTest(base_url=base_url, production=production):
                settings = http_settings(
                    media_api_base=base_url,
                    environment="production" if production else "development",
                )
                with self.assertRaisesRegex(RuntimeError, "Base"):
                    HTTPMediaProvider(
                        settings,
                        client=httpx.Client(
                            transport=httpx.MockTransport(
                                lambda _request: self.fail(
                                    "unsafe media base must not be requested"
                                )
                            )
                        ),
                    )

    def test_download_rejects_any_invalid_allowlist_entry_before_request(self):
        client = httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: self.fail(
                    "invalid allowlist must not reach the network"
                )
            )
        )
        with self.assertRaisesRegex(ValueError, "无效主机名"):
            download_generated_media(
                MediaGeneration(
                    status="ready",
                    download_url="https://assets.example/generated.png",
                ),
                client=client,
                allowed_hosts=("assets.example", "*.example"),
            )

    def test_download_accepts_normalized_ipv6_literal(self):
        client = httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, content=b"asset")
            )
        )
        content = download_generated_media(
            MediaGeneration(
                status="ready",
                download_url="https://[2001:db8::1]/generated.png",
            ),
            client=client,
            allowed_hosts=("2001:db8::1",),
            require_https=True,
        )
        self.assertEqual(content, b"asset")

    def test_download_requires_nonempty_allowlist_before_request(self):
        client = httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: self.fail(
                    "download without allowlist must not be requested"
                )
            )
        )
        with self.assertRaisesRegex(ValueError, "非空"):
            download_generated_media(
                MediaGeneration(
                    status="ready",
                    download_url="https://assets.example/generated.png",
                ),
                client=client,
            )

    def test_production_download_rejects_nondefault_https_port(self):
        client = httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: self.fail(
                    "nondefault production port must not be requested"
                )
            )
        )
        with self.assertRaisesRegex(ValueError, "默认 HTTPS 端口"):
            download_generated_media(
                MediaGeneration(
                    status="ready",
                    download_url="https://assets.example:8443/generated.png",
                ),
                client=client,
                allowed_hosts=("assets.example",),
                require_https=True,
            )

    def test_video_status_enum_is_case_sensitive(self):
        provider = HTTPMediaProvider(
            http_settings(),
            client=httpx.Client(
                transport=httpx.MockTransport(
                    lambda _request: httpx.Response(
                        202,
                        headers={"ContentFlow-Media-Version": "1"},
                        json={"data": {"id": "job-1", "status": "Processing"}},
                    )
                )
            ),
        )
        with self.assertRaisesRegex(RuntimeError, "未知"):
            provider.generate(
                kind="video",
                prompt="video",
                metadata={},
                idempotency_key="video-key-case-sensitive",
            )

    def test_provider_success_response_respects_hard_json_cap(self):
        settings = http_settings(
            max_upload_bytes=100 * 1024 * 1024,
            media_provider_max_response_bytes=64 * 1024,
        )
        provider = HTTPMediaProvider(settings)
        self.assertEqual(provider._max_success_response_bytes, 64 * 1024)
        client = httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    headers={
                        "ContentFlow-Media-Version": "1",
                        "Content-Type": "application/json",
                        "Content-Length": str(64 * 1024 + 1),
                    },
                    content=b"",
                )
            )
        )
        provider = HTTPMediaProvider(settings, client=client)
        with self.assertRaisesRegex(RuntimeError, "大小限制"):
            provider.generate(
                kind="image",
                prompt="cover",
                metadata={},
                idempotency_key="image-key-hard-json-cap",
            )

    def test_success_response_rejects_multiple_image_sources(self):
        provider = HTTPMediaProvider(
            http_settings(),
            client=httpx.Client(
                transport=httpx.MockTransport(
                    lambda _request: httpx.Response(
                        200,
                        headers={"ContentFlow-Media-Version": "1"},
                        json={
                            "data": {
                                "b64_json": base64.b64encode(b"image").decode("ascii"),
                                "url": "https://assets.example/image.png",
                            }
                        },
                    )
                )
            ),
        )
        with self.assertRaisesRegex(RuntimeError, "只能包含一种"):
            provider.generate(
                kind="image",
                prompt="cover",
                metadata={},
                idempotency_key="image-key-multiple-sources",
            )

    def test_failed_video_terminal_requires_closed_error_detail(self):
        private_message = "private-video-provider-detail"
        provider = HTTPMediaProvider(
            http_settings(),
            client=httpx.Client(
                transport=httpx.MockTransport(
                    lambda _request: httpx.Response(
                        200,
                        headers={"ContentFlow-Media-Version": "1"},
                        json={
                            "data": {
                                "id": "job-failed",
                                "status": "failed",
                                "error": {
                                    "code": "moderation_rejected",
                                    "message": private_message,
                                    "retryable": False,
                                },
                            }
                        },
                    )
                )
            ),
        )
        with self.assertRaisesRegex(RuntimeError, "失败终态") as captured:
            provider.generate(
                kind="video",
                prompt="video",
                metadata={},
                idempotency_key="video-key-failed",
            )
        self.assertNotIn(private_message, str(captured.exception))

    def test_failed_video_terminal_must_be_non_retryable(self):
        provider = HTTPMediaProvider(
            http_settings(),
            client=httpx.Client(
                transport=httpx.MockTransport(
                    lambda _request: httpx.Response(
                        200,
                        headers={"ContentFlow-Media-Version": "1"},
                        json={
                            "data": {
                                "id": "job-failed",
                                "status": "failed",
                                "error": {
                                    "code": "temporary_failure",
                                    "message": "must not be retried",
                                    "retryable": True,
                                },
                            }
                        },
                    )
                )
            ),
        )
        with self.assertRaisesRegex(RuntimeError, "重试语义") as captured:
            provider.generate(
                kind="video",
                prompt="video",
                metadata={},
                idempotency_key="video-key-retryable-terminal",
            )
        self.assertFalse(captured.exception.retryable)

    def test_video_task_id_rejects_control_characters(self):
        provider = HTTPMediaProvider(
            http_settings(),
            client=httpx.Client(
                transport=httpx.MockTransport(
                    lambda _request: httpx.Response(
                        202,
                        headers={"ContentFlow-Media-Version": "1"},
                        json={
                            "data": {
                                "id": "job-1\nprivate",
                                "status": "processing",
                            }
                        },
                    )
                )
            ),
        )
        with self.assertRaisesRegex(RuntimeError, "任务 ID"):
            provider.generate(
                kind="video",
                prompt="video",
                metadata={},
                idempotency_key="video-key-control-task-id",
            )

    def test_provider_rejects_empty_or_non_string_request_ids(self):
        for request_id in ("", 0, None):
            with self.subTest(request_id=request_id):
                provider = HTTPMediaProvider(
                    http_settings(),
                    client=httpx.Client(
                        transport=httpx.MockTransport(
                            lambda _request, value=request_id: httpx.Response(
                                200,
                                headers={"ContentFlow-Media-Version": "1"},
                                json={
                                    "request_id": value,
                                    "data": {"b64_json": "YQ=="},
                                },
                            )
                        )
                    ),
                )
                with self.assertRaisesRegex(RuntimeError, "request_id"):
                    provider.generate(
                        kind="image",
                        prompt="cover",
                        metadata={},
                        idempotency_key="image-key-invalid-request-id",
                    )

    def test_provider_closes_only_clients_it_creates(self):
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"ContentFlow-Media-Version": "1"},
                json={
                    "data": {
                        "b64_json": base64.b64encode(b"image").decode("ascii")
                    }
                },
            )

        owned_client = httpx.Client(transport=httpx.MockTransport(handler))
        provider = HTTPMediaProvider(http_settings())
        with patch(
            "contentflow.media_providers.httpx.Client",
            return_value=owned_client,
        ):
            provider.generate(
                kind="image",
                prompt="cover",
                metadata={},
                idempotency_key="image-key-owned-client",
            )
        self.assertTrue(owned_client.is_closed)

        injected_client = httpx.Client(transport=httpx.MockTransport(handler))
        provider = HTTPMediaProvider(http_settings(), client=injected_client)
        provider.generate(
            kind="image",
            prompt="cover",
            metadata={},
            idempotency_key="image-key-injected-client",
        )
        self.assertFalse(injected_client.is_closed)
        injected_client.close()

    def test_download_network_failure_is_retryable_without_url_leak(self):
        private_url = "https://assets.example/generated.png?signature=private-token"

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout(
                f"failed to read {request.url}",
                request=request,
            )

        client = httpx.Client(transport=httpx.MockTransport(handler))
        with self.assertRaises(RuntimeError) as captured:
            download_generated_media(
                MediaGeneration(status="ready", download_url=private_url),
                client=client,
                allowed_hosts=("assets.example",),
            )
        self.assertTrue(captured.exception.retryable)
        self.assertNotIn("private-token", str(captured.exception))
        self.assertFalse(client.is_closed)
        client.close()

    def test_download_http_error_is_classified_without_response_leak(self):
        private_body = "private-origin-response"
        client = httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    429,
                    headers={"Retry-After": "900"},
                    text=private_body,
                )
            )
        )
        with self.assertRaises(RuntimeError) as captured:
            download_generated_media(
                MediaGeneration(
                    status="ready",
                    download_url="https://assets.example/generated.png",
                ),
                client=client,
                allowed_hosts=("assets.example",),
            )
        self.assertTrue(captured.exception.retryable)
        self.assertEqual(captured.exception.status_code, 429)
        self.assertEqual(captured.exception.retry_after_seconds, 300)
        self.assertNotIn(private_body, str(captured.exception))
        client.close()

    def test_download_closes_client_it_creates(self):
        owned_client = httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, content=b"asset")
            )
        )
        with patch(
            "contentflow.media_providers.httpx.Client",
            return_value=owned_client,
        ):
            content = download_generated_media(
                MediaGeneration(
                    status="ready",
                    download_url="https://assets.example/generated.png",
                ),
                allowed_hosts=("assets.example",),
            )
        self.assertEqual(content, b"asset")
        self.assertTrue(owned_client.is_closed)

    def test_provider_rejects_download_url_at_response_boundary(self):
        invalid_results = (
            {"url": "https://untrusted.example/generated.png"},
            {"url": "https://assets.example/generated.png#fragment"},
            {"b64_json": ""},
            {"b64_json": "YQ==", "mime_type": "/"},
        )
        for result in invalid_results:
            with self.subTest(result=result):
                provider = HTTPMediaProvider(
                    http_settings(),
                    client=httpx.Client(
                        transport=httpx.MockTransport(
                            lambda _request, value=result: httpx.Response(
                                200,
                                headers={"ContentFlow-Media-Version": "1"},
                                json={"data": value},
                            )
                        )
                    ),
                )
                with self.assertRaises(RuntimeError) as captured:
                    provider.generate(
                        kind="image",
                        prompt="cover",
                        metadata={},
                        idempotency_key="image-key-response-boundary",
                    )
                self.assertFalse(captured.exception.retryable)

    def test_provider_requires_safe_nonempty_allowlist_at_construction(self):
        for allowed_hosts in ([], ["assets.example", "*.example"]):
            with self.subTest(allowed_hosts=allowed_hosts):
                with self.assertRaisesRegex(RuntimeError, "允许列表"):
                    HTTPMediaProvider(
                        http_settings(
                            media_download_allowed_hosts=allowed_hosts,
                        )
                    )

    def test_provider_rejects_non_utf8_and_non_string_inputs_stably(self):
        provider = HTTPMediaProvider(http_settings())
        invalid_calls = (
            lambda: provider.generate(
                kind="image",
                prompt="bad\ud800prompt",
                metadata={},
                idempotency_key="image-key-invalid-prompt",
            ),
            lambda: provider.generate(
                kind="image",
                prompt="cover",
                metadata={"shots": ["bad\ud800shot"]},
                idempotency_key="image-key-invalid-shot",
            ),
            lambda: provider.poll(None),
        )
        for call in invalid_calls:
            with self.subTest(call=call):
                with self.assertRaises(RuntimeError) as captured:
                    call()
                self.assertFalse(captured.exception.retryable)

    def test_download_rejects_invalid_limit_types_before_network(self):
        client = httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: self.fail("invalid limits must not reach network")
            )
        )
        generation = MediaGeneration(
            status="ready",
            download_url="https://assets.example/generated.png",
        )
        for kwargs in (
            {"max_bytes": True},
            {"max_bytes": "100"},
            {"max_redirects": True},
            {"max_redirects": 1.5},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                download_generated_media(
                    generation,
                    client=client,
                    allowed_hosts=("assets.example",),
                    **kwargs,
                )

    def test_factory_selects_http_provider(self):
        provider = build_media_provider(http_settings(), "image")
        self.assertIsInstance(provider, HTTPMediaProvider)


if __name__ == "__main__":
    unittest.main()
