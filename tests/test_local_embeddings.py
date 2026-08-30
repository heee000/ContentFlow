from __future__ import annotations

import math
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from contentflow.embeddings import (
    LocalBGEM3EmbeddingProvider,
    OpenAICompatibleEmbeddingProvider,
    _LocalSentenceTransformerRuntime,
    build_embedding_provider,
)
from contentflow.embedding_cache import MANIFEST_NAME, prepare_or_verify
from contentflow.settings import Settings


class _Vector:
    def __init__(self, values):
        self.values = values

    def tolist(self):
        return self.values


class _Model:
    def __init__(self, values):
        self.values = values
        self.calls = []

    def encode(self, text, **kwargs):
        self.calls.append((text, kwargs))
        values = [self.values for _ in text] if isinstance(text, list) else self.values
        return _Vector(values)


class LocalEmbeddingTest(unittest.TestCase):
    def test_loader_pins_revision_and_disables_remote_code(self):
        calls = []

        class FakeSentenceTransformer:
            def __init__(self, model_name, **kwargs):
                calls.append((model_name, kwargs))

        module = SimpleNamespace(SentenceTransformer=FakeSentenceTransformer)
        with patch.dict(sys.modules, {"sentence_transformers": module}):
            runtime = _LocalSentenceTransformerRuntime(
                model_name="BAAI/bge-m3",
                revision="a" * 40,
                device="cpu",
                cache_dir="model-cache",
                local_files_only=True,
            )
        self.assertIsInstance(runtime.lock, type(threading.Lock()))
        self.assertEqual(calls[0][0], "BAAI/bge-m3")
        self.assertEqual(calls[0][1]["revision"], "a" * 40)
        self.assertEqual(calls[0][1]["device"], "cpu")
        self.assertFalse(calls[0][1]["trust_remote_code"])
        self.assertTrue(calls[0][1]["local_files_only"])

    def test_provider_is_lazy_and_requests_normalized_dense_vector(self):
        model = _Model([1.0] + [0.0] * 1023)
        runtime = SimpleNamespace(model=model, lock=threading.Lock())
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = LocalBGEM3EmbeddingProvider(
                model_name="BAAI/bge-m3",
                revision="b" * 40,
                device="cpu",
                cache_dir=Path(temp_dir),
                local_files_only=False,
            )
            with patch(
                "contentflow.embeddings._load_local_sentence_transformer",
                return_value=runtime,
            ) as loader:
                vector = provider.encode("真实中文语义检索")
        self.assertEqual(len(vector), 1024)
        loader.assert_called_once()
        self.assertEqual(model.calls[0][0], ["真实中文语义检索"])
        self.assertEqual(
            model.calls[0][1],
            {
                "batch_size": 8,
                "normalize_embeddings": True,
                "convert_to_numpy": True,
                "show_progress_bar": False,
            },
        )

    def test_provider_batches_multiple_texts_in_one_model_call(self):
        model = _Model([1.0] + [0.0] * 1023)
        runtime = SimpleNamespace(model=model, lock=threading.Lock())
        provider = LocalBGEM3EmbeddingProvider(
            model_name="BAAI/bge-m3",
            revision="d" * 40,
            device="cpu",
            cache_dir=Path(".contentflow/models"),
            local_files_only=True,
            batch_size=4,
        )
        with patch(
            "contentflow.embeddings._load_local_sentence_transformer",
            return_value=runtime,
        ):
            vectors = provider.encode_many(["第一段", "第二段"])
        self.assertEqual(len(vectors), 2)
        self.assertEqual(model.calls[0][0], ["第一段", "第二段"])
        self.assertEqual(model.calls[0][1]["batch_size"], 4)

    def test_provider_rejects_wrong_dimensions_and_non_finite_values(self):
        for values, message in (
            ([0.0] * 8, "维度不匹配"),
            ([math.nan] + [0.0] * 1023, "非有限"),
        ):
            with self.subTest(message=message):
                runtime = SimpleNamespace(
                    model=_Model(values),
                    lock=threading.Lock(),
                )
                provider = LocalBGEM3EmbeddingProvider(
                    model_name="BAAI/bge-m3",
                    revision="c" * 40,
                    device="cpu",
                    cache_dir=Path(".contentflow/models"),
                    local_files_only=True,
                )
                with (
                    patch(
                        "contentflow.embeddings._load_local_sentence_transformer",
                        return_value=runtime,
                    ),
                    self.assertRaisesRegex(RuntimeError, message),
                ):
                    provider.encode("test")

    def test_real_local_stack_is_accepted_without_mock_override(self):
        settings = Settings(
            environment="production",
            database_url="postgresql+psycopg://user:pass@db/contentflow",
            secret_key="s" * 32,
            credential_encryption_key="c" * 32,
            storage_backend="s3",
            s3_endpoint_url="https://objects.example",
            s3_access_key="access",
            s3_secret_key="secret",
            require_governed_prompts=True,
            metrics_enabled=True,
            metrics_bearer_token="m" * 32,
            allow_mock_providers=False,
            text_provider="openai-compatible",
            model_api_base="https://models.example/v1",
            model_api_key="model-key",
            text_model="text-model",
            embedding_provider="bge-m3-local",
            image_provider="manual",
            video_provider="manual",
        )
        settings.validate_runtime()
        provider = build_embedding_provider(settings)
        self.assertIsInstance(provider, LocalBGEM3EmbeddingProvider)
        self.assertEqual(provider.dimensions, 1024)

    def test_local_model_revision_and_device_are_fail_closed(self):
        for overrides in (
            {"local_embedding_revision": "main"},
            {"local_embedding_device": "cuda:any"},
            {"local_embedding_model": "some/other-model"},
            {"embedding_dimensions": 768},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                Settings(
                    database_url="sqlite:///test.db",
                    embedding_provider="bge-m3-local",
                    **overrides,
                ).validate_runtime()

    def test_openai_compatible_provider_prefers_independent_embedding_api(self):
        settings = Settings(
            database_url="sqlite:///test.db",
            embedding_provider="openai-compatible",
            model_api_base="https://text.example/v1",
            model_api_key="text-key",
            embedding_api_base="https://embeddings.example/v1",
            embedding_api_key="embedding-key",
            embedding_model="embedding-model",
        )
        settings.validate_runtime()
        provider = build_embedding_provider(settings)
        self.assertIsInstance(provider, OpenAICompatibleEmbeddingProvider)
        self.assertEqual(provider.endpoint, "https://embeddings.example/v1/embeddings")
        self.assertEqual(provider.api_key, "embedding-key")

    def test_cache_prepare_writes_pinned_manifest_and_offline_verify_reads_it(self):
        class FakeProvider:
            def encode_many(self, texts):
                return [[1.0] + [0.0] * 1023 for _ in texts]

        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings(
                database_url="sqlite:///test.db",
                embedding_provider="bge-m3-local",
                local_embedding_cache_dir=Path(temp_dir),
            )
            with patch(
                "contentflow.embedding_cache.build_embedding_provider",
                return_value=FakeProvider(),
            ):
                prepared = prepare_or_verify(settings, offline=False)
                verified = prepare_or_verify(settings, offline=True)
            self.assertEqual(prepared, Path(temp_dir).resolve() / MANIFEST_NAME)
            self.assertEqual(verified, prepared)
            manifest = prepared.read_text(encoding="utf-8")
            self.assertIn(settings.local_embedding_revision, manifest)
            self.assertIn('"dimensions": 1024', manifest)


if __name__ == "__main__":
    unittest.main()
