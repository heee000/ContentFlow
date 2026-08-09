from __future__ import annotations

import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from contentflow.object_storage import LocalObjectStorage


class LocalObjectStorageTest(unittest.TestCase):
    def test_put_read_and_check(self):
        with tempfile.TemporaryDirectory() as temporary:
            storage = LocalObjectStorage(
                Path(temporary),
                max_upload_bytes=5,
            )
            storage.check()
            stored = storage.put(
                workspace_id="workspace",
                category="assets",
                filename="asset.txt",
                stream=BytesIO(b"hello"),
                content_type="text/plain",
            )
            self.assertEqual(stored.size_bytes, 5)
            self.assertEqual(storage.read(stored.uri), b"hello")

    def test_oversized_upload_is_removed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            storage = LocalObjectStorage(root, max_upload_bytes=5)
            with self.assertRaisesRegex(ValueError, "exceeds"):
                storage.put(
                    workspace_id="workspace",
                    category="assets",
                    filename="oversized.bin",
                    stream=BytesIO(b"123456"),
                )
            self.assertFalse(any(root.rglob("*.uploading")))
            self.assertFalse(any(path.is_file() for path in root.rglob("*")))


if __name__ == "__main__":
    unittest.main()
