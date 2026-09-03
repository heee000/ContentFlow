from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .ai_provenance import AIProvenanceRecorder
from .audit import record_audit
from .entities import PromptEvalRun, PromptEvalSuite, PromptRelease
from .prompt_governance import prompt_set_from_release
from .prompts import PROMPT_STAGES
from .settings import Settings
from .text_generation import build_text_provider


MAX_CASE_INPUT_BYTES = 100_000
MAX_ASSERTION_VALUE_BYTES = 100_000
MAX_ASSERTION_STRING_BYTES = 10_000
MAX_SUITE_BYTES = 8_000_000
PATH_PATTERN = re.compile(r"^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*$")
_MISSING = object()


class EvalIntegrityError(RuntimeError):
    """Raised when an immutable evaluation snapshot fails verification."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _normalize_string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field} 必须是数组")
    if len(value) > 100:
        raise ValueError(f"{field} 不能超过 100 项")
    normalized = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{field} 只能包含非空字符串")
        item = item.strip()
        if len(item.encode("utf-8")) > MAX_ASSERTION_STRING_BYTES:
            raise ValueError(
                f"{field} 的单项不能超过 {MAX_ASSERTION_STRING_BYTES} 字节"
            )
        normalized.append(item)
    return normalized


def _validate_path(path: str) -> str:
    path = path.strip()
    if not PATH_PATTERN.fullmatch(path):
        raise ValueError(f"无效 JSON 路径: {path}")
    return path


def normalize_eval_cases(cases: Any) -> list[dict[str, Any]]:
    if not isinstance(cases, list) or not 3 <= len(cases) <= 60:
        raise ValueError("评测套件必须包含 3 到 60 个用例")
    normalized: list[dict[str, Any]] = []
    names: set[str] = set()
    stages: set[str] = set()
    allowed = {
        "name",
        "stage",
        "input_json",
        "required_paths",
        "expected_values",
        "required_substrings",
        "forbidden_substrings",
        "max_output_bytes",
    }
    for raw in cases:
        if not isinstance(raw, dict) or set(raw) - allowed:
            raise ValueError("评测用例包含未知字段")
        name = raw.get("name")
        stage = raw.get("stage")
        input_json = raw.get("input_json")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("评测用例名称不能为空")
        name = name.strip()
        if len(name) > 160 or name in names:
            raise ValueError("评测用例名称必须唯一且不超过 160 字符")
        if stage not in PROMPT_STAGES:
            raise ValueError(f"未知评测阶段: {stage}")
        if not isinstance(input_json, dict):
            raise ValueError(f"{name} 的 input_json 必须是对象")
        if len(_canonical_bytes(input_json)) > MAX_CASE_INPUT_BYTES:
            raise ValueError(f"{name} 的输入超过 {MAX_CASE_INPUT_BYTES} 字节")

        required_paths = [
            _validate_path(item)
            for item in _normalize_string_list(
                raw.get("required_paths", []),
                "required_paths",
            )
        ]
        expected_values = raw.get("expected_values", {})
        if not isinstance(expected_values, dict) or len(expected_values) > 100:
            raise ValueError("expected_values 必须是最多 100 项的对象")
        if len(_canonical_bytes(expected_values)) > MAX_ASSERTION_VALUE_BYTES:
            raise ValueError(
                f"expected_values 不能超过 {MAX_ASSERTION_VALUE_BYTES} 字节"
            )
        expected_values = {
            _validate_path(str(path)): value for path, value in expected_values.items()
        }
        required_substrings = _normalize_string_list(
            raw.get("required_substrings", []),
            "required_substrings",
        )
        forbidden_substrings = _normalize_string_list(
            raw.get("forbidden_substrings", []),
            "forbidden_substrings",
        )
        max_output_bytes = raw.get("max_output_bytes", 100_000)
        if (
            isinstance(max_output_bytes, bool)
            or not isinstance(max_output_bytes, int)
            or not 256 <= max_output_bytes <= 1_000_000
        ):
            raise ValueError("max_output_bytes 必须在 256 到 1000000 之间")
        if not (
            required_paths
            or expected_values
            or required_substrings
            or forbidden_substrings
        ):
            raise ValueError(f"{name} 至少需要一项确定性断言")

        normalized.append(
            {
                "name": name,
                "stage": stage,
                "input_json": input_json,
                "required_paths": required_paths,
                "expected_values": expected_values,
                "required_substrings": required_substrings,
                "forbidden_substrings": forbidden_substrings,
                "max_output_bytes": max_output_bytes,
            }
        )
        names.add(name)
        stages.add(stage)

    if len(_canonical_bytes(normalized)) > MAX_SUITE_BYTES:
        raise ValueError(f"评测套件不能超过 {MAX_SUITE_BYTES} 字节")

    missing = set(PROMPT_STAGES) - stages
    if missing:
        raise ValueError(
            "评测套件必须覆盖 plan、generate、review；缺少: "
            + ", ".join(sorted(missing))
        )
    return normalized


def calculate_suite_hash(cases: list[dict[str, Any]]) -> str:
    return _sha256(cases)


def verify_eval_suite(suite: PromptEvalSuite) -> list[dict[str, Any]]:
    cases = normalize_eval_cases(suite.cases_json)
    if calculate_suite_hash(cases) != suite.suite_hash:
        raise EvalIntegrityError(
            f"Prompt eval suite {suite.id} failed integrity verification"
        )
    return cases


def eval_suite_version(version_number: int) -> str:
    return f"eval-v{version_number}"


def get_active_eval_suite(
    session: Session,
    workspace_id: str,
) -> PromptEvalSuite | None:
    return session.scalar(
        select(PromptEvalSuite).where(
            PromptEvalSuite.workspace_id == workspace_id,
            PromptEvalSuite.status == "active",
        )
    )


def require_current_passed_eval(
    session: Session,
    release: PromptRelease,
    settings: Settings,
    provider_override: str | None = None,
) -> tuple[PromptEvalSuite, PromptEvalRun]:
    suite = get_active_eval_suite(session, release.workspace_id)
    if suite is None:
        raise ValueError("当前工作区没有生效的 Prompt Eval 套件")
    verify_eval_suite(suite)
    prompt_set = prompt_set_from_release(release)
    target_provider = build_text_provider(settings, provider_override)
    target_provider_name = str(getattr(target_provider, "provider_name", "unknown"))[
        :80
    ]
    target_model_name = str(getattr(target_provider, "model_name", "unknown"))[:160]
    run = session.scalar(
        select(PromptEvalRun)
        .where(
            PromptEvalRun.workspace_id == release.workspace_id,
            PromptEvalRun.prompt_release_id == release.id,
            PromptEvalRun.suite_id == suite.id,
            PromptEvalRun.status == "passed",
            PromptEvalRun.suite_hash == suite.suite_hash,
            PromptEvalRun.provider == target_provider_name,
            PromptEvalRun.model == target_model_name,
        )
        .order_by(PromptEvalRun.completed_at.desc())
    )
    if run is None or dict(run.prompt_hashes_json) != dict(prompt_set.hashes):
        raise ValueError(
            "Prompt 版本尚未通过当前评测套件 "
            f"{eval_suite_version(suite.version_number)} 的目标模型门禁 "
            f"({target_provider_name}/{target_model_name})"
        )
    return suite, run


def _resolve_path(value: Any, path: str) -> Any:
    current = value
    for segment in path.split("."):
        if isinstance(current, dict) and segment in current:
            current = current[segment]
        elif isinstance(current, list) and segment.isdigit():
            index = int(segment)
            if index >= len(current):
                return _MISSING
            current = current[index]
        else:
            return _MISSING
    return current


def evaluate_case_output(
    case: dict[str, Any],
    output: dict[str, Any],
) -> dict[str, Any]:
    encoded = _canonical_bytes(output)
    serialized = encoded.decode("utf-8")
    failures: list[dict[str, Any]] = []
    if len(encoded) > case["max_output_bytes"]:
        failures.append(
            {
                "assertion": "max_output_bytes",
                "actual_bytes": len(encoded),
                "limit": case["max_output_bytes"],
            }
        )
    for path in case["required_paths"]:
        if _resolve_path(output, path) is _MISSING:
            failures.append({"assertion": "required_path", "path": path})
    for path, expected in case["expected_values"].items():
        actual = _resolve_path(output, path)
        if actual is _MISSING or actual != expected:
            failures.append({"assertion": "expected_value", "path": path})
    for value in case["required_substrings"]:
        if value not in serialized:
            failures.append(
                {
                    "assertion": "required_substring",
                    "value_sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
                }
            )
    for value in case["forbidden_substrings"]:
        if value in serialized:
            failures.append(
                {
                    "assertion": "forbidden_substring",
                    "value_sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
                }
            )
    return {
        "name": case["name"],
        "stage": case["stage"],
        "passed": not failures,
        "output_sha256": hashlib.sha256(encoded).hexdigest(),
        "output_bytes": len(encoded),
        "failures": failures,
    }


def execute_prompt_eval_run(
    session: Session,
    run: PromptEvalRun,
    settings: Settings,
) -> dict[str, Any]:
    release = session.get(PromptRelease, run.prompt_release_id)
    suite = session.get(PromptEvalSuite, run.suite_id)
    if (
        release is None
        or suite is None
        or release.workspace_id != run.workspace_id
        or suite.workspace_id != run.workspace_id
    ):
        raise ValueError("评测运行关联对象不存在或工作区不一致")

    cases = verify_eval_suite(suite)
    prompt_set = prompt_set_from_release(release)
    if run.suite_hash != suite.suite_hash:
        raise EvalIntegrityError("评测运行绑定的套件哈希不一致")
    if dict(run.prompt_hashes_json) != dict(prompt_set.hashes):
        raise EvalIntegrityError("评测运行绑定的 Prompt 哈希不一致")

    run.status = "running"
    run.started_at = datetime.now(timezone.utc)
    session.commit()
    provider = build_text_provider(settings, run.requested_provider)
    recorder = AIProvenanceRecorder(
        provider,
        embedding_provider="not_used",
        embedding_model="not_used",
        prompt_set=prompt_set,
        ledger_session=(
            session if getattr(provider, "provider_name", "") == "openai-compatible" else None
        ),
        workspace_id=run.workspace_id,
        entity_type="prompt_eval_run",
        entity_id=run.id,
    )
    results = []
    for case in cases:
        output = recorder.complete_json(
            case["stage"],
            dict(case["input_json"]),
        )
        results.append(evaluate_case_output(case, output))

    passed_count = sum(item["passed"] for item in results)
    run.provider = recorder.provider_name
    run.model = recorder.model_name
    run.status = "passed" if passed_count == len(results) else "failed"
    run.completed_at = datetime.now(timezone.utc)
    run.error = None
    run.result_json = {
        "schema_version": 1,
        "suite_version": eval_suite_version(suite.version_number),
        "suite_hash": suite.suite_hash,
        "case_count": len(results),
        "passed_count": passed_count,
        "failed_count": len(results) - passed_count,
        "cases": results,
        "ai_provenance": recorder.snapshot(),
    }
    record_audit(
        session,
        action="prompt_eval.complete",
        entity_type="prompt_eval_run",
        entity_id=run.id,
        workspace_id=run.workspace_id,
        actor_user_id=None,
        metadata={
            "prompt_release_id": release.id,
            "suite_id": suite.id,
            "status": run.status,
            "case_count": len(results),
            "passed_count": passed_count,
            "prompt_hashes": dict(prompt_set.hashes),
            "suite_hash": suite.suite_hash,
        },
    )
    session.flush()
    return run.result_json
