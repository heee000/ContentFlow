from __future__ import annotations

import tempfile
import unittest
import uuid
from io import BytesIO
from pathlib import Path

from contentflow.filenames import safe_filename
from contentflow.object_storage import LocalObjectStorage


class LocalObjectStorageTest(unittest.TestCase):
    def test_safe_filename_is_cross_platform_and_keeps_basename_compatibility(self):
        self.assertEqual(safe_filename("../中文 资料.txt"), "中文 资料.txt")
        for invalid in (
            None,
            "",
            ".",
            "..",
            "asset.txt:secret",
            "bad\nname.txt",
            "trailing.",
            "trailing ",
            "CON",
            "nul.txt",
            "COM1.log",
            "COM1 .log",
            "LPT9",
            "x" * 256,
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "文件名无效"):
                    safe_filename(invalid)

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
            storage.delete(stored.uri)
            with self.assertRaises(FileNotFoundError):
                storage.read(stored.uri)
            storage.delete(stored.uri)

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

    def test_allocation_id_is_embedded_and_workspace_listing_is_paginated(self):
        with tempfile.TemporaryDirectory() as temporary:
            storage = LocalObjectStorage(Path(temporary), max_upload_bytes=10)
            allocation_ids = [str(uuid.uuid4()) for _ in range(3)]
            stored = [
                storage.put(
                    workspace_id="workspace",
                    category="assets",
                    filename=f"asset-{index}.txt",
                    stream=BytesIO(str(index).encode("ascii")),
                    allocation_id=allocation_id,
                )
                for index, allocation_id in enumerate(allocation_ids)
            ]

            first = storage.list_workspace_objects("workspace", limit=2)
            self.assertEqual(len(first.items), 2)
            self.assertIsNotNone(first.next_cursor)
            second = storage.list_workspace_objects(
                "workspace",
                limit=2,
                cursor=first.next_cursor,
            )
            self.assertEqual(len(second.items), 1)
            self.assertIsNone(second.next_cursor)
            listed_uris = {item.uri for item in [*first.items, *second.items]}
            self.assertEqual(listed_uris, {item.uri for item in stored})
            for allocation_id, item in zip(allocation_ids, stored, strict=True):
                self.assertIn(allocation_id, item.uri)

            with self.assertRaisesRegex(ValueError, "不属于当前工作区"):
                storage.list_workspace_objects(
                    "workspace",
                    limit=2,
                    cursor="another-workspace/assets/object.bin",
                )

    def test_generated_physical_name_stays_within_filesystem_byte_limit(self):
        with tempfile.TemporaryDirectory() as temporary:
            storage = LocalObjectStorage(Path(temporary), max_upload_bytes=10)
            stored = storage.put(
                workspace_id="workspace",
                category="assets",
                filename=f"{'长文件名' * 40}.txt",
                stream=BytesIO(b"content"),
                allocation_id=str(uuid.uuid4()),
            )
            object_name = next(
                item.key.rsplit("/", 1)[-1]
                for item in storage.list_workspace_objects(
                    "workspace",
                    limit=10,
                ).items
                if item.uri == stored.uri
            )
            self.assertLessEqual(len(object_name.encode("utf-8")), 255)
            self.assertTrue(object_name.endswith(".txt"))
            self.assertEqual(storage.read(stored.uri), b"content")


if __name__ == "__main__":
    unittest.main()
