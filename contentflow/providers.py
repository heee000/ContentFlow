from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Protocol

from .prompts import PROMPTS


class Provider(Protocol):
    provider_name: str
    model_name: str
    last_call_metadata: dict[str, Any]

    def complete_json(
        self,
        stage: str,
        payload: dict[str, Any],
        *,
        system_prompt: str | None = None,
    ) -> dict[str, Any]: ...


PLATFORM_LABELS = {
    "xiaohongshu": "小红书",
    "douyin": "抖音",
    "wechat": "公众号",
}


class MockProvider:
    """Deterministic provider so the complete workflow runs offline."""

    provider_name = "mock"
    model_name = "mock-deterministic-v1"

    def __init__(self) -> None:
        self.last_call_metadata: dict[str, Any] = {"usage_source": "not_reported"}

    def complete_json(
        self,
        stage: str,
        payload: dict[str, Any],
        *,
        system_prompt: str | None = None,
    ) -> dict[str, Any]:
        self.last_call_metadata = {"usage_source": "not_reported"}
        if stage == "plan":
            brief = payload["brief"]
            return {
                "content_angle": f"{brief['city']}真实场景中的路线规划",
                "key_message": (f"用{brief['product_name']}把临时搜索整理成可执行路线"),
                "evidence_points": [
                    chunk["text"][:80] for chunk in payload["knowledge"][:3]
                ],
                "platform_notes": {
                    "xiaohongshu": "突出收藏价值和真实体验感",
                    "douyin": "前三秒给出问题，使用短句和镜头节奏",
                    "wechat": "补足背景、步骤和使用边界",
                },
                "asset_direction": "城市夜景、路线节点和手机地图操作的组合",
                "posting_window": "工作日 18:00-21:00，最终由运营人员结合账号数据确认",
            }

        if stage == "generate":
            brief = payload["brief"]
            platform = payload["platform"]
            product = brief["product_name"]
            must_include = "、".join(brief.get("must_include", []))
            product_facts = "、".join(brief.get("product_facts", []))
            cta = brief["call_to_action"]
            if platform == "xiaohongshu":
                return {
                    "title": f"{brief['city']}夜游路线怎么规划更省心",
                    "body": (
                        f"临时决定出门时，我会先用{product}把想去的地点放进同一条路线，"
                        f"再根据时间和交通方式调整顺序。{must_include}。"
                        f"这份流程适合想少做攻略、又希望保留临场选择的人。{cta}。"
                    ),
                    "hashtags": ["城市出行", "夜游路线", "地图攻略"],
                    "layout": {
                        "cover_title": "夜游路线这样排更清楚",
                        "cards": [
                            {"heading": "先列地点", "copy": "把候选地点集中整理"},
                            {"heading": "再排顺序", "copy": "结合时间和交通方式调整"},
                            {
                                "heading": "最后确认",
                                "copy": product_facts or "出发前人工确认路线",
                            },
                        ],
                        "visual_notes": "3:4 竖版；封面一句结论，正文卡片保持同一层级",
                    },
                }
            if platform == "douyin":
                return {
                    "title": "一条路线解决夜游选择困难",
                    "body": (
                        f"开场：想夜游又不想反复切换攻略？"
                        f"用{product}先整理地点，再按时间调整路线。"
                        f"镜头依次展示地点选择、路线确认和导航准备。"
                        f"{must_include}。结尾：{cta}。"
                    ),
                    "hashtags": ["夜游", "路线规划", "出行技巧"],
                    "layout": {
                        "aspect_ratio": "9:16",
                        "music_mood": "轻快、克制，不掩盖口播",
                        "shots": [
                            {
                                "time": "0-3秒",
                                "visual": "多个地点与路线选择快速切换",
                                "voiceover": "想夜游，又不想反复切攻略？",
                                "subtitle": "路线选择困难",
                            },
                            {
                                "time": "3-12秒",
                                "visual": "整理候选地点并调整路线顺序",
                                "voiceover": f"先用{product}整理地点，再按时间调整路线。",
                                "subtitle": "地点整理 → 顺序调整",
                            },
                            {
                                "time": "12-17秒",
                                "visual": "展示确认后的路线与出发准备",
                                "voiceover": product_facts or must_include,
                                "subtitle": "出发前再次确认",
                            },
                            {
                                "time": "17-20秒",
                                "visual": "产品画面与行动引导",
                                "voiceover": cta,
                                "subtitle": cta,
                            },
                        ],
                    },
                }
            return {
                "title": "从零散地点到一条可执行的夜游路线",
                "body": (
                    f"做夜游计划时，难点往往不是找不到地点，而是信息太散。"
                    f"可以先在{product}中整理候选地点，再结合出发时间、交通方式和停留时长"
                    f"调整顺序。{must_include}。这套方法不替用户做最终选择，"
                    f"而是把零散信息变成便于确认的路线。{cta}。"
                ),
                "hashtags": ["城市出行", "路线规划"],
                "layout": {
                    "lead": "地点不难找，难的是把零散信息变成可执行路线。",
                    "sections": [
                        {"heading": "先收集候选地点", "summary": "统一整理信息来源"},
                        {
                            "heading": "再确定路线顺序",
                            "summary": "结合时间、交通和停留时长",
                        },
                        {
                            "heading": "出发前完成确认",
                            "summary": product_facts or must_include,
                        },
                    ],
                    "closing": cta,
                },
            }

        if stage == "review":
            return {
                "passed": True,
                "risk_level": "low",
                "issues": [],
                "fact_checks": [
                    "未发现超出输入知识范围的具体产品能力承诺",
                    "仍需人工确认品牌语气与平台合规",
                ],
                "suggestion": "保留人工审核后再生成素材和分发",
            }

        raise ValueError(f"未知生成阶段: {stage}")


class OpenAICompatibleProvider:
    def __init__(
        self,
        api_base: str,
        api_key: str,
        model: str,
        timeout_seconds: int = 60,
        provider_name: str = "openai-compatible",
    ):
        self.endpoint = f"{api_base.rstrip('/')}/chat/completions"
        self.api_key = api_key
        self.model = model
        self.model_name = model
        self.provider_name = provider_name
        self.timeout_seconds = timeout_seconds
        self.last_call_metadata: dict[str, Any] = {"usage_source": "not_reported"}

    @classmethod
    def from_environment(cls) -> "OpenAICompatibleProvider":
        required = {
            "CONTENTFLOW_API_BASE": os.getenv("CONTENTFLOW_API_BASE"),
            "CONTENTFLOW_API_KEY": os.getenv("CONTENTFLOW_API_KEY"),
            "CONTENTFLOW_MODEL": os.getenv("CONTENTFLOW_MODEL"),
        }
        missing = [key for key, value in required.items() if not value]
        if missing:
            raise ValueError(f"缺少模型配置: {', '.join(missing)}")
        return cls(
            api_base=required["CONTENTFLOW_API_BASE"] or "",
            api_key=required["CONTENTFLOW_API_KEY"] or "",
            model=required["CONTENTFLOW_MODEL"] or "",
        )

    def complete_json(
        self,
        stage: str,
        payload: dict[str, Any],
        *,
        system_prompt: str | None = None,
    ) -> dict[str, Any]:
        self.last_call_metadata = {"usage_source": "not_reported"}
        if stage not in PROMPTS:
            raise ValueError(f"没有对应提示词模板: {stage}")
        request_body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt or PROMPTS[stage]},
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False),
                },
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.3,
        }
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(request_body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.timeout_seconds
            ) as response:
                raw = json.loads(response.read().decode("utf-8"))
            usage = raw.get("usage") if isinstance(raw, dict) else None
            if isinstance(usage, dict):
                input_tokens = usage.get("prompt_tokens", usage.get("input_tokens"))
                output_tokens = usage.get(
                    "completion_tokens", usage.get("output_tokens")
                )
                total_tokens = usage.get("total_tokens")
                if any(
                    isinstance(value, int) and not isinstance(value, bool)
                    for value in (input_tokens, output_tokens, total_tokens)
                ):
                    self.last_call_metadata = {
                        "usage_source": "provider_reported",
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "total_tokens": total_tokens,
                    }
            if isinstance(raw, dict) and isinstance(raw.get("model"), str):
                self.last_call_metadata["response_model"] = raw["model"]
            content = raw["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                raise ValueError("模型返回的 JSON 顶层不是对象")
            return parsed
        except (urllib.error.URLError, KeyError, json.JSONDecodeError) as error:
            raise RuntimeError(f"模型调用或 JSON 解析失败: {error}") from error


def build_provider(name: str) -> Provider:
    normalized = name.strip().lower()
    if normalized == "mock":
        return MockProvider()
    if normalized == "openai-compatible":
        return OpenAICompatibleProvider.from_environment()
    raise ValueError(f"未知 provider: {name}")
