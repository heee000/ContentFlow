from __future__ import annotations

import hashlib
import io
import json
import warnings
from collections.abc import Iterable
from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError

from .filenames import safe_filename


class PublishEvidenceError(ValueError):
    """The uploaded publication evidence is unsafe or unsupported."""


@dataclass(frozen=True, slots=True)
class NormalizedPublishEvidence:
    kind: str
    original_filename: str
    data: bytes
    source_sha256: str
    object_sha256: str
    mime_type: str
    extension: str
    width: int | None = None
    height: int | None = None


_IMAGE_OUTPUT = {
    "PNG": ("image/png", "png"),
    "JPEG": ("image/jpeg", "jpg"),
    "WEBP": ("image/webp", "webp"),
}


def evidence_manifest_sha256(
    items: Iterable[object],
    *,
    script_attempt_id: str,
    package_sha256: str,
) -> str:
    manifest = {
        "schema_version": "contentflow.publish-evidence.v1",
        "script_attempt_id": script_attempt_id,
        "package_sha256": package_sha256,
        "items": sorted(
            (
                {
                    "id": str(getattr(item, "id")),
                    "kind": str(getattr(item, "kind")),
                    "object_sha256": str(getattr(item, "object_sha256")),
                    "source_sha256": str(getattr(item, "source_sha256")),
                    "mime_type": str(getattr(item, "mime_type")),
                    "size_bytes": int(getattr(item, "size_bytes")),
                }
                for item in items
            ),
            key=lambda item: item["id"],
        ),
    }
    canonical = json.dumps(
        manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _normalize_image(
    raw: bytes,
    *,
    original_filename: str,
    max_bytes: int,
    max_pixels: int,
) -> NormalizedPublishEvidence:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as probe:
                image_format = str(probe.format or "").upper()
                width, height = probe.size
                frames = int(getattr(probe, "n_frames", 1))
                if image_format not in _IMAGE_OUTPUT:
                    raise PublishEvidenceError(
                        "Only PNG, JPEG, and WebP screenshots are supported"
                    )
                if width <= 0 or height <= 0 or width * height > max_pixels:
                    raise PublishEvidenceError(
                        f"Screenshot exceeds the {max_pixels}-pixel safety limit"
                    )
                if frames != 1:
                    raise PublishEvidenceError("Animated screenshots are not supported")
                probe.verify()

            with Image.open(io.BytesIO(raw)) as decoded:
                decoded.load()
                image = decoded.copy()
    except PublishEvidenceError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise PublishEvidenceError("Screenshot dimensions are unsafe") from error
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as error:
        raise PublishEvidenceError("Screenshot cannot be safely decoded") from error

    mime_type, extension = _IMAGE_OUTPUT[image_format]
    if image_format == "JPEG" and image.mode not in {"RGB", "L"}:
        image = image.convert("RGB")
    output = io.BytesIO()
    save_options: dict[str, object]
    if image_format == "JPEG":
        save_options = {"quality": 95, "optimize": True, "progressive": True}
    elif image_format == "WEBP":
        save_options = {"lossless": True, "method": 6}
    else:
        save_options = {"optimize": True}
    image.save(output, format=image_format, **save_options)

    data = output.getvalue()
    if not data or len(data) > max_bytes:
        raise PublishEvidenceError("Normalized screenshot exceeds the upload limit")
    return NormalizedPublishEvidence(
        kind="screenshot",
        original_filename=original_filename,
        data=data,
        source_sha256=hashlib.sha256(raw).hexdigest(),
        object_sha256=hashlib.sha256(data).hexdigest(),
        mime_type=mime_type,
        extension=extension,
        width=width,
        height=height,
    )


def _normalize_json(
    raw: bytes,
    *,
    original_filename: str,
    max_bytes: int,
) -> NormalizedPublishEvidence:
    def reject_duplicate_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise PublishEvidenceError(
                    "Platform export JSON contains duplicate object keys"
                )
            result[key] = value
        return result

    try:
        parsed = json.loads(
            raw.decode("utf-8-sig"), object_pairs_hook=reject_duplicate_keys
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise PublishEvidenceError(
            "Platform export must be valid UTF-8 JSON"
        ) from error
    stack = [(parsed, 1)]
    while stack:
        value, depth = stack.pop()
        if depth > 100:
            raise PublishEvidenceError(
                "Platform export JSON nesting exceeds the safety limit"
            )
        if isinstance(value, dict):
            stack.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, list):
            stack.extend((item, depth + 1) for item in value)

    if not isinstance(parsed, (dict, list)):
        raise PublishEvidenceError("Platform export JSON must be an object or array")
    try:
        data = json.dumps(
            parsed,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as error:
        raise PublishEvidenceError("Platform export contains invalid values") from error
    if not data or len(data) > max_bytes:
        raise PublishEvidenceError(
            "Normalized platform export exceeds the upload limit"
        )
    return NormalizedPublishEvidence(
        kind="platform_export",
        original_filename=original_filename,
        data=data,
        source_sha256=hashlib.sha256(raw).hexdigest(),
        object_sha256=hashlib.sha256(data).hexdigest(),
        mime_type="application/json",
        extension="json",
    )


def normalize_publish_evidence(
    raw: bytes,
    *,
    filename: str,
    kind: str,
    max_bytes: int,
    max_pixels: int,
) -> NormalizedPublishEvidence:
    if not raw:
        raise PublishEvidenceError("Evidence file is empty")
    if len(raw) > max_bytes:
        raise PublishEvidenceError(f"Evidence exceeds the {max_bytes}-byte limit")
    try:
        original_filename = safe_filename(filename)
    except ValueError as error:
        raise PublishEvidenceError("Evidence filename is invalid") from error
    if kind == "screenshot":
        return _normalize_image(
            raw,
            original_filename=original_filename,
            max_bytes=max_bytes,
            max_pixels=max_pixels,
        )
    if kind == "platform_export":
        return _normalize_json(
            raw,
            original_filename=original_filename,
            max_bytes=max_bytes,
        )
    raise PublishEvidenceError("Evidence kind is unsupported")
