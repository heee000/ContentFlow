from __future__ import annotations

import re
from typing import Any

from sqlalchemy.orm import Session

from .entities import AuditLog


SENSITIVE_KEYS = {
    "access_token",
    "refresh_token",
    "api_key",
    "credential_ciphertext",
    "credentials",
    "secret",
    "password",
    "authorization",
}


SENSITIVE_SUFFIXES = (
    "_token",
    "_secret",
    "_password",
    "_api_key",
    "_credentials",
    "_credential_ciphertext",
)


def is_sensitive_key(key: Any) -> bool:
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(key))
    normalized = normalized.replace("-", "_").lower()
    return (
        normalized in SENSITIVE_KEYS
        or normalized.endswith(SENSITIVE_SUFFIXES)
        or normalized.startswith(("authorization_", "password_", "secret_"))
    )


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: ("***" if is_sensitive_key(key) else redact(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def record_audit(
    session: Session,
    *,
    action: str,
    entity_type: str,
    entity_id: str | None,
    workspace_id: str | None,
    actor_user_id: str | None,
    request_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditLog:
    event = AuditLog(
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        request_id=request_id,
        metadata_json=redact(metadata or {}),
    )
    session.add(event)
    return event
