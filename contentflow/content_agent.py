from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .ai_provenance import AIProvenanceRecorder
from .models import CampaignBrief, ReviewResult
from .review import RuleReviewer


QUALITY_DIMENSIONS = (
    "hook",
    "specificity",
    "evidence",
    "platform_native",
    "structure",
    "usefulness",
    "voice",
    "originality",
    "cta",
)
DEFAULT_QUALITY_TARGET = 8.0


@dataclass(slots=True)
class ContentAgentResult:
    draft: dict[str, Any]
    rule_review: ReviewResult
    model_review: dict[str, Any]
    quality_score: float
    revision_count: int
    generation_json: dict[str, Any]


def _score(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return round(max(0.0, min(10.0, float(value))), 2)


def normalize_editorial_review(raw: Any) -> dict[str, Any]:
    review = dict(raw) if isinstance(raw, dict) else {}
    raw_scores = review.get("scores")
    scores = {
        dimension: _score(
            raw_scores.get(dimension) if isinstance(raw_scores, dict) else None
        )
        for dimension in QUALITY_DIMENSIONS
    }
    reported = _score(review.get("quality_score"))
    quality_score = reported or round(sum(scores.values()) / len(scores), 2)
    risk_level = str(review.get("risk_level") or "medium").strip().lower()
    if risk_level not in {"low", "medium", "high"}:
        risk_level = "medium"

    def strings(value: Any, *, limit: int) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip()[:1000] for item in value[:limit] if str(item).strip()]

    return {
        **review,
        "passed": bool(review.get("passed", False)),
        "risk_level": risk_level,
        "quality_score": quality_score,
        "scores": scores,
        "issues": strings(review.get("issues"), limit=20),
        "fact_checks": strings(review.get("fact_checks"), limit=20),
        "strengths": strings(review.get("strengths"), limit=12),
        "revision_instructions": strings(
            review.get("revision_instructions"), limit=12
        ),
        "suggestion": str(review.get("suggestion") or "").strip()[:2000],
    }


def run_content_agent(
    *,
    provenance: AIProvenanceRecorder,
    brief: CampaignBrief,
    platform: str,
    plan: dict[str, Any],
    knowledge: list[dict[str, Any]],
    style_skill: dict[str, Any],
    style_notes: str,
    quality_profile: str,
    quality_target: float = DEFAULT_QUALITY_TARGET,
) -> ContentAgentResult:
    reviewer = RuleReviewer()
    common = {
        "brief": brief.to_dict(),
        "platform": platform,
        "plan": plan,
        "knowledge": knowledge,
        "style_skill": style_skill,
        "style_notes": style_notes,
        "quality_target": quality_target,
        "agent_limits": {
            "max_revision_rounds": 1 if quality_profile == "deep" else 0,
            "no_unbounded_loop": True,
            "allowed_context": [
                "brief",
                "retrieved_knowledge",
                "selected_style_skill",
            ],
        },
    }
    draft = provenance.complete_json(
        "generate",
        {**common, "phase": "initial_draft"},
        platform=platform,
    )
    rule_review = reviewer.review(platform, draft, brief)
    if not rule_review.passed:
        draft = reviewer.repair(platform, draft, brief)
        rule_review = reviewer.review(platform, draft, brief)

    model_review = normalize_editorial_review(
        provenance.complete_json(
            "review",
            {
                **common,
                "review_mode": "editorial_and_safety",
                "content": draft,
                "rule_review": rule_review.to_dict(),
            },
            platform=platform,
        )
    )
    revision_count = 0
    revision_selected = False
    should_revise = quality_profile == "deep" and (
        not model_review["passed"]
        or model_review["quality_score"] < quality_target
    )
    revision_attempt_status = "not_needed"
    revision_error_type = None
    if should_revise:
        revision_count = 1
        revision_attempt_status = "failed"
        try:
            revised = provenance.complete_json(
                "generate",
                {
                    **common,
                    "phase": "targeted_revision",
                    "previous_draft": draft,
                    "editorial_review": model_review,
                    "instruction": (
                        "只针对审核指出的问题重写，保留已经通过的事实、结构和平台字段"
                    ),
                },
                platform=platform,
            )
            revised_rule_review = reviewer.review(platform, revised, brief)
            if not revised_rule_review.passed:
                revised = reviewer.repair(platform, revised, brief)
                revised_rule_review = reviewer.review(platform, revised, brief)
            final_review = normalize_editorial_review(
                provenance.complete_json(
                    "review",
                    {
                        **common,
                        "review_mode": "final_editorial_and_safety",
                        "content": revised,
                        "previous_review": {
                            "passed": model_review["passed"],
                            "risk_level": model_review["risk_level"],
                            "quality_score": model_review["quality_score"],
                            "issues": model_review["issues"],
                            "revision_instructions": model_review[
                                "revision_instructions"
                            ],
                        },
                        "rule_review": revised_rule_review.to_dict(),
                    },
                    platform=platform,
                )
            )
        except (RuntimeError, TimeoutError) as error:
            revision_error_type = type(error).__name__
        else:
            original_safe = (
                rule_review.passed
                and model_review["passed"]
                and model_review["risk_level"] != "high"
            )
            revised_safe = (
                revised_rule_review.passed
                and final_review["passed"]
                and final_review["risk_level"] != "high"
            )
            select_revision = revised_safe and (
                not original_safe
                or final_review["quality_score"] >= model_review["quality_score"]
            )
            if select_revision:
                draft = revised
                rule_review = revised_rule_review
                model_review = final_review
                revision_selected = True
            revision_attempt_status = (
                "selected" if revision_selected else "rejected"
            )
    draft_extras = {
        key: value
        for key, value in draft.items()
        if key not in {"title", "body", "hashtags", "layout"}
    }
    generation_json = {
        "schema_version": 1,
        "mode": "bounded_content_agent",
        "quality_profile": quality_profile,
        "quality_target": quality_target,
        "quality_score": model_review["quality_score"],
        "revision_count": revision_count,
        "revision_selected": revision_selected,
        "revision_attempt_status": revision_attempt_status,
        "revision_error_type": revision_error_type,
        "max_revision_rounds": 1 if quality_profile == "deep" else 0,
        "style_skill": {
            "id": style_skill["id"],
            "source": style_skill["source"],
            "manifest_sha256": style_skill["manifest_sha256"],
            "slug": style_skill["manifest"]["slug"],
            "version": style_skill["manifest"]["version"],
        },
        "draft_extras": draft_extras,
    }
    return ContentAgentResult(
        draft=draft,
        rule_review=rule_review,
        model_review=model_review,
        quality_score=model_review["quality_score"],
        revision_count=revision_count,
        generation_json=generation_json,
    )
