from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .entities import AuditChainHead, AuditLog, new_id, utcnow


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

AUDIT_INTEGRITY_VERSION = 1
AUDIT_GENESIS_HASH = "0" * 64
SYSTEM_AUDIT_SCOPE = "system"


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


def audit_chain_scope(workspace_id: str | None) -> str:
    return f"workspace:{workspace_id}" if workspace_id else SYSTEM_AUDIT_SCOPE


def _canonical_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def calculate_audit_entry_hash(
    *,
    event_id: str,
    chain_scope: str,
    chain_sequence: int,
    workspace_id: str | None,
    actor_user_id: str | None,
    action: str,
    entity_type: str,
    entity_id: str | None,
    request_id: str | None,
    metadata: dict[str, Any],
    created_at: datetime,
    previous_hash: str,
    integrity_version: int = AUDIT_INTEGRITY_VERSION,
) -> str:
    payload = {
        "action": action,
        "actor_user_id": actor_user_id,
        "chain_scope": chain_scope,
        "chain_sequence": chain_sequence,
        "created_at": _canonical_timestamp(created_at),
        "entity_id": entity_id,
        "entity_type": entity_type,
        "event_id": event_id,
        "integrity_version": integrity_version,
        "metadata": metadata,
        "previous_hash": previous_hash,
        "request_id": request_id,
        "workspace_id": workspace_id,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _lock_chain_head(
    session: Session,
    *,
    chain_scope: str,
    workspace_id: str | None,
) -> AuditChainHead:
    if session.get_bind().dialect.name == "postgresql":
        session.execute(
            text(
                "SELECT pg_advisory_xact_lock("
                "hashtextextended(:chain_scope, 0))"
            ),
            {"chain_scope": chain_scope},
        )
    query = select(AuditChainHead).where(
        AuditChainHead.chain_scope == chain_scope
    )
    if session.get_bind().dialect.name == "postgresql":
        query = query.with_for_update()
    head = session.scalar(query)
    if head is None:
        head = AuditChainHead(
            chain_scope=chain_scope,
            workspace_id=workspace_id,
            sequence=0,
            head_hash=AUDIT_GENESIS_HASH,
            updated_at=utcnow(),
        )
        session.add(head)
    elif head.workspace_id != workspace_id:
        raise RuntimeError("Audit chain scope ownership mismatch")
    return head


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
    redacted_metadata = redact(metadata or {})
    chain_scope = audit_chain_scope(workspace_id)
    head = _lock_chain_head(
        session,
        chain_scope=chain_scope,
        workspace_id=workspace_id,
    )
    event_id = new_id()
    created_at = utcnow()
    chain_sequence = head.sequence + 1
    previous_hash = head.head_hash
    entry_hash = calculate_audit_entry_hash(
        event_id=event_id,
        chain_scope=chain_scope,
        chain_sequence=chain_sequence,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        request_id=request_id,
        metadata=redacted_metadata,
        created_at=created_at,
        previous_hash=previous_hash,
    )
    event = AuditLog(
        id=event_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        request_id=request_id,
        metadata_json=redacted_metadata,
        chain_scope=chain_scope,
        chain_sequence=chain_sequence,
        previous_hash=previous_hash,
        entry_hash=entry_hash,
        integrity_version=AUDIT_INTEGRITY_VERSION,
        created_at=created_at,
    )
    session.add(event)
    head.sequence = chain_sequence
    head.head_hash = entry_hash
    head.updated_at = created_at
    return event


@dataclass(frozen=True)
class AuditIntegrityResult:
    valid: bool
    checked_entries: int
    head_sequence: int
    head_hash: str | None
    first_invalid_sequence: int | None = None
    reason: str | None = None


def verify_audit_chain(
    session: Session,
    *,
    workspace_id: str | None,
) -> AuditIntegrityResult:
    chain_scope = audit_chain_scope(workspace_id)
    head_query = select(AuditChainHead).where(
        AuditChainHead.chain_scope == chain_scope
    )
    if session.get_bind().dialect.name == "postgresql":
        head_query = head_query.with_for_update()
    head = session.scalar(head_query)
    entries = session.scalars(
        select(AuditLog)
        .where(AuditLog.chain_scope == chain_scope)
        .order_by(AuditLog.chain_sequence.asc())
        .execution_options(yield_per=500)
    )
    expected_sequence = 1
    expected_previous_hash = AUDIT_GENESIS_HASH
    checked_entries = 0
    last_hash = AUDIT_GENESIS_HASH
    for event in entries:
        if event.chain_sequence != expected_sequence:
            return AuditIntegrityResult(
                valid=False,
                checked_entries=checked_entries,
                head_sequence=head.sequence if head else 0,
                head_hash=head.head_hash if head else None,
                first_invalid_sequence=expected_sequence,
                reason="sequence_gap",
            )
        if event.previous_hash != expected_previous_hash:
            return AuditIntegrityResult(
                valid=False,
                checked_entries=checked_entries,
                head_sequence=head.sequence if head else 0,
                head_hash=head.head_hash if head else None,
                first_invalid_sequence=event.chain_sequence,
                reason="previous_hash_mismatch",
            )
        try:
            calculated_hash = calculate_audit_entry_hash(
                event_id=event.id,
                chain_scope=event.chain_scope,
                chain_sequence=event.chain_sequence,
                workspace_id=event.workspace_id,
                actor_user_id=event.actor_user_id,
                action=event.action,
                entity_type=event.entity_type,
                entity_id=event.entity_id,
                request_id=event.request_id,
                metadata=event.metadata_json,
                created_at=event.created_at,
                previous_hash=event.previous_hash,
                integrity_version=event.integrity_version,
            )
        except (TypeError, ValueError):
            return AuditIntegrityResult(
                valid=False,
                checked_entries=checked_entries,
                head_sequence=head.sequence if head else 0,
                head_hash=head.head_hash if head else None,
                first_invalid_sequence=event.chain_sequence,
                reason="entry_payload_invalid",
            )
        if not hmac.compare_digest(calculated_hash, event.entry_hash):
            return AuditIntegrityResult(
                valid=False,
                checked_entries=checked_entries,
                head_sequence=head.sequence if head else 0,
                head_hash=head.head_hash if head else None,
                first_invalid_sequence=event.chain_sequence,
                reason="entry_hash_mismatch",
            )
        checked_entries += 1
        expected_sequence += 1
        expected_previous_hash = event.entry_hash
        last_hash = event.entry_hash

    if head is None:
        return AuditIntegrityResult(
            valid=checked_entries == 0,
            checked_entries=checked_entries,
            head_sequence=0,
            head_hash=None,
            reason=None if checked_entries == 0 else "chain_head_missing",
        )
    if head.sequence != checked_entries or not hmac.compare_digest(
        head.head_hash, last_hash
    ):
        return AuditIntegrityResult(
            valid=False,
            checked_entries=checked_entries,
            head_sequence=head.sequence,
            head_hash=head.head_hash,
            first_invalid_sequence=checked_entries + 1,
            reason="chain_head_mismatch",
        )
    return AuditIntegrityResult(
        valid=True,
        checked_entries=checked_entries,
        head_sequence=head.sequence,
        head_hash=head.head_hash,
    )
