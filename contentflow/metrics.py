from __future__ import annotations

from collections import defaultdict
from typing import Any


def safe_rate(numerator: float, denominator: float) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def build_recap(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, dict[str, float]] = defaultdict(
        lambda: {
            "impressions": 0,
            "clicks": 0,
            "likes": 0,
            "comments": 0,
            "shares": 0,
        }
    )
    for row in rows:
        platform = str(row["platform"])
        for key in grouped[platform]:
            grouped[platform][key] += float(row.get(key, 0) or 0)

    platform_results = {}
    recommendations = []
    for platform, values in grouped.items():
        engagement = values["likes"] + values["comments"] + values["shares"]
        platform_results[platform] = {
            **values,
            "click_through_rate": safe_rate(
                values["clicks"], values["impressions"]
            ),
            "engagement_rate": safe_rate(engagement, values["impressions"]),
        }
        if platform_results[platform]["click_through_rate"] < 0.03:
            recommendations.append(
                f"{platform}: 下一轮优先测试标题和前两句，减少信息铺垫。"
            )
        if platform_results[platform]["engagement_rate"] >= 0.05:
            recommendations.append(
                f"{platform}: 保留当前内容角度，并测试同主题的系列化表达。"
            )

    return {
        "platforms": platform_results,
        "recommendations": recommendations
        or ["数据量有限，下一轮先保持单一变量测试并补充样本。"],
    }

