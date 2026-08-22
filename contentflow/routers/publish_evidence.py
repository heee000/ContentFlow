from __future__ import annotations

import hashlib
import io
import logging
from datetime import datetime, timezone
from typing import Annotated

from uuid import UUID
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..db import get_db
from ..dependencies import AppSettings, CurrentPrincipal, Principal, require_role
from ..entities import PublishConfirmation, PublishEvidence, PublishJob
from ..object_storage import build_object_storage
from ..publish_evidence import (
    PublishEvidenceError,
    evidence_manifest_sha256,
    normalize_publish_evidence,
)
from ..schemas import (
    PublishConfirmationResponse,
    PublishEvidenceResponse,
    PublishJobResponse,
    PublishScriptResultRequest,
)


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/publishing", tags=["publishing"])
Db = Annotated[Session, Depends(get_db)]
Reviewer = Annotated[Principal, Depends(require_role("reviewer"))]


def _publish_job(
    session: Session,
    *,
    publish_job_id: str,
    workspace_id: str,
    for_update: bool = False,
) -> PublishJob:
    query = select(PublishJob).where(
        PublishJob.id == publish_job_id,
        PublishJob.workspace_id == workspace_id,
    )
    if for_update and session.bind and session.bind.dialect.name == "postgresql":
        query = query.with_for_update()
    job = session.scalar(query)
    if job is None:
        raise HTTPException(status_code=404, detail="Publish job does not exist")
    return job


def _script_context(job: PublishJob) -> tuple[str, str, dict]:
    response_json = dict(job.response_json or {})
    script_attempt_id = response_json.get("script_attempt_id")
    package_sha256 = response_json.get("package_sha256")
    valid_checksum = (
        isinstance(package_sha256, str)
        and len(package_sha256) == 64
        and all(character in "0123456789abcdef" for character in package_sha256)
    )
    try:
        valid_attempt = str(UUID(str(script_attempt_id))) == script_attempt_id
    except (ValueError, AttributeError, TypeError):
        valid_attempt = False

    if (
        job.delivery_mode != "script"
        or not isinstance(script_attempt_id, str)
        or not valid_attempt
        or not valid_checksum
    ):
        raise HTTPException(
            status_code=409,
            detail="Publish job does not have a valid script package context",
        )
    return script_attempt_id, package_sha256, response_json


def _current_evidence(
    session: Session,
    *,
    job: PublishJob,
    script_attempt_id: str,
) -> list[PublishEvidence]:
    return list(
        session.scalars(
            select(PublishEvidence)
            .where(
                PublishEvidence.workspace_id == job.workspace_id,
                PublishEvidence.publish_job_id == job.id,
                PublishEvidence.script_attempt_id == script_attempt_id,
            )
            .order_by(PublishEvidence.created_at, PublishEvidence.id)
        )
    )


@router.get(
    "/jobs/{publish_job_id}/evidence",
    response_model=list[PublishEvidenceResponse],
)
def list_publish_evidence(
    publish_job_id: str,
    principal: CurrentPrincipal,
    session: Db,
):
    job = _publish_job(
        session,
        publish_job_id=publish_job_id,
        workspace_id=principal.workspace_id,
    )
    script_attempt_id, _, _ = _script_context(job)
    return _current_evidence(
        session,
        job=job,
        script_attempt_id=script_attempt_id,
    )


@router.post(
    "/jobs/{publish_job_id}/evidence",
    response_model=PublishEvidenceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_publish_evidence(
    publish_job_id: str,
    principal: Reviewer,
    session: Db,
    settings: AppSettings,
    kind: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
):
    job = _publish_job(
        session,
        publish_job_id=publish_job_id,
        workspace_id=principal.workspace_id,
        for_update=True,
    )
    script_attempt_id, package_sha256, response_json = _script_context(job)
    if job.script_confirmation_expired:
        raise HTTPException(
            status_code=409,
            detail="Script confirmation window expired; create a new script attempt",
        )
    if job.status != "script_ready" or response_json.get("script_evidence_frozen"):
        raise HTTPException(
            status_code=409,
            detail="Evidence is frozen or the script package is not ready",
        )

    raw = await file.read(settings.publish_evidence_max_bytes + 1)
    try:
        normalized = normalize_publish_evidence(
            raw,
            filename=file.filename or "evidence",
            kind=kind,
            max_bytes=settings.publish_evidence_max_bytes,
            max_pixels=settings.publish_evidence_max_pixels,
        )
    except PublishEvidenceError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    duplicate = session.scalar(
        select(PublishEvidence).where(
            PublishEvidence.publish_job_id == job.id,
            PublishEvidence.script_attempt_id == script_attempt_id,
            PublishEvidence.object_sha256 == normalized.object_sha256,
        )
    )
    if duplicate is not None:
        raise HTTPException(
            status_code=409,
            detail="Equivalent evidence is already attached to this script attempt",
        )

    storage = build_object_storage(settings)
    stored = None
    try:
        stored = storage.put(
            workspace_id=principal.workspace_id,
            category=f"publish-evidence/{job.id}/{script_attempt_id}",
            filename=f"evidence-{normalized.object_sha256[:16]}.{normalized.extension}",
            stream=io.BytesIO(normalized.data),
            content_type=normalized.mime_type,
        )
        if stored.checksum != normalized.object_sha256 or stored.size_bytes != len(
            normalized.data
        ):
            raise ValueError("post-storage integrity verification failed")
    except (OSError, ValueError) as error:
        if stored is not None:
            try:
                storage.delete(stored.uri)
            except (OSError, ValueError):
                logger.exception("failed to compensate invalid evidence object")
        raise HTTPException(
            status_code=503, detail="Evidence storage failed"
        ) from error

    try:
        evidence = PublishEvidence(
            workspace_id=principal.workspace_id,
            publish_job_id=job.id,
            script_attempt_id=script_attempt_id,
            package_sha256=package_sha256,
            kind=normalized.kind,
            original_filename=normalized.original_filename,
            storage_uri=stored.uri,
            source_sha256=normalized.source_sha256,
            object_sha256=normalized.object_sha256,
            mime_type=normalized.mime_type,
            size_bytes=len(normalized.data),
            uploaded_by_user_id=principal.user_id,
        )
        session.add(evidence)
        session.flush()
        evidence_items = _current_evidence(
            session,
            job=job,
            script_attempt_id=script_attempt_id,
        )
        response_json["script_evidence_count"] = len(evidence_items)
        job.response_json = response_json
        record_audit(
            session,
            action="publish.evidence_uploaded",
            entity_type="publish_evidence",
            entity_id=evidence.id,
            workspace_id=principal.workspace_id,
            actor_user_id=principal.user_id,
            metadata={
                "publish_job_id": job.id,
                "script_attempt_id": script_attempt_id,
                "package_sha256": package_sha256,
                "kind": evidence.kind,
                "source_sha256": evidence.source_sha256,
                "object_sha256": evidence.object_sha256,
                "size_bytes": evidence.size_bytes,
            },
        )
        session.commit()
    except Exception:
        session.rollback()
        try:
            storage.delete(stored.uri)
        except Exception:
            logger.exception("failed to compensate uncommitted evidence object")
        raise
    session.refresh(evidence)
    return evidence


@router.get("/jobs/{publish_job_id}/evidence/{evidence_id}/download")
def download_publish_evidence(
    publish_job_id: str,
    evidence_id: str,
    principal: CurrentPrincipal,
    session: Db,
    settings: AppSettings,
):
    evidence = session.scalar(
        select(PublishEvidence).where(
            PublishEvidence.id == evidence_id,
            PublishEvidence.publish_job_id == publish_job_id,
            PublishEvidence.workspace_id == principal.workspace_id,
        )
    )
    if evidence is None:
        raise HTTPException(status_code=404, detail="Publish evidence does not exist")
    try:
        data = build_object_storage(settings).read(
            evidence.storage_uri,
            max_bytes=settings.publish_evidence_max_bytes,
        )
    except FileNotFoundError as error:
        raise HTTPException(
            status_code=404, detail="Evidence object is missing"
        ) from error
    except (OSError, ValueError) as error:
        raise HTTPException(
            status_code=409,
            detail="Evidence object failed integrity verification",
        ) from error
    if hashlib.sha256(data).hexdigest() != evidence.object_sha256:
        raise HTTPException(
            status_code=409,
            detail="Evidence object failed database integrity verification",
        )
    extension = {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/webp": "webp",
        "application/json": "json",
    }.get(evidence.mime_type, "bin")
    return Response(
        content=data,
        media_type=evidence.mime_type,
        headers={
            "Content-Disposition": (
                f"attachment; filename=publish-evidence-{evidence.id}.{extension}"
            ),
            "X-ContentFlow-Evidence-SHA256": evidence.object_sha256,
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get(
    "/jobs/{publish_job_id}/confirmations",
    response_model=list[PublishConfirmationResponse],
)
def list_publish_confirmations(
    publish_job_id: str,
    principal: CurrentPrincipal,
    session: Db,
):
    job = _publish_job(
        session,
        publish_job_id=publish_job_id,
        workspace_id=principal.workspace_id,
    )
    script_attempt_id, _, _ = _script_context(job)
    return list(
        session.scalars(
            select(PublishConfirmation)
            .where(
                PublishConfirmation.workspace_id == principal.workspace_id,
                PublishConfirmation.publish_job_id == job.id,
                PublishConfirmation.script_attempt_id == script_attempt_id,
            )
            .order_by(PublishConfirmation.created_at, PublishConfirmation.id)
        )
    )


def confirm_script_publish_result(
    *,
    session: Session,
    publish_job_id: str,
    workspace_id: str,
    actor_user_id: str,
    payload: PublishScriptResultRequest,
) -> PublishJob:
    job = _publish_job(
        session,
        publish_job_id=publish_job_id,
        workspace_id=workspace_id,
        for_update=True,
    )
    if job.status not in {"script_ready", "script_confirmation_pending"}:
        raise HTTPException(
            status_code=409,
            detail="Script result cannot be confirmed in the current state",
        )
    script_attempt_id, package_sha256, response_json = _script_context(job)
    if job.script_confirmation_expired:
        raise HTTPException(
            status_code=409,
            detail="Script confirmation window expired; create a new script attempt",
        )
    requested_by = response_json.get("script_requested_by_user_id")
    if not isinstance(requested_by, str):
        raise HTTPException(status_code=409, detail="Script requester is missing")
    if requested_by == actor_user_id:
        raise HTTPException(
            status_code=409,
            detail="The script requester cannot confirm the same attempt",
        )
    evidence_items = _current_evidence(
        session,
        job=job,
        script_attempt_id=script_attempt_id,
    )
    if not evidence_items:
        raise HTTPException(
            status_code=409,
            detail="At least one verified evidence item is required",
        )
    if any(item.package_sha256 != package_sha256 for item in evidence_items):
        raise HTTPException(
            status_code=409,
            detail="Evidence is not bound to the current script package",
        )

    confirmations = list(
        session.scalars(
            select(PublishConfirmation)
            .where(
                PublishConfirmation.workspace_id == workspace_id,
                PublishConfirmation.publish_job_id == job.id,
                PublishConfirmation.script_attempt_id == script_attempt_id,
            )
            .order_by(PublishConfirmation.created_at, PublishConfirmation.id)
        )
    )
    if any(item.confirmed_by_user_id == actor_user_id for item in confirmations):
        raise HTTPException(
            status_code=409,
            detail="A different reviewer must provide the next confirmation",
        )
    required = job.script_confirmation_required
    if len(confirmations) >= required:
        raise HTTPException(
            status_code=409, detail="Script result is already confirmed"
        )
    if job.status == "script_confirmation_pending" and (
        required != 2 or len(confirmations) != 1
    ):
        raise HTTPException(
            status_code=409,
            detail="Script confirmation state is inconsistent",
        )
    if confirmations and any(
        item.decision != payload.decision for item in confirmations
    ):
        raise HTTPException(
            status_code=409,
            detail="Independent reviewers must agree on the publication decision",
        )

    manifest_sha256 = evidence_manifest_sha256(
        evidence_items,
        script_attempt_id=script_attempt_id,
        package_sha256=package_sha256,
    )
    if confirmations and any(
        item.evidence_manifest_sha256 != manifest_sha256 for item in confirmations
    ):
        raise HTTPException(
            status_code=409,
            detail="Evidence manifest changed after the first confirmation",
        )

    prior_external_id = next(
        (item.external_id for item in confirmations if item.external_id),
        None,
    )
    prior_external_url = next(
        (item.external_url for item in confirmations if item.external_url),
        None,
    )
    if (
        prior_external_id
        and payload.external_id
        and prior_external_id != payload.external_id
    ) or (
        prior_external_url
        and payload.external_url
        and prior_external_url != payload.external_url
    ):
        raise HTTPException(
            status_code=409,
            detail="Independent reviewers supplied conflicting platform references",
        )

    confirmed_at = datetime.now(timezone.utc)
    confirmation = PublishConfirmation(
        workspace_id=workspace_id,
        publish_job_id=job.id,
        script_attempt_id=script_attempt_id,
        package_sha256=package_sha256,
        decision=payload.decision,
        reason=payload.reason,
        external_id=payload.external_id,
        external_url=payload.external_url,
        evidence_manifest_sha256=manifest_sha256,
        confirmed_by_user_id=actor_user_id,
        created_at=confirmed_at,
    )
    session.add(confirmation)
    session.flush()

    confirmation_count = len(confirmations) + 1
    response_json.update(
        {
            "script_confirmation_required": required,
            "script_confirmation_count": confirmation_count,
            "script_confirmation_decision": payload.decision,
            "script_confirmation_last_at": confirmed_at.isoformat(),
            "script_evidence_count": len(evidence_items),
            "script_evidence_frozen": True,
            "script_evidence_manifest_sha256": manifest_sha256,
        }
    )
    record_audit(
        session,
        action="publish.script_confirmation",
        entity_type="publish_confirmation",
        entity_id=confirmation.id,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        metadata={
            "publish_job_id": job.id,
            "script_attempt_id": script_attempt_id,
            "package_sha256": package_sha256,
            "decision": payload.decision,
            "confirmation_count": confirmation_count,
            "confirmation_required": required,
            "evidence_manifest_sha256": manifest_sha256,
        },
    )
    if confirmation_count < required:
        job.status = "script_confirmation_pending"
        job.response_json = response_json
        return job

    final_external_id = payload.external_id or prior_external_id
    final_external_url = payload.external_url or prior_external_url
    response_json["script_result"] = {
        "decision": payload.decision,
        "reason": payload.reason,
        "actor_user_id": actor_user_id,
        "confirmed_at": confirmed_at.isoformat(),
        "confirmation_count": confirmation_count,
        "evidence_manifest_sha256": manifest_sha256,
    }
    job.response_json = response_json
    if payload.decision == "confirmed_published":
        job.status = "script_published"
        job.external_id = final_external_id
        job.external_url = final_external_url
        job.published_at = confirmed_at
        job.error = None
    else:
        job.status = "failed"
        job.external_id = None
        job.external_url = None
        job.published_at = None
        job.error = f"Reviewers confirmed script publication failed: {payload.reason}"[
            :8000
        ]
    record_audit(
        session,
        action="publish.script_result",
        entity_type="publish_job",
        entity_id=job.id,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        metadata={
            "decision": payload.decision,
            "reason": payload.reason,
            "confirmation_count": confirmation_count,
            "confirmation_required": required,
            "script_attempt_id": script_attempt_id,
            "package_sha256": package_sha256,
            "evidence_manifest_sha256": manifest_sha256,
        },
    )
    return job


@router.post(
    "/jobs/{publish_job_id}/script-result",
    response_model=PublishJobResponse,
)
def confirm_script_result(
    publish_job_id: str,
    payload: PublishScriptResultRequest,
    principal: Reviewer,
    session: Db,
):
    return confirm_script_publish_result(
        session=session,
        publish_job_id=publish_job_id,
        workspace_id=principal.workspace_id,
        actor_user_id=principal.user_id,
        payload=payload,
    )
