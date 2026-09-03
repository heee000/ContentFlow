from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

from contentflow.ai_provenance import AIProvenanceRecorder
from contentflow.prompts import PROMPT_HASHES, PROMPT_SET_VERSION, PROMPTS
from contentflow.providers import OpenAICompatibleProvider
from contentflow.settings import Settings
from contentflow.text_generation import build_text_provider


class StaticProvider:
    provider_name = "test-provider"
    model_name = "test-model"

    def __init__(self) -> None:
        self.last_call_metadata = {
            "usage_source": "provider_reported",
            "input_tokens": 11,
            "output_tokens": 7,
            "total_tokens": 18,
        }

    def complete_json(self, _stage, _payload, *, system_prompt=None):
        self.system_prompt = system_prompt
        return {"value": "generated"}


class FailingProvider:
    provider_name = "failing-provider"
    model_name = "failing-model"
    last_call_metadata = {
        "usage_source": "provider_reported",
        "input_tokens": 17,
        "output_tokens": 0,
        "total_tokens": 17,
        "response_model": "failing-model-revision",
    }

    def complete_json(self, _stage, _payload, *, system_prompt=None):
        raise RuntimeError("sensitive-provider-error-body")


class AIProvenanceTest(unittest.TestCase):
    def test_records_hashes_latency_and_provider_reported_usage(self):
        recorder = AIProvenanceRecorder(
            StaticProvider(),
            embedding_provider="hash",
            embedding_model="hash-1024",
        )
        result = recorder.complete_json(
            "plan",
            {"customer_secret": "do-not-store", "brief": {"name": "campaign"}},
        )
        self.assertEqual(result, {"value": "generated"})

        snapshot = recorder.snapshot()
        self.assertEqual(snapshot["schema_version"], 1)
        self.assertEqual(snapshot["provider"], "test-provider")
        self.assertEqual(snapshot["model"], "test-model")
        self.assertEqual(snapshot["prompt_source"], "builtin")
        self.assertIsNone(snapshot["prompt_release_id"])
        self.assertEqual(snapshot["prompt_set_version"], PROMPT_SET_VERSION)
        self.assertEqual(snapshot["prompt_hashes"]["plan"], PROMPT_HASHES["plan"])
        self.assertEqual(recorder.provider.system_prompt, PROMPTS["plan"])
        self.assertEqual(snapshot["invocation_count"], 1)
        self.assertEqual(snapshot["successful_invocations"], 1)
        self.assertEqual(snapshot["token_usage"]["source"], "provider_reported")
        self.assertEqual(snapshot["token_usage"]["total_tokens"], 18)
        invocation = snapshot["invocations"][0]
        self.assertEqual(invocation["status"], "succeeded")
        self.assertEqual(len(invocation["input_sha256"]), 64)
        self.assertEqual(len(invocation["output_sha256"]), 64)
        self.assertGreater(invocation["input_bytes"], 0)
        self.assertGreaterEqual(invocation["latency_ms"], 0)
        serialized = json.dumps(snapshot, ensure_ascii=False)
        self.assertNotIn("do-not-store", serialized)
        self.assertNotIn("customer_secret", serialized)

    def test_failed_call_attaches_redacted_provenance(self):
        recorder = AIProvenanceRecorder(
            FailingProvider(),
            embedding_provider="hash",
            embedding_model="hash-1024",
        )
        with self.assertRaises(RuntimeError) as captured:
            recorder.complete_json(
                "generate",
                {"private_prompt": "never-persist-this"},
                platform="douyin",
            )

        snapshot = captured.exception.ai_provenance
        self.assertEqual(snapshot["failed_invocations"], 1)
        self.assertEqual(snapshot["successful_invocations"], 0)
        invocation = snapshot["invocations"][0]
        self.assertEqual(invocation["error_type"], "RuntimeError")
        self.assertEqual(invocation["platform"], "douyin")
        self.assertEqual(invocation["usage"]["source"], "provider_reported")
        self.assertEqual(invocation["usage"]["total_tokens"], 17)
        self.assertEqual(invocation["response_model"], "failing-model-revision")
        self.assertEqual(snapshot["token_usage"]["source"], "provider_reported")
        self.assertEqual(snapshot["token_usage"]["total_tokens"], 17)
        serialized = json.dumps(snapshot, ensure_ascii=False)
        self.assertNotIn("never-persist-this", serialized)
        self.assertNotIn("sensitive-provider-error-body", serialized)

    @patch("contentflow.providers.urllib.request.urlopen")
    def test_openai_compatible_captures_reported_usage(self, urlopen):
        response = MagicMock()
        response.read.return_value = json.dumps(
            {
                "id": "provider-request-123",
                "model": "provider-model-revision",
                "choices": [{"message": {"content": json.dumps({"passed": True})}}],
                "usage": {
                    "prompt_tokens": 23,
                    "completion_tokens": 5,
                    "total_tokens": 28,
                },
            }
        ).encode("utf-8")
        urlopen.return_value.__enter__.return_value = response
        provider = OpenAICompatibleProvider(
            api_base="https://provider.test/v1",
            api_key="not-recorded",
            model="configured-model",
            provider_name="provider-proxy",
        )
        provider.set_invocation_context("a" * 64)

        result = provider.complete_json(
            "review",
            {"content": "example"},
            system_prompt="approved custom review prompt",
        )
        request = urlopen.call_args.args[0]
        request_payload = json.loads(request.data.decode("utf-8"))

        self.assertEqual(result, {"passed": True})
        self.assertEqual(
            request_payload["messages"][0]["content"],
            "approved custom review prompt",
        )
        self.assertEqual(provider.provider_name, "provider-proxy")
        self.assertEqual(provider.model_name, "configured-model")
        self.assertEqual(request.get_header("Idempotency-key"), "a" * 64)
        self.assertEqual(
            provider.last_call_metadata,
            {
                "usage_source": "provider_reported",
                "idempotency_key_sent": True,
                "provider_request_id": "provider-request-123",
                "provider_request_id_source": "body.id",
                "input_tokens": 23,
                "output_tokens": 5,
                "total_tokens": 28,
                "response_model": "provider-model-revision",
            },
        )

    def test_text_provider_uses_configured_request_timeout(self):
        provider = build_text_provider(
            Settings(
                text_provider="openai-compatible",
                model_api_base="https://provider.test/v1",
                model_api_key="test-key",
                text_model="test-model",
                model_request_timeout_seconds=180,
            )
        )
        self.assertEqual(provider.timeout_seconds, 180)

    def test_prompt_hash_manifest_covers_every_template(self):
        self.assertEqual(set(PROMPT_HASHES), set(PROMPTS))
        self.assertTrue(PROMPT_SET_VERSION)
        self.assertTrue(all(len(value) == 64 for value in PROMPT_HASHES.values()))


if __name__ == "__main__":
    unittest.main()
