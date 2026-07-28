from __future__ import annotations

import argparse
import logging
import os
import socket
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from io import BytesIO
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import db
from .audit import record_audit
from .connectors import build_connector
from .entities import (
    Asset,
    ChannelConnection,
    ContentItem,
    Job,
    KnowledgeDocument,
    MetricSnapshot,
    PublishJob,
    WorkflowRun,
)
from .embeddings import build_embedding_provider
from .job_queue import (
    claim_next_job,
    complete_job,
    enqueue_job,
    fail_job,
)
from .knowledge_service import index_document
from .media_providers import (
    build_media_provider,
    download_generated_media,
)
from .object_storage import build_object_storage
from .settings import Settings, get_settings
from .workflow_service import execute_workflow_run


logger = logging.getLogger("contentflow.worker")
Handler = Callable[[Session, dict[str, Any], Settings], dict[str, Any]]


class JobNotReady(RuntimeError):
    """The external task is healthy but has not reached a terminal state."""


def handle_knowledge_index(
    session: Session, payload: dict[str, Any], settings: Settings
) -> dict[str, Any]:
    document = session.get(KnowledgeDocument, payload["document_id"])
    if document is None:
        raise ValueError("知识文档不存在")
    document.status = "indexing"
    count = index_document(
        session,
        document,
        embedder=build_embedding_provider(settings),
        storage=build_object_storage(settings),
    )
    record_audit(
        session,
        action="knowledge.index",
        entity_type="knowledge_document",
        entity_id=document.id,
        workspace_id=document.workspace_id,
        actor_user_id=None,
        metadata={"chunk_count": count},
    )
    return {"document_id": document.id, "chunk_count": count}


def handle_workflow_execute(
    session: Session, payload: dict[str, Any], settings: Settings
) -> dict[str, Any]:
    run = session.get(WorkflowRun, payload["run_id"])
    if run is None:
        raise ValueError("工作流运行不存在")
    result = execute_workflow_run(session, run, settings)
    record_audit(
        session,
        action="workflow.complete",
        entity_type="workflow_run",
        entity_id=run.id,
        workspace_id=run.workspace_id,
        actor_user_id=None,
        metadata={"content_count": len(result.get("contents") or [])},
    )
    return result


def handle_connector_test(
    session: Session, payload: dict[str, Any], settings: Settings
) -> dict[str, Any]:
    channel = session.get(ChannelConnection, payload["channel_id"])
    if channel is None:
        raise ValueError("连接器不存在")
    connector = build_connector(
        channel=channel,
        settings=settings,
        storage=build_object_storage(settings),
    )
    result = connector.test()
    channel.status = result.status
    record_audit(
        session,
        action="channel.test",
        entity_type="channel_connection",
        entity_id=channel.id,
        workspace_id=channel.workspace_id,
        actor_user_id=None,
        metadata={"status": channel.status},
    )
    return {
        "channel_id": channel.id,
        "status": channel.status,
        "response": result.response,
    }


def _store_generation(
    *,
    asset: Asset,
    settings: Settings,
    generation,
) -> None:
    data = download_generated_media(generation)
    filename = generation.filename or (
        "asset.png" if asset.kind == "image" else "asset.mp4"
    )
    stored = build_object_storage(settings).put(
        workspace_id=asset.workspace_id,
        category="assets",
        filename=filename,
        stream=BytesIO(data),
        content_type=generation.mime_type,
    )
    asset.status = "ready"
    asset.storage_uri = stored.uri
    asset.mime_type = stored.mime_type
    asset.size_bytes = stored.size_bytes
    asset.error = None
    asset.metadata_json = {
        **(asset.metadata_json or {}),
        **generation.metadata,
        "checksum": stored.checksum,
    }


def handle_asset_generate(
    session: Session, payload: dict[str, Any], settings: Settings
) -> dict[str, Any]:
    asset = session.get(Asset, payload["asset_id"])
    if asset is None:
        raise ValueError("素材任务不存在")
    if asset.status == "ready":
        return {"asset_id": asset.id, "status": asset.status}
    asset.status = "generating"
    provider = build_media_provider(settings, asset.kind)
    generation = provider.generate(
        kind=asset.kind,
        prompt=asset.prompt or "",
        metadata=dict(asset.metadata_json or {}),
    )
    if generation.status == "processing":
        if not generation.external_task_id:
            raise RuntimeError("异步素材任务没有 external_task_id")
        asset.status = "processing"
        asset.external_task_id = generation.external_task_id
        asset.metadata_json = {
            **(asset.metadata_json or {}),
            **generation.metadata,
        }
        enqueue_job(
            session,
            job_type="asset.poll",
            payload={"asset_id": asset.id},
            workspace_id=asset.workspace_id,
            idempotency_key=f"asset.poll:{asset.id}:{generation.external_task_id}",
            run_at=datetime.now(timezone.utc),
            max_attempts=60,
        )
    elif generation.status == "ready":
        _store_generation(asset=asset, settings=settings, generation=generation)
    else:
        raise RuntimeError(f"未知素材生成状态: {generation.status}")
    record_audit(
        session,
        action="asset.generate",
        entity_type="asset",
        entity_id=asset.id,
        workspace_id=asset.workspace_id,
        actor_user_id=None,
        metadata={"status": asset.status, "provider": asset.provider},
    )
    return {"asset_id": asset.id, "status": asset.status}


def handle_asset_poll(
    session: Session, payload: dict[str, Any], settings: Settings
) -> dict[str, Any]:
    asset = session.get(Asset, payload["asset_id"])
    if asset is None:
        raise ValueError("素材任务不存在")
    if asset.status == "ready":
        return {"asset_id": asset.id, "status": asset.status}
    if not asset.external_task_id:
        raise ValueError("素材任务缺少 external_task_id")
    provider = build_media_provider(settings, asset.kind)
    generation = provider.poll(asset.external_task_id)
    if generation.status == "processing":
        raise JobNotReady("素材仍在生成中")
    _store_generation(asset=asset, settings=settings, generation=generation)
    record_audit(
        session,
        action="asset.complete",
        entity_type="asset",
        entity_id=asset.id,
        workspace_id=asset.workspace_id,
        actor_user_id=None,
        metadata={"provider": asset.provider},
    )
    return {"asset_id": asset.id, "status": asset.status}


def handle_publish_dispatch(
    session: Session, payload: dict[str, Any], settings: Settings
) -> dict[str, Any]:
    publish_job = session.get(PublishJob, payload["publish_job_id"])
    if publish_job is None:
        raise ValueError("发布任务不存在")
    if publish_job.status in {
        "cancelled",
        "published",
        "exported",
        "draft_created",
        "submitted",
    }:
        return {
            "publish_job_id": publish_job.id,
            "status": publish_job.status,
            "external_id": publish_job.external_id,
        }
    content = session.get(ContentItem, publish_job.content_item_id)
    channel = session.get(ChannelConnection, publish_job.channel_id)
    if content is None or channel is None:
        raise ValueError("发布任务关联的内容或连接器不存在")
    if content.status != "approved":
        raise ValueError("发布前内容必须保持人工审核通过状态")
    if content.version != int(publish_job.request_json.get("content_version", 0)):
        raise ValueError("内容版本已变化，请重新审核并创建发布任务")
    all_assets = list(
        session.scalars(
            select(Asset).where(Asset.content_item_id == content.id)
        )
    )
    assets = [
        asset
        for asset in all_assets
        if int((asset.metadata_json or {}).get("content_version") or 1)
        == content.version
    ]
    if not assets:
        raise ValueError("当前内容版本没有可发布素材")
    unfinished = [asset.id for asset in assets if asset.status != "ready"]
    if unfinished:
        raise ValueError(f"仍有素材未就绪: {', '.join(unfinished)}")
    connector = build_connector(
        channel=channel,
        settings=settings,
        storage=build_object_storage(settings),
    )
    publish_job.status = "publishing"
    publish_job.attempts += 1
    result = connector.publish(
        publish_job=publish_job,
        content=content,
        assets=assets,
    )
    publish_job.status = result.status
    publish_job.external_id = result.external_id
    publish_job.external_url = result.external_url
    publish_job.response_json = result.response
    publish_job.error = None
    publish_job.published_at = datetime.now(timezone.utc)
    record_audit(
        session,
        action="publish.dispatch",
        entity_type="publish_job",
        entity_id=publish_job.id,
        workspace_id=publish_job.workspace_id,
        actor_user_id=None,
        metadata={"status": publish_job.status, "channel_id": channel.id},
    )
    return {
        "publish_job_id": publish_job.id,
        "status": publish_job.status,
        "external_id": publish_job.external_id,
        "external_url": publish_job.external_url,
    }


def handle_metrics_pull(
    session: Session, payload: dict[str, Any], settings: Settings
) -> dict[str, Any]:
    publish_job = session.get(PublishJob, payload["publish_job_id"])
    if publish_job is None:
        raise ValueError("发布任务不存在")
    channel = session.get(ChannelConnection, publish_job.channel_id)
    if channel is None:
        raise ValueError("发布任务关联的连接器不存在")
    connector = build_connector(
        channel=channel,
        settings=settings,
        storage=build_object_storage(settings),
    )
    values = connector.pull_metrics(publish_job)
    snapshot = MetricSnapshot(
        workspace_id=publish_job.workspace_id,
        publish_job_id=publish_job.id,
        captured_at=datetime.now(timezone.utc),
        impressions=float(values.get("impressions", 0)),
        clicks=float(values.get("clicks", 0)),
        likes=float(values.get("likes", 0)),
        comments=float(values.get("comments", 0)),
        shares=float(values.get("shares", 0)),
        raw_json={"source": channel.platform, **values},
    )
    session.add(snapshot)
    session.flush()
    record_audit(
        session,
        action="metrics.pull",
        entity_type="metric_snapshot",
        entity_id=snapshot.id,
        workspace_id=publish_job.workspace_id,
        actor_user_id=None,
        metadata={"publish_job_id": publish_job.id},
    )
    return {
        "publish_job_id": publish_job.id,
        "metric_snapshot_id": snapshot.id,
        "metrics": values,
    }


HANDLERS: dict[str, Handler] = {
    "knowledge.index": handle_knowledge_index,
    "workflow.execute": handle_workflow_execute,
    "connector.test": handle_connector_test,
    "asset.generate": handle_asset_generate,
    "asset.poll": handle_asset_poll,
    "publish.dispatch": handle_publish_dispatch,
    "metrics.pull": handle_metrics_pull,
}


def mark_domain_failure(session: Session, job: Job, message: str) -> None:
    payload = dict(job.payload_json or {})
    if job.job_type == "workflow.execute" and payload.get("run_id"):
        run = session.get(WorkflowRun, payload["run_id"])
        if run:
            run.status = "failed"
            run.current_stage = "failed"
            run.error = message[:8000]
            run.completed_at = datetime.now(timezone.utc)
    elif job.job_type == "knowledge.index" and payload.get("document_id"):
        document = session.get(KnowledgeDocument, payload["document_id"])
        if document:
            document.status = "failed"
            document.metadata_json = {
                **(document.metadata_json or {}),
                "error": message[:2000],
            }
    elif job.job_type.startswith("asset.") and payload.get("asset_id"):
        asset = session.get(Asset, payload["asset_id"])
        if asset:
            asset.status = "failed"
            asset.error = message[:8000]
    elif job.job_type == "publish.dispatch" and payload.get("publish_job_id"):
        publish_job = session.get(PublishJob, payload["publish_job_id"])
        if publish_job and publish_job.status != "cancelled":
            publish_job.status = "failed"
            publish_job.error = message[:8000]
    elif job.job_type == "connector.test" and payload.get("channel_id"):
        channel = session.get(ChannelConnection, payload["channel_id"])
        if channel:
            channel.status = "invalid"


class Worker:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        worker_id: str | None = None,
        session_factory=None,
        handlers: dict[str, Handler] | None = None,
    ):
        self.settings = settings or get_settings()
        self.worker_id = worker_id or (
            f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        )
        self.session_factory = session_factory or db.SessionLocal
        self.handlers = handlers or HANDLERS

    def run_once(self) -> bool:
        with self.session_factory() as session:
            job = claim_next_job(
                session,
                worker_id=self.worker_id,
                lease_seconds=self.settings.worker_lease_seconds,
            )
            if job is None:
                session.rollback()
                return False
            session.commit()
            job_id = job.id

        with self.session_factory() as session:
            job = session.get(Job, job_id)
            if job is None:
                return False
            try:
                handler = self.handlers.get(job.job_type)
                if handler is None:
                    raise ValueError(f"没有任务处理器: {job.job_type}")
                result = handler(session, dict(job.payload_json), self.settings)
                complete_job(session, job, result)
                session.commit()
                logger.info("job succeeded id=%s type=%s", job.id, job.job_type)
            except Exception as error:
                session.rollback()
                job = session.get(Job, job_id)
                if job is not None:
                    fail_job(session, job, error)
                    if not isinstance(error, JobNotReady) or job.status == "failed":
                        mark_domain_failure(session, job, str(error))
                    session.commit()
                if isinstance(error, JobNotReady):
                    logger.info("job pending id=%s message=%s", job_id, error)
                else:
                    logger.exception("job failed id=%s", job_id)
            return True

    def run_forever(self) -> None:
        logger.info("worker started id=%s", self.worker_id)
        while True:
            worked = self.run_once()
            if not worked:
                time.sleep(self.settings.worker_poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the ContentFlow worker.")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    if not settings.production:
        from .migrate import upgrade_database

        upgrade_database(settings)
        db.configure_database(settings.database_url)
    db.create_schema()
    worker = Worker(settings=settings)
    if args.once:
        worker.run_once()
    else:
        worker.run_forever()


if __name__ == "__main__":
    main()
