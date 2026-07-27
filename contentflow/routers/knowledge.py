from __future__ import annotations

import io
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..db import get_db
from ..dependencies import AppSettings, CurrentPrincipal, Principal, require_role
from ..entities import KnowledgeDocument
from ..job_queue import enqueue_job
from ..object_storage import build_object_storage
from ..schemas import KnowledgeDocumentResponse


router = APIRouter(prefix="/knowledge", tags=["knowledge"])
Db = Annotated[Session, Depends(get_db)]
Editor = Annotated[Principal, Depends(require_role("editor"))]

ALLOWED_EXTENSIONS = {".md", ".txt", ".csv", ".json"}
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


@router.get("/documents", response_model=list[KnowledgeDocumentResponse])
def list_documents(principal: CurrentPrincipal, session: Db):
    return list(
        session.scalars(
            select(KnowledgeDocument)
            .where(KnowledgeDocument.workspace_id == principal.workspace_id)
            .order_by(KnowledgeDocument.created_at.desc())
        )
    )


@router.post(
    "/documents",
    response_model=KnowledgeDocumentResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_document(
    principal: Editor,
    session: Db,
    settings: AppSettings,
    file: UploadFile = File(...),
):
    filename = file.filename or "knowledge.txt"
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail=f"当前仅支持: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="知识文件不能超过 20MB")
    if not data:
        raise HTTPException(status_code=400, detail="上传文件为空")

    stored = build_object_storage(settings).put(
        workspace_id=principal.workspace_id,
        category="knowledge",
        filename=filename,
        stream=io.BytesIO(data),
        content_type=file.content_type,
    )
    document = KnowledgeDocument(
        workspace_id=principal.workspace_id,
        name=filename,
        source_type="upload",
        storage_uri=stored.uri,
        checksum=stored.checksum,
        status="pending",
        metadata_json={
            "mime_type": stored.mime_type,
            "size_bytes": stored.size_bytes,
        },
    )
    session.add(document)
    session.flush()
    enqueue_job(
        session,
        job_type="knowledge.index",
        payload={"document_id": document.id},
        workspace_id=principal.workspace_id,
        idempotency_key=f"knowledge.index:{document.id}:{stored.checksum}",
    )
    record_audit(
        session,
        action="knowledge.upload",
        entity_type="knowledge_document",
        entity_id=document.id,
        workspace_id=principal.workspace_id,
        actor_user_id=principal.user_id,
        metadata={"filename": filename, "size_bytes": stored.size_bytes},
    )
    return document

