from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Iterable


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with closing(self.connect()) as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS knowledge_chunks (
                    chunk_id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    text TEXT NOT NULL,
                    vector_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    result_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS publish_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'ready_for_human_review'
                );
                """
            )
            connection.commit()

    def replace_chunks(
        self, chunks: Iterable[tuple[str, str, str, list[float]]]
    ) -> int:
        rows = [
            (chunk_id, source, text, json.dumps(vector, ensure_ascii=False))
            for chunk_id, source, text, vector in chunks
        ]
        with closing(self.connect()) as connection:
            connection.execute("DELETE FROM knowledge_chunks")
            connection.executemany(
                """
                INSERT INTO knowledge_chunks(chunk_id, source, text, vector_json)
                VALUES (?, ?, ?, ?)
                """,
                rows,
            )
            connection.commit()
        return len(rows)

    def read_chunks(self) -> list[dict[str, Any]]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                "SELECT chunk_id, source, text, vector_json FROM knowledge_chunks"
            ).fetchall()
        return [
            {
                "chunk_id": row["chunk_id"],
                "source": row["source"],
                "text": row["text"],
                "vector": json.loads(row["vector_json"]),
            }
            for row in rows
        ]

    def save_run(self, run_id: str, created_at: str, result: dict[str, Any]) -> None:
        with closing(self.connect()) as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO runs(run_id, created_at, result_json)
                VALUES (?, ?, ?)
                """,
                (run_id, created_at, json.dumps(result, ensure_ascii=False)),
            )
            connection.commit()

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT result_json FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return json.loads(row["result_json"]) if row else None

    def enqueue(self, run_id: str, platform: str, payload: dict[str, Any]) -> None:
        with closing(self.connect()) as connection:
            connection.execute(
                """
                INSERT INTO publish_queue(run_id, platform, payload_json)
                VALUES (?, ?, ?)
                """,
                (run_id, platform, json.dumps(payload, ensure_ascii=False)),
            )
            connection.commit()

    def queue_for_run(self, run_id: str) -> list[dict[str, Any]]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                """
                SELECT id, platform, payload_json, status
                FROM publish_queue
                WHERE run_id = ?
                ORDER BY id
                """,
                (run_id,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "platform": row["platform"],
                "payload": json.loads(row["payload_json"]),
                "status": row["status"],
            }
            for row in rows
        ]
