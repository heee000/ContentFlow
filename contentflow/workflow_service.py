from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.orm import Session

from .ai_provenance import AIProvenanceRecorder
from .content_agent import run_content_agent
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
from .settings import Settings
from .style_skills import resolve_style_skill
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


def campaign_generation_preferences(campaign: Campaign) -> dict:
    stored = dict(campaign.brief or {})
    return {
        "style_skill_id": stored.get("style_skill_id") or "builtin:editorial",
        "style_notes": str(stored.get("style_notes") or "").strip(),
        "quality_profile": (
            stored.get("quality_profile")
            if stored.get("quality_profile") in {"standard", "deep"}
            else "deep"
        ),
        "image_source": (
            stored.get("image_source")
            if stored.get("image_source") in {"manual", "generate", "search", "hybrid"}
            else "manual"
        ),
        "image_search_query": str(stored.get("image_search_query") or "").strip(),
    }


def execute_workflow_run(
    session: Session,
    run: WorkflowRun,
    settings: Settings,
) -> dict:
    campaign = session.get(Campaign, run.campaign_id)
    if campaign is None or campaign.workspace_id != run.workspace_id:
        raise ValueError("工作流关联的活动不存在")

    prompt_set = resolve_active_prompt_set(session, run.workspace_id)
    if not prompt_set.release_id and settings.require_governed_prompts:
        raise ValueError(
            "当前环境要求受治理 Prompt；请先完成 Eval 套件、Prompt 评测、双人审批与激活"
        )
    provider_override = run.request_json.get("provider")
    provider = build_text_provider(settings, provider_override)
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

    run.status = "running"
    run.current_stage = "knowledge_retrieval"
    run.started_at = datetime.now(timezone.utc)
    session.commit()

    def publish_stage(stage: str) -> None:
        """Persist observable progress without committing partial content rows."""
        with Session(session.get_bind()) as progress_session:
            progress_session.execute(
                update(WorkflowRun)
                .where(
                    WorkflowRun.id == run.id,
                    WorkflowRun.workspace_id == run.workspace_id,
                )
                .values(current_stage=stage)
            )
            progress_session.commit()

    brief_raw = dict(
        run.request_json.get("campaign_brief_snapshot") or campaign_to_brief(campaign)
    )
    preferences = dict(
        run.request_json.get("generation_preferences")
        or campaign_generation_preferences(campaign)
    )
    style_skill = run.request_json.get("style_skill_snapshot")
    if not isinstance(style_skill, dict):
        style_skill = resolve_style_skill(
            session,
            run.workspace_id,
            preferences.get("style_skill_id"),
        )
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

    provenance = AIProvenanceRecorder(
        provider,
        embedding_provider=settings.embedding_provider,
        embedding_model=embedder.model_name,
        prompt_set=prompt_set,
    )
    run.provider = provenance.provider_name
    session.commit()
    publish_stage("planning")
    plan = provenance.complete_json(
        "plan",
        {
            "brief": brief.to_dict(),
            "knowledge": knowledge_payload,
            "style_skill": style_skill,
            "style_notes": preferences.get("style_notes") or "",
            "quality_profile": preferences.get("quality_profile") or "deep",
        },
    )

    requested_platforms = set(
        run.request_json.get("regenerate_platforms") or brief.platforms
    )
    platforms_to_generate = [
        platform for platform in brief.platforms if platform in requested_platforms
    ]
    platform_count = len(platforms_to_generate)
    result_items = []
    for platform_index, platform in enumerate(platforms_to_generate, start=1):
        agent_result = run_content_agent(
            provenance=provenance,
            brief=brief,
            platform=platform,
            plan=plan,
            knowledge=knowledge_payload,
            style_skill=style_skill,
            style_notes=str(preferences.get("style_notes") or ""),
            quality_profile=str(preferences.get("quality_profile") or "deep"),
            on_stage=lambda stage, index=platform_index: publish_stage(
                f"{stage}__{index}_of_{platform_count}"
            ),
        )
        draft = agent_result.draft
        rule_review = agent_result.rule_review
        model_review = agent_result.model_review
        model_review_passed = bool(model_review.get("passed", False))
        safety_passed = model_review_passed and model_review.get("risk_level") != "high"

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
                "needs_review" if rule_review.passed and safety_passed else "blocked"
            ),
            source_chunk_ids=[chunk.chunk_id for chunk in retrieved],
            review_json={
                "rule_review": rule_review.to_dict(),
                "model_review": model_review,
                "quality_score": agent_result.quality_score,
                "quality_target": agent_result.generation_json["quality_target"],
                "requires_human_approval": True,
            },
            generation_json=agent_result.generation_json,
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
                generation_json=dict(item.generation_json),
                changed_by=None,
                change_reason=(
                    "agent_revised"
                    if agent_result.generation_json["revision_selected"]
                    else "agent_generated"
                ),
            )
        )
        for task in build_asset_tasks(
            platform,
            brief,
            plan,
            draft,
            image_source=str(preferences.get("image_source") or "manual"),
            image_search_query=str(
                preferences.get("image_search_query")
                or plan.get("image_search_query")
                or ""
            ),
        ):
            session.add(
                Asset(
                    workspace_id=run.workspace_id,
                    content_item_id=item.id,
                    kind=str(task["type"]),
                    provider=str(task.get("provider") or "manual"),
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
                "quality_score": agent_result.quality_score,
                "revision_count": agent_result.revision_count,
                "review": rule_review.to_dict(),
            }
        )

    run.current_stage = "human_review"
    run.status = "awaiting_review"
    run.completed_at = datetime.now(timezone.utc)
    run.result_json = {
        "agent_schema_version": 1,
        "agent_mode": "bounded_content_agent",
        "plan": plan,
        "style_skill": {
            "id": style_skill["id"],
            "source": style_skill["source"],
            "manifest_sha256": style_skill["manifest_sha256"],
            "slug": style_skill["manifest"]["slug"],
            "version": style_skill["manifest"]["version"],
        },
        "generation_preferences": preferences,
        "retrieved_knowledge": knowledge_payload,
        "contents": result_items,
        "ai_provenance": provenance.snapshot(),
    }
    session.flush()
    return run.result_json
