"""Non-credential browser preconditions; cookies remain the authentication proof."""

import hashlib
import hmac
import json

from fastapi import HTTPException, Request

from .entities import AuthSession
from .settings import Settings

CONTEXT_HEADER = "X-ContentFlow-Context"


class SessionContextError(HTTPException):
    def __init__(self, *, missing: bool = False):
        self.code = "session_context_required" if missing else "session_context_changed"
        super().__init__(
            status_code=428 if missing else 409,
            detail="缺少页面会话上下文，请更新或重新加载客户端"
            if missing
            else "会话或工作区已变化，本次请求未按新上下文执行；请保留输入并确认当前会话",
        )


def browser_context(auth_session: AuthSession, settings: Settings) -> str:
    # Stable across access/refresh rotation, different for every login/workspace.
    # Knowing this value cannot authenticate a request or recover any token.
    value = json.dumps(
        [
            "browser-context-v1",
            auth_session.id,
            auth_session.user_id,
            auth_session.workspace_id,
        ],
        separators=(",", ":"),
    )
    return hmac.new(
        settings.secret_key.encode(), value.encode(), hashlib.sha256
    ).hexdigest()


def require_browser_context(
    request: Request, auth_session: AuthSession, settings: Settings
) -> None:
    expected = request.headers.get(CONTEXT_HEADER)
    if not expected:
        raise SessionContextError(missing=True)
    if (
        len(expected) != 64
        or not expected.isascii()
        or not hmac.compare_digest(expected, browser_context(auth_session, settings))
    ):
        raise SessionContextError()
