from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..audit import record_audit, verify_audit_chain
from ..db import get_db
from ..dependencies import AppSettings, Principal, require_role
from ..entities import (
    AuditLog,
    Job,
    JobManualReview,
    Membership,
    PromptEvalRun,
    PromptEvalSuite,
    PromptRelease,
    StorageObjectAllocation,
    User,
    WorkerNode,
    Workspace,
    WorkspaceStorageUsage,
)
from ..job_queue import enqueue_job
from ..pagination import (
    DEFAULT_PAGE_LIMIT,
    PageCursor,
    PageLimit,
    paginate,
    paginate_sequence,
)
from ..prompt_eval import (
    EvalIntegrityError,
    calculate_suite_hash,
    eval_suite_version,
    get_active_eval_suite,
    normalize_eval_cases,
    require_current_passed_eval,
    verify_eval_suite,
)
from ..prompt_governance import (
    PromptIntegrityError,
    normalize_prompts,
    prompt_release_version,
    prompt_set_from_release,
    resolve_active_prompt_set,
)
from ..prompts import BUILTIN_PROMPT_SET, calculate_prompt_hashes
from ..schemas import (
    AuditLogResponse,
    AuditIntegrityResponse,
    JobResponse,
    MemberCreate,
    MemberResponse,
    MemberUpdate,
    PromptEvalGovernanceResponse,
    PromptEvalRequest,
    PromptEvalRunResponse,
    PromptEvalSuiteCreate,
    PromptEvalSuiteResponse,
    PromptGovernanceResponse,
    PromptReleaseCreate,
    PromptReleaseResponse,
    PromptReviewRequest,
    StorageObjectAllocationResponse,
    StorageReconcileRequest,
    StorageUsageResponse,
    WorkerHealthResponse,
    WorkerQueueHealthResponse,
)
from ..storage_ledger import (
    enqueue_storage_reconciliation,
    pending_storage_counts,
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
    workspace_query = select(Workspace.id).where(Workspace.id == workspace_id)
    if session.bind and session.bind.dialect.name == "postgresql":
        workspace_query = workspace_query.with_for_update()
    if session.scalar(workspace_query) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workspace not found",
        )
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
def list_members(
    principal: Admin,
    session: Db,
    response: Response,
    limit: PageLimit = DEFAULT_PAGE_LIMIT,
    cursor: PageCursor = None,
):
    rows = paginate(
        session,
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(Membership.workspace_id == principal.workspace_id),
        timestamp_column=Membership.created_at,
        id_column=Membership.id,
        limit=limit,
        cursor=cursor,
        response=response,
        ascending=True,
        scalar=False,
    )
    return [member_response(membership, user) for membership, user in rows]


@router.post(
    "/members",
    response_model=MemberResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_member(payload: MemberCreate, principal: Admin, session: Db):
    user = session.scalar(select(User).where(User.email == payload.email.lower()))
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


def prompt_release_response(release: PromptRelease) -> PromptReleaseResponse:
    return PromptReleaseResponse(
        id=release.id,
        workspace_id=release.workspace_id,
        release_number=release.release_number,
        version=prompt_release_version(release.release_number),
        status=release.status,
        prompts=dict(release.prompts_json),
        prompt_hashes=dict(release.prompt_hashes_json),
        change_summary=release.change_summary,
        review_note=release.review_note,
        created_by_user_id=release.created_by_user_id,
        reviewed_by_user_id=release.reviewed_by_user_id,
        activated_by_user_id=release.activated_by_user_id,
        reviewed_at=release.reviewed_at,
        activated_at=release.activated_at,
        created_at=release.created_at,
        updated_at=release.updated_at,
    )


def prompt_eval_suite_response(suite: PromptEvalSuite) -> PromptEvalSuiteResponse:
    return PromptEvalSuiteResponse(
        id=suite.id,
        workspace_id=suite.workspace_id,
        version_number=suite.version_number,
        version=eval_suite_version(suite.version_number),
        status=suite.status,
        name=suite.name,
        description=suite.description,
        cases=list(suite.cases_json),
        suite_hash=suite.suite_hash,
        created_by_user_id=suite.created_by_user_id,
        activated_by_user_id=suite.activated_by_user_id,
        activated_at=suite.activated_at,
        created_at=suite.created_at,
        updated_at=suite.updated_at,
    )


def prompt_eval_run_response(run: PromptEvalRun) -> PromptEvalRunResponse:
    return PromptEvalRunResponse(
        id=run.id,
        workspace_id=run.workspace_id,
        prompt_release_id=run.prompt_release_id,
        suite_id=run.suite_id,
        status=run.status,
        requested_provider=run.requested_provider,
        provider=run.provider,
        model=run.model,
        prompt_hashes=dict(run.prompt_hashes_json),
        suite_hash=run.suite_hash,
        result_json=dict(run.result_json or {}),
        error=run.error,
        created_by_user_id=run.created_by_user_id,
        started_at=run.started_at,
        completed_at=run.completed_at,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


def require_eval_gate_or_409(
    session: Session,
    release: PromptRelease,
    settings: AppSettings,
) -> tuple[PromptEvalSuite, PromptEvalRun]:
    try:
        return require_current_passed_eval(session, release, settings)
    except (EvalIntegrityError, PromptIntegrityError) as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Prompt 或当前 Eval 套件完整性校验失败，禁止审批或激活",
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error


def prompt_generation_readiness(
    session: Session,
    *,
    active_release_id: str | None,
    settings: AppSettings,
) -> tuple[bool, str | None]:
    if active_release_id is None:
        if settings.require_governed_prompts:
            return (
                False,
                "当前环境要求受治理 Prompt；请先创建并激活 Eval 套件，"
                "再完成 Prompt 评测、双人审批与激活",
            )
        return True, None

    release = session.get(PromptRelease, active_release_id)
    if release is None:
        return False, "当前生效 Prompt 版本不存在"
    try:
        require_current_passed_eval(session, release, settings)
    except (EvalIntegrityError, PromptIntegrityError):
        return False, "当前 Prompt 或 Eval 套件完整性校验失败"
    except ValueError as error:
        return False, str(error)
    return True, None


def lock_workspace(session: Session, workspace_id: str) -> Workspace:
    query = select(Workspace).where(Workspace.id == workspace_id)
    if session.bind and session.bind.dialect.name == "postgresql":
        query = query.with_for_update()
    workspace = session.scalar(query)
    if workspace is None:
        raise HTTPException(status_code=404, detail="工作区不存在")
    return workspace


def get_prompt_release_or_404(
    session: Session,
    *,
    workspace_id: str,
    release_id: str,
    lock: bool = False,
) -> PromptRelease:
    query = select(PromptRelease).where(
        PromptRelease.id == release_id,
        PromptRelease.workspace_id == workspace_id,
    )
    if lock and session.bind and session.bind.dialect.name == "postgresql":
        query = query.with_for_update()
    release = session.scalar(query)
    if release is None:
        raise HTTPException(status_code=404, detail="Prompt 版本不存在")
    return release


@router.get(
    "/prompt-releases",
    response_model=PromptGovernanceResponse,
)
def list_prompt_releases(principal: Admin, session: Db, settings: AppSettings):
    releases = list(
        session.scalars(
            select(PromptRelease)
            .where(PromptRelease.workspace_id == principal.workspace_id)
            .order_by(PromptRelease.release_number.desc())
            .limit(DEFAULT_PAGE_LIMIT)
        )
    )
    try:
        active = resolve_active_prompt_set(session, principal.workspace_id)
    except PromptIntegrityError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="当前生效 Prompt 的完整性校验失败，请暂停生成并联系管理员",
        ) from error
    ready_for_generation, generation_block_reason = prompt_generation_readiness(
        session,
        active_release_id=active.release_id,
        settings=settings,
    )
    return PromptGovernanceResponse(
        active={
            "source": active.source,
            "version": active.version,
            "release_id": active.release_id,
            "prompts": dict(active.prompts),
            "prompt_hashes": dict(active.hashes),
        },
        builtin={
            "source": BUILTIN_PROMPT_SET.source,
            "version": BUILTIN_PROMPT_SET.version,
            "release_id": None,
            "prompts": dict(BUILTIN_PROMPT_SET.prompts),
            "prompt_hashes": dict(BUILTIN_PROMPT_SET.hashes),
        },
        governance_required=settings.require_governed_prompts,
        ready_for_generation=ready_for_generation,
        generation_block_reason=generation_block_reason,
        releases=[prompt_release_response(release) for release in releases],
    )


@router.get(
    "/prompt-releases/history",
    response_model=list[PromptReleaseResponse],
)
def list_prompt_release_history(
    principal: Admin,
    session: Db,
    response: Response,
    limit: PageLimit = DEFAULT_PAGE_LIMIT,
    cursor: PageCursor = None,
):
    releases = paginate_sequence(
        session,
        select(PromptRelease).where(
            PromptRelease.workspace_id == principal.workspace_id
        ),
        sequence_column=PromptRelease.release_number,
        id_column=PromptRelease.id,
        limit=limit,
        cursor=cursor,
        response=response,
    )
    return [prompt_release_response(release) for release in releases]


@router.post(
    "/prompt-releases",
    response_model=PromptReleaseResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_prompt_release(
    payload: PromptReleaseCreate,
    principal: Admin,
    session: Db,
):
    try:
        prompts = normalize_prompts(payload.prompts)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error

    summary = payload.change_summary.strip()
    if len(summary) < 3:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="变更摘要去除首尾空白后至少需要 3 个字符",
        )

    lock_workspace(session, principal.workspace_id)
    latest = session.scalar(
        select(func.max(PromptRelease.release_number)).where(
            PromptRelease.workspace_id == principal.workspace_id
        )
    )
    release = PromptRelease(
        workspace_id=principal.workspace_id,
        release_number=int(latest or 0) + 1,
        status="draft",
        prompts_json=prompts,
        prompt_hashes_json=calculate_prompt_hashes(prompts),
        change_summary=summary,
        created_by_user_id=principal.user_id,
    )
    session.add(release)
    session.flush()
    record_audit(
        session,
        action="prompt_release.create",
        entity_type="prompt_release",
        entity_id=release.id,
        workspace_id=principal.workspace_id,
        actor_user_id=principal.user_id,
        metadata={
            "release_number": release.release_number,
            "version": prompt_release_version(release.release_number),
            "prompt_hashes": dict(release.prompt_hashes_json),
        },
    )
    session.commit()
    session.refresh(release)
    return prompt_release_response(release)


@router.post(
    "/prompt-releases/{release_id}/approve",
    response_model=PromptReleaseResponse,
)
def approve_prompt_release(
    release_id: str,
    payload: PromptReviewRequest,
    principal: Admin,
    session: Db,
    settings: AppSettings,
):
    release = get_prompt_release_or_404(
        session,
        workspace_id=principal.workspace_id,
        release_id=release_id,
        lock=True,
    )
    if release.created_by_user_id == principal.user_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="创建者不能审批自己的 Prompt 版本",
        )
    if release.status != "draft":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="只有草稿状态的 Prompt 版本可以审批",
        )
    require_eval_gate_or_409(session, release, settings)
    release.status = "approved"
    release.review_note = payload.note.strip() or None
    release.reviewed_by_user_id = principal.user_id
    release.reviewed_at = datetime.now(timezone.utc)
    record_audit(
        session,
        action="prompt_release.approve",
        entity_type="prompt_release",
        entity_id=release.id,
        workspace_id=principal.workspace_id,
        actor_user_id=principal.user_id,
        metadata={
            "release_number": release.release_number,
            "prompt_hashes": dict(release.prompt_hashes_json),
        },
    )
    session.commit()
    session.refresh(release)
    return prompt_release_response(release)


@router.post(
    "/prompt-releases/{release_id}/reject",
    response_model=PromptReleaseResponse,
)
def reject_prompt_release(
    release_id: str,
    payload: PromptReviewRequest,
    principal: Admin,
    session: Db,
):
    note = payload.note.strip()
    if not note:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="拒绝 Prompt 版本时必须填写原因",
        )
    release = get_prompt_release_or_404(
        session,
        workspace_id=principal.workspace_id,
        release_id=release_id,
        lock=True,
    )
    if release.created_by_user_id == principal.user_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="创建者不能复核自己的 Prompt 版本",
        )
    if release.status != "draft":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="只有草稿状态的 Prompt 版本可以拒绝",
        )
    release.status = "rejected"
    release.review_note = note
    release.reviewed_by_user_id = principal.user_id
    release.reviewed_at = datetime.now(timezone.utc)
    record_audit(
        session,
        action="prompt_release.reject",
        entity_type="prompt_release",
        entity_id=release.id,
        workspace_id=principal.workspace_id,
        actor_user_id=principal.user_id,
        metadata={
            "release_number": release.release_number,
            "reason": note,
            "prompt_hashes": dict(release.prompt_hashes_json),
        },
    )
    session.commit()
    session.refresh(release)
    return prompt_release_response(release)


@router.post(
    "/prompt-releases/{release_id}/activate",
    response_model=PromptReleaseResponse,
)
def activate_prompt_release(
    release_id: str,
    principal: Admin,
    session: Db,
    settings: AppSettings,
):
    lock_workspace(session, principal.workspace_id)
    release = get_prompt_release_or_404(
        session,
        workspace_id=principal.workspace_id,
        release_id=release_id,
        lock=True,
    )
    if release.status not in {"approved", "retired"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="只有已审批或已退役的 Prompt 版本可以激活",
        )
    try:
        prompt_set_from_release(release)
    except (PromptIntegrityError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="该 Prompt 版本的完整性校验失败，禁止激活",
        ) from error

    require_eval_gate_or_409(session, release, settings)
    rollback = release.status == "retired"
    current = session.scalar(
        select(PromptRelease).where(
            PromptRelease.workspace_id == principal.workspace_id,
            PromptRelease.status == "active",
            PromptRelease.id != release.id,
        )
    )
    previous_release_id = current.id if current is not None else None
    if current is not None:
        current.status = "retired"
        session.flush()

    release.status = "active"
    release.activated_by_user_id = principal.user_id
    release.activated_at = datetime.now(timezone.utc)
    session.flush()
    record_audit(
        session,
        action=("prompt_release.rollback" if rollback else "prompt_release.activate"),
        entity_type="prompt_release",
        entity_id=release.id,
        workspace_id=principal.workspace_id,
        actor_user_id=principal.user_id,
        metadata={
            "release_number": release.release_number,
            "previous_release_id": previous_release_id,
            "prompt_hashes": dict(release.prompt_hashes_json),
        },
    )
    session.commit()
    session.refresh(release)
    return prompt_release_response(release)


@router.get("/prompt-eval", response_model=PromptEvalGovernanceResponse)
def list_prompt_eval(principal: Admin, session: Db):
    suites = list(
        session.scalars(
            select(PromptEvalSuite)
            .where(PromptEvalSuite.workspace_id == principal.workspace_id)
            .order_by(PromptEvalSuite.version_number.desc())
            .limit(DEFAULT_PAGE_LIMIT)
        )
    )
    runs = list(
        session.scalars(
            select(PromptEvalRun)
            .where(PromptEvalRun.workspace_id == principal.workspace_id)
            .order_by(PromptEvalRun.created_at.desc())
            .limit(100)
        )
    )
    active = next((suite for suite in suites if suite.status == "active"), None)
    return PromptEvalGovernanceResponse(
        active_suite=(prompt_eval_suite_response(active) if active else None),
        suites=[prompt_eval_suite_response(suite) for suite in suites],
        runs=[prompt_eval_run_response(run) for run in runs],
    )


@router.get(
    "/prompt-eval/suites",
    response_model=list[PromptEvalSuiteResponse],
)
def list_prompt_eval_suites(
    principal: Admin,
    session: Db,
    response: Response,
    limit: PageLimit = DEFAULT_PAGE_LIMIT,
    cursor: PageCursor = None,
):
    suites = paginate_sequence(
        session,
        select(PromptEvalSuite).where(
            PromptEvalSuite.workspace_id == principal.workspace_id
        ),
        sequence_column=PromptEvalSuite.version_number,
        id_column=PromptEvalSuite.id,
        limit=limit,
        cursor=cursor,
        response=response,
    )
    return [prompt_eval_suite_response(suite) for suite in suites]


@router.get(
    "/prompt-eval/runs",
    response_model=list[PromptEvalRunResponse],
)
def list_prompt_eval_runs(
    principal: Admin,
    session: Db,
    response: Response,
    limit: PageLimit = DEFAULT_PAGE_LIMIT,
    cursor: PageCursor = None,
):
    runs = paginate(
        session,
        select(PromptEvalRun).where(
            PromptEvalRun.workspace_id == principal.workspace_id
        ),
        timestamp_column=PromptEvalRun.created_at,
        id_column=PromptEvalRun.id,
        limit=limit,
        cursor=cursor,
        response=response,
    )
    return [prompt_eval_run_response(run) for run in runs]


@router.post(
    "/prompt-eval/suites",
    response_model=PromptEvalSuiteResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_prompt_eval_suite(
    payload: PromptEvalSuiteCreate,
    principal: Admin,
    session: Db,
):
    try:
        cases = normalize_eval_cases(
            [case.model_dump(mode="json") for case in payload.cases]
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    name = payload.name.strip()
    if len(name) < 3:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Eval 套件名称去除首尾空白后至少需要 3 个字符",
        )

    lock_workspace(session, principal.workspace_id)
    latest = session.scalar(
        select(func.max(PromptEvalSuite.version_number)).where(
            PromptEvalSuite.workspace_id == principal.workspace_id
        )
    )
    suite = PromptEvalSuite(
        workspace_id=principal.workspace_id,
        version_number=int(latest or 0) + 1,
        status="draft",
        name=name,
        description=payload.description.strip(),
        cases_json=cases,
        suite_hash=calculate_suite_hash(cases),
        created_by_user_id=principal.user_id,
    )
    session.add(suite)
    session.flush()
    record_audit(
        session,
        action="prompt_eval_suite.create",
        entity_type="prompt_eval_suite",
        entity_id=suite.id,
        workspace_id=principal.workspace_id,
        actor_user_id=principal.user_id,
        metadata={
            "version": eval_suite_version(suite.version_number),
            "suite_hash": suite.suite_hash,
            "case_count": len(cases),
        },
    )
    session.commit()
    session.refresh(suite)
    return prompt_eval_suite_response(suite)


def get_prompt_eval_suite_or_404(
    session: Session,
    *,
    workspace_id: str,
    suite_id: str,
    lock: bool = False,
) -> PromptEvalSuite:
    query = select(PromptEvalSuite).where(
        PromptEvalSuite.id == suite_id,
        PromptEvalSuite.workspace_id == workspace_id,
    )
    if lock and session.bind and session.bind.dialect.name == "postgresql":
        query = query.with_for_update()
    suite = session.scalar(query)
    if suite is None:
        raise HTTPException(status_code=404, detail="Prompt Eval 套件不存在")
    return suite


@router.post(
    "/prompt-eval/suites/{suite_id}/activate",
    response_model=PromptEvalSuiteResponse,
)
def activate_prompt_eval_suite(
    suite_id: str,
    principal: Admin,
    session: Db,
):
    lock_workspace(session, principal.workspace_id)
    suite = get_prompt_eval_suite_or_404(
        session,
        workspace_id=principal.workspace_id,
        suite_id=suite_id,
        lock=True,
    )
    if suite.created_by_user_id == principal.user_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="创建者不能激活自己的 Prompt Eval 套件",
        )
    if suite.status not in {"draft", "retired"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="只有草稿或已退役的 Prompt Eval 套件可以激活",
        )
    try:
        verify_eval_suite(suite)
    except (EvalIntegrityError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Prompt Eval 套件完整性校验失败，禁止激活",
        ) from error

    current = session.scalar(
        select(PromptEvalSuite).where(
            PromptEvalSuite.workspace_id == principal.workspace_id,
            PromptEvalSuite.status == "active",
            PromptEvalSuite.id != suite.id,
        )
    )
    previous_suite_id = current.id if current else None
    if current:
        current.status = "retired"
        session.flush()
    suite.status = "active"
    suite.activated_by_user_id = principal.user_id
    suite.activated_at = datetime.now(timezone.utc)
    session.flush()
    record_audit(
        session,
        action="prompt_eval_suite.activate",
        entity_type="prompt_eval_suite",
        entity_id=suite.id,
        workspace_id=principal.workspace_id,
        actor_user_id=principal.user_id,
        metadata={
            "version": eval_suite_version(suite.version_number),
            "suite_hash": suite.suite_hash,
            "previous_suite_id": previous_suite_id,
        },
    )
    session.commit()
    session.refresh(suite)
    return prompt_eval_suite_response(suite)


@router.post(
    "/prompt-releases/{release_id}/evaluate",
    response_model=PromptEvalRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def evaluate_prompt_release(
    release_id: str,
    payload: PromptEvalRequest,
    principal: Admin,
    session: Db,
    settings: AppSettings,
):
    lock_workspace(session, principal.workspace_id)
    release = get_prompt_release_or_404(
        session,
        workspace_id=principal.workspace_id,
        release_id=release_id,
        lock=True,
    )
    if release.status == "rejected":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="已拒绝的 Prompt 版本不能运行评测",
        )
    try:
        prompt_set = prompt_set_from_release(release)
        suite = get_active_eval_suite(session, principal.workspace_id)
        if suite is None:
            raise ValueError("当前工作区没有生效的 Prompt Eval 套件")
        verify_eval_suite(suite)
    except (EvalIntegrityError, PromptIntegrityError) as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Prompt 或当前 Eval 套件完整性校验失败，禁止运行评测",
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error

    provider = (payload.provider or settings.text_provider).strip().lower()
    if provider not in {"mock", "openai-compatible"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"不支持的文本模型 Provider: {provider}",
        )
    run = PromptEvalRun(
        workspace_id=principal.workspace_id,
        prompt_release_id=release.id,
        suite_id=suite.id,
        status="queued",
        requested_provider=provider,
        prompt_hashes_json=dict(prompt_set.hashes),
        suite_hash=suite.suite_hash,
        result_json={},
        created_by_user_id=principal.user_id,
    )
    session.add(run)
    session.flush()
    enqueue_job(
        session,
        job_type="prompt_eval.execute",
        payload={"run_id": run.id},
        workspace_id=principal.workspace_id,
        idempotency_key=f"prompt_eval.execute:{run.id}",
    )
    record_audit(
        session,
        action="prompt_eval.queue",
        entity_type="prompt_eval_run",
        entity_id=run.id,
        workspace_id=principal.workspace_id,
        actor_user_id=principal.user_id,
        metadata={
            "prompt_release_id": release.id,
            "suite_id": suite.id,
            "suite_hash": suite.suite_hash,
            "prompt_hashes": dict(prompt_set.hashes),
            "requested_provider": provider,
        },
    )
    session.commit()
    session.refresh(run)
    return prompt_eval_run_response(run)


@router.get("/audit-logs", response_model=list[AuditLogResponse])
def list_audit_logs(
    principal: Admin,
    session: Db,
    response: Response,
    action: str | None = None,
    entity_type: str | None = None,
    limit: PageLimit = DEFAULT_PAGE_LIMIT,
    cursor: PageCursor = None,
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
    rows = paginate_sequence(
        session,
        query,
        sequence_column=AuditLog.chain_sequence,
        id_column=AuditLog.id,
        limit=limit,
        cursor=cursor,
        response=response,
        scalar=False,
    )
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
            chain_sequence=audit.chain_sequence,
            entry_hash=audit.entry_hash,
            integrity_version=audit.integrity_version,
            created_at=audit.created_at,
        )
        for audit, display_name in rows
    ]


@router.get("/audit-integrity", response_model=AuditIntegrityResponse)
def audit_integrity(principal: Admin, session: Db):
    result = verify_audit_chain(
        session,
        workspace_id=principal.workspace_id,
    )
    return AuditIntegrityResponse(
        valid=result.valid,
        checked_entries=result.checked_entries,
        head_sequence=result.head_sequence,
        head_hash=result.head_hash,
        first_invalid_sequence=result.first_invalid_sequence,
        reason=result.reason,
        verified_at=datetime.now(timezone.utc),
    )


def heartbeat_age_seconds(value: datetime, now: datetime) -> float:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return max(0.0, (now - value).total_seconds())


@router.get("/worker-health", response_model=WorkerHealthResponse)
def worker_health(
    principal: Admin,
    session: Db,
    settings: AppSettings,
):
    now = datetime.now(timezone.utc)
    nodes = list(
        session.scalars(
            select(WorkerNode).order_by(WorkerNode.heartbeat_at.desc()).limit(500)
        )
    )
    active_workers = 0
    stale_workers = 0
    stopped_workers = 0
    for node in nodes:
        age = heartbeat_age_seconds(node.heartbeat_at, now)
        if node.status == "stopped":
            stopped_workers += 1
        elif age > settings.worker_stale_seconds:
            stale_workers += 1
        else:
            active_workers += 1

    queue_counts = {
        "queued": 0,
        "retry": 0,
        "running": 0,
        "manual_review": 0,
        "failed": 0,
    }
    for job_status, count in session.execute(
        select(Job.status, func.count(Job.id))
        .where(
            Job.workspace_id == principal.workspace_id,
            Job.status.in_(queue_counts),
        )
        .group_by(Job.status)
    ):
        queue_counts[job_status] = int(count)

    ready_filter = (
        Job.workspace_id == principal.workspace_id,
        Job.status.in_(["queued", "retry"]),
        Job.run_at <= now,
    )
    ready_jobs = int(
        session.scalar(select(func.count(Job.id)).where(*ready_filter)) or 0
    )
    oldest_ready_at = session.scalar(select(func.min(Job.run_at)).where(*ready_filter))
    oldest_ready_age = (
        heartbeat_age_seconds(oldest_ready_at, now)
        if oldest_ready_at is not None
        else None
    )
    oldest_manual_review_at = session.scalar(
        select(func.min(JobManualReview.requested_at)).where(
            JobManualReview.workspace_id == principal.workspace_id,
            JobManualReview.resolved_at.is_(None),
        )
    )
    oldest_manual_review_age = (
        heartbeat_age_seconds(oldest_manual_review_at, now)
        if oldest_manual_review_at is not None
        else None
    )

    issues: list[str] = []
    if active_workers == 0:
        issues.append("no_active_workers")
    if stale_workers:
        issues.append("stale_worker_nodes")
    if queue_counts["manual_review"]:
        issues.append("manual_review_pending")
    if ready_jobs and active_workers == 0:
        issues.append("ready_jobs_without_active_workers")
    if (
        oldest_ready_age is not None
        and oldest_ready_age > settings.worker_queue_stall_seconds
    ):
        issues.append("queue_ready_age_exceeded")

    if active_workers == 0:
        health_status = "unavailable"
    elif issues:
        health_status = "degraded"
    else:
        health_status = "healthy"

    return WorkerHealthResponse(
        status=health_status,
        checked_at=now,
        active_workers=active_workers,
        stale_workers=stale_workers,
        stopped_workers=stopped_workers,
        issues=issues,
        thresholds={
            "heartbeat_seconds": settings.worker_heartbeat_seconds,
            "stale_seconds": settings.worker_stale_seconds,
            "queue_stall_seconds": settings.worker_queue_stall_seconds,
        },
        queue=WorkerQueueHealthResponse(
            **queue_counts,
            ready=ready_jobs,
            oldest_ready_age_seconds=(
                round(oldest_ready_age, 3) if oldest_ready_age is not None else None
            ),
            oldest_manual_review_age_seconds=(
                round(oldest_manual_review_age, 3)
                if oldest_manual_review_age is not None
                else None
            ),
        ),
    )


@router.get("/storage/usage", response_model=StorageUsageResponse)
def storage_usage(
    principal: Admin,
    session: Db,
    settings: AppSettings,
):
    usage = session.get(WorkspaceStorageUsage, principal.workspace_id)
    counts = pending_storage_counts(session, principal.workspace_id)
    return StorageUsageResponse(
        used_bytes=usage.used_bytes if usage is not None else 0,
        used_objects=usage.used_objects if usage is not None else 0,
        reserved_bytes=usage.reserved_bytes if usage is not None else 0,
        reserved_objects=usage.reserved_objects if usage is not None else 0,
        unverified_objects=usage.unverified_objects if usage is not None else 0,
        max_bytes=settings.workspace_storage_max_bytes,
        max_objects=settings.workspace_storage_max_objects,
        delete_pending_objects=counts["delete_pending"],
        missing_objects=counts["missing"],
        integrity_error_objects=counts["integrity_error"],
        abandoned_reservations=counts["abandoned"],
        last_reconciled_at=(
            usage.last_reconciled_at if usage is not None else None
        ),
    )


@router.get(
    "/storage/objects",
    response_model=list[StorageObjectAllocationResponse],
)
def list_storage_objects(
    principal: Admin,
    session: Db,
    response: Response,
    status_filter: str | None = None,
    attention_only: bool = False,
    limit: PageLimit = DEFAULT_PAGE_LIMIT,
    cursor: PageCursor = None,
):
    allowed_statuses = {
        "reserved",
        "active",
        "delete_pending",
        "missing",
        "integrity_error",
        "deleted",
        "abandoned",
    }
    if status_filter is not None and status_filter not in allowed_statuses:
        raise HTTPException(status_code=422, detail="存储对象状态筛选值无效")
    if status_filter is not None and attention_only:
        raise HTTPException(
            status_code=422,
            detail="单一状态筛选与异常对象筛选不能同时使用",
        )
    query = select(StorageObjectAllocation).where(
        StorageObjectAllocation.workspace_id == principal.workspace_id
    )
    if status_filter is not None:
        query = query.where(StorageObjectAllocation.status == status_filter)
    elif attention_only:
        query = query.where(
            StorageObjectAllocation.status.in_(
                ("delete_pending", "missing", "integrity_error", "abandoned")
            )
        )
    return paginate(
        session,
        query,
        timestamp_column=StorageObjectAllocation.updated_at,
        id_column=StorageObjectAllocation.id,
        limit=limit,
        cursor=cursor,
        response=response,
    )


@router.post(
    "/storage/reconcile",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def reconcile_storage(
    payload: StorageReconcileRequest,
    principal: Admin,
    session: Db,
    settings: AppSettings,
):
    workspace_query = select(Workspace.id).where(
        Workspace.id == principal.workspace_id
    )
    if session.bind and session.bind.dialect.name == "postgresql":
        workspace_query = workspace_query.with_for_update()
    if session.scalar(workspace_query) is None:
        raise HTTPException(status_code=404, detail="工作区不存在")
    active_job = session.scalar(
        select(Job)
        .where(
            Job.workspace_id == principal.workspace_id,
            Job.job_type == "storage.reconcile",
            Job.status.in_(("queued", "retry", "running")),
        )
        .order_by(Job.created_at.asc())
        .limit(1)
    )
    if active_job is not None:
        active_deletes_orphans = (
            (active_job.payload_json or {}).get("delete_orphans") is True
        )
        if payload.delete_orphans and not active_deletes_orphans:
            raise HTTPException(
                status_code=409,
                detail=(
                    "已有仅核对任务正在运行；请等待完成后再发起孤儿对象清理"
                ),
            )
        return active_job

    requested_at = datetime.now(timezone.utc)
    job, run_id, _created = enqueue_storage_reconciliation(
        session,
        settings=settings,
        workspace_id=principal.workspace_id,
        delete_orphans=payload.delete_orphans,
        trigger="manual",
        requested_at=requested_at,
    )
    record_audit(
        session,
        action="storage.reconcile_requested",
        entity_type="workspace",
        entity_id=principal.workspace_id,
        workspace_id=principal.workspace_id,
        actor_user_id=principal.user_id,
        metadata={
            "run_id": run_id,
            "delete_orphans": payload.delete_orphans,
            "job_id": job.id,
        },
    )
    return job
