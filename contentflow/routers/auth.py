from __future__ import annotations

import re
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..db import get_db
from ..dependencies import AppSettings, CurrentPrincipal
from ..entities import Membership, User, Workspace
from ..schemas import (
    LoginRequest,
    RegisterRequest,
    SessionResponse,
    TokenResponse,
    WorkspaceAccessResponse,
    WorkspaceCreate,
)
from ..security import create_access_token, hash_password, verify_password


router = APIRouter(prefix="/auth", tags=["auth"])
Db = Annotated[Session, Depends(get_db)]


def build_token(
    user: User,
    workspace: Workspace,
    membership: Membership,
    settings,
) -> TokenResponse:
    return TokenResponse(
        access_token=create_access_token(
            subject=user.id,
            workspace_id=workspace.id,
            role=membership.role,
            secret_key=settings.secret_key,
            expires_minutes=settings.access_token_minutes,
        ),
        expires_in=settings.access_token_minutes * 60,
        workspace_id=workspace.id,
        role=membership.role,
    )


def make_slug(name: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    base = normalized[:48] or "workspace"
    return f"{base}-{uuid.uuid4().hex[:8]}"


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
)
def register(payload: RegisterRequest, session: Db, settings: AppSettings):
    if not settings.allow_registration:
        raise HTTPException(status_code=403, detail="当前环境未开放注册")
    email = payload.email.lower()
    if session.scalar(select(User).where(User.email == email)):
        raise HTTPException(status_code=409, detail="该邮箱已注册")

    user = User(
        email=email,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name.strip(),
    )
    session.add(user)
    session.flush()
    workspace = Workspace(
        name=payload.workspace_name.strip(),
        slug=make_slug(payload.workspace_name),
        created_by=user.id,
    )
    session.add(workspace)
    session.flush()
    membership = Membership(
        workspace_id=workspace.id,
        user_id=user.id,
        role="admin",
    )
    session.add(membership)
    record_audit(
        session,
        action="auth.register",
        entity_type="user",
        entity_id=user.id,
        workspace_id=workspace.id,
        actor_user_id=user.id,
    )
    # The access token becomes usable as soon as the response reaches the client.
    # Commit here instead of relying only on the yield-dependency finalizer, which
    # newer FastAPI versions may execute after the response body has been sent.
    session.commit()
    return build_token(user, workspace, membership, settings)


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, session: Db, settings: AppSettings):
    user = session.scalar(
        select(User).where(User.email == payload.email.lower())
    )
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="邮箱或密码错误",
        )
    membership_query = select(Membership).where(Membership.user_id == user.id)
    if payload.workspace_id:
        membership_query = membership_query.where(
            Membership.workspace_id == payload.workspace_id
        )
    membership = session.scalar(membership_query.order_by(Membership.created_at))
    if not membership:
        raise HTTPException(status_code=403, detail="没有可访问的工作区")
    workspace = session.get(Workspace, membership.workspace_id)
    if workspace is None:
        raise HTTPException(status_code=403, detail="工作区已失效")
    record_audit(
        session,
        action="auth.login",
        entity_type="user",
        entity_id=user.id,
        workspace_id=workspace.id,
        actor_user_id=user.id,
    )
    session.commit()
    return build_token(user, workspace, membership, settings)


@router.get("/session", response_model=SessionResponse)
def session_info(principal: CurrentPrincipal):
    return SessionResponse(
        user=principal.user,
        workspace=principal.workspace,
        role=principal.role,
    )


@router.get("/workspaces", response_model=list[WorkspaceAccessResponse])
def list_workspaces(principal: CurrentPrincipal, session: Db):
    rows = session.execute(
        select(Workspace, Membership.role)
        .join(Membership, Membership.workspace_id == Workspace.id)
        .where(Membership.user_id == principal.user_id)
        .order_by(Membership.created_at)
    ).all()
    return [
        WorkspaceAccessResponse(
            id=workspace.id,
            name=workspace.name,
            slug=workspace.slug,
            role=role,
        )
        for workspace, role in rows
    ]


@router.post(
    "/workspaces",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_workspace(
    payload: WorkspaceCreate,
    principal: CurrentPrincipal,
    session: Db,
    settings: AppSettings,
):
    workspace = Workspace(
        name=payload.name.strip(),
        slug=make_slug(payload.name),
        created_by=principal.user_id,
    )
    session.add(workspace)
    session.flush()
    membership = Membership(
        workspace_id=workspace.id,
        user_id=principal.user_id,
        role="admin",
    )
    session.add(membership)
    record_audit(
        session,
        action="workspace.create",
        entity_type="workspace",
        entity_id=workspace.id,
        workspace_id=workspace.id,
        actor_user_id=principal.user_id,
    )
    session.commit()
    return build_token(principal.user, workspace, membership, settings)


@router.post("/switch/{workspace_id}", response_model=TokenResponse)
def switch_workspace(
    workspace_id: str,
    principal: CurrentPrincipal,
    session: Db,
    settings: AppSettings,
):
    membership = session.scalar(
        select(Membership).where(
            Membership.user_id == principal.user_id,
            Membership.workspace_id == workspace_id,
        )
    )
    workspace = session.get(Workspace, workspace_id)
    if not membership or not workspace:
        raise HTTPException(status_code=403, detail="无权访问目标工作区")
    return build_token(principal.user, workspace, membership, settings)
