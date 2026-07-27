from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..db import get_db
from ..dependencies import Principal, require_role
from ..entities import AuditLog, Membership, User
from ..schemas import (
    AuditLogResponse,
    MemberCreate,
    MemberResponse,
    MemberUpdate,
)


router = APIRouter(prefix="/admin", tags=["administration"])
Db = Annotated[Session, Depends(get_db)]
Admin = Annotated[Principal, Depends(require_role("admin"))]


def member_response(membership: Membership, user: User) -> MemberResponse:
    return MemberResponse(
        id=membership.id,
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=membership.role,
        created_at=membership.created_at,
    )


def get_membership_or_404(
    session: Session,
    *,
    workspace_id: str,
    membership_id: str,
) -> tuple[Membership, User]:
    row = session.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(
            Membership.id == membership_id,
            Membership.workspace_id == workspace_id,
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="成员不存在")
    return row[0], row[1]


def ensure_another_admin(
    session: Session,
    *,
    workspace_id: str,
    membership: Membership,
) -> None:
    if membership.role != "admin":
        return
    admin_count = session.scalar(
        select(func.count(Membership.id)).where(
            Membership.workspace_id == workspace_id,
            Membership.role == "admin",
        )
    )
    if int(admin_count or 0) <= 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="工作区必须至少保留一名管理员",
        )


@router.get("/members", response_model=list[MemberResponse])
def list_members(principal: Admin, session: Db):
    rows = session.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(Membership.workspace_id == principal.workspace_id)
        .order_by(Membership.created_at)
    ).all()
    return [
        member_response(membership, user) for membership, user in rows
    ]


@router.post(
    "/members",
    response_model=MemberResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_member(payload: MemberCreate, principal: Admin, session: Db):
    user = session.scalar(
        select(User).where(User.email == payload.email.lower())
    )
    if user is None:
        raise HTTPException(
            status_code=404,
            detail="该邮箱尚未注册，请先让成员创建账户",
        )
    existing = session.scalar(
        select(Membership).where(
            Membership.workspace_id == principal.workspace_id,
            Membership.user_id == user.id,
        )
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="该用户已在当前工作区")
    membership = Membership(
        workspace_id=principal.workspace_id,
        user_id=user.id,
        role=payload.role,
    )
    session.add(membership)
    session.flush()
    record_audit(
        session,
        action="member.add",
        entity_type="membership",
        entity_id=membership.id,
        workspace_id=principal.workspace_id,
        actor_user_id=principal.user_id,
        metadata={"user_id": user.id, "role": payload.role},
    )
    session.commit()
    return member_response(membership, user)


@router.patch("/members/{membership_id}", response_model=MemberResponse)
def update_member(
    membership_id: str,
    payload: MemberUpdate,
    principal: Admin,
    session: Db,
):
    membership, user = get_membership_or_404(
        session,
        workspace_id=principal.workspace_id,
        membership_id=membership_id,
    )
    if membership.role == "admin" and payload.role != "admin":
        ensure_another_admin(
            session,
            workspace_id=principal.workspace_id,
            membership=membership,
        )
    old_role = membership.role
    membership.role = payload.role
    record_audit(
        session,
        action="member.role_update",
        entity_type="membership",
        entity_id=membership.id,
        workspace_id=principal.workspace_id,
        actor_user_id=principal.user_id,
        metadata={
            "user_id": user.id,
            "old_role": old_role,
            "new_role": payload.role,
        },
    )
    session.commit()
    return member_response(membership, user)


@router.delete(
    "/members/{membership_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_member(
    membership_id: str,
    principal: Admin,
    session: Db,
):
    membership, user = get_membership_or_404(
        session,
        workspace_id=principal.workspace_id,
        membership_id=membership_id,
    )
    if membership.user_id == principal.user_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="不能从当前工作区移除自己",
        )
    ensure_another_admin(
        session,
        workspace_id=principal.workspace_id,
        membership=membership,
    )
    record_audit(
        session,
        action="member.remove",
        entity_type="membership",
        entity_id=membership.id,
        workspace_id=principal.workspace_id,
        actor_user_id=principal.user_id,
        metadata={"user_id": user.id, "role": membership.role},
    )
    session.delete(membership)
    session.commit()


@router.get("/audit-logs", response_model=list[AuditLogResponse])
def list_audit_logs(
    principal: Admin,
    session: Db,
    action: str | None = None,
    entity_type: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
):
    query = (
        select(AuditLog, User.display_name)
        .outerjoin(User, User.id == AuditLog.actor_user_id)
        .where(AuditLog.workspace_id == principal.workspace_id)
    )
    if action:
        query = query.where(AuditLog.action == action)
    if entity_type:
        query = query.where(AuditLog.entity_type == entity_type)
    rows = session.execute(
        query.order_by(AuditLog.created_at.desc()).limit(limit)
    ).all()
    return [
        AuditLogResponse(
            id=audit.id,
            action=audit.action,
            entity_type=audit.entity_type,
            entity_id=audit.entity_id,
            actor_user_id=audit.actor_user_id,
            actor_display_name=display_name,
            request_id=audit.request_id,
            metadata_json=audit.metadata_json,
            created_at=audit.created_at,
        )
        for audit, display_name in rows
    ]
