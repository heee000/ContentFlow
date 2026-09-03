from __future__ import annotations

import hashlib
import json
import logging
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from .audit import record_audit
from .entities import Job, ProviderInvocation, ProviderInvocationAttempt


logger = logging.getLogger("contentflow.provider_invocations")
HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
TERMINAL_ATTEMPT_STATUSES = {
    "succeeded",
    "outcome_unknown",
    "late_succeeded",
    "late_failed",
}
PROVIDER_KINDS = frozenset({"text", "embedding", "media", "search"})


class ProviderInvocationLedgerError(RuntimeError):
    """Raised when an external call cannot be durably recorded."""


@dataclass(frozen=True)
class ProviderJobContext:
    job_id: str
    workspace_id: str | None


@dataclass(frozen=True)
class ProviderInvocationHandle:
    invocation_id: str
    attempt_id: str
    request_key: str
    attempt_number: int


_current_job: ContextVar[ProviderJobContext | None] = ContextVar(
    "contentflow_provider_job",
    default=None,
)


@contextmanager
def provider_job_context(job: Job) -> Iterator[None]:
    token = _current_job.set(
        ProviderJobContext(job_id=job.id, workspace_id=job.workspace_id)
    )
    try:
        yield
    finally:
        _current_job.reset(token)


def current_provider_job_id(workspace_id: str) -> str | None:
    current = _current_job.get()
    if current is None or current.workspace_id != workspace_id:
        return None
    return current.job_id


def canonical_evidence(value: Any) -> tuple[str, int]:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest(), len(encoded)


def stable_provider_request_key(
    *,
    workspace_id: str,
    job_id: str | None,
    entity_type: str,
    entity_id: str,
    provider_kind: str,
    provider_name: str,
    model_name: str,
    operation: str,
    ordinal: int,
    request_sha256: str,
) -> str:
    digest, _ = canonical_evidence(
        {
            "schema_version": 1,
            "workspace_id": workspace_id,
            "job_id": job_id,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "provider_kind": provider_kind,
            "provider_name": provider_name,
            "model_name": model_name,
            "operation": operation,
            "ordinal": ordinal,
            "request_sha256": request_sha256,
        }
    )
    return digest


def set_provider_request_key(provider: Any, request_key: str) -> bool:
    setter = getattr(provider, "set_invocation_context", None)
    if not callable(setter):
        return False
    return setter(request_key) is True


def _bounded_text(value: Any, *, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > limit
        or any(ord(character) < 32 or ord(character) == 127 for character in normalized)
    ):
        return None
    return normalized


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _safe_call_metadata(metadata: Any) -> dict[str, Any]:
    source = metadata.get("usage_source") if isinstance(metadata, dict) else None
    values = {
        "input_tokens": _nonnegative_int(
            metadata.get("input_tokens") if isinstance(metadata, dict) else None
        ),
        "output_tokens": _nonnegative_int(
            metadata.get("output_tokens") if isinstance(metadata, dict) else None
        ),
        "total_tokens": _nonnegative_int(
            metadata.get("total_tokens") if isinstance(metadata, dict) else None
        ),
    }
    usage_source = (
        "provider_reported"
        if source == "provider_reported" and any(value is not None for value in values.values())
        else "not_reported"
    )
    return {
        "provider_request_id": _bounded_text(
            metadata.get("provider_request_id") if isinstance(metadata, dict) else None,
            limit=255,
        ),
        "provider_request_id_source": _bounded_text(
            metadata.get("provider_request_id_source")
            if isinstance(metadata, dict)
            else None,
            limit=40,
        ),
        "response_model": _bounded_text(
            metadata.get("response_model") if isinstance(metadata, dict) else None,
            limit=160,
        ),
        "usage_source": usage_source,
        **values,
    }


class ProviderInvocationLedger:
    def __init__(self, bind: Engine) -> None:
        self._session_factory = sessionmaker(
            bind=bind,
            expire_on_commit=False,
            future=True,
        )

    def start(
        self,
        *,
        workspace_id: str,
        job_id: str | None,
        entity_type: str,
        entity_id: str,
        provider_kind: str,
        provider_name: str,
        model_name: str,
        operation: str,
        ordinal: int,
        request_sha256: str,
        request_bytes: int,
        idempotency_key_sent: bool,
    ) -> ProviderInvocationHandle:
        if provider_kind not in PROVIDER_KINDS:
            raise ValueError("unsupported provider_kind")
        if ordinal < 1 or request_bytes < 0 or not HEX_SHA256.fullmatch(request_sha256):
            raise ValueError("invalid provider invocation evidence")
        bounded = {
            "entity_type": _bounded_text(entity_type, limit=80),
            "entity_id": _bounded_text(entity_id, limit=80),
            "provider_name": _bounded_text(provider_name, limit=80),
            "model_name": _bounded_text(model_name, limit=160),
            "operation": _bounded_text(operation, limit=80),
        }
        if any(value is None for value in bounded.values()):
            raise ValueError("invalid provider invocation identity")
        request_key = stable_provider_request_key(
            workspace_id=workspace_id,
            job_id=job_id,
            entity_type=bounded["entity_type"] or "",
            entity_id=bounded["entity_id"] or "",
            provider_kind=provider_kind,
            provider_name=bounded["provider_name"] or "",
            model_name=bounded["model_name"] or "",
            operation=bounded["operation"] or "",
            ordinal=ordinal,
            request_sha256=request_sha256,
        )

        for retry in range(2):
            try:
                with self._session_factory() as session:
                    query = select(ProviderInvocation).where(
                        ProviderInvocation.request_key == request_key
                    )
                    if session.bind and session.bind.dialect.name == "postgresql":
                        query = query.with_for_update()
                    invocation = session.scalar(query)
                    if invocation is None:
                        invocation = ProviderInvocation(
                            workspace_id=workspace_id,
                            job_id=job_id,
                            entity_type=bounded["entity_type"] or "",
                            entity_id=bounded["entity_id"] or "",
                            provider_kind=provider_kind,
                            provider_name=bounded["provider_name"] or "",
                            model_name=bounded["model_name"] or "",
                            operation=bounded["operation"] or "",
                            request_key=request_key,
                            request_sha256=request_sha256,
                            request_bytes=request_bytes,
                            last_status="started",
                        )
                        session.add(invocation)
                        session.flush()
                        superseded_attempts = 0
                    else:
                        expected = (
                            workspace_id,
                            job_id,
                            bounded["entity_type"],
                            bounded["entity_id"],
                            provider_kind,
                            bounded["provider_name"],
                            bounded["model_name"],
                            bounded["operation"],
                            request_sha256,
                            request_bytes,
                        )
                        actual = (
                            invocation.workspace_id,
                            invocation.job_id,
                            invocation.entity_type,
                            invocation.entity_id,
                            invocation.provider_kind,
                            invocation.provider_name,
                            invocation.model_name,
                            invocation.operation,
                            invocation.request_sha256,
                            invocation.request_bytes,
                        )
                        if actual != expected:
                            raise ProviderInvocationLedgerError(
                                "Provider invocation request key conflict"
                            )
                        previous_started = list(
                            session.scalars(
                                select(ProviderInvocationAttempt)
                                .where(
                                    ProviderInvocationAttempt.invocation_id
                                    == invocation.id,
                                    ProviderInvocationAttempt.status == "started",
                                )
                                .order_by(
                                    ProviderInvocationAttempt.attempt_number.asc()
                                )
                            )
                        )
                        completed_at = datetime.now(timezone.utc)
                        for previous in previous_started:
                            previous.status = "outcome_unknown"
                            previous.completed_at = completed_at
                            previous.error_type = "superseded_by_retry"
                            record_audit(
                                session,
                                action="provider.invocation_outcome_unknown",
                                entity_type="provider_invocation",
                                entity_id=invocation.id,
                                workspace_id=workspace_id,
                                actor_user_id=None,
                                metadata={
                                    "attempt": previous.attempt_number,
                                    "job_id": job_id,
                                    "provider_kind": provider_kind,
                                    "provider_name": invocation.provider_name,
                                    "operation": invocation.operation,
                                    "request_key": request_key,
                                    "reason_code": "superseded_by_retry",
                                },
                            )
                        superseded_attempts = len(previous_started)
                        invocation.last_status = "started"

                    attempt_number = int(
                        session.scalar(
                            select(
                                func.coalesce(
                                    func.max(ProviderInvocationAttempt.attempt_number),
                                    0,
                                )
                            ).where(
                                ProviderInvocationAttempt.invocation_id
                                == invocation.id
                            )
                        )
                        or 0
                    ) + 1
                    attempt = ProviderInvocationAttempt(
                        invocation_id=invocation.id,
                        attempt_number=attempt_number,
                        status="started",
                        idempotency_key_sent=idempotency_key_sent,
                        usage_source="not_reported",
                    )
                    session.add(attempt)
                    session.flush()
                    record_audit(
                        session,
                        action="provider.invocation_started",
                        entity_type="provider_invocation",
                        entity_id=invocation.id,
                        workspace_id=workspace_id,
                        actor_user_id=None,
                        metadata={
                            "attempt": attempt_number,
                            "job_id": job_id,
                            "provider_kind": provider_kind,
                            "provider_name": invocation.provider_name,
                            "operation": invocation.operation,
                            "request_key": request_key,
                            "request_sha256": request_sha256,
                            "idempotency_key_sent": idempotency_key_sent,
                            "superseded_attempts": superseded_attempts,
                        },
                    )
                    session.commit()
                    return ProviderInvocationHandle(
                        invocation_id=invocation.id,
                        attempt_id=attempt.id,
                        request_key=request_key,
                        attempt_number=attempt_number,
                    )
            except IntegrityError as error:
                if retry:
                    raise ProviderInvocationLedgerError(
                        "Provider invocation could not be started"
                    ) from error
        raise ProviderInvocationLedgerError("Provider invocation could not be started")

    def finish(
        self,
        handle: ProviderInvocationHandle,
        *,
        status: str,
        call_metadata: Any,
        response_sha256: str | None = None,
        response_bytes: int | None = None,
        error_type: str | None = None,
    ) -> str:
        if status not in {"succeeded", "outcome_unknown"}:
            raise ValueError("invalid provider invocation completion status")
        if response_sha256 is not None and not HEX_SHA256.fullmatch(response_sha256):
            raise ValueError("invalid response_sha256")
        if response_bytes is not None and response_bytes < 0:
            raise ValueError("invalid response_bytes")
        safe_metadata = _safe_call_metadata(call_metadata)
        safe_error_type = _bounded_text(error_type, limit=160)

        with self._session_factory() as session:
            attempt_query = select(ProviderInvocationAttempt).where(
                ProviderInvocationAttempt.id == handle.attempt_id,
                ProviderInvocationAttempt.invocation_id == handle.invocation_id,
            )
            invocation_query = select(ProviderInvocation).where(
                ProviderInvocation.id == handle.invocation_id
            )
            if session.bind and session.bind.dialect.name == "postgresql":
                attempt_query = attempt_query.with_for_update()
                invocation_query = invocation_query.with_for_update()
            invocation = session.scalar(invocation_query)
            attempt = session.scalar(attempt_query)
            if attempt is None or invocation is None:
                raise ProviderInvocationLedgerError(
                    "Provider invocation attempt is missing"
                )
            if attempt.status in TERMINAL_ATTEMPT_STATUSES and attempt.status != "outcome_unknown":
                return attempt.status

            final_status = status
            if attempt.status == "outcome_unknown":
                final_status = (
                    "late_succeeded" if status == "succeeded" else "outcome_unknown"
                )
            attempt.status = final_status
            attempt.completed_at = datetime.now(timezone.utc)
            attempt.provider_request_id = safe_metadata["provider_request_id"]
            attempt.provider_request_id_source = safe_metadata[
                "provider_request_id_source"
            ]
            attempt.response_sha256 = response_sha256
            attempt.response_bytes = response_bytes
            attempt.response_model = safe_metadata["response_model"]
            attempt.usage_source = safe_metadata["usage_source"]
            attempt.input_tokens = safe_metadata["input_tokens"]
            attempt.output_tokens = safe_metadata["output_tokens"]
            attempt.total_tokens = safe_metadata["total_tokens"]
            attempt.error_type = safe_error_type
            invocation.last_status = final_status
            session.flush()
            record_audit(
                session,
                action="provider.invocation_completed",
                entity_type="provider_invocation",
                entity_id=invocation.id,
                workspace_id=invocation.workspace_id,
                actor_user_id=None,
                metadata={
                    "attempt": attempt.attempt_number,
                    "job_id": invocation.job_id,
                    "provider_kind": invocation.provider_kind,
                    "provider_name": invocation.provider_name,
                    "operation": invocation.operation,
                    "request_key": invocation.request_key,
                    "status": final_status,
                    "idempotency_key_sent": attempt.idempotency_key_sent,
                    "provider_request_id_recorded": (
                        attempt.provider_request_id is not None
                    ),
                    "usage_source": attempt.usage_source,
                },
            )
            session.commit()
            return final_status


def mark_job_provider_attempts_outcome_unknown(
    session: Session,
    *,
    job_id: str,
    reason_code: str,
) -> int:
    query = (
        select(ProviderInvocationAttempt, ProviderInvocation)
        .join(
            ProviderInvocation,
            ProviderInvocation.id == ProviderInvocationAttempt.invocation_id,
        )
        .where(
            ProviderInvocation.job_id == job_id,
            ProviderInvocationAttempt.status == "started",
        )
        .order_by(ProviderInvocationAttempt.started_at.asc())
    )
    if session.bind and session.bind.dialect.name == "postgresql":
        query = query.with_for_update()
    rows = list(session.execute(query))
    completed_at = datetime.now(timezone.utc)
    safe_reason = _bounded_text(reason_code, limit=160) or "provider_outcome_unknown"
    for attempt, invocation in rows:
        attempt.status = "outcome_unknown"
        attempt.completed_at = completed_at
        attempt.error_type = safe_reason
        invocation.last_status = "outcome_unknown"
        record_audit(
            session,
            action="provider.invocation_outcome_unknown",
            entity_type="provider_invocation",
            entity_id=invocation.id,
            workspace_id=invocation.workspace_id,
            actor_user_id=None,
            metadata={
                "attempt": attempt.attempt_number,
                "job_id": job_id,
                "provider_kind": invocation.provider_kind,
                "provider_name": invocation.provider_name,
                "operation": invocation.operation,
                "request_key": invocation.request_key,
                "reason_code": safe_reason,
            },
        )
    if rows:
        session.flush()
    return len(rows)


class LedgeredEmbeddingProvider:
    def __init__(
        self,
        provider: Any,
        *,
        ledger: ProviderInvocationLedger,
        workspace_id: str,
        entity_type: str,
        entity_id: str,
        operation: str,
        provider_name: str,
    ) -> None:
        self.provider = provider
        self.ledger = ledger
        self.workspace_id = workspace_id
        self.entity_type = entity_type
        self.entity_id = entity_id
        self.operation = operation
        self.provider_name = provider_name[:80]
        self.model_name = str(getattr(provider, "model_name", "unknown"))[:160]
        self.dimensions = int(getattr(provider, "dimensions"))
        self.ordinal = 0

    def encode(self, text: str) -> list[float]:
        return self.encode_many([text])[0]

    def encode_many(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        self.ordinal += 1
        request_sha256, request_bytes = canonical_evidence(
            {
                "model": self.model_name,
                "dimensions": self.dimensions,
                "input": texts,
            }
        )
        job_id = current_provider_job_id(self.workspace_id)
        request_key = stable_provider_request_key(
            workspace_id=self.workspace_id,
            job_id=job_id,
            entity_type=self.entity_type,
            entity_id=self.entity_id,
            provider_kind="embedding",
            provider_name=self.provider_name,
            model_name=self.model_name,
            operation=self.operation,
            ordinal=self.ordinal,
            request_sha256=request_sha256,
        )
        idempotency_key_sent = set_provider_request_key(self.provider, request_key)
        handle = self.ledger.start(
            workspace_id=self.workspace_id,
            job_id=job_id,
            entity_type=self.entity_type,
            entity_id=self.entity_id,
            provider_kind="embedding",
            provider_name=self.provider_name,
            model_name=self.model_name,
            operation=self.operation,
            ordinal=self.ordinal,
            request_sha256=request_sha256,
            request_bytes=request_bytes,
            idempotency_key_sent=idempotency_key_sent,
        )
        try:
            vectors = self.provider.encode_many(texts)
        except Exception as error:
            try:
                self.ledger.finish(
                    handle,
                    status="outcome_unknown",
                    call_metadata=getattr(self.provider, "last_call_metadata", {}),
                    error_type=type(error).__name__,
                )
            except Exception:
                logger.exception(
                    "provider invocation failure could not be finalized id=%s",
                    handle.invocation_id,
                )
            raise
        try:
            response_sha256, response_bytes = canonical_evidence(vectors)
            self.ledger.finish(
                handle,
                status="succeeded",
                call_metadata=getattr(self.provider, "last_call_metadata", {}),
                response_sha256=response_sha256,
                response_bytes=response_bytes,
            )
        except Exception as error:
            raise ProviderInvocationLedgerError(
                "Provider response was received but its ledger could not be finalized"
            ) from error
        return vectors


def _media_call_metadata(
    *,
    provider: Any,
    model_name: str,
    generation: Any | None = None,
    error: Exception | None = None,
) -> dict[str, Any]:
    metadata = getattr(provider, "last_call_metadata", None)
    safe = dict(metadata) if isinstance(metadata, dict) else {}
    result_metadata = getattr(generation, "metadata", None)
    if isinstance(result_metadata, dict):
        request_id = result_metadata.get("request_id")
        if request_id is not None:
            safe["provider_request_id"] = request_id
            safe["provider_request_id_source"] = "body.request_id"
    if error is not None:
        request_id = getattr(error, "provider_request_id", None)
        request_id_source = getattr(error, "provider_request_id_source", None)
        if request_id is not None:
            safe["provider_request_id"] = request_id
            safe["provider_request_id_source"] = (
                request_id_source or "body.request_id"
            )
    safe["response_model"] = model_name
    return safe


def _media_response_evidence(generation: Any) -> dict[str, Any]:
    content = getattr(generation, "content", None)
    download_url = getattr(generation, "download_url", None)
    return {
        "status": getattr(generation, "status", None),
        "external_task_id": getattr(generation, "external_task_id", None),
        "mime_type": getattr(generation, "mime_type", None),
        "filename": getattr(generation, "filename", None),
        "inline_content_sha256": (
            hashlib.sha256(bytes(content)).hexdigest()
            if isinstance(content, (bytes, bytearray))
            else None
        ),
        "inline_content_bytes": (
            len(content) if isinstance(content, (bytes, bytearray)) else None
        ),
        "download_url_sha256": (
            hashlib.sha256(download_url.encode("utf-8")).hexdigest()
            if isinstance(download_url, str)
            else None
        ),
    }


class LedgeredMediaProvider:
    """Record external media generation and polling without storing media bodies."""

    def __init__(
        self,
        provider: Any,
        *,
        ledger: ProviderInvocationLedger,
        workspace_id: str,
        entity_id: str,
        provider_name: str,
        model_name: str,
    ) -> None:
        self.provider = provider
        self.ledger = ledger
        self.workspace_id = workspace_id
        self.entity_id = entity_id
        self.provider_name = provider_name[:80]
        self.model_name = model_name[:160]
        self.ordinal = 0

    def generate(
        self,
        *,
        kind: str,
        prompt: str,
        metadata: dict[str, Any],
        idempotency_key: str,
    ) -> Any:
        return self._call(
            operation="media.generate",
            request={
                "kind": kind,
                "model": self.model_name,
                "prompt": prompt,
                "metadata": metadata,
            },
            idempotency_key_sent=True,
            invoke=lambda: self.provider.generate(
                kind=kind,
                prompt=prompt,
                metadata=metadata,
                idempotency_key=idempotency_key,
            ),
        )

    def poll(self, external_task_id: str) -> Any:
        return self._call(
            operation="media.poll",
            request={
                "model": self.model_name,
                "external_task_id": external_task_id,
            },
            idempotency_key_sent=False,
            invoke=lambda: self.provider.poll(external_task_id),
        )

    def _call(
        self,
        *,
        operation: str,
        request: dict[str, Any],
        idempotency_key_sent: bool,
        invoke: Any,
    ) -> Any:
        self.ordinal += 1
        request_sha256, request_bytes = canonical_evidence(request)
        job_id = current_provider_job_id(self.workspace_id)
        handle = self.ledger.start(
            workspace_id=self.workspace_id,
            job_id=job_id,
            entity_type="asset",
            entity_id=self.entity_id,
            provider_kind="media",
            provider_name=self.provider_name,
            model_name=self.model_name,
            operation=operation,
            ordinal=self.ordinal,
            request_sha256=request_sha256,
            request_bytes=request_bytes,
            idempotency_key_sent=idempotency_key_sent,
        )
        try:
            generation = invoke()
        except Exception as error:
            try:
                self.ledger.finish(
                    handle,
                    status="outcome_unknown",
                    call_metadata=_media_call_metadata(
                        provider=self.provider,
                        model_name=self.model_name,
                        error=error,
                    ),
                    error_type=type(error).__name__,
                )
            except Exception:
                logger.exception(
                    "media provider invocation failure could not be finalized id=%s",
                    handle.invocation_id,
                )
            raise
        try:
            response_sha256, response_bytes = canonical_evidence(
                _media_response_evidence(generation)
            )
            self.ledger.finish(
                handle,
                status="succeeded",
                call_metadata=_media_call_metadata(
                    provider=self.provider,
                    model_name=self.model_name,
                    generation=generation,
                ),
                response_sha256=response_sha256,
                response_bytes=response_bytes,
            )
        except Exception as error:
            raise ProviderInvocationLedgerError(
                "Media provider response was received but its ledger could not be finalized"
            ) from error
        return generation


class LedgeredSearchProvider:
    """Record read-only external search calls without storing queries or results."""

    def __init__(
        self,
        provider: Any,
        *,
        ledger: ProviderInvocationLedger,
        workspace_id: str,
        entity_id: str,
        model_name: str = "search-api",
    ) -> None:
        self.provider = provider
        self.ledger = ledger
        self.workspace_id = workspace_id
        self.entity_id = entity_id
        self.provider_name = str(getattr(provider, "provider_name", "search"))[:80]
        self.model_name = model_name[:160]
        self.ordinal = 0

    def search(self, *, query: str, limit: int | None = None) -> list[dict[str, Any]]:
        self.ordinal += 1
        request_sha256, request_bytes = canonical_evidence(
            {"query": query, "limit": limit}
        )
        job_id = current_provider_job_id(self.workspace_id)
        handle = self.ledger.start(
            workspace_id=self.workspace_id,
            job_id=job_id,
            entity_type="asset",
            entity_id=self.entity_id,
            provider_kind="search",
            provider_name=self.provider_name,
            model_name=self.model_name,
            operation="search.image",
            ordinal=self.ordinal,
            request_sha256=request_sha256,
            request_bytes=request_bytes,
            idempotency_key_sent=False,
        )
        try:
            results = self.provider.search(query=query, limit=limit)
        except Exception as error:
            try:
                self.ledger.finish(
                    handle,
                    status="outcome_unknown",
                    call_metadata=getattr(self.provider, "last_call_metadata", {}),
                    error_type=type(error).__name__,
                )
            except Exception:
                logger.exception(
                    "search provider invocation failure could not be finalized id=%s",
                    handle.invocation_id,
                )
            raise
        try:
            response_sha256, response_bytes = canonical_evidence(results)
            self.ledger.finish(
                handle,
                status="succeeded",
                call_metadata=getattr(self.provider, "last_call_metadata", {}),
                response_sha256=response_sha256,
                response_bytes=response_bytes,
            )
        except Exception as error:
            raise ProviderInvocationLedgerError(
                "Search provider response was received but its ledger could not be finalized"
            ) from error
        return results
