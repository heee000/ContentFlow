from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Any

from .prompts import BUILTIN_PROMPT_SET, PromptSet
from .providers import Provider


def _json_evidence(value: Any) -> tuple[str, int]:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest(), len(encoded)


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _usage(metadata: Any) -> dict[str, Any]:
    if not isinstance(metadata, dict):
        return {"source": "not_reported"}
    source = metadata.get("usage_source")
    values = {
        "input_tokens": _nonnegative_int(metadata.get("input_tokens")),
        "output_tokens": _nonnegative_int(metadata.get("output_tokens")),
        "total_tokens": _nonnegative_int(metadata.get("total_tokens")),
    }
    if source != "provider_reported" or all(value is None for value in values.values()):
        return {"source": "not_reported"}
    return {"source": "provider_reported", **values}


class AIProvenanceRecorder:
    def __init__(
        self,
        provider: Provider,
        *,
        embedding_provider: str,
        embedding_model: str,
        prompt_set: PromptSet | None = None,
    ) -> None:
        self.provider = provider
        self.provider_name = str(getattr(provider, "provider_name", "unknown"))[:80]
        self.model_name = str(getattr(provider, "model_name", "unknown"))[:160]
        self.embedding_provider = embedding_provider[:80]
        self.embedding_model = embedding_model[:160]
        self.prompt_set = prompt_set or BUILTIN_PROMPT_SET
        self.invocations: list[dict[str, Any]] = []

    def complete_json(
        self,
        stage: str,
        payload: dict[str, Any],
        *,
        platform: str | None = None,
    ) -> dict[str, Any]:
        input_sha256, input_bytes = _json_evidence(payload)
        started_at = datetime.now(timezone.utc)
        started = time.perf_counter()
        base = {
            "ordinal": len(self.invocations) + 1,
            "stage": stage,
            "platform": platform,
            "started_at": started_at.isoformat(),
            "prompt_sha256": self.prompt_set.hashes[stage],
            "input_sha256": input_sha256,
            "input_bytes": input_bytes,
        }
        try:
            result = self.provider.complete_json(
                stage,
                payload,
                system_prompt=self.prompt_set.prompts[stage],
            )
        except Exception as error:
            call_metadata = getattr(self.provider, "last_call_metadata", {})
            invocation = {
                **base,
                "status": "failed",
                "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                "error_type": type(error).__name__,
                "usage": _usage(call_metadata),
            }
            if isinstance(call_metadata, dict) and isinstance(
                call_metadata.get("response_model"), str
            ):
                invocation["response_model"] = call_metadata["response_model"][:160]
            self.invocations.append(invocation)
            snapshot = self.snapshot()
            try:
                error.ai_provenance = snapshot
            except (AttributeError, TypeError):
                wrapped = RuntimeError("AI provider call failed")
                wrapped.ai_provenance = snapshot
                raise wrapped from error
            raise
        output_sha256, output_bytes = _json_evidence(result)
        call_metadata = getattr(self.provider, "last_call_metadata", {})
        invocation = {
            **base,
            "status": "succeeded",
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
            "output_sha256": output_sha256,
            "output_bytes": output_bytes,
            "usage": _usage(call_metadata),
        }
        if isinstance(call_metadata, dict) and isinstance(
            call_metadata.get("response_model"), str
        ):
            invocation["response_model"] = call_metadata["response_model"][:160]
        self.invocations.append(invocation)
        return result

    def snapshot(self) -> dict[str, Any]:
        reported = [
            invocation["usage"]
            for invocation in self.invocations
            if invocation["usage"]["source"] == "provider_reported"
        ]
        if not reported:
            usage = {
                "source": "not_reported",
                "input_tokens": None,
                "output_tokens": None,
                "total_tokens": None,
            }
        else:
            complete = len(reported) == len(self.invocations)

            def aggregate(field: str) -> int | None:
                values = [item[field] for item in reported]
                return (
                    sum(values) if all(value is not None for value in values) else None
                )

            usage = {
                "source": "provider_reported" if complete else "partial",
                "input_tokens": aggregate("input_tokens"),
                "output_tokens": aggregate("output_tokens"),
                "total_tokens": aggregate("total_tokens"),
            }
        stages = {item["stage"] for item in self.invocations}
        return {
            "schema_version": 1,
            "provider": self.provider_name,
            "model": self.model_name,
            "embedding": {
                "provider": self.embedding_provider,
                "model": self.embedding_model,
            },
            "prompt_source": self.prompt_set.source,
            "prompt_release_id": self.prompt_set.release_id,
            "prompt_set_version": self.prompt_set.version,
            "prompt_hashes": {
                stage: self.prompt_set.hashes[stage] for stage in sorted(stages)
            },
            "invocation_count": len(self.invocations),
            "successful_invocations": sum(
                item["status"] == "succeeded" for item in self.invocations
            ),
            "failed_invocations": sum(
                item["status"] == "failed" for item in self.invocations
            ),
            "token_usage": usage,
            "invocations": list(self.invocations),
        }
