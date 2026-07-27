from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from .entities import AuditLog


SENSITIVE_KEYS = {
    "access_token",
    "refresh_token",
    "api_key",
    "secret",
    "password",
    "authorization",
}


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: ("***" if key.lower() in SENSITIVE_KEYS else redact(item))
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

