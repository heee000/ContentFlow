import copy
import json

import pytest

from contentflow.ai_provenance import AIProvenanceRecorder
from contentflow.content_agent import normalize_editorial_review
from contentflow.model_output import (
    ModelOutputError,
    complete_model_json,
    validate_model_output,
)
from contentflow.models import CampaignBrief
from contentflow.providers import MockProvider


def valid_output(stage, platform="wechat"):
    brief = CampaignBrief(
        "contract", "product", "test", "readers", [platform]
    ).to_dict()
    payload = {"brief": brief, "knowledge": [], "platform": platform, "plan": {}}
    return MockProvider().complete_json(stage, payload)


@pytest.mark.parametrize("value", ["false", "true", 1, 0, None, [], {}, [False]])
def test_review_boolean_is_never_coerced(value):
    output = valid_output("review")
    output["passed"] = value
    with pytest.raises(ModelOutputError):
        normalize_editorial_review(output)


@pytest.mark.parametrize(
    "value", [float("nan"), float("inf"), -float("inf"), True, "9", -1, 11, None]
)
@pytest.mark.parametrize("dimension", [None, "hook"])
def test_scores_require_finite_bounded_numbers(value, dimension):
    output = valid_output("review")
    if dimension:
        output["scores"][dimension] = value
    else:
        output["quality_score"] = value
    with pytest.raises(ModelOutputError):
        normalize_editorial_review(output)


def test_zero_score_and_boolean_false_are_preserved():
    output = valid_output("review")
    output.update(passed=False, quality_score=0)
    validated = normalize_editorial_review(output)
    assert validated["passed"] is False
    assert validated["quality_score"] == 0


@pytest.mark.parametrize(
    "field",
    [
        "passed",
        "risk_level",
        "quality_score",
        "scores",
        "issues",
        "fact_checks",
        "strengths",
        "revision_instructions",
        "suggestion",
    ],
)
def test_missing_review_evidence_is_not_fabricated(field):
    output = valid_output("review")
    del output[field]
    with pytest.raises(ModelOutputError):
        normalize_editorial_review(output)


@pytest.mark.parametrize("stage", ["plan", "generate", "review"])
@pytest.mark.parametrize("platform", ["wechat", "douyin", "xiaohongshu"])
def test_current_mock_contracts_are_valid(stage, platform):
    validate_model_output(stage, valid_output(stage, platform), platform=platform)


@pytest.mark.parametrize(
    "stage,field,bad",
    [
        ("plan", "angle_candidates", []),
        (
            "plan",
            "evidence_ledger",
            [{"claim": "fact", "supporting_chunk_ids": "not-an-array"}],
        ),
        ("generate", "title", 123),
        ("generate", "body", {"body": "not-a-string"}),
        ("generate", "hashtags", "tag"),
        ("generate", "layout", {}),
        ("generate", "evidence_usage", [{"claim": "fact", "chunk_ids": [False]}]),
        ("review", "issues", "not-an-array"),
        ("review", "risk_level", "unknown"),
    ],
)
def test_each_stage_checks_structure(stage, field, bad):
    output = valid_output(stage)
    output[field] = copy.deepcopy(bad)
    with pytest.raises(ModelOutputError):
        validate_model_output(stage, output, platform="wechat")


def test_invalid_model_response_preserves_received_call_evidence_without_retry():
    class BadOutputProvider:
        provider_name = "test"
        model_name = "test-model"
        last_call_metadata = {"usage_source": "provider_reported", "total_tokens": 21}
        calls = 0

        def complete_json(self, *args, **kwargs):
            self.calls += 1
            output = valid_output("review")
            output["passed"] = "PRIVATE-MODEL-TEXT"
            return output

    provider = BadOutputProvider()
    recorder = AIProvenanceRecorder(
        provider, embedding_provider="none", embedding_model="none"
    )
    with pytest.raises(ModelOutputError) as captured:
        complete_model_json(recorder, "review", {})
    assert provider.calls == 1
    evidence = captured.value.ai_provenance
    assert evidence["successful_invocations"] == 1
    assert evidence["token_usage"]["total_tokens"] == 21
    assert evidence["output_validation"]["status"] == "failed"
    assert "PRIVATE-MODEL-TEXT" not in str(captured.value)
    assert "PRIVATE-MODEL-TEXT" not in json.dumps(evidence)
