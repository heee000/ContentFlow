from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated, Callable

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .entities import AuthSession, Membership, User, Workspace
from .security import decode_access_token
from .settings import Settings, get_settings


bearer = HTTPBearer(auto_error=False)
DbSession = Annotated[Session, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@dataclass(slots=True)
class Principal:
    user: User
    workspace: Workspace
    membership: Membership
    auth_session: AuthSession

    @property
    def user_id(self) -> str:
        return self.user.id

    @property
    def workspace_id(self) -> str:
        return self.workspace.id

    @property
    def role(self) -> str:
        return self.membership.role


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _validate_cookie_origin(request: Request, settings: Settings) -> None:
    if request.method.upper() in SAFE_METHODS:
        return
    origin = (request.headers.get("origin") or "").rstrip("/")
    allowed = {item.rstrip("/") for item in settings.cors_origins}
    allowed.add(f"{request.url.scheme}://{request.url.netloc}".rstrip("/"))
    if not origin or origin not in allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cookie 会话来源校验失败",
        )


def get_principal(
    request: Request,
    session: DbSession,
    settings: AppSettings,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer)
    ] = None,
) -> Principal:
    token = None
    cookie_auth = False
    if credentials is not None and credentials.scheme.lower() == "bearer":
        token = credentials.credentials
    else:
        token = request.cookies.get(settings.access_cookie_name)
        cookie_auth = token is not None
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少访问令牌",
        )
    if cookie_auth:
        _validate_cookie_origin(request, settings)
    try:
        payload = decode_access_token(
            token,
            settings.secret_key,
            issuer=settings.auth_token_issuer,
            audience=settings.auth_token_audience,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(error),
        ) from error

    auth_session = session.get(AuthSession, str(payload["sid"]))
    now = datetime.now(timezone.utc)
    if (
        auth_session is None
        or auth_session.revoked_at is not None
        or _aware(auth_session.expires_at) <= now
        or auth_session.user_id != str(payload["sub"])
        or auth_session.workspace_id != str(payload["wid"])
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="访问会话已失效",
        )

    user = session.get(User, auth_session.user_id)
    workspace = session.get(Workspace, auth_session.workspace_id)
    membership = session.scalar(
        select(Membership).where(
            Membership.workspace_id == auth_session.workspace_id,
            Membership.user_id == auth_session.user_id,
        )
    )
    if not user or not user.is_active or not workspace or not membership:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户或工作区访问权限已失效",
        )
    return Principal(
        user=user,
        workspace=workspace,
        membership=membership,
        auth_session=auth_session,
    )


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]

ROLE_LEVEL = {"viewer": 10, "editor": 20, "reviewer": 30, "admin": 40}


def require_role(minimum_role: str) -> Callable:
    minimum = ROLE_LEVEL[minimum_role]

    def dependency(principal: CurrentPrincipal) -> Principal:
        if ROLE_LEVEL.get(principal.role, 0) < minimum:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"该操作需要 {minimum_role} 或更高权限",
            )
        return principal

    return dependency
