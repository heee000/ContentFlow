from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from io import BytesIO
from typing import Any

import pytest

from contentflow.object_storage import S3ObjectStorage
from contentflow.settings import Settings


TEST_S3_ENDPOINT_URL = os.getenv("CONTENTFLOW_TEST_S3_ENDPOINT_URL")
TEST_S3_ACCESS_KEY = os.getenv("CONTENTFLOW_TEST_S3_ACCESS_KEY")
TEST_S3_SECRET_KEY = os.getenv("CONTENTFLOW_TEST_S3_SECRET_KEY")

pytestmark = pytest.mark.skipif(
    not all(
        (
            TEST_S3_ENDPOINT_URL,
            TEST_S3_ACCESS_KEY,
            TEST_S3_SECRET_KEY,
        )
    ),
    reason="ContentFlow S3 integration test settings are required",
)


@dataclass(frozen=True)
class MinioHarness:
    storage: S3ObjectStorage
    client: Any
    bucket: str


def _delete_all_bucket_objects(client, bucket: str) -> None:
    version_paginator = client.get_paginator("list_object_versions")
    for page in version_paginator.paginate(Bucket=bucket):
        objects = [
            {"Key": item["Key"], "VersionId": item["VersionId"]}
            for item in [
                *(page.get("Versions") or []),
                *(page.get("DeleteMarkers") or []),
            ]
        ]
        if objects:
            client.delete_objects(
                Bucket=bucket,
                Delete={"Objects": objects, "Quiet": True},
            )

    object_paginator = client.get_paginator("list_objects_v2")
    for page in object_paginator.paginate(Bucket=bucket):
        objects = [{"Key": item["Key"]} for item in page.get("Contents") or []]
        if objects:
            client.delete_objects(
                Bucket=bucket,
                Delete={"Objects": objects, "Quiet": True},
            )


@pytest.fixture(scope="module")
def minio_harness():
    bucket = f"contentflow-test-{uuid.uuid4().hex}"
    settings = Settings(
        environment="test",
        secret_key="minio-integration-test-secret-key",
        storage_backend="s3",
        max_upload_bytes=64,
        publish_evidence_max_bytes=64,
        s3_endpoint_url=TEST_S3_ENDPOINT_URL,
        s3_region="us-east-1",
        s3_bucket=bucket,
        s3_access_key=TEST_S3_ACCESS_KEY,
        s3_secret_key=TEST_S3_SECRET_KEY,
        text_provider="mock",
        embedding_provider="hash",
        image_provider="mock",
        video_provider="mock",
    )
    storage = S3ObjectStorage(settings)
    bucket_created = False
    try:
        storage.client.create_bucket(Bucket=bucket)
        bucket_created = True
        storage.check()
        yield MinioHarness(
            storage=storage,
            client=storage.client,
            bucket=bucket,
        )
    finally:
        if bucket_created:
            _delete_all_bucket_objects(storage.client, bucket)
            storage.client.delete_bucket(Bucket=bucket)


def _key_from_uri(bucket: str, uri: str) -> str:
    prefix = f"s3://{bucket}/"
    assert uri.startswith(prefix)
    return uri[len(prefix) :]


def test_minio_round_trip_metadata_boundaries_and_size_limit(
    minio_harness: MinioHarness,
):
    payload = "ContentFlow MinIO 完整性证据".encode()
    stored = minio_harness.storage.put(
        workspace_id="workspace-minio",
        category="knowledge",
        filename="../evidence.txt",
        stream=BytesIO(payload),
        content_type="text/plain; charset=utf-8",
    )
    key = _key_from_uri(minio_harness.bucket, stored.uri)
    head = minio_harness.client.head_object(
        Bucket=minio_harness.bucket,
        Key=key,
    )

    assert stored.checksum == hashlib.sha256(payload).hexdigest()
    assert stored.size_bytes == len(payload)
    assert head["Metadata"]["sha256"] == stored.checksum
    assert head["ContentType"] == "text/plain; charset=utf-8"
    assert minio_harness.storage.read(stored.uri) == payload

    with pytest.raises(ValueError, match="不属于当前 bucket"):
        minio_harness.storage.read("s3://another-bucket/object")
    with pytest.raises(ValueError, match="超过读取大小限制"):
        minio_harness.storage.read(stored.uri, max_bytes=len(payload) - 1)

    before = minio_harness.client.list_objects_v2(Bucket=minio_harness.bucket).get(
        "KeyCount", 0
    )
    with pytest.raises(ValueError, match="exceeds"):
        minio_harness.storage.put(
            workspace_id="workspace-minio",
            category="knowledge",
            filename="oversized.bin",
            stream=BytesIO(b"x" * 65),
        )
    after = minio_harness.client.list_objects_v2(Bucket=minio_harness.bucket).get(
        "KeyCount", 0
    )
    assert after == before
    disposable = minio_harness.storage.put(
        workspace_id="workspace-minio",
        category="evidence",
        filename="delete-me.bin",
        stream=BytesIO(b"delete-me"),
    )
    disposable_key = _key_from_uri(minio_harness.bucket, disposable.uri)
    minio_harness.storage.delete(disposable.uri)
    assert (
        minio_harness.client.list_objects_v2(
            Bucket=minio_harness.bucket, Prefix=disposable_key
        ).get("KeyCount", 0)
        == 0
    )


def test_minio_detects_corruption_and_reads_legacy_checksum_keys(
    minio_harness: MinioHarness,
):
    original = b"contentflow-original-object"
    stored = minio_harness.storage.put(
        workspace_id="workspace-minio",
        category="assets",
        filename="asset.bin",
        stream=BytesIO(original),
    )
    key = _key_from_uri(minio_harness.bucket, stored.uri)
    minio_harness.client.put_object(
        Bucket=minio_harness.bucket,
        Key=key,
        Body=b"tampered-object",
        Metadata={"sha256": stored.checksum},
    )
    with pytest.raises(ValueError, match="完整性校验失败"):
        minio_harness.storage.read(stored.uri)

    legacy_payload = b"legacy-contentflow-object"
    legacy_checksum = hashlib.sha256(legacy_payload).hexdigest()
    legacy_key = f"workspace-minio/legacy/{legacy_checksum[:16]}-legacy.bin"
    minio_harness.client.put_object(
        Bucket=minio_harness.bucket,
        Key=legacy_key,
        Body=legacy_payload,
    )
    assert (
        minio_harness.storage.read(f"s3://{minio_harness.bucket}/{legacy_key}")
        == legacy_payload
    )
