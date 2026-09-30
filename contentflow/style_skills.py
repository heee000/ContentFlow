from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .entities import StyleSkill


STYLE_MANIFEST_VERSION = 1
STYLE_SKILL_ID_MAX_LENGTH = 80
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9.-]+)?$")
_PLATFORMS = {"xiaohongshu", "douyin", "wechat"}


BUILTIN_STYLE_SKILLS: dict[str, dict[str, Any]] = {
    "builtin:editorial": {
        "manifest_version": STYLE_MANIFEST_VERSION,
        "slug": "editorial",
        "name": "专业社媒编辑",
        "version": "1.0.0",
        "description": "以具体场景、可信证据和平台原生结构完成可发布内容。",
        "instructions": [
            "开头快速建立与目标受众有关的真实矛盾或具体收益",
            "用可核验细节替代空泛形容词，每一段都推进信息或行动",
            "保留自然的人类语气，避免模板化总分总和连续口号",
            "事实、体验判断和建议必须分开表达",
        ],
        "forbidden_patterns": [
            "首先、其次、最后的机械三段式",
            "赋能、颠覆、全方位、闭眼冲等无证据营销词",
            "连续感叹号和未经证实的效果承诺",
        ],
        "platform_instructions": {
            "xiaohongshu": [
                "标题提供具体结果、反差或适用人群，不写泛化疑问句",
                "正文包含可收藏的步骤、清单或避坑信息，并保持口语节奏",
            ],
            "douyin": [
                "前三秒给冲突或结果，口播每句都能单独成为字幕",
                "镜头、口播和字幕表达同一信息但不要逐字重复",
            ],
            "wechat": [
                "使用有观点的导语、清晰小标题和充分展开的论证",
                "结论说明适用边界，并自然衔接行动引导",
            ],
        },
        "examples": [],
    },
    "builtin:storytelling": {
        "manifest_version": STYLE_MANIFEST_VERSION,
        "slug": "storytelling",
        "name": "场景叙事",
        "version": "1.0.0",
        "description": "从一个可信的使用瞬间切入，用细节和转折承载信息。",
        "instructions": [
            "从人物、时间、地点和一个具体困扰开始，而不是先介绍产品",
            "用动作、选择和结果推动叙事，产品只在解决问题时出现",
            "故事必须来自 brief 或知识证据；无法确认的个人经历不得伪装成事实",
            "结尾回到读者可执行的下一步，并说明适用边界",
        ],
        "forbidden_patterns": [
            "虚构用户证言、交易数据或亲身经历",
            "为了戏剧性夸大风险或承诺结果",
        ],
        "platform_instructions": {
            "xiaohongshu": ["使用第一现场感，但明确区分示例场景与真实见闻"],
            "douyin": ["每个镜头都应包含可见动作或信息变化"],
            "wechat": ["叙事之后补足方法、证据和可复用清单"],
        },
        "examples": [],
    },
    "builtin:expert-explainer": {
        "manifest_version": STYLE_MANIFEST_VERSION,
        "slug": "expert-explainer",
        "name": "专业解释型",
        "version": "1.0.0",
        "description": "适合产品方法论、决策指南和需要建立专业可信度的内容。",
        "instructions": [
            "先给判断，再解释判断依据和不适用条件",
            "用对比、步骤或决策树降低理解成本",
            "术语首次出现时使用普通语言解释",
            "证据不足时明确标注未知，不用推测补齐",
        ],
        "forbidden_patterns": ["堆砌术语", "把相关性写成因果关系"],
        "platform_instructions": {
            "xiaohongshu": ["把专业判断转换成可保存的检查清单"],
            "douyin": ["一条视频只解释一个核心判断"],
            "wechat": ["保留完整推理链，并用小标题帮助扫读"],
        },
        "examples": [],
    },
}


def _text(value: Any, field: str, *, minimum: int, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} 必须是字符串")
    normalized = value.strip()
    if not minimum <= len(normalized) <= maximum:
        raise ValueError(f"{field} 长度必须在 {minimum} 到 {maximum} 之间")
    try:
        normalized.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError(f"{field} 必须是有效 UTF-8 文本") from None
    if any(ord(char) < 0x20 and char not in "\n\t" for char in normalized):
        raise ValueError(f"{field} 包含非法控制字符")
    return normalized


def _text_list(
    value: Any,
    field: str,
    *,
    minimum: int = 0,
    maximum: int = 20,
) -> list[str]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise ValueError(f"{field} 必须包含 {minimum} 到 {maximum} 项")
    return [
        _text(item, f"{field}[{index}]", minimum=1, maximum=500)
        for index, item in enumerate(value)
    ]


def normalize_style_manifest(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("风格 Skill manifest 必须是 JSON 对象")
    encoded = json.dumps(raw, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    if len(encoded) > 64 * 1024:
        raise ValueError("风格 Skill manifest 不能超过 64 KiB")
    allowed = {
        "manifest_version",
        "slug",
        "name",
        "version",
        "description",
        "instructions",
        "forbidden_patterns",
        "platform_instructions",
        "examples",
    }
    extra = sorted(set(raw) - allowed)
    if extra:
        raise ValueError(f"风格 Skill 包含未知字段: {', '.join(extra)}")
    if raw.get("manifest_version") != STYLE_MANIFEST_VERSION:
        raise ValueError(f"manifest_version 必须为 {STYLE_MANIFEST_VERSION}")
    slug = _text(raw.get("slug"), "slug", minimum=2, maximum=64).lower()
    if not _SLUG.fullmatch(slug):
        raise ValueError("slug 只能包含小写字母、数字和单个连字符")
    version = _text(raw.get("version"), "version", minimum=5, maximum=40).lower()
    if not _VERSION.fullmatch(version):
        raise ValueError("version 必须使用语义化版本，例如 1.0.0")

    platform_raw = raw.get("platform_instructions") or {}
    if not isinstance(platform_raw, dict):
        raise ValueError("platform_instructions 必须是对象")
    unknown_platforms = sorted(set(platform_raw) - _PLATFORMS)
    if unknown_platforms:
        raise ValueError(f"包含不支持的平台: {', '.join(unknown_platforms)}")
    platform_instructions = {
        platform: _text_list(
            instructions,
            f"platform_instructions.{platform}",
            maximum=12,
        )
        for platform, instructions in sorted(platform_raw.items())
    }

    examples_raw = raw.get("examples") or []
    if not isinstance(examples_raw, list) or len(examples_raw) > 8:
        raise ValueError("examples 最多包含 8 项")
    examples = []
    for index, example in enumerate(examples_raw):
        if not isinstance(example, dict) or set(example) != {
            "platform",
            "title",
            "excerpt",
        }:
            raise ValueError(
                f"examples[{index}] 必须且只能包含 platform、title、excerpt"
            )
        platform = str(example.get("platform") or "").strip().lower()
        if platform not in _PLATFORMS:
            raise ValueError(f"examples[{index}].platform 不受支持")
        examples.append(
            {
                "platform": platform,
                "title": _text(
                    example.get("title"),
                    f"examples[{index}].title",
                    minimum=1,
                    maximum=200,
                ),
                "excerpt": _text(
                    example.get("excerpt"),
                    f"examples[{index}].excerpt",
                    minimum=1,
                    maximum=1200,
                ),
            }
        )

    return {
        "manifest_version": STYLE_MANIFEST_VERSION,
        "slug": slug,
        "name": _text(raw.get("name"), "name", minimum=2, maximum=120),
        "version": version,
        "description": _text(
            raw.get("description"), "description", minimum=5, maximum=1000
        ),
        "instructions": _text_list(
            raw.get("instructions"), "instructions", minimum=1, maximum=20
        ),
        "forbidden_patterns": _text_list(
            raw.get("forbidden_patterns") or [],
            "forbidden_patterns",
            maximum=20,
        ),
        "platform_instructions": platform_instructions,
        "examples": examples,
    }


def style_manifest_hash(manifest: dict[str, Any]) -> str:
    canonical = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def builtin_style_responses() -> list[dict[str, Any]]:
    return [
        {
            "id": skill_id,
            "source": "builtin",
            "status": "enabled",
            "manifest": deepcopy(manifest),
            "manifest_sha256": style_manifest_hash(manifest),
            "created_at": None,
            "updated_at": None,
        }
        for skill_id, manifest in BUILTIN_STYLE_SKILLS.items()
    ]


def resolve_style_skill(
    session: Session,
    workspace_id: str,
    skill_id: str | None,
) -> dict[str, Any]:
    selected = (skill_id or "builtin:editorial").strip()
    if selected in BUILTIN_STYLE_SKILLS:
        manifest = deepcopy(BUILTIN_STYLE_SKILLS[selected])
        return {
            "id": selected,
            "source": "builtin",
            "manifest": manifest,
            "manifest_sha256": style_manifest_hash(manifest),
        }
    skill = session.scalar(
        select(StyleSkill).where(
            StyleSkill.id == selected,
            StyleSkill.workspace_id == workspace_id,
            StyleSkill.status == "enabled",
        )
    )
    if skill is None:
        raise ValueError("所选风格 Skill 不存在或已停用")
    normalized = normalize_style_manifest(skill.manifest_json)
    calculated = style_manifest_hash(normalized)
    if calculated != skill.manifest_sha256:
        raise ValueError("所选风格 Skill 完整性校验失败")
    return {
        "id": skill.id,
        "source": "workspace",
        "manifest": normalized,
        "manifest_sha256": calculated,
    }
