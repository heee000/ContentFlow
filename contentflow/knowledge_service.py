from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from .entities import KnowledgeChunk, KnowledgeDocument
from .embeddings import EmbeddingProvider
from .models import RetrievedChunk
from .object_storage import ObjectStorage
from .rag import HashEmbedding, cosine_similarity


def local_path_from_uri(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        raise ValueError("当前本地索引器只能读取 file:// 对象")
    raw_path = url2pathname(unquote(parsed.path))
    if re.match(r"^/[A-Za-z]:/", raw_path):
        raw_path = raw_path[1:]
    return Path(raw_path)


def split_text(text: str, max_chars: int = 900, overlap: int = 120) -> list[str]:
    blocks = [
        block.strip()
        for block in re.split(r"\n\s*\n|(?<=[。！？!?])\s*", text)
        if block.strip()
    ]
    chunks: list[str] = []
    current = ""
    for block in blocks:
        if len(current) + len(block) + 1 <= max_chars:
            current = f"{current}\n{block}".strip()
            continue
        if current:
            chunks.append(current)
        if len(block) <= max_chars:
            current = block
            continue
        start = 0
        step = max(1, max_chars - overlap)
        while start < len(block):
            chunks.append(block[start : start + max_chars])
            start += step
        current = ""
    if current:
        chunks.append(current)
    return chunks


def index_document(
    session: Session,
    document: KnowledgeDocument,
    embedder: EmbeddingProvider | HashEmbedding | None = None,
    storage: ObjectStorage | None = None,
) -> int:
    if not document.storage_uri:
        raise ValueError("知识文档没有可读取的存储地址")
    if storage is not None:
        raw = storage.read(document.storage_uri, max_bytes=20 * 1024 * 1024)
    else:
        path = local_path_from_uri(document.storage_uri)
        raw = path.read_bytes()
    try:
        document_text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        document_text = raw.decode("gb18030")
    chunks = split_text(document_text)
    if not chunks:
        raise ValueError("文档没有可索引文本")

    embedder = embedder or HashEmbedding()
    encode_many = getattr(embedder, "encode_many", None)
    embeddings = (
        encode_many(chunks)
        if callable(encode_many)
        else [embedder.encode(chunk) for chunk in chunks]
    )
    if len(embeddings) != len(chunks):
        raise RuntimeError("Embedding provider 返回的向量数量与知识分块不一致")
    session.execute(
        delete(KnowledgeChunk).where(KnowledgeChunk.document_id == document.id)
    )
    for index, (chunk, embedding) in enumerate(zip(chunks, embeddings, strict=True)):
        session.add(
            KnowledgeChunk(
                workspace_id=document.workspace_id,
                document_id=document.id,
                chunk_index=index,
                text=chunk,
                embedding=embedding,
                embedding_model=getattr(
                    embedder,
                    "model_name",
                    f"hash-{embedder.dimensions}",
                ),
                metadata_json={"source": document.name},
            )
        )
    session.flush()
    if session.bind and session.bind.dialect.name == "postgresql":
        for chunk in session.scalars(
            select(KnowledgeChunk).where(KnowledgeChunk.document_id == document.id)
        ):
            vector_literal = (
                "[" + ",".join(f"{value:.9g}" for value in chunk.embedding) + "]"
            )
            session.execute(
                text(
                    """
                    INSERT INTO knowledge_vectors (chunk_id, embedding)
                    VALUES (:chunk_id, CAST(:embedding AS vector))
                    ON CONFLICT (chunk_id)
                    DO UPDATE SET embedding = EXCLUDED.embedding
                    """
                ),
                {"chunk_id": chunk.id, "embedding": vector_literal},
            )
    document.status = "indexed"
    document.metadata_json = {
        **(document.metadata_json or {}),
        "chunk_count": len(chunks),
    }
    session.flush()
    return len(chunks)


def search_workspace_knowledge(
    session: Session,
    *,
    workspace_id: str,
    query: str,
    limit: int = 6,
    embedder: EmbeddingProvider | HashEmbedding | None = None,
) -> list[RetrievedChunk]:
    embedder = embedder or HashEmbedding()
    query_vector = embedder.encode(query)
    if session.bind and session.bind.dialect.name == "postgresql":
        vector_literal = "[" + ",".join(f"{value:.9g}" for value in query_vector) + "]"
        rows = session.execute(
            text(
                """
                SELECT
                    kc.id,
                    kc.text,
                    kc.document_id,
                    kc.metadata_json,
                    1 - (kv.embedding <=> CAST(:embedding AS vector)) AS score
                FROM knowledge_vectors kv
                JOIN knowledge_chunks_v2 kc ON kc.id = kv.chunk_id
                WHERE kc.workspace_id = :workspace_id
                ORDER BY kv.embedding <=> CAST(:embedding AS vector)
                LIMIT :limit
                """
            ),
            {
                "workspace_id": workspace_id,
                "embedding": vector_literal,
                "limit": limit,
            },
        ).mappings()
        return [
            RetrievedChunk(
                chunk_id=row["id"],
                source=str(
                    (row["metadata_json"] or {}).get("source") or row["document_id"]
                ),
                text=row["text"],
                score=float(row["score"]),
            )
            for row in rows
        ]
    chunks = list(
        session.scalars(
            select(KnowledgeChunk).where(KnowledgeChunk.workspace_id == workspace_id)
        )
    )
    scored = [
        RetrievedChunk(
            chunk_id=chunk.id,
            source=str((chunk.metadata_json or {}).get("source") or chunk.document_id),
            text=chunk.text,
            score=cosine_similarity(query_vector, chunk.embedding),
        )
        for chunk in chunks
    ]
    return sorted(scored, key=lambda item: item.score, reverse=True)[:limit]
