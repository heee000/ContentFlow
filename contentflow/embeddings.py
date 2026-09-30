from __future__ import annotations

import math
import json
import threading
from functools import lru_cache
from pathlib import Path
from typing import Protocol

import httpx

from .providers import PROVIDER_REQUEST_KEY, _provider_request_metadata
from .rag import HashEmbedding
from .settings import Settings
from .execution_fence import assert_execution_active
from .provider_resources import ProviderResourceLimitError, ProviderResourceLimits


class EmbeddingProvider(Protocol):
    dimensions: int
    model_name: str

    def encode(self, text: str) -> list[float]: ...

    def encode_many(self, texts: list[str]) -> list[list[float]]: ...


class HashEmbeddingProvider:
    def __init__(self, dimensions: int):
        self.dimensions = dimensions
        self.model_name = f"hash-{dimensions}"
        self._embedder = HashEmbedding(dimensions=dimensions)

    def encode(self, text: str) -> list[float]:
        return self._embedder.encode(text)

    def encode_many(self, texts: list[str]) -> list[list[float]]:
        return [self.encode(text) for text in texts]


class _LocalSentenceTransformerRuntime:
    def __init__(
        self,
        *,
        model_name: str,
        revision: str,
        device: str,
        cache_dir: str,
        local_files_only: bool,
    ):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as error:
            raise RuntimeError(
                "本地 BGE-M3 需要安装 local-embeddings 可选依赖"
            ) from error

        selected_device = None if device == "auto" else device
        self.model = SentenceTransformer(
            model_name,
            revision=revision,
            device=selected_device,
            cache_folder=cache_dir,
            trust_remote_code=False,
            local_files_only=local_files_only,
        )
        self.lock = threading.Lock()


@lru_cache(maxsize=4)
def _load_local_sentence_transformer(
    model_name: str,
    revision: str,
    device: str,
    cache_dir: str,
    local_files_only: bool,
) -> _LocalSentenceTransformerRuntime:
    return _LocalSentenceTransformerRuntime(
        model_name=model_name,
        revision=revision,
        device=device,
        cache_dir=cache_dir,
        local_files_only=local_files_only,
    )


class LocalBGEM3EmbeddingProvider:
    """Lazy, process-cached BGE-M3 dense embeddings for local inference."""

    dimensions = 1024

    def __init__(
        self,
        *,
        model_name: str,
        revision: str,
        device: str,
        cache_dir: Path,
        local_files_only: bool,
        batch_size: int = 8,
    ):
        self.model_name = f"{model_name}@{revision}"
        self._model_id = model_name
        self._revision = revision
        self._device = device
        self._cache_dir = str(cache_dir.resolve())
        self._local_files_only = local_files_only
        self._batch_size = batch_size

    def encode(self, text: str) -> list[float]:
        return self.encode_many([text])[0]

    def encode_many(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError("Embedding 输入必须是非空文本")
        runtime = _load_local_sentence_transformer(
            self._model_id,
            self._revision,
            self._device,
            self._cache_dir,
            self._local_files_only,
        )
        with runtime.lock:
            encoded = runtime.model.encode(
                texts,
                batch_size=self._batch_size,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        try:
            vectors = [
                [float(value) for value in vector] for vector in encoded.tolist()
            ]
        except (AttributeError, TypeError, ValueError) as error:
            raise RuntimeError("本地 BGE-M3 返回了无效向量") from error
        if len(vectors) != len(texts):
            raise RuntimeError(
                f"Embedding 数量不匹配: 预期 {len(texts)}，实际 {len(vectors)}"
            )
        for vector in vectors:
            if len(vector) != self.dimensions:
                raise RuntimeError(
                    f"Embedding 维度不匹配: 预期 {self.dimensions}，实际 {len(vector)}"
                )
            if not all(math.isfinite(value) for value in vector):
                raise RuntimeError("本地 BGE-M3 返回了非有限数值")
        return vectors


class OpenAICompatibleEmbeddingProvider:
    def __init__(
        self,
        *,
        api_base: str,
        api_key: str,
        model: str,
        dimensions: int,
        send_dimensions: bool = True,
        client: httpx.Client | None = None,
        max_batch_size: int = 32,
        max_text_chars: int = 8192,
        max_request_bytes: int = 256 * 1024,
        max_response_bytes: int = 8 * 1024 * 1024,
    ):
        self.endpoint = f"{api_base.rstrip('/')}/embeddings"
        self.api_key = api_key
        self.model_name = model
        self.dimensions = dimensions
        self.send_dimensions = send_dimensions
        self.client = client or httpx.Client(timeout=60)
        if any(type(value) is not int or value < 1 for value in
               (dimensions, max_batch_size, max_text_chars, max_request_bytes, max_response_bytes)):
            raise ValueError("Embedding limits must be positive integers")
        self.max_batch_size, self.max_text_chars = max_batch_size, max_text_chars
        self.max_request_bytes, self.max_response_bytes = max_request_bytes, max_response_bytes
        self.last_call_metadata: dict[str, object] = {"usage_source": "not_reported"}
        self._invocation_key: str | None = None

    def set_invocation_context(self, request_key: str) -> bool:
        if not PROVIDER_REQUEST_KEY.fullmatch(request_key):
            raise ValueError("Provider invocation request key is invalid")
        self._invocation_key = request_key
        return True

    def encode(self, text: str) -> list[float]:
        return self.encode_many([text])[0]

    def _payload(self, texts):
        result = {"model": self.model_name, "input": texts, "encoding_format": "float"}
        if self.send_dimensions:
            result["dimensions"] = self.dimensions
        return result

    def validate_inputs(self, texts):
        if (len(texts) > self.max_batch_size or any(type(text) is not str
                or not text.strip() or len(text) > self.max_text_chars for text in texts)):
            raise ProviderResourceLimitError("embedding_batch_limit")
        if len(json.dumps(self._payload(texts), ensure_ascii=False).encode("utf-8")) > self.max_request_bytes:
            raise ProviderResourceLimitError("provider_request_too_large")

    def encode_many(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        self.validate_inputs(texts)
        invocation_key = self._invocation_key
        self._invocation_key = None
        self.last_call_metadata = {
            "usage_source": "not_reported",
            "idempotency_key_sent": invocation_key is not None,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept-Encoding": "identity",
        }
        if invocation_key is not None:
            headers["Idempotency-Key"] = invocation_key
        payload = self._payload(texts)
        try:
            assert_execution_active()
            with self.client.stream("POST", self.endpoint, headers=headers, json=payload) as response:
                self.last_call_metadata.update(_provider_request_metadata(headers=response.headers))
                response.raise_for_status()
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise RuntimeError("Embedding compressed response is not supported")
                data = bytearray()
                for chunk in response.iter_bytes(chunk_size=64 * 1024):
                    if len(data) + len(chunk) > self.max_response_bytes:
                        raise ProviderResourceLimitError("provider_response_too_large")
                    data.extend(chunk)
                body = json.loads(data)
        except httpx.HTTPStatusError as error:
            raise RuntimeError(
                f"Embedding 调用失败 (HTTP {error.response.status_code})"
            ) from error
        except httpx.RequestError as error:
            raise RuntimeError("Embedding 调用失败 (network_error)") from error
        except ValueError as error:
            raise RuntimeError("Embedding 响应不是有效 JSON") from error
        self.last_call_metadata.update(_provider_request_metadata(body=body))
        if isinstance(body, dict) and isinstance(body.get("model"), str):
            self.last_call_metadata["response_model"] = body["model"][:160]
        usage = body.get("usage") if isinstance(body, dict) else None
        if isinstance(usage, dict):
            input_tokens = usage.get("prompt_tokens", usage.get("input_tokens"))
            total_tokens = usage.get("total_tokens")
            if any(
                isinstance(value, int) and not isinstance(value, bool)
                for value in (input_tokens, total_tokens)
            ):
                self.last_call_metadata.update(
                    {
                        "usage_source": "provider_reported",
                        "input_tokens": input_tokens,
                        "output_tokens": None,
                        "total_tokens": total_tokens,
                    }
                )
        try:
            items = body["data"]
            if (not isinstance(items, list) or len(items) != len(texts)
                    or any(not isinstance(item, dict) or type(item.get("index")) is not int for item in items)
                    or {item["index"] for item in items} != set(range(len(texts)))):
                raise ValueError("invalid indices")
            items = sorted(items, key=lambda item: item["index"])
            vectors = [item["embedding"] for item in items]
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise RuntimeError("Embedding 响应结构错误") from error
        if len(vectors) != len(texts):
            raise RuntimeError(
                f"Embedding 数量不匹配: 预期 {len(texts)}，实际 {len(vectors)}"
            )
        normalized: list[list[float]] = []
        for vector in vectors:
            if not isinstance(vector, list) or len(vector) != self.dimensions:
                raise RuntimeError(
                    "Embedding 维度不匹配"
                )
            if any(type(value) not in {int, float} for value in vector):
                raise RuntimeError("Embedding 响应包含非数值元素")
            try:
                normalized_vector = [float(value) for value in vector]
            except OverflowError as error:
                raise RuntimeError("Embedding 响应包含非有限数值") from error
            if not all(math.isfinite(value) for value in normalized_vector):
                raise RuntimeError("Embedding 响应包含非有限数值")
            normalized.append(normalized_vector)
        return normalized


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    if settings.embedding_provider == "hash":
        return HashEmbeddingProvider(settings.embedding_dimensions)
    if settings.embedding_provider == "openai-compatible":
        if (
            not settings.resolved_embedding_api_base
            or not settings.resolved_embedding_api_key
            or not settings.embedding_model
        ):
            raise ValueError("OpenAI 兼容 Embedding 缺少 API Base、API Key 或模型名")
        result = OpenAICompatibleEmbeddingProvider(
            api_base=settings.resolved_embedding_api_base,
            api_key=settings.resolved_embedding_api_key,
            model=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
            send_dimensions=settings.embedding_send_dimensions,
            max_batch_size=settings.embedding_api_batch_size,
            max_text_chars=settings.embedding_max_text_chars,
            max_request_bytes=settings.embedding_max_request_bytes,
            max_response_bytes=settings.embedding_max_response_bytes,
        )
        result.resource_limits = ProviderResourceLimits.from_settings(settings)
        return result
    if settings.embedding_provider == "bge-m3-local":
        return LocalBGEM3EmbeddingProvider(
            model_name=settings.local_embedding_model,
            revision=settings.local_embedding_revision,
            device=settings.local_embedding_device,
            cache_dir=settings.local_embedding_cache_dir,
            local_files_only=settings.local_embedding_offline,
            batch_size=settings.local_embedding_batch_size,
        )
    raise ValueError(f"不支持的 Embedding provider: {settings.embedding_provider}")
