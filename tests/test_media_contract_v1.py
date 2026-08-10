from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

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
    MediaProviderError,
)
from contentflow.settings import Settings
from contentflow.worker import Worker, media_generation_idempotency_key


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
        version = contract["components"]["parameters"]["ContractVersion"]
        self.assertEqual(version["schema"]["const"], MEDIA_CONTRACT_VERSION)
        self.assertTrue(version["required"])


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
                        headers={"Retry-After": "900"},
                        json={"detail": "private-quota-detail"},
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
                        json={"detail": "private-validation-detail"},
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
            metadata_json={"content_version": 2},
        )
        first = media_generation_idempotency_key(asset)
        second = media_generation_idempotency_key(asset)
        self.assertEqual(first, second)
        self.assertRegex(first, r"^cfm-[0-9a-f]{64}$")
        self.assertNotIn(asset.id, first)
        self.assertNotIn(asset.workspace_id, first)
        asset.metadata_json = {"content_version": 3}
        self.assertNotEqual(first, media_generation_idempotency_key(asset))
        for invalid_version in (0, "invalid"):
            with self.subTest(invalid_version=invalid_version):
                asset.metadata_json = {"content_version": invalid_version}
                with self.assertRaises(MediaProviderError) as captured:
                    media_generation_idempotency_key(asset)
                self.assertFalse(captured.exception.retryable)

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

    def test_non_printable_idempotency_key_is_rejected_before_request(self):
        provider = HTTPMediaProvider(http_settings())
        with self.assertRaises(MediaProviderError) as captured:
            provider.generate(
                kind="image",
                prompt="cover",
                metadata={},
                idempotency_key="invalid\nkey",
            )
        self.assertFalse(captured.exception.retryable)

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
