from __future__ import annotations

import hashlib
import unittest

from contentflow.object_storage import StoredObject
from contentflow.s3_conformance import RepeatingByteStream, run_conformance


class _FakeS3Client:
    def __init__(self, storage):
        self.storage = storage

    def head_object(self, *, Bucket, Key):
        assert Bucket == self.storage.bucket
        payload, checksum = self.storage.objects[Key]
        if self.storage.corrupt_metadata:
            checksum = "0" * 64
        return {
            "ContentLength": len(payload),
            "Metadata": {"sha256": checksum},
        }


class _FakeStorage:
    bucket = "conformance-test"
    max_upload_bytes = 100 * 1024 * 1024

    def __init__(self):
        self.objects = {}
        self.deleted = []
        self.checked = False
        self.corrupt_metadata = False
        self.client = _FakeS3Client(self)

    def check(self):
        self.checked = True

    def put(self, *, workspace_id, category, filename, stream, content_type):
        payload = bytearray()
        while chunk := stream.read(1024 * 1024):
            payload.extend(chunk)
        checksum = hashlib.sha256(payload).hexdigest()
        key = f"{workspace_id}/{category}/{checksum[:16]}-{filename}"
        self.objects[key] = (bytes(payload), checksum)
        return StoredObject(
            uri=f"s3://{self.bucket}/{key}",
            checksum=checksum,
            size_bytes=len(payload),
            mime_type=content_type,
        )

    def read(self, uri, *, max_bytes):
        key = uri.split(f"s3://{self.bucket}/", 1)[1]
        payload = self.objects[key][0]
        if len(payload) > max_bytes:
            raise ValueError("too large")
        return payload

    def delete(self, uri):
        key = uri.split(f"s3://{self.bucket}/", 1)[1]
        self.deleted.append(key)
        self.objects.pop(key)


class S3ConformanceTest(unittest.TestCase):
    def test_repeating_stream_is_bounded(self):
        stream = RepeatingByteStream(5, 0x41)
        self.assertEqual(stream.read(2), b"AA")
        self.assertEqual(stream.read(10), b"AAA")
        self.assertEqual(stream.read(1), b"")

    def test_quick_matrix_checks_and_exactly_cleans_test_objects(self):
        storage = _FakeStorage()
        results = run_conformance(storage, include_boundary=False)
        self.assertTrue(storage.checked)
        self.assertEqual([item["case"] for item in results], ["single-part", "multipart"])
        self.assertEqual(len(storage.deleted), 2)
        self.assertEqual(storage.objects, {})

    def test_failed_integrity_case_still_deletes_the_uploaded_object(self):
        storage = _FakeStorage()
        storage.corrupt_metadata = True
        with self.assertRaisesRegex(RuntimeError, "missing SHA-256"):
            run_conformance(storage, include_boundary=False)
        self.assertEqual(len(storage.deleted), 1)
        self.assertEqual(storage.objects, {})


if __name__ == "__main__":
    unittest.main()
