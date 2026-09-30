"""Versioned application contracts for untrusted model JSON.

Transport success and content validity are separate: a rejected response can
still have incurred cost. Validation never invokes or retries a provider.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError


MODEL_OUTPUT_SCHEMA_VERSION = 1
Text = Annotated[str, Field(strict=True, min_length=1, max_length=10_000)]
ShortText = Annotated[str, Field(strict=True, min_length=1, max_length=1000)]
Strings = Annotated[list[ShortText], Field(max_length=50)]
Score = Annotated[float, Field(strict=True, ge=0, le=10, allow_inf_nan=False)]


class ModelOutputError(RuntimeError):
    def __init__(self, stage: str) -> None:
        # Never include model text, arbitrary dictionary keys or Pydantic's raw
        # validation message (which includes input values) in logs/API errors.
        self.stage = stage
        super().__init__(
            f"模型 {stage} 阶段输出不符合结构契约，已停止使用该结果；请核对，不会自动重试"
        )


class OutputModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="allow")


class Angle(OutputModel):
    angle: Text
    hook: Text
    reader_value: Text
    evidence_chunk_ids: Strings
    risk: Text


class EvidenceClaim(OutputModel):
    claim: Text
    supporting_chunk_ids: Strings


class PlatformStrategy(OutputModel):
    hook: Text
    structure: Text | Strings
    native_devices: Strings
    target_length: Text


class PlanOutput(OutputModel):
    audience_tension: Text
    angle_candidates: Annotated[list[Angle], Field(min_length=3, max_length=20)]
    selected_angle: Text
    selection_reason: Text
    content_thesis: Text
    key_message: Text
    evidence_ledger: Annotated[list[EvidenceClaim], Field(max_length=100)]
    narrative_arc: Annotated[list[Text], Field(min_length=1, max_length=30)]
    platform_strategies: Annotated[
        dict[str, PlatformStrategy], Field(min_length=1, max_length=3)
    ]
    known_unknowns: Strings


class EvidenceUsage(OutputModel):
    claim: Text
    chunk_ids: Strings


class DraftOutput(OutputModel):
    title: ShortText
    body: Annotated[str, Field(strict=True, min_length=1, max_length=32_000)]
    hashtags: Strings
    layout: dict[str, Any]
    evidence_usage: Annotated[list[EvidenceUsage], Field(max_length=100)] = Field(
        default_factory=list
    )
    media_brief: dict[str, Any] = Field(default_factory=dict)


class QualityScores(OutputModel):
    hook: Score
    specificity: Score
    evidence: Score
    platform_native: Score
    structure: Score
    usefulness: Score
    voice: Score
    originality: Score
    cta: Score


class EditorialReviewOutput(OutputModel):
    passed: StrictBool
    risk_level: Literal["low", "medium", "high"]
    quality_score: Score
    scores: QualityScores
    issues: Strings
    fact_checks: Strings
    strengths: Strings
    revision_instructions: Strings
    suggestion: Annotated[str, Field(strict=True, max_length=2000)]


class Card(OutputModel):
    heading: ShortText
    text: Text = Field(alias="copy")


class XiaohongshuLayout(OutputModel):
    cover_title: ShortText
    cards: Annotated[list[Card], Field(min_length=1, max_length=20)]
    visual_notes: Text


class Shot(OutputModel):
    time: ShortText
    visual: Text
    voiceover: Annotated[str, Field(strict=True, max_length=10_000)]
    subtitle: Annotated[str, Field(strict=True, max_length=10_000)]


class DouyinLayout(OutputModel):
    aspect_ratio: ShortText
    music_mood: Text
    shots: Annotated[list[Shot], Field(min_length=1, max_length=30)]


class Section(OutputModel):
    heading: ShortText
    summary: Text


class WechatLayout(OutputModel):
    lead: Text
    sections: Annotated[list[Section], Field(min_length=1, max_length=20)]
    closing: Text


OUTPUT_MODELS = {
    "plan": PlanOutput,
    "generate": DraftOutput,
    "review": EditorialReviewOutput,
}
LAYOUT_MODELS = {
    "xiaohongshu": XiaohongshuLayout,
    "douyin": DouyinLayout,
    "wechat": WechatLayout,
}


def validate_model_output(
    stage: str, raw: Any, *, platform: str | None = None
) -> dict[str, Any]:
    if stage not in OUTPUT_MODELS:
        raise ModelOutputError("unknown")
    try:
        if not isinstance(raw, dict):
            raise ValueError("object required")
        # Also reject NaN/Infinity hidden in optional/extraneous JSON fields.
        json.dumps(raw, allow_nan=False)
        result = OUTPUT_MODELS[stage].model_validate(raw).model_dump(by_alias=True)
        if stage == "generate":
            if platform not in LAYOUT_MODELS:
                raise ValueError("platform required")
            result["layout"] = (
                LAYOUT_MODELS[platform].model_validate(result["layout"]).model_dump(by_alias=True)
            )
        return result
    except (ValidationError, ValueError, TypeError, OverflowError, RecursionError):
        raise ModelOutputError(stage) from None


def complete_model_json(
    provenance, stage: str, payload: dict[str, Any], *, platform: str | None = None
) -> dict[str, Any]:
    result = provenance.complete_json(stage, payload, platform=platform)
    try:
        return validate_model_output(
            stage, result, platform=platform or payload.get("platform")
        )
    except ModelOutputError as error:
        # The ledger correctly retains a received/charged response as success;
        # the application contract is a separate, non-secret failure dimension.
        snapshot = getattr(provenance, "snapshot", None)
        if callable(snapshot):
            error.ai_provenance = snapshot()
            error.ai_provenance["output_validation"] = {
                "stage": stage,
                "status": "failed",
                "schema_version": MODEL_OUTPUT_SCHEMA_VERSION,
            }
        raise
