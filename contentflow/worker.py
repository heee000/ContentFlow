from __future__ import annotations

import argparse
import hashlib
import logging
import os
import signal
import socket
import threading
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
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
    PromptEvalRun,
    PublishJob,
    WorkflowRun,
    WorkerNode,
)
from .embeddings import build_embedding_provider
from .job_queue import (
    JobLeaseLost,
    claim_next_job,
    complete_job,
    enqueue_job,
    fail_exhausted_leases,
    fail_job,
    renew_job_lease,
)
from .knowledge_service import index_document
from .media_providers import (
    MediaProviderError,
    build_media_provider,
    download_generated_media,
    media_provider_profile_fingerprint,
)
from .object_storage import build_object_storage
from .prompt_eval import execute_prompt_eval_run
from .script_publishing import build_script_package, store_script_package
from .settings import Settings, get_settings
from .workflow_service import execute_workflow_run


logger = logging.getLogger("contentflow.worker")
Handler = Callable[[Session, dict[str, Any], Settings], dict[str, Any]]


def configure_worker_logging() -> None:
    logging.basicConfig(level=logging.INFO, force=True)
    logger.disabled = False
    logger.setLevel(logging.INFO)


class JobNotReady(RuntimeError):
    """The external task is healthy but has not reached a terminal state."""


class PublishReconciliationRequired(RuntimeError):
    """The remote outcome is uncertain and requires reconciliation before retry."""


class LeaseHeartbeat:
    def __init__(
        self,
        *,
        session_factory,
        job_id: str,
        worker_id: str,
        attempt: int,
        lease_seconds: int,
    ):
        self.session_factory = session_factory
        self.job_id = job_id
        self.worker_id = worker_id
        self.attempt = attempt
        self.interval_seconds = max(0.25, min(30.0, lease_seconds / 3))
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"contentflow-lease-{job_id[:8]}",
            daemon=True,
        )

    @property
    def lost(self) -> bool:
        return self._lost.is_set()

    def __enter__(self) -> LeaseHeartbeat:
        self._thread.start()
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self._stop.set()
        self._thread.join(timeout=self.interval_seconds + 2)

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                with self.session_factory() as session:
                    renewed = renew_job_lease(
                        session,
                        job_id=self.job_id,
                        worker_id=self.worker_id,
                        attempt=self.attempt,
                    )
                    if not renewed:
                        session.rollback()
                        self._lost.set()
                        logger.error(
                            "job lease lost id=%s worker=%s attempt=%s",
                            self.job_id,
                            self.worker_id,
                            self.attempt,
                        )
                        return
                    session.commit()
            except Exception:
                self._lost.set()
                logger.exception(
                    "job lease heartbeat failed id=%s worker=%s attempt=%s",
                    self.job_id,
                    self.worker_id,
                    self.attempt,
                )
                return


class WorkerNodeHeartbeat:
    def __init__(
        self,
        *,
        session_factory,
        worker_id: str,
        interval_seconds: float,
        hostname: str | None = None,
        process_id: int | None = None,
    ):
        self.session_factory = session_factory
        self.worker_id = worker_id
        self.interval_seconds = interval_seconds
        self.hostname = hostname or socket.gethostname()
        self.process_id = process_id or os.getpid()
        self.started_at = datetime.now(timezone.utc)
        self._stop = threading.Event()
        self._started = False
        self._closed = False
        self._thread = threading.Thread(
            target=self._run,
            name=f"contentflow-worker-node-{worker_id[-12:]}",
            daemon=True,
        )

    def __enter__(self) -> WorkerNodeHeartbeat:
        self.start()
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.stop()

    def start(self) -> None:
        if self._started:
            return
        self.pulse("online")
        self._started = True
        self._thread.start()

    def stop(self) -> None:
        if not self._started or self._closed:
            return
        self._closed = True
        self._stop.set()
        self._thread.join(timeout=self.interval_seconds + 2)
        self.pulse("stopped")

    def pulse(self, status: str = "online") -> bool:
        now = datetime.now(timezone.utc)
        try:
            with self.session_factory() as session:
                node = session.get(WorkerNode, self.worker_id)
                if node is None:
                    node = WorkerNode(
                        id=self.worker_id,
                        hostname=self.hostname,
                        process_id=self.process_id,
                        status=status,
                        started_at=self.started_at,
                        heartbeat_at=now,
                        metadata_json={
                            "heartbeat_interval_seconds": self.interval_seconds,
                        },
                    )
                    session.add(node)
                else:
                    node.hostname = self.hostname
                    node.process_id = self.process_id
                    node.status = status
                    node.heartbeat_at = now
                if status == "stopped":
                    node.stopped_at = now
                else:
                    node.stopped_at = None
                session.commit()
            return True
        except Exception:
            logger.exception(
                "worker node heartbeat failed id=%s status=%s",
                self.worker_id,
                status,
            )
            return False

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            self.pulse("online")


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
        metadata={
            "content_count": len(result.get("contents") or []),
            "ai_provider": (result.get("ai_provenance") or {}).get("provider"),
            "ai_model": (result.get("ai_provenance") or {}).get("model"),
            "ai_invocations": (result.get("ai_provenance") or {}).get(
                "invocation_count"
            ),
            "token_usage_source": (
                (result.get("ai_provenance") or {}).get("token_usage") or {}
            ).get("source"),
        },
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
    try:
        data = download_generated_media(
            generation,
            max_bytes=settings.max_upload_bytes,
            allowed_hosts=tuple(settings.media_download_allowed_hosts),
            require_https=settings.production,
        )
    except MediaProviderError:
        raise
    except ValueError:
        raise MediaProviderError(
            "素材下载结果违反安全或大小边界",
            retryable=False,
        ) from None
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


def media_generation_idempotency_key(asset: Asset) -> str:
    """Return a stable opaque key for one asset content version."""

    try:
        content_version = int((asset.metadata_json or {}).get("content_version", 1))
    except (TypeError, ValueError) as error:
        raise MediaProviderError(
            "素材内容版本格式无效",
            retryable=False,
        ) from error
    if content_version < 1:
        raise MediaProviderError(
            "素材内容版本格式无效",
            retryable=False,
        )
    digest = hashlib.sha256(
        (
            f"contentflow-media-v1:{asset.workspace_id}:{asset.id}:"
            f"{asset.kind}:{content_version}"
        ).encode("utf-8")
    ).hexdigest()
    return f"cfm-{digest}"


def handle_asset_generate(
    session: Session, payload: dict[str, Any], settings: Settings
) -> dict[str, Any]:
    asset = session.get(Asset, payload["asset_id"])
    if asset is None:
        raise ValueError("素材任务不存在")
    if asset.status == "ready":
        return {"asset_id": asset.id, "status": asset.status}
    configured_provider = (
        settings.image_provider if asset.kind == "image" else settings.video_provider
    )
    if asset.provider in {"manual", "manual-upload"} or configured_provider == "manual":
        asset.provider = "manual"
        asset.status = "awaiting_upload"
        asset.error = None
        asset.metadata_json = {
            **(asset.metadata_json or {}),
            "manual_upload_required": True,
        }
        record_audit(
            session,
            action="asset.awaiting_upload",
            entity_type="asset",
            entity_id=asset.id,
            workspace_id=asset.workspace_id,
            actor_user_id=None,
        )
        return {"asset_id": asset.id, "status": asset.status}
    asset.status = "generating"
    provider = build_media_provider(settings, asset.kind)
    provider_profile = media_provider_profile_fingerprint(settings, asset.kind)
    generation = provider.generate(
        kind=asset.kind,
        prompt=asset.prompt or "",
        metadata=dict(asset.metadata_json or {}),
        idempotency_key=media_generation_idempotency_key(asset),
    )
    generation.metadata = {
        **generation.metadata,
        "media_provider_profile_fingerprint": provider_profile,
    }
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
    expected_profile = (asset.metadata_json or {}).get(
        "media_provider_profile_fingerprint"
    )
    current_profile = media_provider_profile_fingerprint(settings, asset.kind)
    if not isinstance(expected_profile, str) or expected_profile != current_profile:
        raise MediaProviderError(
            "异步素材任务的 Provider 配置已变化或缺少目标指纹，请人工核对",
            retryable=False,
        )
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


def publish_reconciliation_job_key(publish_job_id: str) -> str:
    return f"publish.reconcile:{publish_job_id}"


def ensure_publish_reconciliation_job(
    session: Session,
    *,
    publish_job: PublishJob,
    settings: Settings,
    reason: str,
) -> tuple[Job, bool]:
    key = publish_reconciliation_job_key(publish_job.id)
    run_at = datetime.now(timezone.utc) + timedelta(
        seconds=settings.publish_reconciliation_initial_delay_seconds
    )
    payload = {
        "publish_job_id": publish_job.id,
        "lookup_external_id": publish_job.external_id,
    }
    existing = session.scalar(select(Job).where(Job.idempotency_key == key))
    if existing is not None:
        if publish_job.status == "submitted" and existing.status in {
            "succeeded",
            "failed",
        }:
            previous_status = existing.status
            previous_attempts = existing.attempts
            existing.status = "queued"
            existing.payload_json = payload
            existing.result_json = {}
            existing.attempts = 0
            existing.max_attempts = settings.publish_reconciliation_max_attempts
            existing.run_at = run_at
            existing.locked_by = None
            existing.locked_at = None
            existing.last_error = None
            record_audit(
                session,
                action="publish.reconciliation_requeued",
                entity_type="publish_job",
                entity_id=publish_job.id,
                workspace_id=publish_job.workspace_id,
                actor_user_id=None,
                metadata={
                    "queue_job_id": existing.id,
                    "reason": reason,
                    "previous_status": previous_status,
                    "previous_attempts": previous_attempts,
                },
            )
            return existing, True
        return existing, False

    job = enqueue_job(
        session,
        job_type="publish.reconcile",
        payload=payload,
        workspace_id=publish_job.workspace_id,
        idempotency_key=key,
        run_at=run_at,
        max_attempts=settings.publish_reconciliation_max_attempts,
    )
    record_audit(
        session,
        action="publish.reconciliation_queued",
        entity_type="publish_job",
        entity_id=publish_job.id,
        workspace_id=publish_job.workspace_id,
        actor_user_id=None,
        metadata={"queue_job_id": job.id, "reason": reason},
    )
    return job, True


def schedule_pending_publish_reconciliations(
    session: Session,
    *,
    settings: Settings,
    limit: int = 100,
) -> int:
    query = (
        select(PublishJob)
        .join(
            ChannelConnection,
            ChannelConnection.id == PublishJob.channel_id,
        )
        .where(
            PublishJob.status == "submitted",
            PublishJob.external_id.is_not(None),
            ChannelConnection.platform == "wechat",
        )
        .order_by(PublishJob.updated_at.asc())
        .limit(limit)
    )
    if session.bind and session.bind.dialect.name == "postgresql":
        query = query.with_for_update(of=PublishJob, skip_locked=True)
    publish_jobs = list(session.scalars(query))
    scheduled = 0
    for publish_job in publish_jobs:
        _, created = ensure_publish_reconciliation_job(
            session,
            publish_job=publish_job,
            settings=settings,
            reason="worker_sweep",
        )
        scheduled += int(created)
    return scheduled


def handle_publish_dispatch(
    session: Session, payload: dict[str, Any], settings: Settings
) -> dict[str, Any]:
    publish_query = select(PublishJob).where(PublishJob.id == payload["publish_job_id"])
    if session.bind and session.bind.dialect.name == "postgresql":
        publish_query = publish_query.with_for_update()
    publish_job = session.scalar(publish_query)
    if publish_job is None:
        raise ValueError("发布任务不存在")
    if publish_job.status in {
        "cancelled",
        "published",
        "exported",
        "script_ready",
        "script_published",
        "script_confirmation_pending",
        "draft_created",
        "submitted",
    }:
        return {
            "publish_job_id": publish_job.id,
            "status": publish_job.status,
            "external_id": publish_job.external_id,
        }
    if publish_job.status in {"publishing", "reconciliation_required"}:
        publish_job.status = "reconciliation_required"
        publish_job.error = (
            "上一次分发已开始但未保存确定结果，禁止自动重试；"
            "请先到平台核对并完成人工对账。"
        )
        session.commit()
        raise PublishReconciliationRequired(publish_job.error)

    content = session.get(ContentItem, publish_job.content_item_id)
    channel = session.get(ChannelConnection, publish_job.channel_id)
    if content is None or channel is None:
        raise ValueError("发布任务关联的内容或连接器不存在")
    if content.status != "approved":
        raise ValueError("发布前内容必须保持人工审核通过状态")
    if content.version != int(publish_job.request_json.get("content_version", 0)):
        raise ValueError("内容版本已变化，请重新审核并创建发布任务")
    all_assets = list(
        session.scalars(select(Asset).where(Asset.content_item_id == content.id))
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
    delivery_mode = publish_job.delivery_mode
    # Jobs queued before delivery modes were introduced used the connector default
    # for Xiaohongshu's export-only channel. Preserve that already-supported path.
    if (
        delivery_mode == "connector"
        and channel.platform == "xiaohongshu"
        and channel.status == "export_only"
    ):
        delivery_mode = "manual_export"
        request_json = dict(publish_job.request_json or {})
        request_json["delivery_mode"] = delivery_mode
        publish_job.request_json = request_json
    if delivery_mode not in {"connector", "script", "manual_export"}:
        raise ValueError(f"未知发布方式: {delivery_mode}")
    if delivery_mode == "connector" and channel.status != "connected":
        raise ValueError("官方 API 发布要求已通过连接测试的 connected 渠道")
    if delivery_mode == "manual_export" and channel.platform != "xiaohongshu":
        raise ValueError("人工导出目前只适用于小红书")

    storage = build_object_storage(settings)
    if delivery_mode == "script":
        requested_by = (publish_job.request_json or {}).get("script_requested_by")
        if not isinstance(requested_by, str) or not requested_by:
            raise ValueError("脚本发布尝试缺少可审计的发起人")
        script_attempt_id = str(uuid.uuid4())
        expires_at = datetime.now(timezone.utc) + timedelta(
            minutes=settings.script_confirmation_ttl_minutes
        )
        confirmation_required = (
            2
            if (channel.config_json or {}).get("script_confirmation_required") == 2
            else 1
        )
        package = build_script_package(
            publish_job=publish_job,
            content=content,
            channel=channel,
            assets=assets,
            script_attempt_id=script_attempt_id,
            expires_at=expires_at,
            storage=storage,
            max_total_bytes=settings.max_upload_bytes,
        )
        stored = store_script_package(
            package=package,
            publish_job=publish_job,
            storage=storage,
        )
        publish_job.status = "script_ready"
        publish_job.attempts += 1
        publish_job.external_id = None
        publish_job.external_url = stored.uri
        publish_job.response_json = {
            "mode": "script",
            "script_attempt_id": script_attempt_id,
            "package_uri": stored.uri,
            "package_sha256": stored.checksum,
            "size_bytes": stored.size_bytes,
            "platform": channel.platform,
            "final_submission_requires_human": True,
            "script_requested_by_user_id": requested_by,
            "script_confirmation_expires_at": expires_at.isoformat(),
            "script_confirmation_required": confirmation_required,
            "script_confirmation_count": 0,
            "script_confirmation_decision": None,
            "script_evidence_count": 0,
            "script_evidence_frozen": False,
        }
        publish_job.error = None
        publish_job.published_at = None
        record_audit(
            session,
            action="publish.script_package_ready",
            entity_type="publish_job",
            entity_id=publish_job.id,
            workspace_id=publish_job.workspace_id,
            actor_user_id=None,
            metadata={
                "channel_id": channel.id,
                "package_sha256": stored.checksum,
                "script_attempt_id": script_attempt_id,
                "script_requested_by_user_id": requested_by,
                "script_confirmation_expires_at": expires_at.isoformat(),
                "script_confirmation_required": confirmation_required,
                "size_bytes": stored.size_bytes,
            },
        )
        try:
            session.commit()
        except Exception:
            session.rollback()
            try:
                storage.delete(stored.uri)
            except Exception:
                logger.exception("failed to compensate uncommitted script package")
            raise
        return {
            "publish_job_id": publish_job.id,
            "status": publish_job.status,
            "external_id": publish_job.external_id,
            "external_url": publish_job.external_url,
        }

    connector = build_connector(channel=channel, settings=settings, storage=storage)

    request_json = dict(publish_job.request_json or {})
    request_json["dispatch_token"] = (
        request_json.get("dispatch_token") or uuid.uuid4().hex
    )
    request_json["dispatch_started_at"] = datetime.now(timezone.utc).isoformat()
    request_json["dispatch_attempt"] = (
        int(request_json.get("dispatch_attempt") or 0) + 1
    )
    publish_job.request_json = request_json
    publish_job.status = "publishing"
    publish_job.attempts += 1
    publish_job.error = None
    record_audit(
        session,
        action="publish.dispatch_started",
        entity_type="publish_job",
        entity_id=publish_job.id,
        workspace_id=publish_job.workspace_id,
        actor_user_id=None,
        metadata={
            "channel_id": channel.id,
            "dispatch_attempt": request_json["dispatch_attempt"],
        },
    )
    session.commit()

    try:
        result = connector.publish(
            publish_job=publish_job,
            content=content,
            assets=assets,
        )
    except Exception as error:
        publish_job = session.get(PublishJob, publish_job.id)
        if publish_job is not None:
            publish_job.status = "reconciliation_required"
            publish_job.error = (
                "平台调用已开始但结果不确定，禁止自动重试："
                f"{type(error).__name__}: {error}"
            )[:8000]
            record_audit(
                session,
                action="publish.reconciliation_required",
                entity_type="publish_job",
                entity_id=publish_job.id,
                workspace_id=publish_job.workspace_id,
                actor_user_id=None,
                metadata={
                    "channel_id": channel.id,
                    "error_type": type(error).__name__,
                },
            )
            session.commit()
        raise PublishReconciliationRequired(
            "平台分发结果不确定，需要人工对账后再决定是否重试"
        ) from error

    publish_job.status = result.status
    publish_job.external_id = result.external_id
    publish_job.external_url = result.external_url
    publish_job.response_json = result.response
    publish_job.error = None
    reconciliation_job: Job | None = None
    if result.status == "submitted":
        if not result.external_id or not connector.reconciliation_supported:
            publish_job.status = "reconciliation_required"
            publish_job.error = (
                "平台已接受发布请求，但没有可用的确定性状态查询键；请完成人工对账。"
            )
            record_audit(
                session,
                action="publish.reconciliation_required",
                entity_type="publish_job",
                entity_id=publish_job.id,
                workspace_id=publish_job.workspace_id,
                actor_user_id=None,
                metadata={"reason": "automatic_reconciliation_unavailable"},
            )
            session.commit()
            raise PublishReconciliationRequired(publish_job.error)

        reconciliation_job, _ = ensure_publish_reconciliation_job(
            session,
            publish_job=publish_job,
            settings=settings,
            reason="platform_submission_accepted",
        )

    publish_job.published_at = (
        datetime.now(timezone.utc) if publish_job.status == "published" else None
    )
    record_audit(
        session,
        action="publish.dispatch",
        entity_type="publish_job",
        entity_id=publish_job.id,
        workspace_id=publish_job.workspace_id,
        actor_user_id=None,
        metadata={
            "status": publish_job.status,
            "channel_id": channel.id,
            "reconciliation_job_id": (
                reconciliation_job.id if reconciliation_job is not None else None
            ),
        },
    )
    # Persist the remote result and follow-up job before the queue attempt is
    # finalized. If the worker dies after this commit, replay sees a terminal
    # domain state instead of invoking the external publish endpoint again.
    session.commit()
    return {
        "publish_job_id": publish_job.id,
        "status": publish_job.status,
        "external_id": publish_job.external_id,
        "external_url": publish_job.external_url,
        "reconciliation_job_id": (
            reconciliation_job.id if reconciliation_job is not None else None
        ),
    }


def handle_publish_reconcile(
    session: Session, payload: dict[str, Any], settings: Settings
) -> dict[str, Any]:
    publish_query = select(PublishJob).where(PublishJob.id == payload["publish_job_id"])
    if session.bind and session.bind.dialect.name == "postgresql":
        publish_query = publish_query.with_for_update()
    publish_job = session.scalar(publish_query)
    if publish_job is None:
        raise ValueError("发布任务不存在")
    if publish_job.status in {
        "cancelled",
        "published",
        "exported",
        "script_ready",
        "script_published",
        "script_confirmation_pending",
        "draft_created",
        "failed",
    }:
        return {
            "publish_job_id": publish_job.id,
            "status": publish_job.status,
            "external_id": publish_job.external_id,
        }
    if publish_job.status not in {"submitted", "reconciliation_required"}:
        raise ValueError(f"当前发布状态不能自动对账: {publish_job.status}")
    if not publish_job.external_id:
        raise PublishReconciliationRequired("缺少平台状态查询键，必须人工核对发布结果")

    channel = session.get(ChannelConnection, publish_job.channel_id)
    if channel is None:
        raise ValueError("发布任务关联的连接器不存在")
    connector = build_connector(
        channel=channel,
        settings=settings,
        storage=build_object_storage(settings),
    )
    if not connector.reconciliation_supported:
        raise PublishReconciliationRequired(
            f"{channel.platform} 不支持确定性的自动发布对账"
        )

    publish_job_id = publish_job.id
    channel_id = channel.id
    lookup_external_id = publish_job.external_id
    # Never keep a row lock open while waiting for a remote platform. The
    # second phase re-locks and validates the domain state before applying the
    # response, so a reviewer can safely win the race with a slow query.
    session.commit()
    result = connector.reconcile(publish_job)
    checked_at = datetime.now(timezone.utc)

    publish_query = (
        select(PublishJob)
        .where(PublishJob.id == publish_job_id)
        .execution_options(populate_existing=True)
    )
    if session.bind and session.bind.dialect.name == "postgresql":
        publish_query = publish_query.with_for_update()
    current_publish_job = session.scalar(publish_query)
    if current_publish_job is None:
        raise ValueError("Publish job not found during reconciliation")
    terminal_states = {
        "cancelled",
        "published",
        "exported",
        "script_ready",
        "script_published",
        "script_confirmation_pending",
        "draft_created",
        "failed",
    }
    state_changed = (
        current_publish_job.status not in {"submitted", "reconciliation_required"}
        or current_publish_job.external_id != lookup_external_id
    )
    if current_publish_job.status in terminal_states or state_changed:
        record_audit(
            session,
            action="publish.reconciliation_stale_ignored",
            entity_type="publish_job",
            entity_id=current_publish_job.id,
            workspace_id=current_publish_job.workspace_id,
            actor_user_id=None,
            metadata={
                "current_status": current_publish_job.status,
                "remote_status": result.status,
                "lookup_external_id": lookup_external_id,
                "current_external_id": current_publish_job.external_id,
                "channel_id": channel_id,
            },
        )
        session.commit()
        return {
            "publish_job_id": current_publish_job.id,
            "status": current_publish_job.status,
            "external_id": current_publish_job.external_id,
            "external_url": current_publish_job.external_url,
            "ignored_remote_status": result.status,
        }

    publish_job = current_publish_job
    response_json = dict(publish_job.response_json or {})
    response_json["automatic_reconciliation"] = {
        "state": result.status,
        "lookup_external_id": lookup_external_id,
        "checked_at": checked_at.isoformat(),
        "response": result.response,
    }
    publish_job.response_json = response_json
    if result.status == "pending":
        record_audit(
            session,
            action="publish.reconciliation_checked",
            entity_type="publish_job",
            entity_id=publish_job.id,
            workspace_id=publish_job.workspace_id,
            actor_user_id=None,
            metadata={"state": "pending", "channel_id": channel.id},
        )
        session.commit()
        raise JobNotReady("平台仍在处理发布请求")
    if result.status != "published":
        raise RuntimeError(f"连接器返回了未知的对账状态: {result.status}")

    publish_job.status = "published"
    publish_job.external_id = result.external_id or lookup_external_id
    publish_job.external_url = result.external_url or publish_job.external_url
    publish_job.error = None
    publish_job.published_at = checked_at

    dispatch_query = select(Job).where(
        Job.idempotency_key == f"publish.dispatch:{publish_job.id}"
    )
    if session.bind and session.bind.dialect.name == "postgresql":
        dispatch_query = dispatch_query.with_for_update()
    dispatch_job = session.scalar(dispatch_query)
    if dispatch_job is not None:
        dispatch_job.status = "succeeded"
        dispatch_job.result_json = {
            "publish_job_id": publish_job.id,
            "status": "published",
            "reconciled": "automatic",
        }
        dispatch_job.last_error = None
        dispatch_job.locked_by = None
        dispatch_job.locked_at = None

    record_audit(
        session,
        action="publish.reconcile_auto",
        entity_type="publish_job",
        entity_id=publish_job.id,
        workspace_id=publish_job.workspace_id,
        actor_user_id=None,
        metadata={
            "channel_id": channel.id,
            "lookup_external_id": lookup_external_id,
            "external_id": publish_job.external_id,
        },
    )
    # Keep the remote terminal result durable even if queue completion crashes.
    session.commit()
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


def handle_prompt_eval_execute(
    session: Session,
    payload: dict[str, Any],
    settings: Settings,
) -> dict[str, Any]:
    run = session.get(PromptEvalRun, payload["run_id"])
    if run is None:
        raise ValueError("Prompt Eval 运行不存在")
    if run.status in {"passed", "failed"}:
        return dict(run.result_json or {})
    return execute_prompt_eval_run(session, run, settings)


HANDLERS: dict[str, Handler] = {
    "knowledge.index": handle_knowledge_index,
    "prompt_eval.execute": handle_prompt_eval_execute,
    "workflow.execute": handle_workflow_execute,
    "connector.test": handle_connector_test,
    "asset.generate": handle_asset_generate,
    "asset.poll": handle_asset_poll,
    "publish.dispatch": handle_publish_dispatch,
    "publish.reconcile": handle_publish_reconcile,
    "metrics.pull": handle_metrics_pull,
}


def mark_domain_failure(
    session: Session,
    job: Job,
    message: str,
    *,
    publish_outcome_uncertain: bool = False,
    ai_provenance: dict[str, Any] | None = None,
) -> None:
    payload = dict(job.payload_json or {})
    if job.job_type == "prompt_eval.execute" and payload.get("run_id"):
        run = session.get(PromptEvalRun, payload["run_id"])
        if run:
            run.status = "error"
            run.error = message[:2000]
            run.completed_at = datetime.now(timezone.utc)
            run.result_json = {
                "schema_version": 1,
                **({"ai_provenance": ai_provenance} if ai_provenance else {}),
            }
            record_audit(
                session,
                action="prompt_eval.error",
                entity_type="prompt_eval_run",
                entity_id=run.id,
                workspace_id=run.workspace_id,
                actor_user_id=None,
                metadata={
                    "prompt_release_id": run.prompt_release_id,
                    "suite_id": run.suite_id,
                    "error": message[:200],
                },
            )
    elif job.job_type == "workflow.execute" and payload.get("run_id"):
        run = session.get(WorkflowRun, payload["run_id"])
        if run:
            run.status = "failed"
            run.current_stage = "failed"
            run.error = message[:8000]
            run.completed_at = datetime.now(timezone.utc)
            if ai_provenance:
                run.result_json = {
                    **(run.result_json or {}),
                    "ai_provenance": ai_provenance,
                }
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
        publish_query = select(PublishJob).where(
            PublishJob.id == payload["publish_job_id"]
        )
        if session.bind and session.bind.dialect.name == "postgresql":
            publish_query = publish_query.with_for_update()
        publish_job = session.scalar(publish_query)
        if (
            publish_job
            and publish_outcome_uncertain
            and publish_job.status == "publishing"
        ):
            publish_job.status = "reconciliation_required"
            publish_job.error = (
                f"Worker 在发布结果落库前失联，禁止自动重试；请先人工对账。{message}"
            )[:8000]
            record_audit(
                session,
                action="publish.reconciliation_required",
                entity_type="publish_job",
                entity_id=publish_job.id,
                workspace_id=publish_job.workspace_id,
                actor_user_id=None,
                metadata={"reason": "worker_lease_exhausted"},
            )
        elif publish_job and publish_job.status in {
            "scheduled",
            "queued",
            "publishing",
        }:
            publish_job.status = "failed"
            publish_job.error = message[:8000]
    elif job.job_type == "publish.reconcile" and payload.get("publish_job_id"):
        publish_query = select(PublishJob).where(
            PublishJob.id == payload["publish_job_id"]
        )
        if session.bind and session.bind.dialect.name == "postgresql":
            publish_query = publish_query.with_for_update()
        publish_job = session.scalar(publish_query)
        if publish_job and publish_job.status in {
            "submitted",
            "reconciliation_required",
        }:
            publish_job.status = "reconciliation_required"
            publish_job.error = (
                f"自动发布对账未能获得确定结果，已转人工处理：{message}"
            )[:8000]
            record_audit(
                session,
                action="publish.reconciliation_required",
                entity_type="publish_job",
                entity_id=publish_job.id,
                workspace_id=publish_job.workspace_id,
                actor_user_id=None,
                metadata={"reason": "automatic_reconciliation_exhausted"},
            )
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
        stop_event: threading.Event | None = None,
    ):
        self.settings = settings or get_settings()
        self.worker_id = worker_id or (
            f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        )
        self.session_factory = session_factory or db.SessionLocal
        self.handlers = handlers or HANDLERS
        self._stop_event = stop_event or threading.Event()
        self._shutdown_signal: int | None = None

    @property
    def stop_requested(self) -> bool:
        return self._stop_event.is_set()

    def request_stop(self, signum: int | None = None) -> None:
        self._shutdown_signal = signum
        self._stop_event.set()

    def run_once(self) -> bool:
        if self.stop_requested:
            return False

        expired_job_refs: list[tuple[str, str]] = []
        with self.session_factory() as session:
            scheduled_reconciliations = schedule_pending_publish_reconciliations(
                session,
                settings=self.settings,
            )
            if scheduled_reconciliations:
                session.commit()
                logger.info(
                    "publish reconciliation jobs queued count=%s",
                    scheduled_reconciliations,
                )
            expired_jobs = fail_exhausted_leases(
                session,
                lease_seconds=self.settings.worker_lease_seconds,
            )
            if expired_jobs:
                expired_job_refs = [
                    (expired_job.id, expired_job.job_type)
                    for expired_job in expired_jobs
                ]
                session.commit()
            else:
                job = claim_next_job(
                    session,
                    worker_id=self.worker_id,
                    lease_seconds=self.settings.worker_lease_seconds,
                )
                if job is None:
                    session.rollback()
                    return False
                if self.stop_requested:
                    session.rollback()
                    return False
                session.commit()
                job_id = job.id
                attempt = job.attempts

        if expired_job_refs:
            with self.session_factory() as session:
                for expired_job_id, _job_type in expired_job_refs:
                    expired_job = session.get(Job, expired_job_id)
                    if expired_job is not None:
                        mark_domain_failure(
                            session,
                            expired_job,
                            expired_job.last_error or "",
                            publish_outcome_uncertain=True,
                        )
                session.commit()
            for expired_job_id, job_type in expired_job_refs:
                logger.error(
                    "job lease exhausted id=%s type=%s",
                    expired_job_id,
                    job_type,
                )
            return True

        with self.session_factory() as session:
            job = session.get(Job, job_id)
            if job is None:
                return False
            try:
                handler = self.handlers.get(job.job_type)
                if handler is None:
                    raise ValueError(f"没有任务处理器: {job.job_type}")
                with LeaseHeartbeat(
                    session_factory=self.session_factory,
                    job_id=job_id,
                    worker_id=self.worker_id,
                    attempt=attempt,
                    lease_seconds=self.settings.worker_lease_seconds,
                ) as heartbeat:
                    result = handler(
                        session,
                        dict(job.payload_json),
                        self.settings,
                    )
                if heartbeat.lost:
                    raise JobLeaseLost(
                        f"Job lease heartbeat was lost: id={job_id} "
                        f"worker={self.worker_id} attempt={attempt}"
                    )
                complete_job(
                    session,
                    job,
                    result,
                    worker_id=self.worker_id,
                    attempt=attempt,
                )
                session.commit()
                logger.info("job succeeded id=%s type=%s", job.id, job.job_type)
            except Exception as error:
                session.rollback()
                if isinstance(error, JobLeaseLost):
                    logger.error("stale worker stopped id=%s error=%s", job_id, error)
                    return True
                job = session.get(Job, job_id)
                ai_provenance = getattr(error, "ai_provenance", None)
                persisted_error: Exception | str = error
                if job is not None and job.job_type == "prompt_eval.execute":
                    persisted_error = (
                        f"AI prompt evaluation failed ({type(error).__name__})"
                    )
                elif (
                    job is not None
                    and job.job_type == "workflow.execute"
                    and ai_provenance
                ):
                    persisted_error = f"AI workflow failed ({type(error).__name__})"
                if job is not None:
                    mark_publish_first = (
                        job.job_type == "publish.dispatch"
                        and not isinstance(error, JobNotReady)
                    )
                    if mark_publish_first:
                        mark_domain_failure(session, job, str(error))
                    try:
                        job = fail_job(
                            session,
                            job,
                            persisted_error,
                            worker_id=self.worker_id,
                            attempt=attempt,
                            force_terminal=isinstance(
                                error,
                                PublishReconciliationRequired,
                            )
                            or (
                                isinstance(error, MediaProviderError)
                                and not error.retryable
                            ),
                            retry_after_seconds=getattr(
                                error,
                                "retry_after_seconds",
                                None,
                            ),
                        )
                    except JobLeaseLost as lease_error:
                        session.rollback()
                        logger.error(
                            "stale worker failure ignored id=%s error=%s",
                            job_id,
                            lease_error,
                        )
                        return True
                    if not mark_publish_first and (
                        not isinstance(error, JobNotReady) or job.status == "failed"
                    ):
                        mark_domain_failure(
                            session,
                            job,
                            str(persisted_error),
                            ai_provenance=ai_provenance,
                        )
                    session.commit()
                if isinstance(error, JobNotReady):
                    logger.info("job pending id=%s message=%s", job_id, error)
                elif ai_provenance:
                    logger.error(
                        "AI job failed id=%s error_type=%s",
                        job_id,
                        type(error).__name__,
                    )
                else:
                    logger.exception("job failed id=%s", job_id)
            return True

    def run_forever(self) -> None:
        logger.info("worker started id=%s", self.worker_id)
        node_heartbeat = WorkerNodeHeartbeat(
            session_factory=self.session_factory,
            worker_id=self.worker_id,
            interval_seconds=self.settings.worker_heartbeat_seconds,
        )
        try:
            with node_heartbeat:
                while not self.stop_requested:
                    worked = self.run_once()
                    if not worked:
                        self._stop_event.wait(self.settings.worker_poll_seconds)
        finally:
            logger.info(
                "worker stopped id=%s signal=%s",
                self.worker_id,
                self._shutdown_signal,
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the ContentFlow worker.")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    if not settings.production:
        from .migrate import upgrade_database

        upgrade_database(settings)
        db.configure_database(settings.database_url)
    db.create_schema()
    # Alembic configures logging while migrations run. Re-apply the worker
    # logger afterwards so startup and job failures remain visible.
    configure_worker_logging()
    worker = Worker(settings=settings)

    def stop_worker(signum, _frame) -> None:
        worker.request_stop(signum)

    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, stop_worker)
    if args.once:
        worker.run_once()
    else:
        worker.run_forever()


if __name__ == "__main__":
    main()
