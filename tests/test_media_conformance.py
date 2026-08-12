from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import httpx

from contentflow.media_conformance import (
    ConformanceConfigurationError,
    MediaConformanceConfig,
    MediaContractConformanceRunner,
    _write_report,
    run_cli,
)


CONTRACT_HEADERS = {"ContentFlow-Media-Version": "1"}
API_KEY = "super-secret-media-key"


def conformance_config(**overrides) -> MediaConformanceConfig:
    values = {
        "base_url": "https://media.example/v1",
        "api_key": API_KEY,
        "image_model": "configured-image-model",
        "video_model": "configured-video-model",
        "kinds": ("image", "video"),
        "allowed_download_hosts": ("assets.example",),
        "poll_interval_seconds": 0,
        "poll_timeout_seconds": 5,
        "max_response_bytes": 1024 * 1024,
    }
    values.update(overrides)
    return MediaConformanceConfig(**values)


class ConformantMediaService:
    def __init__(self, *, mutate_image_replay: bool = False):
        self.records: dict[tuple[str, str], tuple[str, dict]] = {}
        self.create_counts = {"image": 0, "video": 0}
        self.replay_counts = {"image": 0, "video": 0}
        self.mutate_image_replay = mutate_image_replay
        self.poll_count = 0

    @staticmethod
    def response(status: int, body: dict) -> httpx.Response:
        return httpx.Response(status, headers=CONTRACT_HEADERS, json=body)

    @classmethod
    def error(cls, status: int, code: str) -> httpx.Response:
        return cls.response(
            status,
            {
                "request_id": f"error-{status}",
                "error": {
                    "code": code,
                    "message": "Conformance error response",
                    "retryable": False,
                },
            },
        )

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization") != f"Bearer {API_KEY}":
            return self.error(401, "unauthorized")
        if request.headers.get("ContentFlow-Media-Version") != "1":
            return self.error(400, "contract_version_unsupported")
        if request.method == "GET":
            self.poll_count += 1
            return self.response(
                200,
                {
                    "request_id": "video-poll-request",
                    "data": {
                        "id": "video-task-1",
                        "status": "completed",
                        "url": "https://assets.example/video.mp4",
                        "mime_type": "video/mp4",
                    },
                },
            )

        kind = "image" if request.url.path.endswith("/images/generations") else "video"
        key = request.headers.get("idempotency-key", "")
        payload = json.loads(request.content)
        digest = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        record_key = (kind, key)
        existing = self.records.get(record_key)
        if existing:
            original_digest, original_body = existing
            if digest != original_digest:
                return self.error(409, "idempotency_conflict")
            self.replay_counts[kind] += 1
            if kind == "image" and self.mutate_image_replay:
                return self.response(
                    200,
                    {
                        "request_id": "image-mutated-request",
                        "data": {
                            "b64_json": base64.b64encode(b"different").decode(),
                            "mime_type": "image/png",
                        },
                    },
                )
            return self.response(200 if kind == "image" else 202, original_body)

        self.create_counts[kind] += 1
        if kind == "image":
            body = {
                "request_id": "image-create-request",
                "data": {
                    "b64_json": base64.b64encode(b"blue").decode(),
                    "mime_type": "image/png",
                    "filename": "blue.png",
                },
            }
            status = 200
        else:
            body = {
                "request_id": "video-create-request",
                "data": {"id": "video-task-1", "status": "processing"},
            }
            status = 202
        self.records[record_key] = (digest, body)
        return self.response(status, body)


class MediaConformanceRunnerTest(unittest.TestCase):
    def test_conformant_service_passes_without_duplicate_generation_or_secrets(self):
        service = ConformantMediaService()
        with (
            patch(
                "contentflow.media_conformance.secrets.token_hex",
                side_effect=("public-report-id", "private-request-nonce"),
            ),
            patch(
                "contentflow.media_conformance.secrets.token_bytes",
                return_value=b"K" * 32,
            ),
            httpx.Client(transport=httpx.MockTransport(service)) as client,
        ):
            runner = MediaContractConformanceRunner(
                conformance_config(),
                client=client,
            )
            report = runner.run()

        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["schema_version"], 2)
        self.assertEqual(
            report["fingerprint_algorithm"],
            "hmac-sha256-96-run-scoped",
        )
        self.assertEqual(
            report["target_fingerprint"],
            hmac.new(
                b"K" * 32,
                b"https://media.example/v1",
                hashlib.sha256,
            ).hexdigest()[:24],
        )
        self.assertNotEqual(
            report["target_fingerprint"],
            hashlib.sha256(b"https://media.example/v1").hexdigest()[:24],
        )
        self.assertEqual(report["summary"], {"passed": 11, "failed": 0, "skipped": 0})
        self.assertEqual(report["billable_generation_upper_bound_if_conformant"], 2)
        self.assertEqual(service.create_counts, {"image": 1, "video": 1})
        self.assertEqual(service.replay_counts, {"image": 1, "video": 1})
        self.assertEqual(service.poll_count, 1)
        self.assertEqual(report["run_id"], "public-report-id")
        request_material = repr(service.records)
        self.assertIn("private-request-nonce", request_material)
        self.assertNotIn("public-report-id", request_material)
        serialized = json.dumps(report, ensure_ascii=False)
        expected_secret_material = (
            API_KEY,
            "https://media.example/v1",
            "https://assets.example/video.mp4",
            "configured-image-model",
            "configured-video-model",
            "private-request-nonce",
            "Ymx1ZQ==",
            "image-create-request",
            "video-task-1",
            "video-poll-request",
        )
        for secret in expected_secret_material:
            self.assertIn(secret, runner.secret_values)
            self.assertNotIn(secret, serialized)
        for (_kind, key), (request, _response) in service.records.items():
            self.assertIn(key, runner.secret_values)
            self.assertIn(json.loads(request)["prompt"], runner.secret_values)
        self.assertNotIn("Generate a plain blue", serialized)
        self.assertNotIn("cf-conformance-", serialized)

    def test_video_state_payloads_are_mutually_exclusive(self):
        invalid_results = (
            {
                "id": "task-1",
                "status": "processing",
                "url": "https://assets.example/early.mp4",
            },
            {"id": "task-1", "status": "processing", "error": None},
            {
                "id": "task-1",
                "status": "completed",
                "url": "https://assets.example/video.mp4",
                "error": None,
            },
            {
                "id": "task-1",
                "status": "failed",
                "error": {
                    "code": "failed",
                    "message": "failed",
                    "retryable": True,
                },
            },
            {
                "id": "task-1",
                "status": "completed",
                "url": "https://assets.example/video.mp4",
                "error": {"code": "late", "message": "late", "retryable": False},
            },
            {
                "id": "task-1",
                "status": "failed",
                "url": "https://assets.example/video.mp4",
                "error": {
                    "code": "failed",
                    "message": "failed",
                    "retryable": False,
                },
            },
        )
        for result in invalid_results:
            with self.subTest(status=result["status"]):
                with httpx.Client(
                    transport=httpx.MockTransport(
                        lambda _request, value=result: httpx.Response(
                            202,
                            headers=CONTRACT_HEADERS,
                            json={"data": value},
                        )
                    )
                ) as client:
                    report = MediaContractConformanceRunner(
                        conformance_config(kinds=("video",)),
                        client=client,
                    ).run()
                self.assertEqual(report["status"], "failed")
                self.assertEqual(report["steps"][0]["status"], "failed")

    def test_replay_mismatch_fails_but_keeps_redacted_evidence(self):
        service = ConformantMediaService(mutate_image_replay=True)
        with httpx.Client(transport=httpx.MockTransport(service)) as client:
            report = MediaContractConformanceRunner(
                conformance_config(kinds=("image",)),
                client=client,
            ).run()

        self.assertEqual(report["status"], "failed")
        step = next(
            item
            for item in report["steps"]
            if item["name"] == "image.idempotency_replay"
        )
        self.assertEqual(step["status"], "failed")
        self.assertEqual(step["code"], "idempotency_replay_mismatch")
        self.assertNotIn(API_KEY, json.dumps(report))

    def test_missing_version_and_oversized_response_fail_closed(self):
        handlers = (
            lambda _request: httpx.Response(200, json={"data": {}}),
            lambda _request: httpx.Response(
                200,
                headers=CONTRACT_HEADERS,
                json={"data": {"b64_json": "A" * 2000}},
            ),
        )
        expected_codes = ("response_version_invalid", "response_too_large")
        for handler, expected_code in zip(handlers, expected_codes, strict=True):
            with self.subTest(expected_code=expected_code):
                with httpx.Client(transport=httpx.MockTransport(handler)) as client:
                    report = MediaContractConformanceRunner(
                        conformance_config(
                            kinds=("image",),
                            max_response_bytes=1024,
                        ),
                        client=client,
                    ).run()
                self.assertEqual(report["status"], "failed")
                self.assertEqual(report["steps"][0]["code"], expected_code)
                self.assertEqual(
                    [step["status"] for step in report["steps"][1:]],
                    ["skipped"] * 4,
                )

    def test_unsafe_target_and_download_host_are_rejected(self):
        unsafe_configs = (
            conformance_config(base_url="http://media.example/v1"),
            conformance_config(base_url="https://user:pass@media.example/v1"),
            conformance_config(base_url="https://[broken"),
            conformance_config(allowed_download_hosts=("assets.example/path",)),
            conformance_config(allowed_download_hosts=("[broken",)),
            conformance_config(allowed_download_hosts=("*.example",)),
            conformance_config(allowed_download_hosts=("asset_service.example",)),
            conformance_config(allowed_download_hosts=("assets.example.",)),
        )
        for config in unsafe_configs:
            with self.subTest(base_url=config.base_url):
                with httpx.Client(
                    transport=httpx.MockTransport(lambda _r: None)
                ) as client:
                    with self.assertRaises(ConformanceConfigurationError):
                        MediaContractConformanceRunner(config, client=client)

    def test_config_rejects_unsafe_credentials_and_model_names(self):
        unsafe_configs = (
            conformance_config(api_key="bad\nkey"),
            conformance_config(api_key="x" * 4097),
            conformance_config(image_model="x" * 201),
            conformance_config(video_model="bad\nmodel"),
        )
        for config in unsafe_configs:
            with self.subTest(
                api_key_length=len(config.api_key),
                image_model_length=len(config.image_model or ""),
            ):
                with httpx.Client(
                    transport=httpx.MockTransport(
                        lambda _request: self.fail(
                            "unsafe configuration must not reach the network"
                        )
                    )
                ) as client:
                    with self.assertRaises(ConformanceConfigurationError):
                        MediaContractConformanceRunner(config, client=client)

    def test_response_rejects_non_string_or_empty_request_ids(self):
        invalid_bodies = (
            {"request_id": 0, "data": {"b64_json": "YQ=="}},
            {"request_id": "", "data": {"b64_json": "YQ=="}},
            {"requestId": None, "data": {"b64_json": "YQ=="}},
        )
        for body in invalid_bodies:
            with self.subTest(body=body):
                with httpx.Client(
                    transport=httpx.MockTransport(
                        lambda _request, value=body: httpx.Response(
                            200,
                            headers=CONTRACT_HEADERS,
                            json=value,
                        )
                    )
                ) as client:
                    report = MediaContractConformanceRunner(
                        conformance_config(kinds=("image",)),
                        client=client,
                    ).run()
                self.assertEqual(report["steps"][0]["code"], "response_request_id_invalid")

    def test_error_response_rejects_explicit_null_request_id(self):
        service = ConformantMediaService()

        def handler(request: httpx.Request) -> httpx.Response:
            response = service(request)
            if response.status_code != 409:
                return response
            return httpx.Response(
                409,
                headers=CONTRACT_HEADERS,
                json={
                    "request_id": None,
                    "error": {
                        "code": "idempotency_conflict",
                        "message": "Conformance error response",
                        "retryable": False,
                    },
                },
            )

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            report = MediaContractConformanceRunner(
                conformance_config(kinds=("image",)),
                client=client,
            ).run()
        conflict = next(
            step
            for step in report["steps"]
            if step["name"] == "image.idempotency_conflict"
        )
        self.assertEqual(conflict["status"], "failed")
        self.assertEqual(conflict["code"], "error_request_id_invalid")

    def test_response_rejects_control_labels_reserved_names_and_unsafe_urls(self):
        invalid_results = (
            {"b64_json": "YQ==", "filename": "CON.png"},
            {"b64_json": "YQ==", "filename": "COM1 .png"},
            {"b64_json": ""},
            {"b64_json": "YQ==", "mime_type": "/"},
            {"b64_json": "YQ==", "mime_type": "image/png\r\nx: bad"},
            {"url": "https://assets.example/image.png#fragment"},
            {"url": "https://assets.example:8443/image.png"},
        )
        for result in invalid_results:
            with self.subTest(result=result):
                def handler(request: httpx.Request, value=result) -> httpx.Response:
                    if request.headers.get("authorization") != f"Bearer {API_KEY}":
                        return ConformantMediaService.error(401, "unauthorized")
                    if request.headers.get("ContentFlow-Media-Version") != "1":
                        return ConformantMediaService.error(
                            400,
                            "contract_version_unsupported",
                        )
                    return httpx.Response(
                        200,
                        headers=CONTRACT_HEADERS,
                        json={"data": value},
                    )

                with httpx.Client(
                    transport=httpx.MockTransport(handler)
                ) as client:
                    report = MediaContractConformanceRunner(
                        conformance_config(kinds=("image",)),
                        client=client,
                    ).run()
                self.assertEqual(report["status"], "failed")
                self.assertEqual(report["steps"][0]["status"], "failed")

    def test_config_rejects_non_string_and_non_utf8_values_stably(self):
        unsafe_configs = (
            conformance_config(api_key=123),
            conformance_config(image_model=123),
            conformance_config(image_model="bad\ud800model"),
            conformance_config(kinds=["image"]),
            conformance_config(allowed_download_hosts=["assets.example"]),
            conformance_config(poll_timeout_seconds="120"),
            conformance_config(max_response_bytes=True),
        )
        for config in unsafe_configs:
            with self.subTest(config=config):
                with httpx.Client(
                    transport=httpx.MockTransport(
                        lambda _request: self.fail(
                            "invalid config must not reach network"
                        )
                    )
                ) as client:
                    with self.assertRaises(ConformanceConfigurationError):
                        MediaContractConformanceRunner(config, client=client)

    def test_video_state_enum_is_case_sensitive(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.headers.get("authorization") != f"Bearer {API_KEY}":
                return ConformantMediaService.error(401, "unauthorized")
            if request.headers.get("ContentFlow-Media-Version") != "1":
                return ConformantMediaService.error(
                    400,
                    "contract_version_unsupported",
                )
            return httpx.Response(
                202,
                headers=CONTRACT_HEADERS,
                json={"data": {"id": "task-1", "status": "Processing"}},
            )

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            report = MediaContractConformanceRunner(
                conformance_config(kinds=("video",)),
                client=client,
            ).run()
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["steps"][0]["code"], "video_state_invalid")

    def test_non_finite_poll_timing_is_rejected(self):
        for field, value in (
            ("poll_timeout_seconds", float("nan")),
            ("poll_timeout_seconds", float("inf")),
            ("poll_interval_seconds", float("nan")),
            ("poll_interval_seconds", float("inf")),
        ):
            with self.subTest(field=field, value=value):
                with httpx.Client(
                    transport=httpx.MockTransport(lambda _request: None)
                ) as client:
                    with self.assertRaisesRegex(
                        ConformanceConfigurationError,
                        "poll_configuration_invalid",
                    ):
                        MediaContractConformanceRunner(
                            conformance_config(**{field: value}),
                            client=client,
                        )


class MediaConformanceCliTest(unittest.TestCase):
    def test_cli_requires_explicit_live_confirmation_before_environment_or_network(
        self,
    ):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "report.json"
            stderr = io.StringIO()
            with patch.dict(os.environ, {}, clear=True), redirect_stderr(stderr):
                result = run_cli(["--output", str(output)])
        self.assertEqual(result, 2)
        self.assertIn("live_generation_confirmation_required", stderr.getvalue())
        self.assertFalse(output.exists())

    def test_cli_rejects_non_finite_request_timeout_before_report_or_network(self):
        environment = {
            "CONTENTFLOW_MEDIA_API_BASE": "https://media.example/v1",
            "CONTENTFLOW_MEDIA_API_KEY": API_KEY,
            "CONTENTFLOW_IMAGE_MODEL": "configured-image-model",
            "CONTENTFLOW_MEDIA_DOWNLOAD_ALLOWED_HOSTS": "assets.example",
        }
        for value in ("nan", "inf"):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as temp_dir:
                output = Path(temp_dir) / "report.json"
                with (
                    patch.dict(os.environ, environment, clear=True),
                    patch("contentflow.media_conformance.httpx.Client") as client,
                ):
                    result = run_cli(
                        [
                            "--kind",
                            "image",
                            "--output",
                            str(output),
                            "--confirm-live-generation",
                            "--request-timeout-seconds",
                            value,
                        ]
                    )
                self.assertEqual(result, 2)
                self.assertFalse(output.exists())
                client.assert_not_called()

    def test_cli_writes_exclusive_redacted_report(self):
        service = ConformantMediaService()
        environment = {
            "CONTENTFLOW_MEDIA_API_BASE": "https://media.example/v1",
            "CONTENTFLOW_MEDIA_API_KEY": API_KEY,
            "CONTENTFLOW_IMAGE_MODEL": "configured-image-model",
            "CONTENTFLOW_MEDIA_DOWNLOAD_ALLOWED_HOSTS": '["assets.example"]',
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "report.json"
            stdout = io.StringIO()
            client = httpx.Client(transport=httpx.MockTransport(service))

            def client_factory(**_kwargs):
                self.assertTrue(output.exists())
                return client

            with (
                patch.dict(os.environ, environment, clear=True),
                patch(
                    "contentflow.media_conformance.httpx.Client",
                    side_effect=client_factory,
                ),
                redirect_stdout(stdout),
            ):
                result = run_cli(
                    [
                        "--kind",
                        "image",
                        "--output",
                        str(output),
                        "--confirm-live-generation",
                    ]
                )
            self.assertEqual(result, 0)
            report_text = output.read_text(encoding="utf-8")
            self.assertNotIn(API_KEY, report_text)
            self.assertNotIn("configured-image-model", report_text)
            self.assertIn('"status": "passed"', report_text)
            self.assertEqual(json.loads(stdout.getvalue())["report_path"], str(output))
            original = report_text
            with self.assertRaises(ConformanceConfigurationError):
                _write_report(output, {"status": "new"}, secret_values=(API_KEY,))
            self.assertEqual(output.read_text(encoding="utf-8"), original)

    def test_report_writer_refuses_secret_material_before_creating_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "leaky.json"
            with self.assertRaisesRegex(
                ConformanceConfigurationError,
                "report_secret_redaction_failed",
            ):
                _write_report(
                    output,
                    {"accidental": API_KEY},
                    secret_values=(API_KEY,),
                )
            self.assertFalse(output.exists())
            with self.assertRaisesRegex(
                ConformanceConfigurationError,
                "report_secret_redaction_failed",
            ):
                _write_report(
                    output,
                    {"accidental": "line\nbreak"},
                    secret_values=("line\nbreak",),
                )
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
