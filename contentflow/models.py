from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


SUPPORTED_PLATFORMS = {"xiaohongshu", "douyin", "wechat"}


@dataclass(slots=True)
class CampaignBrief:
    campaign_name: str
    product_name: str
    goal: str
    audience: str
    platforms: list[str]
    tone: str = "清楚、可信、不过度承诺"
    city: str = "北京"
    must_include: list[str] = field(default_factory=list)
    product_facts: list[str] = field(default_factory=list)
    forbidden_phrases: list[str] = field(default_factory=list)
    call_to_action: str = "打开星图地图，规划适合自己的路线"

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "CampaignBrief":
        required = ("campaign_name", "product_name", "goal", "audience", "platforms")
        missing = [key for key in required if not raw.get(key)]
        if missing:
            raise ValueError(f"brief 缺少必填字段: {', '.join(missing)}")

        platforms = [str(item).strip().lower() for item in raw["platforms"]]
        unsupported = sorted(set(platforms) - SUPPORTED_PLATFORMS)
        if unsupported:
            raise ValueError(f"暂不支持的平台: {', '.join(unsupported)}")

        return cls(
            campaign_name=str(raw["campaign_name"]).strip(),
            product_name=str(raw["product_name"]).strip(),
            goal=str(raw["goal"]).strip(),
            audience=str(raw["audience"]).strip(),
            platforms=platforms,
            tone=str(raw.get("tone") or "清楚、可信、不过度承诺").strip(),
            city=str(raw.get("city") or "北京").strip(),
            must_include=[str(item).strip() for item in raw.get("must_include", [])],
            product_facts=[
                str(item).strip() for item in raw.get("product_facts", [])
            ],
            forbidden_phrases=[
                str(item).strip() for item in raw.get("forbidden_phrases", [])
            ],
            call_to_action=str(
                raw.get("call_to_action")
                or "打开星图地图，规划适合自己的路线"
            ).strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RetrievedChunk:
    chunk_id: str
    source: str
    text: str
    score: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ReviewResult:
    passed: bool
    issues: list[str]
    checks: dict[str, bool]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ContentItem:
    platform: str
    title: str
    body: str
    hashtags: list[str]
    call_to_action: str
    layout_json: dict[str, Any]
    source_chunk_ids: list[str]
    asset_tasks: list[dict[str, Any]]
    review: ReviewResult
    status: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["review"] = self.review.to_dict()
        return data
