from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..db import get_db
from ..dependencies import CurrentPrincipal, Principal, require_role
from ..entities import StyleSkill
from ..pagination import DEFAULT_PAGE_LIMIT, PageCursor, PageLimit, paginate
from ..schemas import StyleSkillInstall, StyleSkillResponse, StyleSkillStatusUpdate
from ..style_skills import (
    builtin_style_responses,
    normalize_style_manifest,
    style_manifest_hash,
)


router = APIRouter(prefix="/style-skills", tags=["style-skills"])
Db = Annotated[Session, Depends(get_db)]
Editor = Annotated[Principal, Depends(require_role("editor"))]


def _response(skill: StyleSkill) -> dict:
    return {
        "id": skill.id,
        "source": "workspace",
        "status": skill.status,
        "manifest": dict(skill.manifest_json),
        "manifest_sha256": skill.manifest_sha256,
        "created_at": skill.created_at,
        "updated_at": skill.updated_at,
    }


@router.get("", response_model=list[StyleSkillResponse])
def list_style_skills(
    principal: CurrentPrincipal,
    session: Db,
    response: Response,
    limit: PageLimit = DEFAULT_PAGE_LIMIT,
    cursor: PageCursor = None,
):
    installed = paginate(
        session,
        select(StyleSkill).where(
            StyleSkill.workspace_id == principal.workspace_id
        ),
        timestamp_column=StyleSkill.created_at,
        id_column=StyleSkill.id,
        limit=limit,
        cursor=cursor,
        response=response,
        ascending=True,
    )
    builtin = builtin_style_responses() if cursor is None else []
    return [*builtin, *[_response(skill) for skill in installed]]


@router.post("", response_model=StyleSkillResponse, status_code=status.HTTP_201_CREATED)
def install_style_skill(payload: StyleSkillInstall, principal: Editor, session: Db):
    try:
        manifest = normalize_style_manifest(payload.manifest)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    skill = StyleSkill(
        workspace_id=principal.workspace_id,
        slug=manifest["slug"],
        name=manifest["name"],
        version=manifest["version"],
        description=manifest["description"],
        status="enabled",
        manifest_json=manifest,
        manifest_sha256=style_manifest_hash(manifest),
        installed_by_user_id=principal.user_id,
    )
    session.add(skill)
    try:
        session.flush()
    except IntegrityError as error:
        raise HTTPException(
            status_code=409,
            detail="同一 slug 和版本的风格 Skill 已安装",
        ) from error
    record_audit(
        session,
        action="style_skill.install",
        entity_type="style_skill",
        entity_id=skill.id,
        workspace_id=principal.workspace_id,
        actor_user_id=principal.user_id,
        metadata={
            "slug": skill.slug,
            "version": skill.version,
            "manifest_sha256": skill.manifest_sha256,
        },
    )
    return _response(skill)


@router.patch("/{skill_id}", response_model=StyleSkillResponse)
def update_style_skill_status(
    skill_id: str,
    payload: StyleSkillStatusUpdate,
    principal: Editor,
    session: Db,
):
    skill = session.scalar(
        select(StyleSkill).where(
            StyleSkill.id == skill_id,
            StyleSkill.workspace_id == principal.workspace_id,
        )
    )
    if skill is None:
        raise HTTPException(status_code=404, detail="风格 Skill 不存在")
    skill.status = payload.status
    record_audit(
        session,
        action=f"style_skill.{payload.status}",
        entity_type="style_skill",
        entity_id=skill.id,
        workspace_id=principal.workspace_id,
        actor_user_id=principal.user_id,
    )
    return _response(skill)
