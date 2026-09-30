from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import CampaignBrief, ContentItem
from .providers import Provider
from .rag import KnowledgeIndex
from .review import RuleReviewer
from .storage import Database


def build_asset_tasks(
    platform: str,
    brief: CampaignBrief,
    plan: dict[str, Any],
    draft: dict[str, Any],
    *,
    image_source: str = "manual",
    image_search_query: str = "",
) -> list[dict[str, Any]]:
    media_brief = (
        draft.get("media_brief")
        if isinstance(draft.get("media_brief"), dict)
        else {}
    )
    generation_prompt = str(
        media_brief.get("generation_prompt")
        or plan.get("image_generation_prompt")
        or plan.get("asset_direction")
        or ""
    ).strip()
    base_prompt = (
        f"{brief.city}城市内容视觉，{generation_prompt}，"
        f"画面服务于主题“{draft['title']}”，不得出现虚构优惠、未确认产品界面、"
        "无法核验的文字或品牌标识；构图应为社媒封面留出标题安全区"
    )
    search_query = str(
        image_search_query
        or media_brief.get("search_query")
        or plan.get("image_search_query")
        or f"{brief.city} {draft['title']}"
    ).strip()[:500]
    ratio = {
        "xiaohongshu": "3:4",
        "douyin": "9:16",
        "wechat": "16:9",
    }.get(platform, "1:1")
    source = (
        image_source
        if image_source in {"manual", "generate", "search", "hybrid"}
        else "manual"
    )
    providers = {
        "manual": ["manual"],
        "generate": ["configured-image-generation"],
        "search": ["openverse"],
        "hybrid": ["openverse", "configured-image-generation"],
    }[source]
    tasks = []
    for provider in providers:
        tasks.append(
            {
                "type": "image",
                "provider": provider,
                "media_source": (
                    "search"
                    if provider == "openverse"
                    else "generate"
                    if provider == "configured-image-generation"
                    else "manual"
                ),
                "model_input": base_prompt,
                "search_query": search_query,
                "ratio": ratio,
                "candidate_group": "cover" if source == "hybrid" else None,
                "candidate_optional": source == "hybrid",
                "selected": False if source == "hybrid" else True,
                "status": "pending_generation",
            }
        )
    if platform == "douyin":
        layout = draft.get("layout") if isinstance(draft.get("layout"), dict) else {}
        generated_shots = layout.get("shots")
        tasks.append(
            {
                "type": "video_storyboard",
                "provider": "configured-video-generation",
                "media_source": "generate",
                "duration_seconds": 20,
                "shots": generated_shots
                if isinstance(generated_shots, list) and generated_shots
                else [
                    "0-3 秒：提出具体冲突或展示结果",
                    "3-12 秒：展示关键操作和信息变化",
                    "12-17 秒：说明结果与适用边界",
                    "17-20 秒：自然衔接 CTA 与人工确认提示",
                ],
                "aspect_ratio": layout.get("aspect_ratio") or "9:16",
                "status": "pending_generation",
            }
        )
    return tasks


class ContentMarketingWorkflow:
    def __init__(
        self,
        workspace: Path,
        provider: Provider,
        reviewer: RuleReviewer | None = None,
    ):
        self.workspace = workspace
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.runs_dir = self.workspace / "runs"
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.database = Database(self.workspace / "contentflow.db")
        self.index = KnowledgeIndex(self.database)
        self.provider = provider
        self.reviewer = reviewer or RuleReviewer()

    def rebuild_knowledge(self, knowledge_dir: Path) -> int:
        return self.index.rebuild(knowledge_dir)

    def run(
        self,
        brief_raw: dict[str, Any],
        knowledge_dir: Path,
    ) -> dict[str, Any]:
        brief = CampaignBrief.from_dict(brief_raw)
        if not self.database.read_chunks():
            self.rebuild_knowledge(knowledge_dir)

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
        retrieved = self.index.search(query, limit=4)
        knowledge_payload = [item.to_dict() for item in retrieved]
        plan = self.provider.complete_json(
            "plan",
            {"brief": brief.to_dict(), "knowledge": knowledge_payload},
        )

        items: list[ContentItem] = []
        for platform in brief.platforms:
            draft = self.provider.complete_json(
                "generate",
                {
                    "brief": brief.to_dict(),
                    "platform": platform,
                    "plan": plan,
                    "knowledge": knowledge_payload,
                },
            )
            review = self.reviewer.review(platform, draft, brief)
            if not review.passed:
                draft = self.reviewer.repair(platform, draft, brief)
                review = self.reviewer.review(platform, draft, brief)

            status = "ready_for_human_review" if review.passed else "blocked"
            item = ContentItem(
                platform=platform,
                title=str(draft.get("title") or ""),
                body=str(draft.get("body") or ""),
                hashtags=[str(tag) for tag in draft.get("hashtags", [])],
                call_to_action=brief.call_to_action,
                layout_json=(
                    draft.get("layout")
                    if isinstance(draft.get("layout"), dict)
                    else {}
                ),
                source_chunk_ids=[chunk.chunk_id for chunk in retrieved],
                asset_tasks=build_asset_tasks(
                    platform, brief, plan, draft
                ),
                review=review,
                status=status,
            )
            items.append(item)

        run_id = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
        created_at = datetime.now(timezone.utc).isoformat()
        result = {
            "run_id": run_id,
            "created_at": created_at,
            "mode": "dry-run",
            "brief": brief.to_dict(),
            "retrieved_knowledge": knowledge_payload,
            "plan": plan,
            "contents": [item.to_dict() for item in items],
        }

        for item in items:
            if item.status == "ready_for_human_review":
                self.database.enqueue(run_id, item.platform, item.to_dict())
        result["publish_queue"] = self.database.queue_for_run(run_id)

        self.database.save_run(run_id, created_at, result)
        output_path = self.runs_dir / f"{run_id}.json"
        output_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        result["output_path"] = str(output_path)
        return result
