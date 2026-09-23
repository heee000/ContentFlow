"""Version-bound review evidence. Local rechecks never call a model."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from .entities import Campaign, ContentItem, ContentReviewEvidence, WorkflowRun
from .models import CampaignBrief
from .review import RuleReviewer

if TYPE_CHECKING:
    from .schemas import ContentResponse


REVIEW_SCHEMA_VERSION = 1
RULESET_VERSION = "deterministic-v1"


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()


def content_fingerprint(item: ContentItem | ContentResponse) -> str:
    return digest(
        {
            key: getattr(item, key)
            for key in (
                "id",
                "version",
                "platform",
                "title",
                "body",
                "hashtags",
                "call_to_action",
                "layout_json",
            )
        }
    )


def review_presentation(
    item: ContentItem | ContentResponse, record: dict[str, Any] | None = None
) -> dict[str, Any]:
    review = copy.deepcopy(item.review_json if record is None else record)
    review = review or {}
    has_model = isinstance(review.get("model_review"), dict) and bool(
        review["model_review"]
    )
    current = (
        has_model
        and type(review.get("model_content_version")) is int
        and review["model_content_version"] == item.version
        and review.get("model_content_sha256") == content_fingerprint(item)
    )
    if current:
        state = "current"
    elif has_model:
        state = "stale" if "model_content_version" in review else "unversioned"
    else:
        state = "stale" if review.get("model_review_status") == "stale" else "missing"
    review["model_review_status"] = state
    review["model_review_current"] = current
    return review


def resolve_brief(session: Session, item: ContentItem) -> CampaignBrief:
    from .workflow_service import campaign_to_brief

    stored = (item.review_json or {}).get("brief_snapshot")
    if not isinstance(stored, dict):
        run = session.scalar(
            select(WorkflowRun).where(
                WorkflowRun.id == item.run_id,
                WorkflowRun.workspace_id == item.workspace_id,
            )
        )
        stored = (
            (run.request_json or {}).get("campaign_brief_snapshot") if run else None
        )
    if not isinstance(stored, dict):
        campaign = session.scalar(
            select(Campaign).where(
                Campaign.id == item.campaign_id,
                Campaign.workspace_id == item.workspace_id,
            )
        )
        if campaign is None:
            raise ValueError("审核关联活动不存在")
        stored = campaign_to_brief(campaign)
    return CampaignBrief.from_dict(stored)


def local_review(
    item: ContentItem,
    brief: CampaignBrief,
    *,
    generated_model: dict[str, Any] | None = None,
) -> dict[str, Any]:
    old = review_presentation(item)
    record = {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "content_version": item.version,
        "content_sha256": content_fingerprint(item),
        "ruleset_version": RULESET_VERSION,
        "brief_snapshot": brief.to_dict(),
        "brief_sha256": digest(brief.to_dict()),
        "rule_review": RuleReviewer()
        .review(item.platform, {"title": item.title, "body": item.body}, brief)
        .to_dict(),
        "requires_human_approval": True,
        "model_review": {},
        "quality_score": None,
        "model_review_status": "missing"
        if old["model_review_status"] == "missing"
        else "stale",
    }
    source = (
        generated_model
        if generated_model is not None
        else (old if old["model_review_current"] else None)
    )
    if source is not None:
        for key in ("model_review", "quality_score", "quality_target"):
            if key in source:
                record[key] = copy.deepcopy(source[key])
        record.update(
            model_content_version=item.version,
            model_content_sha256=content_fingerprint(item),
            model_review_status="current",
        )
    return review_presentation(item, record)


def approval_warnings(review: dict[str, Any]) -> list[str]:
    warnings = []
    if review.get("rule_review", {}).get("passed") is not True:
        warnings.append("当前版本的本地规则未全部通过")
    if not review.get("model_review_current"):
        warnings.append("当前版本没有可用的 AI 审核证据（缺失、旧版或未绑定版本）")
    elif (
        review.get("model_review", {}).get("passed") is not True
        or review["model_review"].get("risk_level") == "high"
    ):
        warnings.append("当前版本 AI 审核未通过或标为高风险")
    return warnings


def capture_review(
    session: Session, item: ContentItem, event: str, actor_user_id: str | None = None
) -> ContentReviewEvidence:
    snapshot = copy.deepcopy(item.review_json or {})
    evidence = ContentReviewEvidence(
        workspace_id=item.workspace_id,
        content_item_id=item.id,
        content_version=item.version,
        content_sha256=content_fingerprint(item),
        event=event,
        actor_user_id=actor_user_id,
        snapshot_json=snapshot,
        snapshot_sha256=digest(snapshot),
        model_binding=review_presentation(item)["model_review_status"],
    )
    session.add(evidence)
    session.flush()
    return evidence
