from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from dataclasses import asdict

from .object_storage import S3ObjectStorage, StoredObject
from .settings import Settings


class RepeatingByteStream:
    def __init__(self, size: int, value: int):
        self.remaining = size
        self.value = value

    def read(self, size: int = -1) -> bytes:
        if self.remaining <= 0:
            return b""
        count = self.remaining if size < 0 else min(self.remaining, size)
        self.remaining -= count
        return bytes((self.value,)) * count


def _expected_digest(size: int, value: int) -> str:
    digest = hashlib.sha256()
    chunk = bytes((value,)) * (1024 * 1024)
    remaining = size
    while remaining:
        current = chunk[: min(len(chunk), remaining)]
        digest.update(current)
        remaining -= len(current)
    return digest.hexdigest()


def _verify_case(
    storage: S3ObjectStorage,
    *,
    workspace_id: str,
    name: str,
    size: int,
    value: int,
) -> StoredObject:
    stored: StoredObject | None = None
    try:
        stored = storage.put(
            workspace_id=workspace_id,
            category="s3-conformance",
            filename=f"{name}.bin",
            stream=RepeatingByteStream(size, value),
            content_type="application/octet-stream",
        )
        expected = _expected_digest(size, value)
        if stored.size_bytes != size or stored.checksum != expected:
            raise RuntimeError(f"{name} upload metadata did not match the source")
        key = stored.uri.split(f"s3://{storage.bucket}/", 1)[1]
        head = storage.client.head_object(Bucket=storage.bucket, Key=key)
        if (head.get("Metadata") or {}).get("sha256") != expected:
            raise RuntimeError(f"{name} object is missing SHA-256 metadata")
        downloaded = storage.read(stored.uri, max_bytes=size)
        if (
            len(downloaded) != size
            or hashlib.sha256(downloaded).hexdigest() != expected
        ):
            raise RuntimeError(f"{name} download failed integrity verification")
        return stored
    except Exception:
        if stored is not None:
            storage.delete(stored.uri)
        raise


def run_conformance(
    storage: S3ObjectStorage,
    *,
    include_boundary: bool,
) -> list[dict]:
    storage.check()
    workspace_id = f"contentflow-conformance-{uuid.uuid4().hex}"
    cases = [
        ("single-part", 256 * 1024, 0x31),
        ("multipart", 9 * 1024 * 1024, 0x72),
    ]
    if include_boundary:
        cases.append(("max-boundary", storage.max_upload_bytes, 0xA5))
    created: list[StoredObject] = []
    results: list[dict] = []
    try:
        for name, size, value in cases:
            stored = _verify_case(
                storage,
                workspace_id=workspace_id,
                name=name,
                size=size,
                value=value,
            )
            created.append(stored)
            results.append({"case": name, **asdict(stored)})
    finally:
        for stored in reversed(created):
            storage.delete(stored.uri)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate an S3-compatible bucket using unique, exactly deleted test objects."
        )
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Skip the configured maximum-upload boundary case.",
    )
    args = parser.parse_args()
    settings = Settings(
        _env_file=None,
        environment="development",
        storage_backend="s3",
    )
    settings.validate_runtime()
    results = run_conformance(
        S3ObjectStorage(settings),
        include_boundary=not args.quick,
    )
    print(
        json.dumps(
            {
                "status": "passed",
                "bucket": settings.s3_bucket,
                "cases": results,
                "cleanup": "exact test objects deleted",
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
