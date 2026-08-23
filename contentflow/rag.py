from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path

from .models import RetrievedChunk
from .storage import Database


TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")


class HashEmbedding:
    """Dependency-free embedding used for reproducible local validation."""

    def __init__(self, dimensions: int = 256):
        self.dimensions = dimensions

    def encode(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        tokens = TOKEN_PATTERN.findall(text.lower())
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[bucket] += sign

        norm = math.sqrt(sum(value * value for value in vector))
        if norm:
            vector = [value / norm for value in vector]
        return vector

    def encode_many(self, texts: list[str]) -> list[list[float]]:
        return [self.encode(text) for text in texts]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


def chunk_markdown(path: Path) -> list[tuple[str, str, str]]:
    text = path.read_text(encoding="utf-8")
    blocks = [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]
    chunks: list[tuple[str, str, str]] = []
    heading = ""
    for index, block in enumerate(blocks):
        if block.startswith("#"):
            heading = block.lstrip("#").strip()
            continue
        content = f"{heading}\n{block}".strip()
        digest = hashlib.sha1(
            f"{path.name}:{index}:{content}".encode("utf-8")
        ).hexdigest()[:12]
        chunks.append((digest, path.name, content))
    return chunks


class KnowledgeIndex:
    def __init__(self, database: Database, embedder: HashEmbedding | None = None):
        self.database = database
        self.embedder = embedder or HashEmbedding()

    def rebuild(self, knowledge_dir: Path) -> int:
        prepared: list[tuple[str, str, str, list[float]]] = []
        for path in sorted(knowledge_dir.glob("*.md")):
            for chunk_id, source, text in chunk_markdown(path):
                prepared.append((chunk_id, source, text, self.embedder.encode(text)))
        if not prepared:
            raise ValueError(f"知识库目录中没有可用 Markdown: {knowledge_dir}")
        return self.database.replace_chunks(prepared)

    def search(self, query: str, limit: int = 4) -> list[RetrievedChunk]:
        query_vector = self.embedder.encode(query)
        scored = []
        for row in self.database.read_chunks():
            scored.append(
                RetrievedChunk(
                    chunk_id=row["chunk_id"],
                    source=row["source"],
                    text=row["text"],
                    score=cosine_similarity(query_vector, row["vector"]),
                )
            )
        return sorted(scored, key=lambda item: item.score, reverse=True)[:limit]
