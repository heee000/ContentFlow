from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from contentflow import db
from contentflow.ai_provenance import AIProvenanceRecorder
from contentflow.entities import (
    AuditLog,
    Job,
    ProviderInvocation,
    ProviderInvocationAttempt,
    User,
    Workspace,
)
from contentflow.media_providers import MediaGeneration, MediaProviderError
from contentflow.provider_invocations import (
    LedgeredEmbeddingProvider,
    LedgeredMediaProvider,
    LedgeredSearchProvider,
    ProviderInvocationLedger,
    canonical_evidence,
    provider_job_context,
)


class _LedgerAwareProvider:
    provider_name = "openai-compatible"
    model_name = "ledger-test-model"

    def __init__(self, session_factory) -> None:
        self.session_factory = session_factory
        self.last_call_metadata = {"usage_source": "not_reported"}
        self.request_key: str | None = None
        self.fail = True
        self.started_was_visible = False

    def set_invocation_context(self, request_key: str) -> bool:
        self.request_key = request_key
        return True

    def complete_json(self, _stage, _payload, *, system_prompt=None):
        with self.session_factory() as session:
            attempt = session.scalar(
                select(ProviderInvocationAttempt).where(
                    ProviderInvocationAttempt.status == "started"
                )
            )
            self.started_was_visible = attempt is not None
        if self.fail:
            raise RuntimeError("provider-secret-response-body")
        self.last_call_metadata = {
            "usage_source": "provider_reported",
            "input_tokens": 12,
            "output_tokens": 4,
            "total_tokens": 16,
            "provider_request_id": "request-id-456",
            "provider_request_id_source": "body.id",
            "response_model": "ledger-test-model-r2",
        }
        return {"passed": True}


class ProviderInvocationLedgerTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database = Path(self.temp_dir.name) / "provider-ledger.db"
        self.engine = db.build_engine(f"sqlite:///{database.as_posix()}")
        db.Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
            future=True,
        )
        with self.Session() as session:
            user = User(
                email="ledger@example.com",
                password_hash="not-a-real-password",
                display_name="Ledger Tester",
            )
            session.add(user)
            session.flush()
            workspace = Workspace(
                name="Ledger Workspace",
                slug="ledger-workspace",
                created_by=user.id,
            )
            session.add(workspace)
            session.flush()
            job = Job(
                workspace_id=workspace.id,
                job_type="workflow.execute",
                status="running",
                payload_json={"run_id": "run-ledger"},
                attempts=1,
                idempotency_key="provider-ledger-job",
            )
            session.add(job)
            session.commit()
            self.workspace_id = workspace.id
            self.job_id = job.id

    def tearDown(self):
        self.engine.dispose()
        self.temp_dir.cleanup()

    def _recorder(self, session, provider):
        return AIProvenanceRecorder(
            provider,
            embedding_provider="hash",
            embedding_model="hash-1024",
            ledger_session=session,
            workspace_id=self.workspace_id,
            entity_type="workflow_run",
            entity_id="run-ledger",
        )

    def test_attempt_is_committed_before_call_and_retry_reuses_logical_request(self):
        provider = _LedgerAwareProvider(self.Session)
        sensitive_payload = {"private_prompt": "never-store-provider-input"}
        with self.Session() as session:
            job = session.get(Job, self.job_id)
            recorder = self._recorder(session, provider)
            with provider_job_context(job):
                with self.assertRaisesRegex(RuntimeError, "provider-secret"):
                    recorder.complete_json("review", sensitive_payload)

        self.assertTrue(provider.started_was_visible)
        self.assertIsNotNone(provider.request_key)
        with self.Session() as session:
            invocation = session.scalar(select(ProviderInvocation))
            attempts = list(
                session.scalars(
                    select(ProviderInvocationAttempt).order_by(
                        ProviderInvocationAttempt.attempt_number
                    )
                )
            )
            self.assertEqual(invocation.job_id, self.job_id)
            self.assertEqual(invocation.last_status, "outcome_unknown")
            self.assertEqual([attempt.status for attempt in attempts], ["outcome_unknown"])
            self.assertTrue(attempts[0].idempotency_key_sent)

        provider.fail = False
        with self.Session() as session:
            job = session.get(Job, self.job_id)
            recorder = self._recorder(session, provider)
            with provider_job_context(job):
                result = recorder.complete_json("review", sensitive_payload)
        self.assertEqual(result, {"passed": True})

        with self.Session() as session:
            invocations = list(session.scalars(select(ProviderInvocation)))
            attempts = list(
                session.scalars(
                    select(ProviderInvocationAttempt).order_by(
                        ProviderInvocationAttempt.attempt_number
                    )
                )
            )
            self.assertEqual(len(invocations), 1)
            self.assertEqual(invocations[0].request_key, provider.request_key)
            self.assertEqual(invocations[0].last_status, "succeeded")
            self.assertEqual(
                [attempt.status for attempt in attempts],
                ["outcome_unknown", "succeeded"],
            )
            self.assertEqual(attempts[1].provider_request_id, "request-id-456")
            self.assertEqual(attempts[1].total_tokens, 16)
            self.assertEqual(len(attempts[1].response_sha256 or ""), 64)

            serialized = json.dumps(
                {
                    "invocations": [
                        {
                            column.name: getattr(invocation, column.name)
                            for column in ProviderInvocation.__table__.columns
                        }
                        for invocation in invocations
                    ],
                    "attempts": [
                        {
                            column.name: getattr(attempt, column.name)
                            for column in ProviderInvocationAttempt.__table__.columns
                        }
                        for attempt in attempts
                    ],
                    "audit": [
                        log.metadata_json for log in session.scalars(select(AuditLog))
                    ],
                },
                default=str,
                ensure_ascii=False,
            )
        self.assertNotIn("never-store-provider-input", serialized)
        self.assertNotIn("private_prompt", serialized)
        self.assertNotIn("provider-secret-response-body", serialized)

    def test_embedding_wrapper_records_response_evidence_without_input_text(self):
        class FakeEmbeddingProvider:
            dimensions = 2
            model_name = "embedding-test-model"

            def __init__(self):
                self.last_call_metadata = {"usage_source": "not_reported"}

            def set_invocation_context(self, request_key):
                self.request_key = request_key
                return True

            def encode_many(self, texts):
                self.last_call_metadata = {
                    "usage_source": "provider_reported",
                    "input_tokens": 8,
                    "output_tokens": None,
                    "total_tokens": 8,
                    "provider_request_id": "embedding-request-id",
                    "provider_request_id_source": "body.id",
                }
                return [[1.0, 0.0] for _ in texts]

        provider = FakeEmbeddingProvider()
        wrapped = LedgeredEmbeddingProvider(
            provider,
            ledger=ProviderInvocationLedger(self.engine),
            workspace_id=self.workspace_id,
            entity_type="knowledge_document",
            entity_id="document-ledger",
            operation="embedding.knowledge_index",
            provider_name="openai-compatible",
        )
        with self.Session() as session:
            job = session.get(Job, self.job_id)
            with provider_job_context(job):
                vectors = wrapped.encode_many(
                    ["sensitive embedding input one", "sensitive input two"]
                )
        self.assertEqual(vectors, [[1.0, 0.0], [1.0, 0.0]])

        with self.Session() as session:
            invocation = session.scalar(select(ProviderInvocation))
            attempt = session.scalar(select(ProviderInvocationAttempt))
            self.assertEqual(invocation.provider_kind, "embedding")
            self.assertEqual(invocation.operation, "embedding.knowledge_index")
            self.assertEqual(attempt.status, "succeeded")
            self.assertEqual(attempt.provider_request_id, "embedding-request-id")
            self.assertEqual(attempt.total_tokens, 8)
            self.assertEqual(len(attempt.response_sha256 or ""), 64)
            serialized = json.dumps(
                {
                    "request_sha256": invocation.request_sha256,
                    "response_sha256": attempt.response_sha256,
                }
            )
        self.assertNotIn("sensitive embedding input", serialized)

    def test_retry_closes_a_previous_unfinished_attempt_before_starting_next(self):
        ledger = ProviderInvocationLedger(self.engine)
        evidence_sha256, evidence_bytes = canonical_evidence(
            {"prompt": "private retry prompt"}
        )
        arguments = {
            "workspace_id": self.workspace_id,
            "job_id": self.job_id,
            "entity_type": "asset",
            "entity_id": "asset-retry",
            "provider_kind": "media",
            "provider_name": "http",
            "model_name": "media-model-v1",
            "operation": "media.generate",
            "ordinal": 1,
            "request_sha256": evidence_sha256,
            "request_bytes": evidence_bytes,
            "idempotency_key_sent": True,
        }
        first = ledger.start(**arguments)
        second = ledger.start(**arguments)

        with self.Session() as session:
            attempts = list(
                session.scalars(
                    select(ProviderInvocationAttempt).order_by(
                        ProviderInvocationAttempt.attempt_number
                    )
                )
            )
            self.assertEqual(first.invocation_id, second.invocation_id)
            self.assertEqual(
                [attempt.status for attempt in attempts],
                ["outcome_unknown", "started"],
            )
            self.assertEqual(attempts[0].error_type, "superseded_by_retry")
            self.assertIsNotNone(attempts[0].completed_at)
            self.assertIsNone(attempts[1].completed_at)

        ledger.finish(
            second,
            status="succeeded",
            call_metadata={"usage_source": "not_reported"},
            response_sha256="a" * 64,
            response_bytes=1,
        )
        with self.Session() as session:
            invocation = session.get(ProviderInvocation, first.invocation_id)
            attempts = list(
                session.scalars(
                    select(ProviderInvocationAttempt).order_by(
                        ProviderInvocationAttempt.attempt_number
                    )
                )
            )
            self.assertEqual(invocation.last_status, "succeeded")
            self.assertEqual(
                [attempt.status for attempt in attempts],
                ["outcome_unknown", "succeeded"],
            )

    def test_media_wrapper_records_generation_and_poll_without_media_payloads(self):
        class FakeMediaProvider:
            def generate(self, **_kwargs):
                return MediaGeneration(
                    status="processing",
                    external_task_id="media-task-123",
                    metadata={"request_id": "media-request-123"},
                )

            def poll(self, _external_task_id):
                return MediaGeneration(
                    status="ready",
                    external_task_id="media-task-123",
                    download_url="https://assets.example/private-media-token",
                    mime_type="video/mp4",
                    filename="result.mp4",
                    metadata={"request_id": "media-poll-456"},
                )

        wrapped = LedgeredMediaProvider(
            FakeMediaProvider(),
            ledger=ProviderInvocationLedger(self.engine),
            workspace_id=self.workspace_id,
            entity_id="asset-ledger",
            provider_name="http",
            model_name="media-model-v1",
        )
        with self.Session() as session:
            job = session.get(Job, self.job_id)
            with provider_job_context(job):
                submitted = wrapped.generate(
                    kind="video",
                    prompt="private media prompt must not be stored",
                    metadata={"shots": ["private storyboard"]},
                    idempotency_key="media-idempotency-key",
                )
                completed = wrapped.poll(submitted.external_task_id or "")
        self.assertEqual(completed.status, "ready")

        with self.Session() as session:
            invocations = list(
                session.scalars(
                    select(ProviderInvocation).order_by(
                        ProviderInvocation.created_at,
                        ProviderInvocation.id,
                    )
                )
            )
            attempts = list(
                session.scalars(
                    select(ProviderInvocationAttempt).order_by(
                        ProviderInvocationAttempt.started_at,
                        ProviderInvocationAttempt.id,
                    )
                )
            )
            self.assertEqual(
                [invocation.provider_kind for invocation in invocations],
                ["media", "media"],
            )
            self.assertEqual(
                [invocation.operation for invocation in invocations],
                ["media.generate", "media.poll"],
            )
            self.assertTrue(attempts[0].idempotency_key_sent)
            self.assertFalse(attempts[1].idempotency_key_sent)
            self.assertEqual(attempts[0].provider_request_id, "media-request-123")
            self.assertEqual(attempts[1].provider_request_id, "media-poll-456")
            self.assertEqual(attempts[1].response_model, "media-model-v1")
            serialized = json.dumps(
                {
                    "invocations": [
                        {
                            column.name: getattr(invocation, column.name)
                            for column in ProviderInvocation.__table__.columns
                        }
                        for invocation in invocations
                    ],
                    "attempts": [
                        {
                            column.name: getattr(attempt, column.name)
                            for column in ProviderInvocationAttempt.__table__.columns
                        }
                        for attempt in attempts
                    ],
                },
                default=str,
            )
        self.assertNotIn("private media prompt", serialized)
        self.assertNotIn("private storyboard", serialized)
        self.assertNotIn("private-media-token", serialized)

    def test_media_failure_is_unknown_without_storing_error_body(self):
        class FailingMediaProvider:
            def generate(self, **_kwargs):
                raise MediaProviderError(
                    "private upstream response body",
                    retryable=True,
                    provider_request_id="media-failure-request",
                    provider_request_id_source="body.request_id",
                )

        wrapped = LedgeredMediaProvider(
            FailingMediaProvider(),
            ledger=ProviderInvocationLedger(self.engine),
            workspace_id=self.workspace_id,
            entity_id="asset-failure",
            provider_name="http",
            model_name="media-model-v1",
        )
        with self.Session() as session:
            job = session.get(Job, self.job_id)
            with provider_job_context(job):
                with self.assertRaisesRegex(MediaProviderError, "private upstream"):
                    wrapped.generate(
                        kind="image",
                        prompt="private failed prompt",
                        metadata={},
                        idempotency_key="media-idempotency-key",
                    )
        with self.Session() as session:
            invocation = session.scalar(select(ProviderInvocation))
            attempt = session.scalar(select(ProviderInvocationAttempt))
            self.assertEqual(invocation.last_status, "outcome_unknown")
            self.assertEqual(attempt.status, "outcome_unknown")
            self.assertEqual(attempt.error_type, "MediaProviderError")
            self.assertEqual(attempt.provider_request_id, "media-failure-request")
            serialized = json.dumps(
                {
                    "request_sha256": invocation.request_sha256,
                    "error_type": attempt.error_type,
                }
            )
        self.assertNotIn("private upstream response body", serialized)
        self.assertNotIn("private failed prompt", serialized)

    def test_search_wrapper_records_only_evidence_hashes(self):
        class FakeSearchProvider:
            provider_name = "openverse"

            def search(self, *, query, limit=None):
                self.query = query
                self.limit = limit
                return [
                    {
                        "title": "private result title",
                        "download_url": "https://upload.wikimedia.org/private.jpg",
                    }
                ]

        wrapped = LedgeredSearchProvider(
            FakeSearchProvider(),
            ledger=ProviderInvocationLedger(self.engine),
            workspace_id=self.workspace_id,
            entity_id="asset-search",
            model_name="openverse-images-v1",
        )
        with self.Session() as session:
            job = session.get(Job, self.job_id)
            with provider_job_context(job):
                results = wrapped.search(query="private search query", limit=3)
        self.assertEqual(results[0]["title"], "private result title")

        with self.Session() as session:
            invocation = session.scalar(select(ProviderInvocation))
            attempt = session.scalar(select(ProviderInvocationAttempt))
            self.assertEqual(invocation.provider_kind, "search")
            self.assertEqual(invocation.operation, "search.image")
            self.assertEqual(invocation.provider_name, "openverse")
            self.assertEqual(attempt.status, "succeeded")
            self.assertFalse(attempt.idempotency_key_sent)
            serialized = json.dumps(
                {
                    "request_sha256": invocation.request_sha256,
                    "response_sha256": attempt.response_sha256,
                }
            )
        self.assertNotIn("private search query", serialized)
        self.assertNotIn("private result title", serialized)


if __name__ == "__main__":
    unittest.main()
