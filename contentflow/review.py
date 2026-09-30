from __future__ import annotations

from typing import Any

from .models import CampaignBrief, ReviewResult


MAX_BODY_LENGTH = {
    "xiaohongshu": 700,
    "douyin": 350,
    "wechat": 1600,
}


def publication_text_fields(draft: dict[str, Any]) -> list[tuple[str, str]]:
    """Enumerate persisted/exported text, including nested layout and script fields."""
    pending = [(key, draft.get(key)) for key in
        ("title", "body", "hashtags", "call_to_action", "layout")]
    fields = []
    while pending:
        path, value = pending.pop()
        if isinstance(value, str):
            fields.append((path, value))
        elif isinstance(value, dict):
            fields.extend((f"{path}.<key:{index}>", key)
                for index, key in enumerate(value) if isinstance(key, str))
            pending.extend((f"{path}.{key}", item) for key, item in value.items())
        elif isinstance(value, list):
            pending.extend((f"{path}[{index}]", item) for index, item in enumerate(value))
    return fields


class RuleReviewer:
    def review(
        self,
        platform: str,
        draft: dict[str, Any],
        brief: CampaignBrief,
    ) -> ReviewResult:
        title = str(draft.get("title") or "").strip()
        body = str(draft.get("body") or "").strip()
        issues: list[str] = []
        forbidden_fields = [path for path, value in publication_text_fields(draft)
            if any(phrase in value for phrase in brief.forbidden_phrases if phrase)]
        checks = {
            "has_title": bool(title),
            "has_body": bool(body),
            "within_length": len(body) <= MAX_BODY_LENGTH[platform],
            "contains_product": brief.product_name in body,
            "contains_cta": brief.call_to_action.rstrip("。") in body,
            "contains_required_facts": all(
                item in body for item in brief.must_include
            ),
            "avoids_forbidden_phrases": not forbidden_fields,
        }
        issue_messages = {
            "has_title": "缺少标题",
            "has_body": "缺少正文",
            "within_length": "正文超过平台长度限制",
            "contains_product": "正文未出现产品名",
            "contains_cta": "正文未包含 CTA",
            "contains_required_facts": "正文缺少 brief 中的必含信息",
            "avoids_forbidden_phrases": "发布字段包含禁用词：" + "、".join(sorted(forbidden_fields)),
        }
        for key, passed in checks.items():
            if not passed:
                issues.append(issue_messages[key])
        return ReviewResult(passed=all(checks.values()), issues=issues, checks=checks)

    def repair(
        self,
        platform: str,
        draft: dict[str, Any],
        brief: CampaignBrief,
    ) -> dict[str, Any]:
        repaired = dict(draft)
        title = str(repaired.get("title") or f"{brief.campaign_name}内容建议")
        body = str(repaired.get("body") or "")
        for phrase in brief.forbidden_phrases:
            title = title.replace(phrase, "")
            body = body.replace(phrase, "")

        additions = []
        if brief.product_name not in body:
            additions.append(f"使用{brief.product_name}整理路线信息")
        for fact in brief.must_include:
            if fact not in body:
                additions.append(fact)
        if brief.call_to_action.rstrip("。") not in body:
            additions.append(brief.call_to_action)
        if additions:
            body = f"{body.rstrip('。')}。{'。'.join(additions)}。"

        limit = MAX_BODY_LENGTH[platform]
        if len(body) > limit:
            cta = f"{brief.call_to_action.rstrip('。')}。"
            body = f"{body[: max(0, limit - len(cta) - 1)].rstrip('，。')}。{cta}"

        repaired["title"] = title.strip()
        repaired["body"] = body.strip()
        repaired.setdefault("hashtags", [])
        return repaired

