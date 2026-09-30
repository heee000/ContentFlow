"""Shared admission rules for user-initiated asset mutations.

Queue replay with the same provider idempotency key is a separate recovery
protocol. These checks prevent UI/API entry points from silently replacing a
generation whose outcome still needs review.
"""

from __future__ import annotations

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session

from .entities import (
    Asset,
    ContentItem,
    Job,
    JobManualReview,
    ProviderInvocation,
    ProviderInvocationAttempt,
)


class AssetOperationConflict(ValueError):
    """A new operation would conflict with active or unreviewed asset work."""


def lock_asset_for_mutation(
    session: Session, workspace_id: str, asset_id: str
) -> Asset | None:
    # All user entry points take the parent before the asset, matching content
    # approval. Re-read after waiting: the initially observed state may be stale.
    query = select(Asset).where(
        Asset.workspace_id == workspace_id, Asset.id == asset_id
    )
    observed = session.scalar(query)
    if observed is None:
        return None
    if observed.content_item_id is not None:
        session.scalar(
            select(ContentItem)
            .where(
                ContentItem.workspace_id == workspace_id,
                ContentItem.id == observed.content_item_id,
            )
            .with_for_update()
        )
    return session.scalar(
        query.with_for_update().execution_options(populate_existing=True)
    )


def require_new_asset_operation(
    session: Session, asset: Asset, *, resolving_job_id: str | None = None
) -> None:
    """Caller holds the content/asset mutation locks until commit.

    Unknown generation history follows the content across edits. Active work
    blocks only this asset, so distinct hybrid candidates can run in parallel.
    Read-only search/download failures do not imply a new generation charge.
    """
    if asset.content_item_id is not None:
        content = session.scalar(
            select(ContentItem).where(
                ContentItem.id == asset.content_item_id,
                ContentItem.workspace_id == asset.workspace_id,
            )
        )
        if (
            content is None
            or content.status != "approved"
            or asset.content_version != content.version
        ):
            raise AssetOperationConflict("素材必须属于当前已审核通过的内容版本")
        related_ids = select(Asset.id).where(
            Asset.workspace_id == asset.workspace_id,
            Asset.content_item_id == content.id,
        )
    else:
        related_ids = select(Asset.id).where(
            Asset.workspace_id == asset.workspace_id, Asset.id == asset.id
        )

    related_jobs = select(Job.id).where(
        Job.workspace_id == asset.workspace_id,
        Job.job_type.in_(
            ("asset.generate", "asset.poll", "asset.search", "asset.download")
        ),
        Job.payload_json["asset_id"].as_string().in_(related_ids),
    )
    if resolving_job_id is not None:
        related_jobs = related_jobs.where(Job.id != resolving_job_id)
    open_review = exists().where(
        JobManualReview.workspace_id == asset.workspace_id,
        JobManualReview.job_id == Job.id,
        JobManualReview.resolved_at.is_(None),
    )
    if session.scalar(
        select(Job.id)
        .where(
            Job.id.in_(related_jobs), or_(Job.status == "manual_review", open_review)
        )
        .limit(1)
    ):
        raise AssetOperationConflict(
            "该内容的素材仍有待人工核对任务；请先在任务队列核对供应商结果，不能另开请求"
        )

    if session.scalar(
        select(Job.id)
        .where(
            Job.workspace_id == asset.workspace_id,
            Job.job_type.in_(
                ("asset.generate", "asset.poll", "asset.search", "asset.download")
            ),
            Job.payload_json["asset_id"].as_string() == asset.id,
            Job.status.in_(("queued", "retry", "running")),
        )
        .limit(1)
    ):
        raise AssetOperationConflict("该素材已有排队或执行中的任务，不能重复操作")

    # A completed review covers only attempts already started at that time,
    # never a subsequent retry that also becomes uncertain.
    reviewed = (
        exists()
        .where(
            JobManualReview.workspace_id == asset.workspace_id,
            JobManualReview.job_id == ProviderInvocation.job_id,
            JobManualReview.provider_checked.is_(True),
            JobManualReview.resolved_at >= ProviderInvocationAttempt.started_at,
        )
        .correlate(ProviderInvocation, ProviderInvocationAttempt)
    )
    if session.scalar(
        select(ProviderInvocationAttempt.id)
        .join(
            ProviderInvocation,
            ProviderInvocation.id == ProviderInvocationAttempt.invocation_id,
        )
        .where(
            ProviderInvocation.workspace_id == asset.workspace_id,
            ProviderInvocation.entity_type == "asset",
            ProviderInvocation.entity_id.in_(related_ids),
            ProviderInvocation.operation.in_(("media.generate", "media.poll")),
            ProviderInvocationAttempt.status.in_(
                ("started", "outcome_unknown", "late_succeeded", "late_failed")
            ),
            ~reviewed,
        )
        .limit(1)
    ):
        raise AssetOperationConflict(
            "该内容的素材生成结果尚未核对；请在任务队列发起人工核对后再操作"
        )
