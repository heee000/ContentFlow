from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .ai_provenance import AIProvenanceRecorder
from .entities import (
    Asset,
    Campaign,
    ContentItem,
    ContentRevision,
    PromptRelease,
    WorkflowRun,
)
from .embeddings import build_embedding_provider
from .knowledge_service import search_workspace_knowledge
from .models import CampaignBrief
from .prompt_eval import require_current_passed_eval
from .prompt_governance import resolve_active_prompt_set
from .review import RuleReviewer
from .settings import Settings
from .text_generation import build_text_provider
from .workflow import build_asset_tasks


def campaign_to_brief(campaign: Campaign) -> dict:
    stored = dict(campaign.brief or {})
    return {
        "campaign_name": campaign.name,
        "product_name": campaign.product_name,
        "goal": campaign.objective,
        "audience": campaign.audience,
        "platforms": list(campaign.platforms),
        "tone": campaign.tone,
        "city": stored.get("city") or "北京",
        "must_include": list(stored.get("must_include") or []),
        "product_facts": list(stored.get("product_facts") or []),
        "forbidden_phrases": list(stored.get("forbidden_phrases") or []),
        "call_to_action": stored.get("call_to_action")
        or f"打开{campaign.product_name}了解更多",
    }


def execute_workflow_run(
    session: Session,
    run: WorkflowRun,
    settings: Settings,
) -> dict:
    campaign = session.get(Campaign, run.campaign_id)
    if campaign is None or campaign.workspace_id != run.workspace_id:
        raise ValueError("工作流关联的活动不存在")

    run.status = "running"
    run.current_stage = "knowledge_retrieval"
    run.started_at = datetime.now(timezone.utc)
    session.flush()

    brief_raw = campaign_to_brief(campaign)
    brief = CampaignBrief.from_dict(brief_raw)
    query = " ".join(
        [
            brief.product_name,
            brief.goal,
            brief.audience,
            brief.city,
            *brief.must_include,
            *brief.product_facts,
        ]
    )
    embedder = build_embedding_provider(settings)
    retrieved = search_workspace_knowledge(
        session,
        workspace_id=run.workspace_id,
        query=query,
        limit=6,
        embedder=embedder,
    )
    knowledge_payload = [chunk.to_dict() for chunk in retrieved]

    provider_override = run.request_json.get("provider")
    provider = build_text_provider(settings, provider_override)
    prompt_set = resolve_active_prompt_set(session, run.workspace_id)
    if prompt_set.release_id:
        release = session.get(PromptRelease, prompt_set.release_id)
        if release is None or release.workspace_id != run.workspace_id:
            raise ValueError("工作流关联的 Prompt 版本不存在")
        require_current_passed_eval(
            session,
            release,
            settings,
            provider_override,
        )
    provenance = AIProvenanceRecorder(
        provider,
        embedding_provider=settings.embedding_provider,
        embedding_model=embedder.model_name,
        prompt_set=prompt_set,
    )
    run.provider = provenance.provider_name
    run.current_stage = "planning"
    session.flush()
    plan = provenance.complete_json(
        "plan",
        {"brief": brief.to_dict(), "knowledge": knowledge_payload},
    )

    run.current_stage = "content_generation"
    session.flush()
    requested_platforms = set(
        run.request_json.get("regenerate_platforms") or brief.platforms
    )
    reviewer = RuleReviewer()
    result_items = []
    for platform in brief.platforms:
        if platform not in requested_platforms:
            continue
        draft = provenance.complete_json(
            "generate",
            {
                "brief": brief.to_dict(),
                "platform": platform,
                "plan": plan,
                "knowledge": knowledge_payload,
            },
            platform=platform,
        )
        rule_review = reviewer.review(platform, draft, brief)
        if not rule_review.passed:
            draft = reviewer.repair(platform, draft, brief)
            rule_review = reviewer.review(platform, draft, brief)
        model_review = provenance.complete_json(
            "review",
            {
                "brief": brief.to_dict(),
                "platform": platform,
                "content": draft,
                "knowledge": knowledge_payload,
            },
            platform=platform,
        )
        model_review_passed = bool(model_review.get("passed", False))

        item = ContentItem(
            workspace_id=run.workspace_id,
            campaign_id=campaign.id,
            run_id=run.id,
            platform=platform,
            title=str(draft.get("title") or ""),
            body=str(draft.get("body") or ""),
            hashtags=[str(value) for value in draft.get("hashtags", [])],
            call_to_action=brief.call_to_action,
            layout_json=(
                draft.get("layout") if isinstance(draft.get("layout"), dict) else {}
            ),
            status=(
                "needs_review"
                if rule_review.passed and model_review_passed
                else "blocked"
            ),
            source_chunk_ids=[chunk.chunk_id for chunk in retrieved],
            review_json={
                "rule_review": rule_review.to_dict(),
                "model_review": model_review,
                "requires_human_approval": True,
            },
        )
        session.add(item)
        session.flush()
        session.add(
            ContentRevision(
                workspace_id=run.workspace_id,
                content_item_id=item.id,
                version=item.version,
                title=item.title,
                body=item.body,
                hashtags=list(item.hashtags),
                call_to_action=item.call_to_action,
                layout_json=dict(item.layout_json),
                changed_by=None,
                change_reason="generated",
            )
        )
        for task in build_asset_tasks(platform, brief, plan, draft):
            session.add(
                Asset(
                    workspace_id=run.workspace_id,
                    content_item_id=item.id,
                    kind=str(task["type"]),
                    provider=settings.image_provider
                    if task["type"] == "image"
                    else settings.video_provider,
                    status="planned",
                    prompt=str(task.get("model_input") or draft.get("body") or ""),
                    metadata_json={**task, "content_version": 1},
                )
            )
        result_items.append(
            {
                "content_item_id": item.id,
                "platform": platform,
                "status": item.status,
                "review": rule_review.to_dict(),
            }
        )

    run.current_stage = "human_review"
    run.status = "awaiting_review"
    run.completed_at = datetime.now(timezone.utc)
    run.result_json = {
        "plan": plan,
        "retrieved_knowledge": knowledge_payload,
        "contents": result_items,
        "ai_provenance": provenance.snapshot(),
    }
    session.flush()
    return run.result_json
