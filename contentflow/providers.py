from __future__ import annotations

import json
import os
import re
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

PROVIDER_REQUEST_ID_HEADERS = (
    "x-request-id",
    "x-requestid",
    "request-id",
    "openai-request-id",
    "x-amzn-requestid",
)
PROVIDER_REQUEST_KEY = re.compile(r"^[0-9a-f]{64}$")


def _bounded_provider_identifier(value: Any, limit: int = 255) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > limit
        or any(ord(character) < 32 or ord(character) == 127 for character in normalized)
    ):
        return None
    return normalized


def _provider_request_metadata(
    *,
    body: Any = None,
    headers: Any = None,
) -> dict[str, str]:
    if isinstance(body, dict):
        body_id = _bounded_provider_identifier(body.get("id"))
        if body_id:
            return {
                "provider_request_id": body_id,
                "provider_request_id_source": "body.id",
            }
    if headers is not None and callable(getattr(headers, "get", None)):
        for header in PROVIDER_REQUEST_ID_HEADERS:
            header_id = _bounded_provider_identifier(headers.get(header))
            if header_id:
                return {
                    "provider_request_id": header_id,
                    "provider_request_id_source": f"header.{header}"[:40],
                }
    return {}


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
                "audience_tension": "想快速得到可执行路线，又不愿被固定攻略限制",
                "angle_candidates": [
                    {
                        "angle": "从信息过载到可执行路线",
                        "hook": "地点不难找，难的是把十几个收藏排成一条能走完的路线",
                        "reader_value": "提供从候选地点到出发确认的完整方法",
                        "evidence_chunk_ids": [
                            chunk.get("chunk_id")
                            for chunk in payload["knowledge"][:2]
                        ],
                        "risk": "不得暗示自动规划结果绝对准确",
                    },
                    {
                        "angle": "临时出发的最小准备清单",
                        "hook": "不做三小时攻略，也不等于毫无准备",
                        "reader_value": "给临时出发者一份最小行动清单",
                        "evidence_chunk_ids": [],
                        "risk": "清单只能使用已确认产品事实",
                    },
                    {
                        "angle": "路线调整中的取舍",
                        "hook": "真正拖慢出发的不是少一个地点，而是不肯删地点",
                        "reader_value": "帮助读者建立路线取舍顺序",
                        "evidence_chunk_ids": [],
                        "risk": "建议不能伪装成平台数据",
                    },
                ],
                "selected_angle": "从信息过载到可执行路线",
                "selection_reason": "与目标场景和产品已确认能力最贴近",
                "content_thesis": "先集中信息，再按现实约束调整，最后人工确认",
                "evidence_ledger": [],
                "narrative_arc": ["提出矛盾", "给出方法", "说明边界", "引导行动"],
                "platform_strategies": {
                    "xiaohongshu": {
                        "hook": "收藏很多却排不成路线",
                        "structure": "场景、步骤、避坑、CTA",
                        "native_devices": ["清单", "卡片"],
                        "target_length": "350-650 字",
                    },
                    "douyin": {
                        "hook": "前三秒展示地点过多的混乱",
                        "structure": "冲突、操作、结果、CTA",
                        "native_devices": ["短句字幕", "动作镜头"],
                        "target_length": "180-330 字",
                    },
                    "wechat": {
                        "hook": "地点不难找，难的是把信息变成决策",
                        "structure": "观点导语、方法展开、边界、CTA",
                        "native_devices": ["小标题", "步骤说明"],
                        "target_length": "900-1500 字",
                    },
                },
                "image_search_query": f"{brief['city']} 城市路线 街景 地图",
                "image_generation_prompt": "真实城市出行场景，保留自然光线和生活细节",
                "known_unknowns": ["具体平台流量时段需由运营人员结合账号数据确认"],
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
                    "alternate_titles": ["收藏很多，路线却总排不出来", "临时夜游的最小路线清单"],
                    "evidence_usage": [],
                    "media_brief": {
                        "objective": "展示从零散收藏到清晰路线的变化",
                        "must_show": ["真实城市街景", "路线节点"],
                        "must_avoid": ["虚构产品界面", "无法核验的优惠"],
                        "search_query": f"{brief['city']} 城市路线 街景 地图",
                        "generation_prompt": "真实城市夜游场景，竖版构图，留出标题安全区",
                    },
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
                    "alternate_titles": ["收藏十个地点，为什么还是出不了门", "20 秒理清夜游路线"],
                    "evidence_usage": [],
                    "media_brief": {
                        "objective": "用镜头表现信息从混乱到清晰",
                        "must_show": ["地点选择", "路线调整"],
                        "must_avoid": ["虚构产品界面", "夸张效果"],
                        "search_query": f"{brief['city']} 夜景 路线 竖屏",
                        "generation_prompt": "真实城市夜游，竖屏短视频封面，强前后对比",
                    },
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
                "alternate_titles": ["收藏夹不是路线：出发前还差这一步", "把零散地点变成可确认路线"],
                "evidence_usage": [],
                "media_brief": {
                    "objective": "为长文提供可信、克制的城市路线头图",
                    "must_show": ["真实城市空间", "路线感"],
                    "must_avoid": ["虚构产品界面", "促销文字"],
                    "search_query": f"{brief['city']} 城市路线 街景 地图",
                    "generation_prompt": "真实城市街景与路线意象，横版编辑头图，留白克制",
                },
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
                "quality_score": 8.4,
                "scores": {
                    "hook": 8.2,
                    "specificity": 8.3,
                    "evidence": 8.5,
                    "platform_native": 8.6,
                    "structure": 8.5,
                    "usefulness": 8.4,
                    "voice": 8.2,
                    "originality": 8.0,
                    "cta": 8.6,
                },
                "strengths": ["结构完整，事实边界清楚", "行动引导与正文一致"],
                "issues": [],
                "fact_checks": [
                    "未发现超出输入知识范围的具体产品能力承诺",
                    "仍需人工确认品牌语气与平台合规",
                ],
                "revision_instructions": [],
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
        self._invocation_key: str | None = None

    def set_invocation_context(self, request_key: str) -> bool:
        if not PROVIDER_REQUEST_KEY.fullmatch(request_key):
            raise ValueError("Provider invocation request key is invalid")
        self._invocation_key = request_key
        return True

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
        invocation_key = self._invocation_key
        self._invocation_key = None
        self.last_call_metadata = {
            "usage_source": "not_reported",
            "idempotency_key_sent": invocation_key is not None,
        }
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
            "temperature": {
                "plan": 0.45,
                "generate": 0.7,
                "review": 0.15,
            }.get(stage, 0.3),
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if invocation_key is not None:
            headers["Idempotency-Key"] = invocation_key
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(request_body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.timeout_seconds
            ) as response:
                raw = json.loads(response.read().decode("utf-8"))
                self.last_call_metadata.update(
                    _provider_request_metadata(
                        body=raw,
                        headers=getattr(response, "headers", None),
                    )
                )
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
                    self.last_call_metadata.update(
                        {
                            "usage_source": "provider_reported",
                            "input_tokens": input_tokens,
                            "output_tokens": output_tokens,
                            "total_tokens": total_tokens,
                        }
                    )
            if isinstance(raw, dict) and isinstance(raw.get("model"), str):
                self.last_call_metadata["response_model"] = raw["model"]
            content = raw["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                raise ValueError("模型返回的 JSON 顶层不是对象")
            return parsed
        except urllib.error.HTTPError as error:
            self.last_call_metadata.update(
                _provider_request_metadata(headers=getattr(error, "headers", None))
            )
            raise RuntimeError(f"模型调用失败 (HTTP {error.code})") from error
        except urllib.error.URLError as error:
            raise RuntimeError("模型调用失败 (network_error)") from error
        except (KeyError, json.JSONDecodeError) as error:
            raise RuntimeError("模型响应结构或 JSON 解析失败") from error


def build_provider(name: str) -> Provider:
    normalized = name.strip().lower()
    if normalized == "mock":
        return MockProvider()
    if normalized == "openai-compatible":
        return OpenAICompatibleProvider.from_environment()
    raise ValueError(f"未知 provider: {name}")
