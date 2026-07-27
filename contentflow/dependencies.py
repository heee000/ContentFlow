from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .entities import Membership, User, Workspace
from .security import decode_access_token
from .settings import Settings, get_settings


bearer = HTTPBearer(auto_error=False)
DbSession = Annotated[Session, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]


@dataclass(slots=True)
class Principal:
    user: User
    workspace: Workspace
    membership: Membership

    @property
    def user_id(self) -> str:
        return self.user.id

    @property
    def workspace_id(self) -> str:
        return self.workspace.id

    @property
    def role(self) -> str:
        return self.membership.role


def get_principal(
    session: DbSession,
    settings: AppSettings,
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer)
    ] = None,
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少访问令牌",
        )
    try:
        payload = decode_access_token(credentials.credentials, settings.secret_key)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(error),
        ) from error

    user = session.get(User, str(payload["sub"]))
    workspace = session.get(Workspace, str(payload["wid"]))
    membership = session.scalar(
        select(Membership).where(
            Membership.workspace_id == str(payload["wid"]),
            Membership.user_id == str(payload["sub"]),
        )
    )
    if not user or not user.is_active or not workspace or not membership:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户或工作区访问权限已失效",
        )
    return Principal(user=user, workspace=workspace, membership=membership)


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

