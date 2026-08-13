from __future__ import annotations

import hashlib
import io
import json
from types import SimpleNamespace

import pytest
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from contentflow.publish_evidence import (
    PublishEvidenceError,
    evidence_manifest_sha256,
    normalize_publish_evidence,
)


def _png_bytes(comment: str) -> bytes:
    output = io.BytesIO()
    metadata = PngInfo()
    metadata.add_text("Comment", comment)
    Image.new("RGB", (8, 6), color=(25, 90, 140)).save(
        output,
        format="PNG",
        pnginfo=metadata,
    )
    return output.getvalue()


def test_screenshot_is_decoded_reencoded_and_metadata_stripped():
    first_raw = _png_bytes("secret-one")
    second_raw = _png_bytes("secret-two")

    first = normalize_publish_evidence(
        first_raw,
        filename="proof.png",
        kind="screenshot",
        max_bytes=1024 * 1024,
        max_pixels=1000,
    )
    second = normalize_publish_evidence(
        second_raw,
        filename="proof.png",
        kind="screenshot",
        max_bytes=1024 * 1024,
        max_pixels=1000,
    )

    assert first.mime_type == "image/png"
    assert first.source_sha256 != second.source_sha256
    assert first.object_sha256 == second.object_sha256
    assert first.object_sha256 == hashlib.sha256(first.data).hexdigest()
    with Image.open(io.BytesIO(first.data)) as decoded:
        assert decoded.size == (8, 6)
        assert "Comment" not in decoded.info


def test_platform_export_is_canonical_utf8_json():
    normalized = normalize_publish_evidence(
        b'{"z": 2, "a": [3, 1]}',
        filename="platform.json",
        kind="platform_export",
        max_bytes=1024,
        max_pixels=1000,
    )

    assert normalized.mime_type == "application/json"
    assert normalized.data == b'{"a":[3,1],"z":2}'
    assert json.loads(normalized.data) == {"a": [3, 1], "z": 2}


@pytest.mark.parametrize(
    ("raw", "kind", "message"),
    [
        (b"not-an-image", "screenshot", "safely decoded"),
        (b'"scalar"', "platform_export", "object or array"),
        (b"{}", "unknown", "unsupported"),
    ],
)
def test_evidence_rejects_unsupported_or_malformed_inputs(raw, kind, message):
    with pytest.raises(PublishEvidenceError, match=message):
        normalize_publish_evidence(
            raw,
            filename="proof.bin",
            kind=kind,
            max_bytes=1024,
            max_pixels=1000,
        )


def test_platform_export_rejects_duplicate_object_keys():
    with pytest.raises(PublishEvidenceError, match="duplicate object keys"):
        normalize_publish_evidence(
            b'{"status":"published","status":"draft"}',
            filename="ambiguous.json",
            kind="platform_export",
            max_bytes=1024,
            max_pixels=1000,
        )


def test_platform_export_rejects_excessive_nesting():
    raw = b"[" * 2000 + b"]" * 2000
    with pytest.raises(PublishEvidenceError, match="nesting exceeds"):
        normalize_publish_evidence(
            raw,
            filename="deep.json",
            kind="platform_export",
            max_bytes=10_000,
            max_pixels=1000,
        )


def test_evidence_manifest_is_stable_but_attempt_bound():
    items = [
        SimpleNamespace(
            id="evidence-b",
            kind="platform_export",
            object_sha256="b" * 64,
            source_sha256="c" * 64,
            mime_type="application/json",
            size_bytes=12,
        ),
        SimpleNamespace(
            id="evidence-a",
            kind="screenshot",
            object_sha256="a" * 64,
            source_sha256="d" * 64,
            mime_type="image/png",
            size_bytes=20,
        ),
    ]
    first = evidence_manifest_sha256(
        items,
        script_attempt_id="attempt-one",
        package_sha256="e" * 64,
    )
    reordered = evidence_manifest_sha256(
        reversed(items),
        script_attempt_id="attempt-one",
        package_sha256="e" * 64,
    )
    next_attempt = evidence_manifest_sha256(
        items,
        script_attempt_id="attempt-two",
        package_sha256="e" * 64,
    )

    assert first == reordered
    assert first != next_attempt
