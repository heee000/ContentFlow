from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from .embeddings import build_embedding_provider
from .settings import Settings


MANIFEST_NAME = "contentflow-bge-m3-manifest.json"
PROBE_TEXTS = ["ContentFlow 固定版本向量缓存校验", "跨网络内容发布工作流"]


def _validate_vectors(vectors: list[list[float]], dimensions: int) -> list[float]:
    if len(vectors) != len(PROBE_TEXTS):
        raise RuntimeError("Embedding cache probe returned an unexpected vector count")
    norms: list[float] = []
    for vector in vectors:
        if len(vector) != dimensions or not all(math.isfinite(value) for value in vector):
            raise RuntimeError("Embedding cache probe returned an invalid vector")
        norm = math.sqrt(sum(value * value for value in vector))
        if not 0.99 <= norm <= 1.01:
            raise RuntimeError("BGE-M3 cache probe did not return normalized vectors")
        norms.append(norm)
    return norms


def prepare_or_verify(settings: Settings, *, offline: bool) -> Path:
    effective = settings.model_copy(
        update={
            "embedding_provider": "bge-m3-local",
            "local_embedding_offline": offline,
        }
    )
    effective.validate_runtime()
    provider = build_embedding_provider(effective)
    norms = _validate_vectors(
        provider.encode_many(PROBE_TEXTS),
        effective.embedding_dimensions,
    )
    cache_dir = effective.local_embedding_cache_dir.resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = cache_dir / MANIFEST_NAME
    expected = {
        "schema_version": 1,
        "model": effective.local_embedding_model,
        "revision": effective.local_embedding_revision,
        "dimensions": effective.embedding_dimensions,
    }
    if offline:
        if not manifest_path.is_file():
            raise RuntimeError("BGE-M3 cache manifest is missing; run prepare first")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for key, value in expected.items():
            if manifest.get(key) != value:
                raise RuntimeError(f"BGE-M3 cache manifest mismatch: {key}")
        return manifest_path

    manifest = {
        **expected,
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "probe_vector_norms": [round(norm, 8) for norm in norms],
    }
    temporary = manifest_path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(manifest_path)
    return manifest_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare or offline-verify the fixed-revision BGE-M3 cache."
    )
    parser.add_argument("mode", choices=("prepare", "verify"))
    args = parser.parse_args()
    settings = Settings(_env_file=None)
    manifest = prepare_or_verify(settings, offline=args.mode == "verify")
    print(f"BGE-M3 cache {args.mode} passed: {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
