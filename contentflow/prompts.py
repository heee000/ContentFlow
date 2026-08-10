from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Mapping


PROMPT_SET_VERSION = "2026-08-09.1"
PROMPT_STAGES = ("plan", "generate", "review")

PROMPTS = {
    "plan": """
你是内容营销策划 Agent。基于营销 brief 和检索到的知识，只输出 JSON。
不要编造产品能力、活动价格或平台规则。输出字段：
content_angle, key_message, evidence_points, platform_notes, asset_direction。
同时输出 posting_window，给出建议发布时间段及理由，但不要伪造平台流量数据。
""".strip(),
    "generate": """
你是多平台内容生成 Agent。基于 brief、内容计划和知识引用，只输出 JSON。
输出字段：title, body, hashtags, layout。正文必须包含 brief 中的必含信息和 CTA，
不得使用禁用词；只能使用 product_facts 与知识引用中的产品事实。
不同平台应体现不同表达方式和结构，而不是简单改写同一段文字：
- 小红书 layout 包含 cover_title、cards（每项含 heading、copy）与 visual_notes；
- 抖音 layout 包含 aspect_ratio、music_mood、shots（每项含 time、visual、voiceover、subtitle）；
- 公众号 layout 包含 lead、sections（每项含 heading、summary）与 closing。
""".strip(),
    "review": """
你是营销内容风险审核 Agent。只基于 brief、知识引用和待审核内容判断，
不要补充未提供的产品事实。只输出 JSON：
passed（布尔值）、risk_level（low/medium/high）、issues（字符串数组）、
fact_checks（字符串数组）、suggestion（字符串）。
模型审核只作为人工审核参考，不能自行触发外部发布。
""".strip(),
}


def calculate_prompt_hashes(prompts: Mapping[str, str]) -> dict[str, str]:
    return {
        stage: hashlib.sha256(prompts[stage].encode("utf-8")).hexdigest()
        for stage in PROMPT_STAGES
    }


PROMPT_HASHES = calculate_prompt_hashes(PROMPTS)


@dataclass(frozen=True)
class PromptSet:
    source: str
    version: str
    release_id: str | None
    prompts: Mapping[str, str]
    hashes: Mapping[str, str]


BUILTIN_PROMPT_SET = PromptSet(
    source="builtin",
    version=PROMPT_SET_VERSION,
    release_id=None,
    prompts=PROMPTS,
    hashes=PROMPT_HASHES,
)
