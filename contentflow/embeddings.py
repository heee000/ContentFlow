from __future__ import annotations

from typing import Protocol

import httpx

from .rag import HashEmbedding
from .settings import Settings


class EmbeddingProvider(Protocol):
    dimensions: int
    model_name: str

    def encode(self, text: str) -> list[float]: ...


class HashEmbeddingProvider:
    def __init__(self, dimensions: int):
        self.dimensions = dimensions
        self.model_name = f"hash-{dimensions}"
        self._embedder = HashEmbedding(dimensions=dimensions)

    def encode(self, text: str) -> list[float]:
        return self._embedder.encode(text)


class OpenAICompatibleEmbeddingProvider:
    def __init__(
        self,
        *,
        api_base: str,
        api_key: str,
        model: str,
        dimensions: int,
        client: httpx.Client | None = None,
    ):
        self.endpoint = f"{api_base.rstrip('/')}/embeddings"
        self.api_key = api_key
        self.model_name = model
        self.dimensions = dimensions
        self.client = client or httpx.Client(timeout=60)

    def encode(self, text: str) -> list[float]:
        response = self.client.post(
            self.endpoint,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model_name,
                "input": text,
                "dimensions": self.dimensions,
                "encoding_format": "float",
            },
        )
        response.raise_for_status()
        body = response.json()
        try:
            vector = body["data"][0]["embedding"]
        except (KeyError, IndexError, TypeError) as error:
            raise RuntimeError(f"Embedding 响应结构错误: {body}") from error
        if len(vector) != self.dimensions:
            raise RuntimeError(
                f"Embedding 维度不匹配: 预期 {self.dimensions}，实际 {len(vector)}"
            )
        return [float(value) for value in vector]


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    if settings.embedding_provider == "hash":
        return HashEmbeddingProvider(settings.embedding_dimensions)
    if settings.embedding_provider == "openai-compatible":
        if (
            not settings.model_api_base
            or not settings.model_api_key
            or not settings.embedding_model
        ):
            raise ValueError("OpenAI 兼容 Embedding 缺少 API Base、API Key 或模型名")
        return OpenAICompatibleEmbeddingProvider(
            api_base=settings.model_api_base,
            api_key=settings.model_api_key,
            model=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
        )
    raise ValueError(f"不支持的 Embedding provider: {settings.embedding_provider}")
