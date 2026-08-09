from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from .entities import AuthRateLimit
from .security import hash_rate_limit_key
from .settings import Settings


@dataclass(frozen=True, slots=True)
class RateLimitKey:
    scope: str
    identifier: str
    max_attempts: int


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _client_identifier(request: Request, settings: Settings) -> str:
    peer = request.client.host if request.client else "unknown"
    if settings.trusted_proxy_hops <= 0:
        return peer
    forwarded = [
        item.strip()
        for item in (request.headers.get("x-forwarded-for") or "").split(",")
        if item.strip()
    ]
    chain = [*forwarded, peer]
    if len(chain) <= settings.trusted_proxy_hops:
        return peer
    return chain[-(settings.trusted_proxy_hops + 1)]


def _lock_id(key_hash: str) -> int:
    value = int(key_hash[:16], 16)
    return value - 2**64 if value >= 2**63 else value


def _retry_after_seconds(blocked_until: datetime, now: datetime) -> int:
    return max(1, math.ceil((_aware(blocked_until) - now).total_seconds()))


def _rate_limited(retry_after: int) -> HTTPException:
    return HTTPException(
        status_code=429,
        detail="认证请求过于频繁，请稍后重试",
        headers={"Retry-After": str(retry_after)},
    )


def consume_rate_limits(
    session: Session,
    *,
    settings: Settings,
    keys: list[RateLimitKey],
) -> list[str]:
    hashed = [
        (
            key,
            hash_rate_limit_key(
                key.scope,
                key.identifier,
                settings.secret_key,
            ),
        )
        for key in keys
    ]
    if not settings.auth_rate_limit_enabled:
        return [key_hash for _, key_hash in hashed]

    now = _now()
    session.execute(delete(AuthRateLimit).where(AuthRateLimit.expires_at <= now))
    ordered = sorted(hashed, key=lambda item: item[1])
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        for _, key_hash in ordered:
            session.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": _lock_id(key_hash)},
            )

    rows: list[tuple[RateLimitKey, str, AuthRateLimit]] = []
    active_retry_after = 0
    window = timedelta(seconds=settings.auth_rate_limit_window_seconds)
    block = timedelta(seconds=settings.auth_rate_limit_block_seconds)
    for key, key_hash in ordered:
        row = session.scalar(
            select(AuthRateLimit)
            .where(AuthRateLimit.key_hash == key_hash)
            .with_for_update()
        )
        if row is None:
            row = AuthRateLimit(
                key_hash=key_hash,
                scope=key.scope,
                attempts=0,
                window_started_at=now,
                blocked_until=None,
                expires_at=now + window,
            )
            session.add(row)
        elif row.blocked_until and _aware(row.blocked_until) > now:
            active_retry_after = max(
                active_retry_after,
                _retry_after_seconds(row.blocked_until, now),
            )
        elif _aware(row.window_started_at) + window <= now:
            row.attempts = 0
            row.window_started_at = now
            row.blocked_until = None
        rows.append((key, key_hash, row))

    if active_retry_after:
        raise _rate_limited(active_retry_after)

    blocked_retry_after = 0
    for key, _, row in rows:
        row.attempts += 1
        window_end = _aware(row.window_started_at) + window
        row.expires_at = window_end + timedelta(seconds=60)
        if row.attempts > key.max_attempts:
            row.blocked_until = now + block
            row.expires_at = row.blocked_until + timedelta(seconds=60)
            blocked_retry_after = max(
                blocked_retry_after,
                settings.auth_rate_limit_block_seconds,
            )

    session.commit()
    if blocked_retry_after:
        raise _rate_limited(blocked_retry_after)
    return [key_hash for _, key_hash in hashed]


def consume_login_limits(
    session: Session,
    *,
    request: Request,
    email: str,
    settings: Settings,
) -> str:
    hashes = consume_rate_limits(
        session,
        settings=settings,
        keys=[
            RateLimitKey(
                scope="login-account",
                identifier=email,
                max_attempts=settings.auth_login_account_attempts,
            ),
            RateLimitKey(
                scope="login-ip",
                identifier=_client_identifier(request, settings),
                max_attempts=settings.auth_login_ip_attempts,
            ),
        ],
    )
    return hashes[0]


def consume_registration_limit(
    session: Session,
    *,
    request: Request,
    settings: Settings,
) -> None:
    consume_rate_limits(
        session,
        settings=settings,
        keys=[
            RateLimitKey(
                scope="registration-ip",
                identifier=_client_identifier(request, settings),
                max_attempts=settings.auth_registration_ip_attempts,
            )
        ],
    )


def consume_refresh_limits(
    session: Session,
    *,
    request: Request,
    session_id: str | None,
    settings: Settings,
) -> None:
    keys = [
        RateLimitKey(
            scope="refresh-ip",
            identifier=_client_identifier(request, settings),
            max_attempts=settings.auth_refresh_ip_attempts,
        )
    ]
    if session_id:
        keys.append(
            RateLimitKey(
                scope="refresh-session",
                identifier=session_id,
                max_attempts=settings.auth_refresh_session_attempts,
            )
        )
    consume_rate_limits(session, settings=settings, keys=keys)


def clear_rate_limit(session: Session, key_hash: str) -> None:
    if key_hash:
        session.execute(
            delete(AuthRateLimit).where(AuthRateLimit.key_hash == key_hash)
        )
