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
from contentflow.provider_invocations import (
    LedgeredEmbeddingProvider,
    ProviderInvocationLedger,
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


if __name__ == "__main__":
    unittest.main()
