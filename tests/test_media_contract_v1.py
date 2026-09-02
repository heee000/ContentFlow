from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import httpx
import yaml
from sqlalchemy.orm import sessionmaker

from contentflow.db import Base, build_engine
from contentflow.entities import Asset, Job
from contentflow.job_queue import enqueue_job
from contentflow.media_providers import (
    MEDIA_CONTRACT_VERSION,
    MEDIA_CONTRACT_VERSION_HEADER,
    HTTPMediaProvider,
    MediaGeneration,
    MediaProviderError,
    media_provider_profile_fingerprint,
)
from contentflow.settings import Settings
from contentflow.worker import (
    Worker,
    handle_asset_generate,
    handle_asset_poll,
    _store_generation,
    media_generation_idempotency_key,
)


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "docs" / "contracts" / "contentflow-media-v1.openapi.yml"
CONTRACT_HEADERS = {MEDIA_CONTRACT_VERSION_HEADER: MEDIA_CONTRACT_VERSION}


def http_settings() -> Settings:
    return Settings(
        database_url="sqlite:///contentflow-test.db",
        image_provider="http",
        video_provider="http",
        media_api_base="https://media.example/v1",
        media_api_key="test-media-key",
        media_download_allowed_hosts=["assets.example"],
        image_model="image-model",
        video_model="video-model",
    )


class MediaContractSchemaTest(unittest.TestCase):
    def test_openapi_declares_exact_v1_surface_and_closed_requests(self):
        contract = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
        self.assertEqual(contract["openapi"], "3.1.0")
        self.assertEqual(
            set(contract["paths"]),
            {
                "/images/generations",
                "/videos/generations",
                "/videos/generations/{task_id}",
            },
        )
        for path in ("/images/generations", "/videos/generations"):
            parameters = contract["paths"][path]["post"]["parameters"]
            self.assertEqual(
                [parameter["$ref"] for parameter in parameters],
                [
                    "#/components/parameters/ContractVersion",
                    "#/components/parameters/IdempotencyKey",
                ],
            )
        schemas = contract["components"]["schemas"]
        self.assertFalse(schemas["ImageGenerationRequest"]["additionalProperties"])
        self.assertFalse(schemas["VideoGenerationRequest"]["additionalProperties"])
        self.assertFalse(schemas["GenerationParameters"]["additionalProperties"])
        self.assertEqual(
            schemas["ImageResult"]["properties"]["filename"]["$ref"],
            "#/components/schemas/PortableFilename",
        )
        self.assertEqual(
            schemas["VideoResult"]["properties"]["filename"]["$ref"],
            "#/components/schemas/PortableFilename",
        )
        self.assertEqual(
            schemas["ImageResult"]["properties"]["b64_json"]["minLength"],
            4,
        )
        portable_filename = schemas["PortableFilename"]
        self.assertEqual(portable_filename["maxLength"], 255)
        self.assertIn("[Cc][Oo][Nn]", portable_filename["pattern"])
        version = contract["components"]["parameters"]["ContractVersion"]
        self.assertEqual(version["schema"]["const"], MEDIA_CONTRACT_VERSION)
        self.assertTrue(version["required"])
        for schema_name in (
            "ImageGenerationResponse",
            "VideoGenerationResponse",
            "ErrorResponse",
        ):
            request_id = schemas[schema_name]["properties"]["request_id"]
            self.assertEqual(request_id["minLength"], 1)
            self.assertTrue(request_id["pattern"].endswith("+$"))
        terminal_branch = next(
            branch
            for branch in schemas["VideoResult"]["oneOf"]
            if set(branch["properties"]["status"]["enum"])
            == {"failed", "cancelled", "expired"}
        )
        self.assertFalse(
            terminal_branch["properties"]["error"]["allOf"][1]
            ["properties"]["retryable"]["const"]
        )

        idempotency = contract["components"]["parameters"]["IdempotencyKey"]
        self.assertEqual(
            idempotency["x-contentflow-idempotency-retention-hours"],
            24,
        )
        self.assertIn("idempotency_conflict", idempotency["description"])
        self.assertEqual(
            idempotency["schema"]["pattern"],
            r"^[\x21-\x7E](?:[\x20-\x7E]*[\x21-\x7E])?$",
        )

        expected_error_statuses = {
            "/images/generations": {"400", "401", "403", "409", "429", "500"},
            "/videos/generations": {"400", "401", "403", "409", "429", "500"},
            "/videos/generations/{task_id}": {
                "400",
                "401",
                "403",
                "404",
                "429",
                "500",
            },
        }
        for path, statuses in expected_error_statuses.items():
            method = "get" if "{task_id}" in path else "post"
            responses = contract["paths"][path][method]["responses"]
            for status in statuses:
                self.assertEqual(
                    responses[status]["$ref"],
                    "#/components/responses/ContractError",
                )

        video_result = schemas["VideoResult"]
        self.assertTrue(
            {"failed", "cancelled", "expired"}
            <= set(video_result["properties"]["status"]["enum"])
        )
        self.assertEqual(
            video_result["properties"]["error"]["$ref"],
            "#/components/schemas/ErrorDetail",
        )
        self.assertEqual(
            schemas["ErrorResponse"]["properties"]["error"]["$ref"],
            "#/components/schemas/ErrorDetail",
        )
        error_detail = schemas["ErrorDetail"]
        self.assertFalse(error_detail["additionalProperties"])
        self.assertIn(
            "contract_version_unsupported",
            error_detail["properties"]["code"]["description"],
        )


class MediaContractAdapterTest(unittest.TestCase):
    def test_video_storyboard_uses_video_endpoint_and_minimizes_parameters(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/v1/videos/generations")
            self.assertEqual(request.headers["idempotency-key"], "video-key-0001")
            self.assertEqual(
                request.headers[MEDIA_CONTRACT_VERSION_HEADER],
                MEDIA_CONTRACT_VERSION,
            )
            payload = json.loads(request.content)
            self.assertEqual(
                payload["parameters"],
                {
                    "aspect_ratio": "9:16",
                    "duration_seconds": 20,
                    "shots": ["opening", "close"],
                },
            )
            self.assertNotIn("content_version", json.dumps(payload))
            return httpx.Response(
                202,
                headers=CONTRACT_HEADERS,
                json={"data": {"id": "task-1", "status": "processing"}},
            )

        provider = HTTPMediaProvider(
            http_settings(),
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        result = provider.generate(
            kind="video_storyboard",
            prompt="make a short video",
            metadata={
                "aspect_ratio": "9:16",
                "duration_seconds": 20,
                "shots": ["opening", "close"],
                "content_version": 3,
                "workspace_id": "must-not-leak",
            },
            idempotency_key="video-key-0001",
        )
        self.assertEqual(result.status, "processing")
        self.assertEqual(result.external_task_id, "task-1")

    def test_retryable_status_exposes_only_bounded_retry_metadata(self):
        provider = HTTPMediaProvider(
            http_settings(),
            client=httpx.Client(
                transport=httpx.MockTransport(
                    lambda _request: httpx.Response(
                        429,
                        headers={**CONTRACT_HEADERS, "Retry-After": "900"},
                        json={
                            "request_id": "request-rate-limit",
                            "error": {
                                "code": "rate_limited",
                                "message": "private-quota-detail",
                                "retryable": True,
                            },
                        },
                    )
                )
            ),
        )
        with self.assertRaises(MediaProviderError) as captured:
            provider.generate(
                kind="image",
                prompt="cover",
                metadata={},
                idempotency_key="image-key-0001",
            )
        error = captured.exception
        self.assertTrue(error.retryable)
        self.assertEqual(error.status_code, 429)
        self.assertEqual(error.retry_after_seconds, 300)
        self.assertNotIn("private-quota-detail", str(error))

    def test_permanent_status_is_redacted(self):
        provider = HTTPMediaProvider(
            http_settings(),
            client=httpx.Client(
                transport=httpx.MockTransport(
                    lambda _request: httpx.Response(
                        400,
                        headers=CONTRACT_HEADERS,
                        json={
                            "request_id": "request-validation",
                            "error": {
                                "code": "invalid_request",
                                "message": "private-validation-detail",
                                "retryable": False,
                            },
                        },
                    )
                )
            ),
        )
        with self.assertRaises(MediaProviderError) as captured:
            provider.generate(
                kind="image",
                prompt="cover",
                metadata={},
                idempotency_key="image-key-0002",
            )
        error = captured.exception
        self.assertFalse(error.retryable)
        self.assertEqual(error.status_code, 400)
        self.assertNotIn("private-validation-detail", str(error))

    def test_error_retryable_flag_must_match_http_status(self):
        provider = HTTPMediaProvider(
            http_settings(),
            client=httpx.Client(
                transport=httpx.MockTransport(
                    lambda _request: httpx.Response(
                        429,
                        headers=CONTRACT_HEADERS,
                        json={
                            "error": {
                                "code": "bad-retry-semantics",
                                "message": "private-provider-detail",
                                "retryable": False,
                            },
                        },
                    )
                )
            ),
        )
        with self.assertRaises(MediaProviderError) as captured:
            provider.generate(
                kind="image",
                prompt="cover",
                metadata={},
                idempotency_key="image-key-retry-mismatch",
            )
        self.assertFalse(captured.exception.retryable)
        self.assertNotIn("private-provider-detail", str(captured.exception))

    def test_network_timeout_is_retryable_and_redacted(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout(
                "private-network-target",
                request=request,
            )

        provider = HTTPMediaProvider(
            http_settings(),
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        with self.assertRaises(MediaProviderError) as captured:
            provider.generate(
                kind="image",
                prompt="cover",
                metadata={},
                idempotency_key="image-key-timeout",
            )
        self.assertTrue(captured.exception.retryable)
        self.assertNotIn("private-network-target", str(captured.exception))

    def test_declared_oversized_success_is_rejected_before_body_read(self):
        settings = http_settings()
        settings.max_upload_bytes = 4
        provider = HTTPMediaProvider(settings)
        response_limit = provider._max_success_response_bytes
        client = httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    headers={
                        **CONTRACT_HEADERS,
                        "Content-Type": "application/json",
                        "Content-Length": str(response_limit + 1),
                    },
                    content=b"",
                )
            )
        )
        provider = HTTPMediaProvider(settings, client=client)
        with self.assertRaisesRegex(MediaProviderError, "大小限制") as captured:
            provider.generate(
                kind="image",
                prompt="cover",
                metadata={},
                idempotency_key="image-key-oversized",
            )
        self.assertFalse(captured.exception.retryable)

    def test_success_response_rejects_ambiguous_request_id_names(self):
        provider = HTTPMediaProvider(
            http_settings(),
            client=httpx.Client(
                transport=httpx.MockTransport(
                    lambda _request: httpx.Response(
                        200,
                        headers=CONTRACT_HEADERS,
                        json={
                            "request_id": "request-one",
                            "requestId": "request-two",
                            "data": {"b64_json": "YQ=="},
                        },
                    )
                )
            ),
        )
        with self.assertRaisesRegex(MediaProviderError, "字段冲突"):
            provider.generate(
                kind="image",
                prompt="cover",
                metadata={},
                idempotency_key="image-key-double-request-id",
            )

    def test_huge_retry_after_is_bounded_without_integer_parse_failure(self):
        provider = HTTPMediaProvider(
            http_settings(),
            client=httpx.Client(
                transport=httpx.MockTransport(
                    lambda _request: httpx.Response(
                        429,
                        headers={
                            **CONTRACT_HEADERS,
                            "Retry-After": "9" * 5000,
                        },
                        json={
                            "error": {
                                "code": "rate_limited",
                                "message": "retry later",
                                "retryable": True,
                            }
                        },
                    )
                )
            ),
        )
        with self.assertRaises(MediaProviderError) as captured:
            provider.generate(
                kind="image",
                prompt="cover",
                metadata={},
                idempotency_key="image-key-huge-retry-after",
            )
        self.assertTrue(captured.exception.retryable)
        self.assertEqual(captured.exception.retry_after_seconds, 300)

    def test_success_without_version_echo_is_permanent(self):
        provider = HTTPMediaProvider(
            http_settings(),
            client=httpx.Client(
                transport=httpx.MockTransport(
                    lambda _request: httpx.Response(200, json={"data": {}})
                )
            ),
        )
        with self.assertRaises(MediaProviderError) as captured:
            provider.generate(
                kind="image",
                prompt="cover",
                metadata={},
                idempotency_key="image-key-0003",
            )
        self.assertFalse(captured.exception.retryable)

    def test_asset_key_is_stable_and_changes_with_content_version(self):
        asset = Asset(
            id="asset-1",
            workspace_id="workspace-1",
            kind="video_storyboard",
            content_version=2,
            metadata_json={"content_version": 2},
        )
        first = media_generation_idempotency_key(asset)
        second = media_generation_idempotency_key(asset)
        self.assertEqual(first, second)
        self.assertRegex(first, r"^cfm-[0-9a-f]{64}$")
        self.assertNotIn(asset.id, first)
        self.assertNotIn(asset.workspace_id, first)
        asset.content_version = 3
        self.assertNotEqual(first, media_generation_idempotency_key(asset))
        for invalid_version in (0, "invalid"):
            with self.subTest(invalid_version=invalid_version):
                asset.content_version = invalid_version
                with self.assertRaises(MediaProviderError) as captured:
                    media_generation_idempotency_key(asset)
                self.assertFalse(captured.exception.retryable)

    def test_media_profile_fingerprint_is_stable_and_non_disclosing(self):
        settings = http_settings()
        first = media_provider_profile_fingerprint(settings, "video_storyboard")
        second = media_provider_profile_fingerprint(settings, "video")
        self.assertEqual(first, second)
        self.assertRegex(first, r"^cfp-[0-9a-f]{64}$")
        self.assertNotIn(settings.media_api_base or "", first)
        self.assertNotIn(settings.video_model or "", first)
        settings.video_model = "another-video-model"
        self.assertNotEqual(
            first,
            media_provider_profile_fingerprint(settings, "video"),
        )

    def test_async_poll_rejects_media_provider_profile_drift_before_request(self):
        original = http_settings()
        asset = Asset(
            id="asset-video-1",
            workspace_id="workspace-1",
            kind="video_storyboard",
            status="processing",
            external_task_id="task-1",
            metadata_json={
                "media_provider_profile_fingerprint": (
                    media_provider_profile_fingerprint(original, "video_storyboard")
                )
            },
        )
        changed = http_settings()
        changed.media_api_base = "https://another-media.example/v1"
        session = Mock()
        session.get.return_value = asset
        provider = Mock()
        provider.poll.return_value = MediaGeneration(status="processing")
        with patch(
            "contentflow.worker.build_media_provider",
            return_value=provider,
        ):
            with self.assertRaisesRegex(MediaProviderError, "Provider 配置已变化"):
                handle_asset_poll(session, {"asset_id": asset.id}, changed)
        provider.poll.assert_not_called()

    def test_async_generation_persists_media_provider_profile_before_poll(self):
        settings = http_settings()
        asset = Asset(
            id="asset-video-2",
            workspace_id="workspace-1",
            kind="video_storyboard",
            content_version=1,
            status="pending",
            prompt="create a short video",
            metadata_json={"content_version": 1},
        )
        session = Mock()
        session.get.return_value = asset
        provider = Mock()
        provider.generate.return_value = MediaGeneration(
            status="processing",
            external_task_id="task-2",
            metadata={"request_id": "request-2"},
        )
        with (
            patch("contentflow.worker.build_media_provider", return_value=provider),
            patch("contentflow.worker.enqueue_job") as enqueue,
            patch("contentflow.worker.record_audit"),
        ):
            result = handle_asset_generate(
                session,
                {"asset_id": asset.id},
                settings,
            )
        self.assertEqual(result["status"], "processing")
        self.assertEqual(asset.external_task_id, "task-2")
        self.assertEqual(
            asset.metadata_json["media_provider_profile_fingerprint"],
            media_provider_profile_fingerprint(settings, asset.kind),
        )
        self.assertEqual(asset.metadata_json["request_id"], "request-2")
        enqueue.assert_called_once()

    def test_stale_asset_jobs_stop_before_media_provider_calls(self):
        settings = http_settings()
        stale = Asset(
            id="asset-stale",
            workspace_id="workspace-1",
            kind="image",
            content_version=1,
            status="stale",
            external_task_id="should-not-be-polled",
            metadata_json={"content_version": 1},
        )
        session = Mock()
        session.get.return_value = stale
        with patch("contentflow.worker.build_media_provider") as build_provider:
            generated = handle_asset_generate(
                session,
                {"asset_id": stale.id},
                settings,
            )
            polled = handle_asset_poll(
                session,
                {"asset_id": stale.id},
                settings,
            )
        self.assertEqual(generated["status"], "stale")
        self.assertEqual(polled["status"], "stale")
        build_provider.assert_not_called()

    def test_redirect_and_non_json_success_are_permanent_contract_errors(self):
        responses = (
            httpx.Response(302, headers={"Location": "https://other.example"}),
            httpx.Response(200, headers=CONTRACT_HEADERS, content=b"{}"),
        )
        for index, response in enumerate(responses):
            with self.subTest(index=index):
                provider = HTTPMediaProvider(
                    http_settings(),
                    client=httpx.Client(
                        transport=httpx.MockTransport(
                            lambda _request, value=response: value
                        )
                    ),
                )
                with self.assertRaises(MediaProviderError) as captured:
                    provider.generate(
                        kind="image",
                        prompt="cover",
                        metadata={},
                        idempotency_key="image-key-redirect",
                    )
                self.assertFalse(captured.exception.retryable)

    def test_non_printable_or_padded_idempotency_key_is_rejected_before_request(self):
        provider = HTTPMediaProvider(
            http_settings(),
            client=httpx.Client(
                transport=httpx.MockTransport(
                    lambda _request: self.fail(
                        "invalid idempotency key must not reach network"
                    )
                )
            ),
        )
        for key in ("invalid\nkey", " valid-key-0001", "valid-key-0001 "):
            with self.subTest(key=repr(key)):
                with self.assertRaises(MediaProviderError) as captured:
                    provider.generate(
                        kind="image",
                        prompt="cover",
                        metadata={},
                        idempotency_key=key,
                    )
                self.assertFalse(captured.exception.retryable)

    def test_internal_idempotency_key_space_is_preserved(self):
        observed_key = None

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal observed_key
            observed_key = request.headers["Idempotency-Key"]
            return httpx.Response(
                200,
                headers=CONTRACT_HEADERS,
                json={"data": {"b64_json": "YQ=="}},
            )

        provider = HTTPMediaProvider(
            http_settings(),
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        provider.generate(
            kind="image",
            prompt="cover",
            metadata={},
            idempotency_key="valid key 0001",
        )
        self.assertEqual(observed_key, "valid key 0001")

    def test_invalid_prompt_and_parameters_are_permanent(self):
        provider = HTTPMediaProvider(http_settings())
        for prompt, metadata in (
            ("", {}),
            ("cover", {"ratio": "2:1"}),
            ("video", {"duration_seconds": 0}),
            ("video", {"shots": "not-a-list"}),
        ):
            with self.subTest(prompt=prompt, metadata=metadata):
                with self.assertRaises(MediaProviderError) as captured:
                    provider.generate(
                        kind="image",
                        prompt=prompt,
                        metadata=metadata,
                        idempotency_key="image-key-0004",
                    )
                self.assertFalse(captured.exception.retryable)

    def test_non_string_request_inputs_fail_with_stable_media_error(self):
        provider = HTTPMediaProvider(http_settings())
        invalid_calls = (
            {"prompt": None, "metadata": {}, "idempotency_key": "image-key-stable"},
            {"prompt": "cover", "metadata": None, "idempotency_key": "image-key-stable"},
            {
                "prompt": "cover",
                "metadata": {"ratio": ["1:1"]},
                "idempotency_key": "image-key-stable",
            },
            {"prompt": "cover", "metadata": {}, "idempotency_key": None},
        )
        for values in invalid_calls:
            with self.subTest(values=values):
                with self.assertRaises(MediaProviderError) as captured:
                    provider.generate(kind="image", **values)
                self.assertFalse(captured.exception.retryable)

    def test_video_response_rejects_conflicting_ids_and_null_forbidden_fields(self):
        invalid_results = (
            {
                "id": "task-1",
                "task_id": "task-2",
                "status": "processing",
            },
            {
                "id": "task-1",
                "status": "processing",
                "url": None,
            },
            {
                "id": "task-1",
                "status": "failed",
                "url": None,
                "error": {
                    "code": "failed",
                    "message": "failed",
                    "retryable": False,
                },
            },
        )
        for result in invalid_results:
            with self.subTest(result=result):
                provider = HTTPMediaProvider(
                    http_settings(),
                    client=httpx.Client(
                        transport=httpx.MockTransport(
                            lambda _request, value=result: httpx.Response(
                                202,
                                headers=CONTRACT_HEADERS,
                                json={"data": value},
                            )
                        )
                    ),
                )
                with self.assertRaises(MediaProviderError):
                    provider.generate(
                        kind="video",
                        prompt="video",
                        metadata={},
                        idempotency_key="video-key-invalid-state-fields",
                    )

    def test_non_http_600_status_is_not_classified_as_retryable_5xx(self):
        provider = HTTPMediaProvider(
            http_settings(),
            client=httpx.Client(
                transport=httpx.MockTransport(
                    lambda _request: httpx.Response(
                        600,
                        headers=CONTRACT_HEADERS,
                        json={
                            "error": {
                                "code": "invalid_status",
                                "message": "invalid status",
                                "retryable": False,
                            }
                        },
                    )
                )
            ),
        )
        with self.assertRaises(MediaProviderError) as captured:
            provider.generate(
                kind="image",
                prompt="cover",
                metadata={},
                idempotency_key="image-key-status-600",
            )
        self.assertFalse(captured.exception.retryable)
        self.assertEqual(captured.exception.status_code, 600)

    def test_shot_schema_accepts_product_shapes_and_rejects_unbounded_values(self):
        captured_parameters = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured_parameters.append(json.loads(request.content)["parameters"])
            return httpx.Response(
                202,
                headers=CONTRACT_HEADERS,
                json={"data": {"id": "task-1", "status": "processing"}},
            )

        provider = HTTPMediaProvider(
            http_settings(),
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        accepted = (
            ["0-3 秒：提出问题"],
            [
                {
                    "time": "0-3秒",
                    "visual": "展示路线选择",
                    "voiceover": "先确认路线",
                    "subtitle": "确认路线",
                }
            ],
        )
        for index, shots in enumerate(accepted):
            provider.generate(
                kind="video",
                prompt="video",
                metadata={"shots": shots},
                idempotency_key=f"video-key-shot-{index}",
            )
        self.assertEqual(
            [item["shots"] for item in captured_parameters],
            list(accepted),
        )

        rejected = (
            [""],
            [{"unknown": "value"}],
            [{"visual": 42}],
            ["x" * 5001],
            ["x" * 5000 for _ in range(53)],
        )
        for index, shots in enumerate(rejected):
            with self.subTest(index=index):
                with self.assertRaises(MediaProviderError):
                    provider.generate(
                        kind="video",
                        prompt="video",
                        metadata={"shots": shots},
                        idempotency_key=f"video-key-invalid-shot-{index}",
                    )
        self.assertEqual(len(captured_parameters), 2)

    def test_request_credentials_and_model_names_are_validated_before_network(self):
        def fail_request(_request: httpx.Request) -> httpx.Response:
            self.fail("invalid provider configuration must not reach the network")

        for api_key in ("bad\nkey", "x" * 4097):
            with self.subTest(api_key_length=len(api_key)):
                settings = http_settings()
                settings.media_api_key = api_key
                with self.assertRaises(MediaProviderError):
                    HTTPMediaProvider(
                        settings,
                        client=httpx.Client(
                            transport=httpx.MockTransport(fail_request)
                        ),
                    )

        image_settings = http_settings()
        image_settings.image_model = "x" * 201
        video_settings = http_settings()
        video_settings.video_model = "bad\nmodel"
        for kind, settings in (
            ("image", image_settings),
            ("video", video_settings),
        ):
            with self.subTest(kind=kind):
                provider = HTTPMediaProvider(
                    settings,
                    client=httpx.Client(
                        transport=httpx.MockTransport(fail_request)
                    ),
                )
                with self.assertRaises(MediaProviderError):
                    provider.generate(
                        kind=kind,
                        prompt="media",
                        metadata={},
                        idempotency_key=f"media-key-invalid-{kind}-model",
                    )

    def test_response_labels_and_identifiers_reject_control_or_path_values(self):
        invalid_bodies = (
            {"request_id": "bad\nid", "data": {"b64_json": "YQ=="}},
            {
                "data": {
                    "b64_json": "YQ==",
                    "mime_type": "image/png\r\nx-header: bad",
                }
            },
            {"data": {"b64_json": "YQ==", "filename": "../escape.png"}},
            {"data": {"b64_json": "YQ==", "filename": "CON.png"}},
        )
        for index, body in enumerate(invalid_bodies):
            with self.subTest(index=index):
                provider = HTTPMediaProvider(
                    http_settings(),
                    client=httpx.Client(
                        transport=httpx.MockTransport(
                            lambda _request, value=body: httpx.Response(
                                200,
                                headers=CONTRACT_HEADERS,
                                json=value,
                            )
                        )
                    ),
                )
                with self.assertRaises(MediaProviderError):
                    provider.generate(
                        kind="image",
                        prompt="cover",
                        metadata={},
                        idempotency_key=f"image-key-invalid-label-{index}",
                    )

    def test_worker_converts_download_policy_violation_to_permanent_redacted_error(self):
        private_url = "https://assets.example/image.png?signature=private-token"
        with self.assertRaises(MediaProviderError) as captured:
            _store_generation(
                asset=Asset(
                    id="asset-download-policy",
                    workspace_id="workspace-1",
                    kind="image",
                ),
                settings=Settings(
                    database_url="sqlite:///contentflow-test.db",
                    media_download_allowed_hosts=[],
                ),
                generation=MediaGeneration(
                    status="ready",
                    download_url=private_url,
                ),
            )
        self.assertFalse(captured.exception.retryable)
        self.assertNotIn(private_url, str(captured.exception))
        self.assertNotIn("private-token", str(captured.exception))

    def test_inline_image_is_rejected_before_decoding_past_limit(self):
        settings = http_settings()
        settings.max_upload_bytes = 4
        provider = HTTPMediaProvider(
            settings,
            client=httpx.Client(
                transport=httpx.MockTransport(
                    lambda _request: httpx.Response(
                        200,
                        headers=CONTRACT_HEADERS,
                        json={"data": {"b64_json": "MTIzNDU="}},
                    )
                )
            ),
        )
        with self.assertRaisesRegex(MediaProviderError, "大小限制") as captured:
            provider.generate(
                kind="image",
                prompt="cover",
                metadata={},
                idempotency_key="image-key-0005",
            )
        self.assertFalse(captured.exception.retryable)


class MediaWorkerFailureTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_dir.name) / "media-worker.db"
        self.engine = build_engine(f"sqlite:///{database_path.as_posix()}")
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
            future=True,
        )
        self.settings = Settings(
            database_url=f"sqlite:///{database_path.as_posix()}",
            local_storage_dir=Path(self.temp_dir.name) / "storage",
            worker_lease_seconds=30,
        )

    def tearDown(self):
        self.engine.dispose()
        self.temp_dir.cleanup()

    def _run_failure(self, error: MediaProviderError) -> Job:
        with self.session_factory() as session:
            job = enqueue_job(
                session,
                job_type="test.media",
                payload={"value": 1},
                workspace_id=None,
                idempotency_key=f"test.media:{error.status_code}",
                max_attempts=4,
            )
            session.commit()
            job_id = job.id

        def handler(_session, _payload, _settings):
            raise error

        worker = Worker(
            settings=self.settings,
            worker_id="media-worker",
            session_factory=self.session_factory,
            handlers={"test.media": handler},
        )
        self.assertTrue(worker.run_once())
        with self.session_factory() as session:
            current = session.get(Job, job_id)
            self.assertIsNotNone(current)
            session.expunge(current)
            return current

    def test_permanent_media_error_stops_after_first_attempt(self):
        job = self._run_failure(
            MediaProviderError(
                "permanent media failure",
                retryable=False,
                status_code=400,
            )
        )
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.attempts, 1)

    def test_retryable_media_error_uses_retry_after(self):
        job = self._run_failure(
            MediaProviderError(
                "retryable media failure",
                retryable=True,
                status_code=429,
                retry_after_seconds=37,
            )
        )
        self.assertEqual(job.status, "retry")
        self.assertEqual(job.attempts, 1)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        delay = (job.run_at - now).total_seconds()
        self.assertGreater(delay, 30)
        self.assertLessEqual(delay, 37)


if __name__ == "__main__":
    unittest.main()
