"""Live conformance runner for ContentFlow Media Contract v1.

Potentially billable calls require an explicit CLI confirmation. Reports retain
only bounded timing, status and fingerprints, never credentials or media data.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import hmac
import json
import math
import os
import secrets
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import quote, urlparse

import httpx

from .filenames import safe_filename
from .network_validation import normalize_exact_host

from .media_providers import MEDIA_CONTRACT_VERSION, MEDIA_CONTRACT_VERSION_HEADER

ACTIVE_VIDEO_STATES = {"queued", "pending", "processing", "running"}
SUCCESS_VIDEO_STATES = {"ready", "completed", "succeeded"}
FAILED_VIDEO_STATES = {"failed", "cancelled", "expired"}
REPORT_SCHEMA_VERSION = 2
REPORT_FINGERPRINT_ALGORITHM = "hmac-sha256-96-run-scoped"
DEFAULT_MAX_RESPONSE_BYTES = 16 * 1024 * 1024


class ConformanceConfigurationError(ValueError):
    """The runner configuration is unsafe or incomplete."""


class ConformanceViolation(RuntimeError):
    """A stable, redacted contract violation."""

    def __init__(self, code: str, *, http_status: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.http_status = http_status


@dataclass(frozen=True, slots=True)
class MediaConformanceConfig:
    base_url: str
    api_key: str
    image_model: str | None
    video_model: str | None
    kinds: tuple[str, ...]
    allowed_download_hosts: tuple[str, ...]
    allow_insecure_http: bool = False
    poll_timeout_seconds: float = 120.0
    poll_interval_seconds: float = 2.0
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES


@dataclass(slots=True)
class ResponseEvidence:
    status_code: int
    body: dict[str, Any]
    duration_ms: float


@dataclass(slots=True)
class GenerationIdentity:
    fingerprint: str
    state: str
    task_id: str | None = None


@dataclass(slots=True)
class StepOutcome:
    value: Any = None
    evidence: dict[str, Any] = field(default_factory=dict)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fingerprint(value: str | bytes, *, key: bytes) -> str:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return hmac.new(key, raw, hashlib.sha256).hexdigest()[:24]


def _is_utf8_text(value: str) -> bool:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _canonical_fingerprint(value: Any, *, key: bytes) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return _fingerprint(canonical, key=key)


def _validate_config(config: MediaConformanceConfig) -> str:
    if not isinstance(config.base_url, str) or any(
        ord(char) <= 0x20 or ord(char) == 0x7F
        for char in config.base_url
    ):
        raise ConformanceConfigurationError("media_base_url_invalid")
    try:
        config.base_url.encode("utf-8")
        parsed = urlparse(config.base_url)
        parsed.port
    except ValueError as error:
        raise ConformanceConfigurationError("media_base_url_invalid") from error
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ConformanceConfigurationError("media_base_url_contains_unsafe_parts")
    if (
        not parsed.hostname
        or normalize_exact_host(parsed.hostname) is None
        or parsed.scheme not in {"http", "https"}
    ):
        raise ConformanceConfigurationError("media_base_url_invalid")
    if parsed.scheme != "https" and not config.allow_insecure_http:
        raise ConformanceConfigurationError("https_required")
    if not config.api_key:
        raise ConformanceConfigurationError("media_api_key_missing")
    if (
        not isinstance(config.api_key, str)
        or len(config.api_key) > 4096
        or not config.api_key.isascii()
        or any(
            not 0x21 <= ord(char) <= 0x7E
            for char in config.api_key
        )
    ):
        raise ConformanceConfigurationError("media_api_key_invalid")
    if (
        not isinstance(config.kinds, tuple)
        or not config.kinds
        or any(kind not in {"image", "video"} for kind in config.kinds)
    ):
        raise ConformanceConfigurationError("media_kinds_invalid")
    if len(set(config.kinds)) != len(config.kinds):
        raise ConformanceConfigurationError("media_kinds_duplicated")
    if "image" in config.kinds and not config.image_model:
        raise ConformanceConfigurationError("image_model_missing")
    if "video" in config.kinds and not config.video_model:
        raise ConformanceConfigurationError("video_model_missing")
    for model in (config.image_model, config.video_model):
        if model is not None and (
            not isinstance(model, str)
            or not 1 <= len(model) <= 200
            or model != model.strip()
            or not _is_utf8_text(model)
            or any(
                ord(char) < 0x20 or ord(char) == 0x7F
                for char in model
            )
        ):
            raise ConformanceConfigurationError("media_model_invalid")
    if not isinstance(config.allowed_download_hosts, tuple):
        raise ConformanceConfigurationError("download_host_allowlist_invalid")
    hosts = tuple(
        dict.fromkeys(
            normalize_exact_host(host)
            for host in config.allowed_download_hosts
        )
    )
    if not hosts:
        raise ConformanceConfigurationError("download_host_allowlist_missing")
    if any(normalize_exact_host(host) is None for host in hosts):
        raise ConformanceConfigurationError("download_host_allowlist_invalid")
    if (
        isinstance(config.poll_timeout_seconds, bool)
        or not isinstance(config.poll_timeout_seconds, (int, float))
        or isinstance(config.poll_interval_seconds, bool)
        or not isinstance(config.poll_interval_seconds, (int, float))
        or not math.isfinite(config.poll_timeout_seconds)
        or not math.isfinite(config.poll_interval_seconds)
        or config.poll_timeout_seconds <= 0
        or config.poll_interval_seconds < 0
    ):
        raise ConformanceConfigurationError("poll_configuration_invalid")
    if (
        isinstance(config.max_response_bytes, bool)
        or not isinstance(config.max_response_bytes, int)
        or not 1024 <= config.max_response_bytes <= 100 * 1024 * 1024
    ):
        raise ConformanceConfigurationError("max_response_bytes_invalid")
    return config.base_url.rstrip("/")



class MediaContractConformanceRunner:
    """Exercise a target service without retaining provider payloads."""

    def __init__(
        self,
        config: MediaConformanceConfig,
        *,
        client: httpx.Client,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self.base_url = _validate_config(config)
        self.allowed_hosts = {
            normalized
            for host in config.allowed_download_hosts
            if (normalized := normalize_exact_host(host)) is not None
        }
        self.client = client
        self.sleep = sleep
        self.monotonic = monotonic
        self.run_id = secrets.token_hex(8)
        self.request_nonce = secrets.token_hex(16)
        self._evidence_key = secrets.token_bytes(32)
        self.secret_values = {
            value
            for value in (
                config.api_key,
                self.base_url,
                config.image_model,
                config.video_model,
                self.request_nonce,
            )
            if value
        }
        self.steps: list[dict[str, Any]] = []

    def run(self) -> dict[str, Any]:
        started_at = _utc_iso()
        for kind in self.config.kinds:
            self._run_kind(kind)
        failed = sum(step["status"] == "failed" for step in self.steps)
        passed = sum(step["status"] == "passed" for step in self.steps)
        skipped = sum(step["status"] == "skipped" for step in self.steps)
        return {
            "schema_version": REPORT_SCHEMA_VERSION,
            "fingerprint_algorithm": REPORT_FINGERPRINT_ALGORITHM,
            "contract_version": MEDIA_CONTRACT_VERSION,
            "run_id": self.run_id,
            "started_at": started_at,
            "completed_at": _utc_iso(),
            "status": "passed" if failed == 0 else "failed",
            "target_fingerprint": _fingerprint(
                self.base_url,
                key=self._evidence_key,
            ),
            "selected_kinds": list(self.config.kinds),
            "billable_generation_upper_bound_if_conformant": len(self.config.kinds),
            "summary": {"passed": passed, "failed": failed, "skipped": skipped},
            "steps": self.steps,
        }

    def _run_kind(self, kind: str) -> None:
        path, payload = self._generation_request(kind)
        key = f"cf-conformance-{self.request_nonce}-{kind}"
        self.secret_values.add(key)
        self.secret_values.add(payload["prompt"])
        created_ok, created = self._execute(
            f"{kind}.create",
            lambda: self._submit_generation(kind, path, payload, key),
        )
        if not created_ok:
            for suffix in (
                "idempotency_replay",
                "idempotency_conflict",
                "version_rejection",
                "authorization_rejection",
            ):
                self._skip(f"{kind}.{suffix}", "create_failed")
            if kind == "video":
                self._skip("video.poll", "create_failed")
            return
        self._execute(
            f"{kind}.idempotency_replay",
            lambda: self._verify_replay(kind, path, payload, key, created),
        )
        self._execute(
            f"{kind}.idempotency_conflict",
            lambda: self._verify_error_probe(
                path,
                {**payload, "prompt": f"{payload['prompt']} conflict"},
                key,
                {409},
                expected_code="idempotency_conflict",
            ),
        )
        self._execute(
            f"{kind}.version_rejection",
            lambda: self._verify_error_probe(
                path,
                payload,
                key,
                {400},
                expected_code="contract_version_unsupported",
                version="0",
            ),
        )
        self._execute(
            f"{kind}.authorization_rejection",
            lambda: self._verify_error_probe(
                path,
                payload,
                key,
                {401, 403},
                include_authorization=False,
            ),
        )
        if kind == "video":
            if created.state in SUCCESS_VIDEO_STATES:
                self._skip("video.poll", "synchronous_completion")
            elif created.task_id:
                self._execute("video.poll", lambda: self._poll_video(created.task_id))
            else:
                self._skip("video.poll", "task_id_missing")

    def _generation_request(self, kind: str) -> tuple[str, dict[str, Any]]:
        prompt = (
            f"ContentFlow contract conformance {self.request_nonce}. "
            "Generate a plain blue test asset without text or people."
        )
        if kind == "image":
            return "/images/generations", {
                "model": self.config.image_model,
                "prompt": prompt,
                "size": "1024x1024",
                "parameters": {"ratio": "1:1"},
            }
        return "/videos/generations", {
            "model": self.config.video_model,
            "prompt": prompt,
            "parameters": {
                "aspect_ratio": "16:9",
                "duration_seconds": 3,
                "shots": ["static blue background"],
            },
        }

    def _headers(
        self,
        *,
        idempotency_key: str | None = None,
        version: str = MEDIA_CONTRACT_VERSION,
        include_authorization: bool = True,
    ) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            MEDIA_CONTRACT_VERSION_HEADER: version,
        }
        if include_authorization:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str],
        payload: dict[str, Any] | None = None,
    ) -> ResponseEvidence:
        started = self.monotonic()
        try:
            request = self.client.build_request(
                method,
                f"{self.base_url}{path}",
                headers=headers,
                json=payload,
            )
            response = self.client.send(
                request,
                stream=True,
                follow_redirects=False,
            )
            try:
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > self.config.max_response_bytes:
                        raise ConformanceViolation(
                            "response_too_large", http_status=response.status_code
                        )
                status_code = response.status_code
                version = response.headers.get(MEDIA_CONTRACT_VERSION_HEADER)
                if version != MEDIA_CONTRACT_VERSION:
                    raise ConformanceViolation(
                        "response_version_invalid", http_status=status_code
                    )
                content_type = response.headers.get("Content-Type", "")
                if content_type.split(";", 1)[0].strip().lower() != "application/json":
                    raise ConformanceViolation(
                        "response_content_type_invalid", http_status=status_code
                    )
                try:
                    body = json.loads(bytes(raw).decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise ConformanceViolation(
                        "response_json_invalid", http_status=status_code
                    ) from error
                if not isinstance(body, dict):
                    raise ConformanceViolation(
                        "response_top_level_invalid", http_status=status_code
                    )
            finally:
                response.close()
        except ConformanceViolation:
            raise
        except httpx.HTTPError as error:
            raise ConformanceViolation("transport_error") from error
        return ResponseEvidence(
            status_code=status_code,
            body=body,
            duration_ms=round((self.monotonic() - started) * 1000, 2),
        )

    def _submit_generation(
        self,
        kind: str,
        path: str,
        payload: dict[str, Any],
        key: str,
    ) -> StepOutcome:
        response = self._request_json(
            "POST",
            path,
            headers=self._headers(idempotency_key=key),
            payload=payload,
        )
        if kind == "image":
            if response.status_code != 200:
                raise ConformanceViolation(
                    "image_create_status_invalid", http_status=response.status_code
                )
            identity = self._image_identity(response.body, response.status_code)
        else:
            if response.status_code not in {200, 202}:
                raise ConformanceViolation(
                    "video_create_status_invalid", http_status=response.status_code
                )
            identity = self._video_identity(response.body, response.status_code)
            if (
                response.status_code == 200
                and identity.state not in SUCCESS_VIDEO_STATES
            ):
                raise ConformanceViolation(
                    "video_sync_state_invalid", http_status=response.status_code
                )
            if (
                response.status_code == 202
                and identity.state not in ACTIVE_VIDEO_STATES
            ):
                raise ConformanceViolation(
                    "video_async_state_invalid", http_status=response.status_code
                )
        return StepOutcome(
            value=identity,
            evidence=self._response_step_evidence(response, identity),
        )

    def _verify_replay(
        self,
        kind: str,
        path: str,
        payload: dict[str, Any],
        key: str,
        original: GenerationIdentity,
    ) -> StepOutcome:
        outcome = self._submit_generation(kind, path, payload, key)
        replayed: GenerationIdentity = outcome.value
        if replayed.fingerprint != original.fingerprint:
            raise ConformanceViolation("idempotency_replay_mismatch")
        return outcome

    def _verify_error_probe(
        self,
        path: str,
        payload: dict[str, Any],
        key: str,
        expected_statuses: set[int],
        *,
        expected_code: str | None = None,
        version: str = MEDIA_CONTRACT_VERSION,
        include_authorization: bool = True,
    ) -> StepOutcome:
        response = self._request_json(
            "POST",
            path,
            headers=self._headers(
                idempotency_key=key,
                version=version,
                include_authorization=include_authorization,
            ),
            payload=payload,
        )
        if response.status_code not in expected_statuses:
            raise ConformanceViolation(
                "error_probe_status_invalid", http_status=response.status_code
            )
        error_code = self._validate_error_envelope(
            response.body, http_status=response.status_code
        )
        if expected_code and error_code != expected_code:
            raise ConformanceViolation(
                "error_probe_code_invalid", http_status=response.status_code
            )
        return StepOutcome(
            evidence={
                "http_status": response.status_code,
                "duration_ms": response.duration_ms,
                "error_code_fingerprint": _fingerprint(
                    error_code,
                    key=self._evidence_key,
                ),
            }
        )

    def _poll_video(self, task_id: str) -> StepOutcome:
        deadline = self.monotonic() + self.config.poll_timeout_seconds
        attempts = 0
        total_duration_ms = 0.0
        while True:
            attempts += 1
            response = self._request_json(
                "GET",
                f"/videos/generations/{quote(task_id, safe='')}",
                headers=self._headers(),
            )
            total_duration_ms += response.duration_ms
            if response.status_code != 200:
                raise ConformanceViolation(
                    "video_poll_status_invalid", http_status=response.status_code
                )
            identity = self._video_identity(response.body, response.status_code)
            if identity.task_id and identity.task_id != task_id:
                raise ConformanceViolation(
                    "video_poll_task_mismatch", http_status=response.status_code
                )
            if identity.state in SUCCESS_VIDEO_STATES:
                return StepOutcome(
                    value=identity,
                    evidence={
                        "http_status": response.status_code,
                        "duration_ms": round(total_duration_ms, 2),
                        "poll_attempts": attempts,
                        "state": identity.state,
                        "result_fingerprint": identity.fingerprint,
                    },
                )
            if identity.state in FAILED_VIDEO_STATES:
                raise ConformanceViolation(
                    "video_generation_terminal_failure",
                    http_status=response.status_code,
                )
            if self.monotonic() >= deadline:
                raise ConformanceViolation("video_poll_timeout")
            self.sleep(self.config.poll_interval_seconds)

    def _image_identity(
        self,
        body: dict[str, Any],
        http_status: int,
    ) -> GenerationIdentity:
        self._validate_response_wrapper(body, http_status=http_status)
        data = body["data"]
        if isinstance(data, list):
            if len(data) != 1 or not isinstance(data[0], dict):
                raise ConformanceViolation(
                    "image_data_invalid", http_status=http_status
                )
            item = data[0]
        elif isinstance(data, dict):
            item = data
        else:
            raise ConformanceViolation("image_data_invalid", http_status=http_status)
        allowed = {"b64_json", "url", "download_url", "mime_type", "filename"}
        if set(item) - allowed:
            raise ConformanceViolation(
                "image_result_fields_invalid", http_status=http_status
            )
        sources = [key for key in ("b64_json", "url", "download_url") if key in item]
        if len(sources) != 1:
            raise ConformanceViolation(
                "image_result_source_invalid", http_status=http_status
            )
        if sources[0] == "b64_json":
            encoded = item["b64_json"]
            if not isinstance(encoded, str) or not encoded:
                raise ConformanceViolation(
                    "image_base64_invalid", http_status=http_status
                )
            try:
                base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error) as error:
                raise ConformanceViolation(
                    "image_base64_invalid", http_status=http_status
                ) from error
            self.secret_values.add(encoded)
        else:
            download_url = item[sources[0]]
            self._validate_download_url(download_url, http_status=http_status)
            self.secret_values.add(download_url)
        self._validate_optional_result_fields(item, http_status=http_status)
        return GenerationIdentity(
            fingerprint=_canonical_fingerprint(item, key=self._evidence_key),
            state="ready",
        )

    def _video_identity(
        self,
        body: dict[str, Any],
        http_status: int,
    ) -> GenerationIdentity:
        self._validate_response_wrapper(body, http_status=http_status)
        item = body["data"]
        if not isinstance(item, dict):
            raise ConformanceViolation("video_data_invalid", http_status=http_status)
        allowed = {
            "id",
            "task_id",
            "status",
            "url",
            "download_url",
            "mime_type",
            "filename",
            "error",
        }
        if set(item) - allowed:
            raise ConformanceViolation(
                "video_result_fields_invalid", http_status=http_status
            )
        raw_status = item.get("status")
        if not isinstance(raw_status, str):
            raise ConformanceViolation("video_state_invalid", http_status=http_status)
        state = raw_status
        valid_states = ACTIVE_VIDEO_STATES | SUCCESS_VIDEO_STATES | FAILED_VIDEO_STATES
        if state not in valid_states:
            raise ConformanceViolation("video_state_invalid", http_status=http_status)
        identifiers = [
            item[key]
            for key in ("id", "task_id")
            if key in item
        ]
        if any(
            not self._valid_bounded_text(
                value,
                minimum=1,
                maximum=255,
            )
            for value in identifiers
        ):
            raise ConformanceViolation("video_task_id_invalid", http_status=http_status)
        if len(set(identifiers)) > 1:
            raise ConformanceViolation(
                "video_task_id_ambiguous", http_status=http_status
            )
        task_id = identifiers[0] if identifiers else None
        self.secret_values.update(
            value for value in identifiers if isinstance(value, str) and value
        )
        urls = [key for key in ("url", "download_url") if key in item]
        self.secret_values.update(
            item[key]
            for key in urls
            if isinstance(item[key], str) and item[key]
        )
        if state in ACTIVE_VIDEO_STATES and not task_id:
            raise ConformanceViolation("video_task_id_missing", http_status=http_status)
        if state in ACTIVE_VIDEO_STATES and (urls or "error" in item):
            raise ConformanceViolation(
                "video_active_payload_invalid", http_status=http_status
            )
        if state in SUCCESS_VIDEO_STATES:
            if len(urls) != 1 or "error" in item:
                raise ConformanceViolation(
                    "video_result_url_invalid", http_status=http_status
                )
            self._validate_download_url(item[urls[0]], http_status=http_status)
        if state in FAILED_VIDEO_STATES:
            error = item.get("error")
            if urls or not isinstance(error, dict):
                raise ConformanceViolation(
                    "video_terminal_error_missing", http_status=http_status
                )
            self._validate_error_detail(
                error,
                http_status=http_status,
                expected_retryable=False,
            )
            self.secret_values.update(
                value
                for value in (error["message"],)
                if isinstance(value, str) and value
            )
        self._validate_optional_result_fields(item, http_status=http_status)
        identity_source = (
            f"task:{task_id}"
            if task_id
            else json.dumps(
                item,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return GenerationIdentity(
            fingerprint=_fingerprint(identity_source, key=self._evidence_key),
            state=state,
            task_id=task_id,
        )

    def _validate_response_wrapper(
        self,
        body: dict[str, Any],
        *,
        http_status: int,
    ) -> None:
        if set(body) - {"request_id", "requestId", "data"} or "data" not in body:
            raise ConformanceViolation(
                "response_wrapper_invalid", http_status=http_status
            )
        if "request_id" in body and "requestId" in body:
            raise ConformanceViolation(
                "response_request_id_ambiguous",
                http_status=http_status,
            )
        request_ids = [
            body[key]
            for key in ("request_id", "requestId")
            if key in body
        ]
        self.secret_values.update(
            value for value in request_ids if isinstance(value, str) and value
        )
        if any(
            not self._valid_bounded_text(
                value,
                minimum=1,
                maximum=255,
            )
            for value in request_ids
        ):
            raise ConformanceViolation(
                "response_request_id_invalid", http_status=http_status
            )

    def _validate_error_envelope(
        self,
        body: dict[str, Any],
        *,
        http_status: int,
    ) -> str:
        if set(body) - {"request_id", "error"} or "error" not in body:
            raise ConformanceViolation(
                "error_envelope_invalid", http_status=http_status
            )
        request_id = body.get("request_id")
        if "request_id" in body and not self._valid_bounded_text(
            request_id,
            minimum=1,
            maximum=255,
        ):
            raise ConformanceViolation(
                "error_request_id_invalid", http_status=http_status
            )
        error = body["error"]
        if not isinstance(error, dict):
            raise ConformanceViolation("error_detail_invalid", http_status=http_status)
        self._validate_error_detail(error, http_status=http_status)
        self.secret_values.update(
            value
            for value in (request_id, error["message"])
            if isinstance(value, str) and value
        )
        if error["retryable"]:
            raise ConformanceViolation(
                "permanent_error_marked_retryable", http_status=http_status
            )
        return error["code"]

    @staticmethod
    def _validate_error_detail(
        error: dict[str, Any],
        *,
        http_status: int,
        expected_retryable: bool | None = None,
    ) -> None:
        if set(error) != {"code", "message", "retryable"}:
            raise ConformanceViolation("error_detail_invalid", http_status=http_status)
        code = error["code"]
        message = error["message"]
        retryable = error["retryable"]
        if (
            not MediaContractConformanceRunner._valid_bounded_text(
                code,
                minimum=1,
                maximum=80,
                ascii_only=True,
            )
            or any(
                not (char.isalnum() or char in "._-")
                for char in code
            )
        ):
            raise ConformanceViolation("error_code_invalid", http_status=http_status)
        if not MediaContractConformanceRunner._valid_bounded_text(
            message,
            minimum=1,
            maximum=500,
        ):
            raise ConformanceViolation("error_message_invalid", http_status=http_status)
        if not isinstance(retryable, bool) or (
            expected_retryable is not None and retryable is not expected_retryable
        ):
            raise ConformanceViolation(
                "error_retryable_invalid", http_status=http_status
            )

    @staticmethod
    def _valid_bounded_text(
        value: Any,
        *,
        minimum: int,
        maximum: int,
        ascii_only: bool = False,
    ) -> bool:
        if not isinstance(value, str) or not minimum <= len(value) <= maximum:
            return False
        if ascii_only and not value.isascii():
            return False
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            return False
        return all(
            ord(char) >= 0x20 and ord(char) != 0x7F
            for char in value
        )

    @staticmethod
    def _validate_optional_result_fields(
        item: dict[str, Any],
        *,
        http_status: int,
    ) -> None:
        mime_type = item.get("mime_type")
        filename = item.get("filename")
        if mime_type is not None and (
            not MediaContractConformanceRunner._valid_bounded_text(
                mime_type,
                minimum=1,
                maximum=120,
                ascii_only=True,
            )
            or mime_type.count("/") != 1
            or not all(mime_type.split("/", 1))
            or any(char.isspace() for char in mime_type)
        ):
            raise ConformanceViolation(
                "result_mime_type_invalid", http_status=http_status
            )
        if filename is not None:
            if not MediaContractConformanceRunner._valid_bounded_text(
                filename,
                minimum=1,
                maximum=255,
            ):
                raise ConformanceViolation(
                    "result_filename_invalid", http_status=http_status
                )
            try:
                clean_name = safe_filename(filename)
            except ValueError:
                raise ConformanceViolation(
                    "result_filename_invalid", http_status=http_status
                ) from None
            if clean_name != filename:
                raise ConformanceViolation(
                    "result_filename_invalid", http_status=http_status
                )

    def _validate_download_url(self, value: Any, *, http_status: int) -> None:
        if not isinstance(value, str) or not value or len(value) > 2048:
            raise ConformanceViolation("download_url_invalid", http_status=http_status)
        if any(ord(char) <= 0x20 or ord(char) == 0x7F for char in value):
            raise ConformanceViolation("download_url_invalid", http_status=http_status)
        try:
            value.encode("utf-8")
            parsed = urlparse(value)
            port = parsed.port
        except ValueError as error:
            raise ConformanceViolation(
                "download_url_invalid", http_status=http_status
            ) from error
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise ConformanceViolation("download_url_invalid", http_status=http_status)
        if parsed.scheme != "https" and not self.config.allow_insecure_http:
            raise ConformanceViolation("download_url_insecure", http_status=http_status)
        if (
            not self.config.allow_insecure_http
            and parsed.scheme == "https"
            and port not in {None, 443}
        ):
            raise ConformanceViolation(
                "download_url_port_not_allowed",
                http_status=http_status,
            )
        if parsed.hostname.lower() not in self.allowed_hosts:
            raise ConformanceViolation(
                "download_url_host_not_allowed", http_status=http_status
            )

    def _response_step_evidence(
        self,
        response: ResponseEvidence,
        identity: GenerationIdentity,
    ) -> dict[str, Any]:
        request_id = response.body.get("request_id") or response.body.get("requestId")
        evidence: dict[str, Any] = {
            "http_status": response.status_code,
            "duration_ms": response.duration_ms,
            "state": identity.state,
            "result_fingerprint": identity.fingerprint,
        }
        if request_id:
            evidence["request_id_fingerprint"] = _fingerprint(
                request_id,
                key=self._evidence_key,
            )
        return evidence

    def _execute(
        self,
        name: str,
        operation: Callable[[], StepOutcome],
    ) -> tuple[bool, Any]:
        try:
            outcome = operation()
        except ConformanceViolation as error:
            entry: dict[str, Any] = {
                "name": name,
                "status": "failed",
                "code": error.code,
            }
            if error.http_status is not None:
                entry["http_status"] = error.http_status
            self.steps.append(entry)
            return False, None
        except Exception:
            self.steps.append(
                {
                    "name": name,
                    "status": "failed",
                    "code": "runner_internal_error",
                }
            )
            return False, None
        self.steps.append({"name": name, "status": "passed", **outcome.evidence})
        return True, outcome.value

    def _skip(self, name: str, reason: str) -> None:
        self.steps.append({"name": name, "status": "skipped", "code": reason})


def _parse_allowed_hosts(raw: str) -> tuple[str, ...]:
    if not raw.strip():
        return ()
    if raw.lstrip().startswith("["):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ConformanceConfigurationError(
                "download_host_allowlist_invalid"
            ) from error
        if not isinstance(value, list) or any(
            not isinstance(item, str) for item in value
        ):
            raise ConformanceConfigurationError("download_host_allowlist_invalid")
        return tuple(value)
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _serialize_report(report: dict[str, Any], *, secret_values: Sequence[str]) -> str:
    serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    for secret in secret_values:
        if not secret:
            continue
        escaped = json.dumps(secret, ensure_ascii=False)[1:-1]
        if secret in serialized or escaped in serialized:
            raise ConformanceConfigurationError("report_secret_redaction_failed")
    return serialized


def _reserve_report(path: Path) -> TextIO:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        output = path.open("x", encoding="utf-8", newline="\n")
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return output
    except FileExistsError as error:
        raise ConformanceConfigurationError("report_path_already_exists") from error
    except OSError as error:
        raise ConformanceConfigurationError("report_write_failed") from error


def _write_report(
    path: Path,
    report: dict[str, Any],
    *,
    secret_values: Sequence[str],
) -> None:
    serialized = _serialize_report(report, secret_values=secret_values)
    with _reserve_report(path) as output:
        output.write(serialized)
        output.flush()
        os.fsync(output.fileno())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run controlled live checks against ContentFlow Media Contract v1. "
            "Target settings are read only from CONTENTFLOW_* environment variables."
        )
    )
    parser.add_argument("--kind", choices=("image", "video", "both"), default="both")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--confirm-live-generation", action="store_true")
    parser.add_argument("--allow-insecure-http", action="store_true")
    parser.add_argument("--request-timeout-seconds", type=float, default=90.0)
    parser.add_argument("--poll-timeout-seconds", type=float, default=120.0)
    parser.add_argument("--poll-interval-seconds", type=float, default=2.0)
    parser.add_argument(
        "--max-response-bytes", type=int, default=DEFAULT_MAX_RESPONSE_BYTES
    )
    return parser


def _configuration_error(code: str) -> int:
    print(
        json.dumps(
            {"status": "configuration_error", "code": code},
            ensure_ascii=False,
        ),
        file=sys.stderr,
    )
    return 2


def run_cli(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.confirm_live_generation:
        return _configuration_error("live_generation_confirmation_required")
    if args.output.exists():
        return _configuration_error("report_path_already_exists")
    base_url = os.getenv("CONTENTFLOW_MEDIA_API_BASE", "")
    api_key = os.getenv("CONTENTFLOW_MEDIA_API_KEY", "")
    image_model = os.getenv("CONTENTFLOW_IMAGE_MODEL")
    video_model = os.getenv("CONTENTFLOW_VIDEO_MODEL")
    hosts_raw = os.getenv("CONTENTFLOW_MEDIA_DOWNLOAD_ALLOWED_HOSTS", "")
    missing = [
        name
        for name, value in (
            ("CONTENTFLOW_MEDIA_API_BASE", base_url),
            ("CONTENTFLOW_MEDIA_API_KEY", api_key),
            ("CONTENTFLOW_MEDIA_DOWNLOAD_ALLOWED_HOSTS", hosts_raw),
        )
        if not value
    ]
    kinds = ("image", "video") if args.kind == "both" else (args.kind,)
    if "image" in kinds and not image_model:
        missing.append("CONTENTFLOW_IMAGE_MODEL")
    if "video" in kinds and not video_model:
        missing.append("CONTENTFLOW_VIDEO_MODEL")
    if missing:
        return _configuration_error("required_environment_missing")
    report_reserved = False
    try:
        config = MediaConformanceConfig(
            base_url=base_url,
            api_key=api_key,
            image_model=image_model,
            video_model=video_model,
            kinds=kinds,
            allowed_download_hosts=_parse_allowed_hosts(hosts_raw),
            allow_insecure_http=args.allow_insecure_http,
            poll_timeout_seconds=args.poll_timeout_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
            max_response_bytes=args.max_response_bytes,
        )
        _validate_config(config)
        if (
            not math.isfinite(args.request_timeout_seconds)
            or args.request_timeout_seconds <= 0
        ):
            raise ConformanceConfigurationError("request_timeout_invalid")
        output = _reserve_report(args.output)
        report_reserved = True
        with output:
            with httpx.Client(
                timeout=args.request_timeout_seconds,
                follow_redirects=False,
            ) as client:
                runner = MediaContractConformanceRunner(config, client=client)
                report = runner.run()
            output.write(
                _serialize_report(
                    report,
                    secret_values=tuple(runner.secret_values),
                )
            )
            output.flush()
            os.fsync(output.fileno())
    except ConformanceConfigurationError as error:
        if report_reserved:
            try:
                args.output.unlink(missing_ok=True)
            except OSError:
                pass
        return _configuration_error(str(error))
    except Exception:
        if report_reserved:
            try:
                args.output.unlink(missing_ok=True)
            except OSError:
                pass
        return _configuration_error("runner_startup_failed")
    print(
        json.dumps(
            {
                "status": report["status"],
                "report_path": str(args.output),
                "summary": report["summary"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["status"] == "passed" else 1


def main() -> None:
    raise SystemExit(run_cli())


if __name__ == "__main__":
    main()
