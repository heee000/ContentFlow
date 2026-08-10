from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from .entities import PromptRelease
from .prompts import (
    BUILTIN_PROMPT_SET,
    PROMPT_STAGES,
    PromptSet,
    calculate_prompt_hashes,
)


PROMPT_MIN_LENGTH = 20
PROMPT_MAX_LENGTH = 20_000


class PromptIntegrityError(RuntimeError):
    """Raised when a stored release no longer matches its recorded hashes."""


def normalize_prompts(prompts: Mapping[str, object]) -> dict[str, str]:
    expected = set(PROMPT_STAGES)
    actual = set(prompts)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        details = []
        if missing:
            details.append(f"缺少阶段: {', '.join(missing)}")
        if extra:
            details.append(f"未知阶段: {', '.join(extra)}")
        raise ValueError(
            "Prompt 必须且只能包含 plan、generate、review；" + "；".join(details)
        )

    normalized: dict[str, str] = {}
    for stage in PROMPT_STAGES:
        value = prompts[stage]
        if not isinstance(value, str):
            raise ValueError(f"{stage} Prompt 必须是字符串")
        value = value.strip()
        if len(value) < PROMPT_MIN_LENGTH:
            raise ValueError(f"{stage} Prompt 至少需要 {PROMPT_MIN_LENGTH} 个字符")
        if len(value) > PROMPT_MAX_LENGTH:
            raise ValueError(f"{stage} Prompt 不能超过 {PROMPT_MAX_LENGTH} 个字符")
        normalized[stage] = value
    return normalized


def prompt_release_version(release_number: int) -> str:
    return f"workspace-r{release_number}"


def prompt_set_from_release(release: PromptRelease) -> PromptSet:
    prompts = normalize_prompts(release.prompts_json)
    calculated = calculate_prompt_hashes(prompts)
    recorded = dict(release.prompt_hashes_json or {})
    if calculated != recorded:
        raise PromptIntegrityError(
            f"Prompt release {release.id} failed integrity verification"
        )
    return PromptSet(
        source="workspace_release",
        version=prompt_release_version(release.release_number),
        release_id=release.id,
        prompts=prompts,
        hashes=calculated,
    )


def resolve_active_prompt_set(
    session: Session,
    workspace_id: str,
) -> PromptSet:
    release = session.scalar(
        select(PromptRelease).where(
            PromptRelease.workspace_id == workspace_id,
            PromptRelease.status == "active",
        )
    )
    if release is None:
        return BUILTIN_PROMPT_SET
    return prompt_set_from_release(release)
