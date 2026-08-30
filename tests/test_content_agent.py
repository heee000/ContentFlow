from __future__ import annotations

import unittest

from contentflow.content_agent import QUALITY_DIMENSIONS, run_content_agent
from contentflow.models import CampaignBrief
from contentflow.style_skills import BUILTIN_STYLE_SKILLS, style_manifest_hash


class SequencedProvenance:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self.review_count = 0

    def complete_json(self, stage, payload, *, platform=None):
        self.calls.append((stage, platform))
        brief = payload["brief"]
        if stage == "generate":
            revised = payload["phase"] == "targeted_revision"
            detail = (
                "把候选地点集中后，按出发时间、交通方式和停留时长删减，"
                "最后再人工确认路线。"
                if revised
                else "把候选地点集中整理，再确认路线。"
            )
            return {
                "title": "收藏很多地点，为什么还是排不成路线",
                "body": (
                    f"真正拖慢出发的不是少一个地点，而是信息散在不同收藏里。"
                    f"{detail}使用{brief['product_name']}整理候选地点。"
                    f"{brief['must_include'][0]}。{brief['call_to_action']}。"
                ),
                "hashtags": ["城市路线"],
                "layout": {
                    "cover_title": "收藏不是路线",
                    "cards": [
                        {"heading": "集中", "copy": "先收齐地点"},
                        {"heading": "取舍", "copy": "按现实约束删减"},
                        {"heading": "确认", "copy": "出发前人工确认"},
                        {"heading": "行动", "copy": brief["call_to_action"]},
                    ],
                    "visual_notes": "竖版卡片",
                },
                "alternate_titles": ["从收藏到路线", "出发前还差这一步"],
                "evidence_usage": [],
                "media_brief": {"generation_prompt": "真实城市街景"},
            }
        if stage == "review":
            self.review_count += 1
            score = 5.5 if self.review_count == 1 else 8.8
            return {
                "passed": True,
                "risk_level": "low",
                "quality_score": score,
                "scores": {dimension: score for dimension in QUALITY_DIMENSIONS},
                "issues": ["步骤还不够具体"] if self.review_count == 1 else [],
                "fact_checks": [],
                "strengths": ["事实边界清楚"],
                "revision_instructions": (
                    ["补充路线取舍的现实约束"] if self.review_count == 1 else []
                ),
                "suggestion": "仍需人工审核",
            }
        raise AssertionError(stage)


class ContentAgentTest(unittest.TestCase):
    def test_deep_profile_runs_one_bounded_revision(self):
        brief = CampaignBrief.from_dict(
            {
                "campaign_name": "路线内容",
                "product_name": "地图产品",
                "goal": "帮助用户整理路线",
                "audience": "周末临时出发的年轻用户",
                "platforms": ["xiaohongshu"],
                "must_include": ["候选地点"],
                "product_facts": ["支持整理候选地点"],
                "call_to_action": "打开地图产品确认路线",
            }
        )
        style = {
            "id": "builtin:editorial",
            "source": "builtin",
            "manifest": BUILTIN_STYLE_SKILLS["builtin:editorial"],
            "manifest_sha256": style_manifest_hash(
                BUILTIN_STYLE_SKILLS["builtin:editorial"]
            ),
        }
        provenance = SequencedProvenance()
        stages: list[str] = []

        result = run_content_agent(
            provenance=provenance,
            brief=brief,
            platform="xiaohongshu",
            plan={"selected_angle": "从收藏到路线"},
            knowledge=[],
            style_skill=style,
            style_notes="克制、有细节",
            quality_profile="deep",
            on_stage=stages.append,
        )

        self.assertEqual(
            provenance.calls,
            [
                ("generate", "xiaohongshu"),
                ("review", "xiaohongshu"),
                ("generate", "xiaohongshu"),
                ("review", "xiaohongshu"),
            ],
        )
        self.assertEqual(result.revision_count, 1)
        self.assertEqual(
            stages,
            [
                "drafting_xiaohongshu",
                "reviewing_xiaohongshu",
                "revising_xiaohongshu",
                "final_review_xiaohongshu",
            ],
        )
        self.assertEqual(result.quality_score, 8.8)
        self.assertTrue(result.rule_review.passed)
        self.assertEqual(
            result.generation_json["style_skill"]["manifest_sha256"],
            style["manifest_sha256"],
        )
        self.assertEqual(
            result.generation_json["mode"],
            "bounded_content_agent",
        )


class UnsafeRevisionProvenance(SequencedProvenance):
    def complete_json(self, stage, payload, *, platform=None):
        if stage != "review":
            return super().complete_json(stage, payload, platform=platform)
        self.calls.append((stage, platform))
        self.review_count += 1
        score = 5.5 if self.review_count == 1 else 9.2
        return {
            "passed": False,
            "risk_level": "high",
            "quality_score": score,
            "scores": {dimension: score for dimension in QUALITY_DIMENSIONS},
            "issues": ["事实或品牌安全仍未通过"],
            "fact_checks": ["需要人工核验"],
            "strengths": [],
            "revision_instructions": ["只使用已核验事实"],
            "suggestion": "阻断并人工审核",
        }


class ContentAgentSafetyTest(unittest.TestCase):
    def test_higher_scoring_but_unsafe_revision_is_never_selected(self):
        brief = CampaignBrief.from_dict(
            {
                "campaign_name": "安全回归",
                "product_name": "地图产品",
                "goal": "帮助用户整理路线",
                "audience": "周末出行用户",
                "platforms": ["xiaohongshu"],
                "must_include": ["候选地点"],
                "product_facts": ["支持整理候选地点"],
                "call_to_action": "打开地图产品确认路线",
            }
        )
        style = {
            "id": "builtin:editorial",
            "source": "builtin",
            "manifest": BUILTIN_STYLE_SKILLS["builtin:editorial"],
            "manifest_sha256": style_manifest_hash(
                BUILTIN_STYLE_SKILLS["builtin:editorial"]
            ),
        }
        result = run_content_agent(
            provenance=UnsafeRevisionProvenance(),
            brief=brief,
            platform="xiaohongshu",
            plan={"selected_angle": "安全优先"},
            knowledge=[],
            style_skill=style,
            style_notes="",
            quality_profile="deep",
        )
        self.assertEqual(result.revision_count, 1)
        self.assertFalse(result.generation_json["revision_selected"])
        self.assertEqual(result.quality_score, 5.5)


class FailingFinalReviewProvenance(SequencedProvenance):
    def complete_json(self, stage, payload, *, platform=None):
        if stage == "review" and self.review_count == 1:
            self.calls.append((stage, platform))
            self.review_count += 1
            raise RuntimeError("invalid provider JSON")
        return super().complete_json(stage, payload, platform=platform)


class ContentAgentFallbackTest(unittest.TestCase):
    def test_optional_revision_failure_keeps_reviewed_original(self):
        brief = CampaignBrief.from_dict(
            {
                "campaign_name": "修订降级",
                "product_name": "地图产品",
                "goal": "帮助用户整理路线",
                "audience": "周末出行用户",
                "platforms": ["xiaohongshu"],
                "must_include": ["候选地点"],
                "product_facts": ["支持整理候选地点"],
                "call_to_action": "打开地图产品确认路线",
            }
        )
        style = {
            "id": "builtin:editorial",
            "source": "builtin",
            "manifest": BUILTIN_STYLE_SKILLS["builtin:editorial"],
            "manifest_sha256": style_manifest_hash(
                BUILTIN_STYLE_SKILLS["builtin:editorial"]
            ),
        }
        result = run_content_agent(
            provenance=FailingFinalReviewProvenance(),
            brief=brief,
            platform="xiaohongshu",
            plan={"selected_angle": "保留安全原稿"},
            knowledge=[],
            style_skill=style,
            style_notes="",
            quality_profile="deep",
        )
        self.assertEqual(result.quality_score, 5.5)
        self.assertFalse(result.generation_json["revision_selected"])
        self.assertEqual(
            result.generation_json["revision_attempt_status"],
            "failed",
        )
        self.assertEqual(
            result.generation_json["revision_error_type"],
            "RuntimeError",
        )


if __name__ == "__main__":
    unittest.main()
