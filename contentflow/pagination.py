from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated, Any, TypeVar

from fastapi import HTTPException, Query, Response, status
from pydantic import AwareDatetime
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select


DEFAULT_PAGE_LIMIT = 100
MAX_PAGE_LIMIT = 200
NEXT_CURSOR_HEADER = "X-ContentFlow-Next-Cursor"
PAGE_LIMIT_HEADER = "X-ContentFlow-Page-Limit"
SYNC_TIME_HEADER = "X-ContentFlow-Sync-Time"

PageLimit = Annotated[int, Query(ge=1, le=MAX_PAGE_LIMIT)]
PageCursor = Annotated[str | None, Query(max_length=512)]
UpdatedAfter = Annotated[AwareDatetime | None, Query()]

T = TypeVar("T")


@dataclass(frozen=True)
class CursorPosition:
    updated_at: datetime
    row_id: str


def _canonical_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def encode_cursor(updated_at: datetime, row_id: str) -> str:
    payload = json.dumps(
        {"id": row_id, "t": _canonical_datetime(updated_at), "v": 1},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> CursorPosition:
    try:
        padding = "=" * (-len(cursor) % 4)
        raw = base64.b64decode(
            cursor + padding,
            altchars=b"-_",
            validate=True,
        )
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict) or set(payload) != {"id", "t", "v"}:
            raise ValueError("invalid cursor shape")
        if payload["v"] != 1:
            raise ValueError("unsupported cursor version")
        row_id = payload["id"]
        timestamp_value = payload["t"]
        if not isinstance(row_id, str) or not row_id or len(row_id) > 128:
            raise ValueError("invalid row id")
        if not isinstance(timestamp_value, str):
            raise ValueError("invalid timestamp")
        parsed = datetime.fromisoformat(timestamp_value)
        if parsed.tzinfo is None:
            raise ValueError("cursor timestamp must include timezone")
    except (
        binascii.Error,
        json.JSONDecodeError,
        UnicodeDecodeError,
        TypeError,
        ValueError,
    ) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="分页游标无效，请从第一页重新加载",
        ) from error
    return CursorPosition(
        updated_at=parsed.astimezone(timezone.utc),
        row_id=row_id,
    )


def paginate(
    session: Session,
    query: Select[Any],
    *,
    timestamp_column: Any,
    id_column: Any,
    limit: int,
    cursor: str | None,
    response: Response,
) -> list[T]:
    if cursor:
        position = decode_cursor(cursor)
        query = query.where(
            or_(
                timestamp_column < position.updated_at,
                and_(
                    timestamp_column == position.updated_at,
                    id_column < position.row_id,
                ),
            )
        )
    rows = list(
        session.scalars(
            query.order_by(timestamp_column.desc(), id_column.desc()).limit(limit + 1)
        )
    )
    page = rows[:limit]
    response.headers[PAGE_LIMIT_HEADER] = str(limit)
    response.headers[SYNC_TIME_HEADER] = _canonical_datetime(
        datetime.now(timezone.utc)
    )
    if len(rows) > limit and page:
        last = page[-1]
        response.headers[NEXT_CURSOR_HEADER] = encode_cursor(
            getattr(last, timestamp_column.key),
            getattr(last, id_column.key),
        )
    return page
