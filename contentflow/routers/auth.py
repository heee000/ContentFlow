from __future__ import annotations

import hmac
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..auth_rate_limit import (
    clear_rate_limit,
    consume_login_limits,
    consume_refresh_limits,
    consume_registration_limit,
)
from ..db import get_db
from ..dependencies import AppSettings, CurrentPrincipal
from ..entities import (
    AuthRefreshTokenHistory,
    AuthSession,
    Membership,
    User,
    Workspace,
    new_id,
)
from ..schemas import (
    LoginRequest,
    RegisterRequest,
    SessionResponse,
    TokenResponse,
    WorkspaceAccessResponse,
    WorkspaceCreate,
)
from ..security import (
    create_access_token,
    create_refresh_token,
    decode_access_token,
    hash_client_fingerprint,
    hash_password,
    hash_refresh_token,
    parse_refresh_token,
    verify_password,
)


router = APIRouter(prefix="/auth", tags=["auth"])
Db = Annotated[Session, Depends(get_db)]
COOKIE_SESSION_HEADER = "x-contentflow-session-mode"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _wants_cookie_session(request: Request) -> bool:
    return request.headers.get(COOKIE_SESSION_HEADER, "").lower() == "cookie"


def require_trusted_origin(request: Request, settings) -> None:
    origin = (request.headers.get("origin") or "").rstrip("/")
    allowed = {item.rstrip("/") for item in settings.cors_origins}
    request_origin = f"{request.url.scheme}://{request.url.netloc}".rstrip("/")
    allowed.add(request_origin)
    if not origin or origin not in allowed:
        raise HTTPException(status_code=403, detail="Cookie 会话来源校验失败")


def _set_cookie(
    response: Response,
    *,
    key: str,
    value: str,
    max_age: int,
    path: str,
    settings,
) -> None:
    response.set_cookie(
        key=key,
        value=value,
        max_age=max_age,
        path=path,
        domain=settings.resolved_auth_cookie_domain,
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite="lax",
    )


def set_session_cookies(
    response: Response,
    *,
    access_token: str,
    refresh_token: str,
    settings,
) -> None:
    _set_cookie(
        response,
        key=settings.access_cookie_name,
        value=access_token,
        max_age=settings.access_token_minutes * 60,
        path=settings.api_prefix,
        settings=settings,
    )
    _set_cookie(
        response,
        key=settings.refresh_cookie_name,
        value=refresh_token,
        max_age=settings.refresh_token_days * 86_400,
        path=f"{settings.api_prefix}/auth",
        settings=settings,
    )


def clear_session_cookies(response: Response, settings) -> None:
    common = {
        "domain": settings.resolved_auth_cookie_domain,
        "secure": settings.auth_cookie_secure,
        "httponly": True,
        "samesite": "lax",
    }
    response.delete_cookie(
        settings.access_cookie_name,
        path=settings.api_prefix,
        **common,
    )
    response.delete_cookie(
        settings.refresh_cookie_name,
        path=f"{settings.api_prefix}/auth",
        **common,
    )


def _client_fingerprints(request: Request, settings) -> tuple[str | None, str | None]:
    user_agent_hash = hash_client_fingerprint(
        request.headers.get("user-agent"),
        settings.secret_key,
    )
    client_ip_hash = hash_client_fingerprint(
        request.client.host if request.client else None,
        settings.secret_key,
    )
    return user_agent_hash, client_ip_hash


def create_auth_session(
    session: Session,
    *,
    user: User,
    workspace: Workspace,
    request: Request,
    settings,
) -> tuple[AuthSession, str]:
    now = _now()
    session_id = new_id()
    refresh_token = create_refresh_token(session_id)
    user_agent_hash, client_ip_hash = _client_fingerprints(request, settings)
    auth_session = AuthSession(
        id=session_id,
        user_id=user.id,
        workspace_id=workspace.id,
        refresh_token_hash=hash_refresh_token(
            refresh_token,
            settings.secret_key,
        ),
        expires_at=now + timedelta(days=settings.refresh_token_days),
        last_used_at=now,
        user_agent_hash=user_agent_hash,
        client_ip_hash=client_ip_hash,
    )
    session.add(auth_session)
    return auth_session, refresh_token


def rotate_auth_session(
    session: Session,
    auth_session: AuthSession,
    *,
    workspace: Workspace,
    request: Request,
    settings,
) -> str:
    refresh_token = create_refresh_token(auth_session.id)
    session.add(
        AuthRefreshTokenHistory(
            auth_session_id=auth_session.id,
            token_hash=auth_session.refresh_token_hash,
            rotated_at=_now(),
        )
    )
    auth_session.refresh_token_hash = hash_refresh_token(
        refresh_token,
        settings.secret_key,
    )
    auth_session.workspace_id = workspace.id
    auth_session.last_used_at = _now()
    user_agent_hash, client_ip_hash = _client_fingerprints(request, settings)
    auth_session.user_agent_hash = user_agent_hash
    auth_session.client_ip_hash = client_ip_hash
    return refresh_token


def lock_principal_auth_session(
    session: Session,
    principal,
) -> AuthSession:
    auth_session = session.scalar(
        select(AuthSession)
        .where(AuthSession.id == principal.auth_session.id)
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if (
        auth_session is None
        or auth_session.revoked_at is not None
        or _aware(auth_session.expires_at) <= _now()
        or auth_session.user_id != principal.user_id
        or auth_session.workspace_id != principal.workspace_id
    ):
        raise HTTPException(status_code=401, detail="访问会话已失效")
    return auth_session


def issue_token_response(
    *,
    user: User,
    workspace: Workspace,
    membership: Membership,
    auth_session: AuthSession,
    refresh_token: str,
    request: Request,
    response: Response,
    settings,
    force_cookie: bool = False,
) -> TokenResponse:
    access_token = create_access_token(
        subject=user.id,
        workspace_id=workspace.id,
        role=membership.role,
        session_id=auth_session.id,
        secret_key=settings.secret_key,
        expires_minutes=settings.access_token_minutes,
        issuer=settings.auth_token_issuer,
        audience=settings.auth_token_audience,
    )
    cookie_mode = force_cookie or _wants_cookie_session(request)
    if cookie_mode:
        set_session_cookies(
            response,
            access_token=access_token,
            refresh_token=refresh_token,
            settings=settings,
        )
    return TokenResponse(
        access_token=None if cookie_mode else access_token,
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
    response_model_exclude_none=True,
    status_code=status.HTTP_201_CREATED,
)
def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    session: Db,
    settings: AppSettings,
):
    if _wants_cookie_session(request):
        require_trusted_origin(request, settings)
    if not settings.allow_registration:
        raise HTTPException(status_code=403, detail="当前环境未开放注册")
    consume_registration_limit(session, request=request, settings=settings)
    email = payload.email.lower()
    if session.scalar(select(User).where(User.email == email)):
        raise HTTPException(status_code=409, detail="该邮箱已注册")

    user = User(
        email=email,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name.strip(),
    )
    session.add(user)
    try:
        session.flush()
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail="This email is already registered",
        ) from error
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
    auth_session, refresh_token = create_auth_session(
        session,
        user=user,
        workspace=workspace,
        request=request,
        settings=settings,
    )
    record_audit(
        session,
        action="auth.register",
        entity_type="user",
        entity_id=user.id,
        workspace_id=workspace.id,
        actor_user_id=user.id,
        request_id=_request_id(request),
    )
    session.commit()
    return issue_token_response(
        user=user,
        workspace=workspace,
        membership=membership,
        auth_session=auth_session,
        refresh_token=refresh_token,
        request=request,
        response=response,
        settings=settings,
    )


@router.post(
    "/login",
    response_model=TokenResponse,
    response_model_exclude_none=True,
)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: Db,
    settings: AppSettings,
):
    if _wants_cookie_session(request):
        require_trusted_origin(request, settings)
    email = payload.email.lower()
    account_limit_key = consume_login_limits(
        session,
        request=request,
        email=email,
        settings=settings,
    )
    user = session.scalar(select(User).where(User.email == email))
    if (
        not user
        or not user.is_active
        or not verify_password(payload.password, user.password_hash)
    ):
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
    auth_session, refresh_token = create_auth_session(
        session,
        user=user,
        workspace=workspace,
        request=request,
        settings=settings,
    )
    clear_rate_limit(session, account_limit_key)
    record_audit(
        session,
        action="auth.login",
        entity_type="auth_session",
        entity_id=auth_session.id,
        workspace_id=workspace.id,
        actor_user_id=user.id,
        request_id=_request_id(request),
    )
    session.commit()
    return issue_token_response(
        user=user,
        workspace=workspace,
        membership=membership,
        auth_session=auth_session,
        refresh_token=refresh_token,
        request=request,
        response=response,
        settings=settings,
    )


@router.post(
    "/refresh",
    response_model=TokenResponse,
    response_model_exclude_none=True,
)
def refresh_session(
    request: Request,
    response: Response,
    session: Db,
    settings: AppSettings,
):
    require_trusted_origin(request, settings)
    refresh_token = request.cookies.get(settings.refresh_cookie_name)
    if not refresh_token:
        consume_refresh_limits(
            session,
            request=request,
            session_id=None,
            settings=settings,
        )
        raise HTTPException(status_code=401, detail="缺少刷新令牌")
    try:
        session_id = parse_refresh_token(refresh_token)
    except ValueError as error:
        consume_refresh_limits(
            session,
            request=request,
            session_id=None,
            settings=settings,
        )
        raise HTTPException(status_code=401, detail=str(error)) from error
    consume_refresh_limits(
        session,
        request=request,
        session_id=session_id,
        settings=settings,
    )

    auth_session = session.scalar(
        select(AuthSession)
        .where(AuthSession.id == session_id)
        .with_for_update()
    )
    if auth_session is None:
        raise HTTPException(status_code=401, detail="刷新会话不存在")

    presented_hash = hash_refresh_token(refresh_token, settings.secret_key)
    current_token_matches = hmac.compare_digest(
        presented_hash,
        auth_session.refresh_token_hash,
    )
    historical_token_exists = (
        session.scalar(
            select(AuthRefreshTokenHistory.id).where(
                AuthRefreshTokenHistory.auth_session_id == auth_session.id,
                AuthRefreshTokenHistory.token_hash == presented_hash,
            )
        )
        is not None
    )
    if historical_token_exists:
        now = _now()
        auth_session.revoked_at = now
        auth_session.revoke_reason = "refresh_token_reuse"
        record_audit(
            session,
            action="auth.refresh_reuse_detected",
            entity_type="auth_session",
            entity_id=auth_session.id,
            workspace_id=auth_session.workspace_id,
            actor_user_id=auth_session.user_id,
            request_id=_request_id(request),
        )
        session.commit()
        raise HTTPException(status_code=401, detail="刷新令牌已被使用，会话已撤销")

    if not current_token_matches:
        raise HTTPException(status_code=401, detail="刷新令牌无效")
    if auth_session.revoked_at is not None or _aware(auth_session.expires_at) <= _now():
        raise HTTPException(status_code=401, detail="刷新会话已失效")

    user = session.get(User, auth_session.user_id)
    workspace = session.get(Workspace, auth_session.workspace_id)
    membership = session.scalar(
        select(Membership).where(
            Membership.user_id == auth_session.user_id,
            Membership.workspace_id == auth_session.workspace_id,
        )
    )
    if not user or not user.is_active or not workspace or not membership:
        auth_session.revoked_at = _now()
        auth_session.revoke_reason = "access_removed"
        session.commit()
        raise HTTPException(status_code=401, detail="用户或工作区访问权限已失效")

    next_refresh_token = rotate_auth_session(
        session,
        auth_session,
        workspace=workspace,
        request=request,
        settings=settings,
    )
    record_audit(
        session,
        action="auth.refresh",
        entity_type="auth_session",
        entity_id=auth_session.id,
        workspace_id=workspace.id,
        actor_user_id=user.id,
        request_id=_request_id(request),
    )
    session.commit()
    return issue_token_response(
        user=user,
        workspace=workspace,
        membership=membership,
        auth_session=auth_session,
        refresh_token=next_refresh_token,
        request=request,
        response=response,
        settings=settings,
        force_cookie=True,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    session: Db,
    settings: AppSettings,
):
    refresh_token = request.cookies.get(settings.refresh_cookie_name)
    access_token = request.cookies.get(settings.access_cookie_name)
    authorization = request.headers.get("authorization") or ""
    bearer_token = (
        authorization[7:].strip()
        if authorization.lower().startswith("bearer ")
        else None
    )
    if refresh_token or access_token:
        require_trusted_origin(request, settings)

    authenticated_session_ids: set[str] = set()
    if refresh_token:
        try:
            refresh_session_id = parse_refresh_token(refresh_token)
            auth_session = session.scalar(
                select(AuthSession)
                .where(AuthSession.id == refresh_session_id)
                .with_for_update()
            )
            presented_hash = hash_refresh_token(
                refresh_token,
                settings.secret_key,
            )
            refresh_matches = (
                auth_session is not None
                and hmac.compare_digest(
                    presented_hash,
                    auth_session.refresh_token_hash,
                )
            )
            if auth_session is not None and not refresh_matches:
                refresh_matches = (
                    session.scalar(
                        select(AuthRefreshTokenHistory.id).where(
                            AuthRefreshTokenHistory.auth_session_id
                            == auth_session.id,
                            AuthRefreshTokenHistory.token_hash == presented_hash,
                        )
                    )
                    is not None
                )
            if refresh_matches:
                authenticated_session_ids.add(refresh_session_id)
        except ValueError:
            pass

    for token in (bearer_token, access_token):
        if not token:
            continue
        try:
            payload = decode_access_token(
                token,
                settings.secret_key,
                issuer=settings.auth_token_issuer,
                audience=settings.auth_token_audience,
            )
            authenticated_session_ids.add(str(payload["sid"]))
        except ValueError:
            continue

    if authenticated_session_ids:
        auth_sessions = session.scalars(
            select(AuthSession)
            .where(AuthSession.id.in_(authenticated_session_ids))
            .with_for_update()
        ).all()
        now = _now()
        for auth_session in auth_sessions:
            if auth_session.revoked_at is not None:
                continue
            auth_session.revoked_at = now
            auth_session.revoke_reason = "logout"
            record_audit(
                session,
                action="auth.logout",
                entity_type="auth_session",
                entity_id=auth_session.id,
                workspace_id=auth_session.workspace_id,
                actor_user_id=auth_session.user_id,
                request_id=_request_id(request),
            )
        session.commit()
    clear_session_cookies(response, settings)


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
def logout_all(
    request: Request,
    response: Response,
    principal: CurrentPrincipal,
    session: Db,
    settings: AppSettings,
):
    now = _now()
    session.execute(
        update(AuthSession)
        .where(
            AuthSession.user_id == principal.user_id,
            AuthSession.revoked_at.is_(None),
        )
        .values(revoked_at=now, revoke_reason="logout_all", updated_at=now)
    )
    record_audit(
        session,
        action="auth.logout_all",
        entity_type="user",
        entity_id=principal.user_id,
        workspace_id=principal.workspace_id,
        actor_user_id=principal.user_id,
        request_id=_request_id(request),
    )
    session.commit()
    clear_session_cookies(response, settings)


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
    response_model_exclude_none=True,
    status_code=status.HTTP_201_CREATED,
)
def create_workspace(
    payload: WorkspaceCreate,
    request: Request,
    response: Response,
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
    auth_session = lock_principal_auth_session(session, principal)
    refresh_token = rotate_auth_session(
        session,
        auth_session,
        workspace=workspace,
        request=request,
        settings=settings,
    )
    record_audit(
        session,
        action="workspace.create",
        entity_type="workspace",
        entity_id=workspace.id,
        workspace_id=workspace.id,
        actor_user_id=principal.user_id,
        request_id=_request_id(request),
    )
    session.commit()
    return issue_token_response(
        user=principal.user,
        workspace=workspace,
        membership=membership,
        auth_session=auth_session,
        refresh_token=refresh_token,
        request=request,
        response=response,
        settings=settings,
    )


@router.post(
    "/switch/{workspace_id}",
    response_model=TokenResponse,
    response_model_exclude_none=True,
)
def switch_workspace(
    workspace_id: str,
    request: Request,
    response: Response,
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
    auth_session = lock_principal_auth_session(session, principal)
    refresh_token = rotate_auth_session(
        session,
        auth_session,
        workspace=workspace,
        request=request,
        settings=settings,
    )
    record_audit(
        session,
        action="auth.workspace_switch",
        entity_type="auth_session",
        entity_id=principal.auth_session.id,
        workspace_id=workspace.id,
        actor_user_id=principal.user_id,
        request_id=_request_id(request),
    )
    session.commit()
    return issue_token_response(
        user=principal.user,
        workspace=workspace,
        membership=membership,
        auth_session=auth_session,
        refresh_token=refresh_token,
        request=request,
        response=response,
        settings=settings,
    )
