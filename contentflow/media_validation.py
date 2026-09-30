"""Decode media before storage and before consuming confirmed object bytes."""

from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import tempfile

from .media_providers import MediaProviderError
from .filenames import safe_filename
from .publish_evidence import PublishEvidenceError, normalize_publish_evidence
from .review import publication_text_fields


VALIDATION_VERSION = "decoded-media-v1"
MAX_VIDEO_PIXELS = 3840 * 2160
MAX_VIDEO_SECONDS = 300
MAX_VIDEO_FPS = 60
_ERRORS = {
    "media_invalid": "素材无法通过格式、解码或大小检查，请替换有效文件并重新核对素材",
    "media_decoder_unavailable": "视频校验需要 ffmpeg 和 ffprobe；请安装校验工具后核对原素材，不要直接重新生成",
    "media_video_limit": "视频超过安全边界（最长 300 秒、单帧 4K 像素、最高 60 fps），请缩短或压缩后上传",
    "media_decode_timeout": "视频解码校验超时，请缩短或压缩素材后重新校验",
    "media_text_review_required": "分镜文件包含活动禁用词，请修正分镜后重新上传或生成",
}


class MediaValidationError(MediaProviderError):
    def __init__(self, code="media_invalid"):
        super().__init__(_ERRORS[code], retryable=False)
        self.code = code


def validation_error_receipt(error):
    if type(error) is MediaValidationError and type(error.code) is str and error.code in _ERRORS:
        return f"[{error.code}] {_ERRORS[error.code]}"
    return None


@dataclass(frozen=True)
class ValidatedMedia:
    data: bytes
    mime_type: str
    extension: str
    original_filename: str
    source_sha256: str
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None

    @property
    def evidence(self):
        return {"version": VALIDATION_VERSION, "sha256": hashlib.sha256(self.data).hexdigest(),
            "mime_type": self.mime_type, "width": self.width, "height": self.height,
            "duration_seconds": self.duration_seconds}


def _check_mp4_boxes(raw: bytes) -> None:
    # Bound top-level ISO BMFF boxes before invoking tolerant media decoders.
    # This is a framing check, not a replacement for demuxing/full decoding.
    offset, count = 0, 0
    found = set()
    while offset < len(raw):
        count += 1
        if len(raw) - offset < 8 or count > 100_000:
            raise MediaValidationError()
        size = int.from_bytes(raw[offset:offset + 4], "big")
        box = raw[offset + 4:offset + 8]
        header = 8
        if size == 1:
            if len(raw) - offset < 16:
                raise MediaValidationError()
            size = int.from_bytes(raw[offset + 8:offset + 16], "big")
            header = 16
        elif size == 0:
            size = len(raw) - offset
        if size < header or offset + size > len(raw):
            raise MediaValidationError()
        found.add(box)
        offset += size
    if not {b"ftyp", b"moov", b"mdat"} <= found:
        raise MediaValidationError()


def require_video_decoders() -> tuple[str, str]:
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise MediaValidationError("media_decoder_unavailable")
    return ffmpeg, ffprobe


def _video(raw: bytes, filename: str) -> ValidatedMedia:
    ffmpeg, ffprobe = require_video_decoders()
    # Only MP4/MOV demuxing, local file input, and no external data references.
    # Never use a supplied filename/URL as a command, option or input path.
    if len(raw) < 12 or raw[4:8] != b"ftyp":
        raise MediaValidationError()
    _check_mp4_boxes(raw)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    with tempfile.TemporaryDirectory(prefix="contentflow-media-check-") as folder:
        source = Path(folder) / "input.mp4"
        source.write_bytes(raw)
        inputs = ["-protocol_whitelist", "file,pipe", "-f", "mov",
            "-enable_drefs", "0", "-use_absolute_path", "0", "-i", str(source)]
        try:
            with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
                subprocess.run([ffprobe, "-v", "error", *inputs,
                    "-show_entries", "stream=codec_type,width,height,avg_frame_rate:format=duration",
                    "-of", "json"], stdout=output, stderr=errors,
                    check=True, timeout=20, creationflags=flags)
                errors.seek(0)
                if errors.read(1):
                    raise MediaValidationError()
                output.seek(0)
                report = output.read(65537)
            if len(report) > 65536:
                raise MediaValidationError()
            probe = json.loads(report)
            streams = probe["streams"]
            videos = [s for s in streams if s.get("codec_type") == "video"]
            if (len(videos) != 1 or len(streams) > 2
                    or any(s.get("codec_type") not in {"video", "audio"} for s in streams)):
                raise MediaValidationError()
            video = videos[0]
            width, height = video["width"], video["height"]
            duration = float(probe["format"]["duration"])
            fps = float(Fraction(video["avg_frame_rate"]))
            if (type(width) is not int or type(height) is not int or min(width, height) < 1
                    or not math.isfinite(duration) or duration <= 0 or not 0 < fps <= MAX_VIDEO_FPS
                    or width * height > MAX_VIDEO_PIXELS or duration > MAX_VIDEO_SECONDS):
                raise MediaValidationError("media_video_limit")
            # Probe metadata is insufficient: consume/decode all selected frames
            # and audio; corrupt/truncated bitstreams must not become ready.
            with tempfile.TemporaryFile() as progress:
                subprocess.run([ffmpeg, "-v", "error", "-xerror", "-nostdin",
                    "-threads", "1", "-max_pixels", str(MAX_VIDEO_PIXELS), *inputs,
                    "-map", "0:v:0", "-map", "0:a?", "-threads", "1",
                    "-progress", "pipe:1", "-nostats", "-f", "null", "-"],
                    stdout=progress, stderr=subprocess.DEVNULL,
                    check=True, timeout=45, creationflags=flags)
                progress.seek(0)
                decoded = progress.read(65537)
            if (len(decoded) > 65536 or b"progress=end" not in decoded.splitlines()
                    or not any(int(line.split(b"=", 1)[1]) > 0 for line in decoded.splitlines()
                        if line.startswith(b"frame="))):
                raise MediaValidationError()
        except subprocess.TimeoutExpired:
            raise MediaValidationError("media_decode_timeout") from None
        except MediaValidationError:
            raise
        except (OSError, subprocess.CalledProcessError, ValueError, KeyError, TypeError, ZeroDivisionError):
            raise MediaValidationError() from None
    return ValidatedMedia(raw, "video/mp4", "mp4", filename,
        hashlib.sha256(raw).hexdigest(), width, height, duration)


def validate_media(raw: bytes, *, kind: str, mime_type: str | None, filename: str,
                   max_bytes: int, max_pixels: int, normalize: bool = True,
                   forbidden_phrases: tuple[str, ...] = ()) -> ValidatedMedia:
    if not raw or len(raw) > max_bytes:
        raise MediaValidationError()
    try:
        filename = safe_filename(filename)
        if kind == "image":
            normalized = normalize_publish_evidence(raw, filename=filename, kind="screenshot",
                max_bytes=max_bytes, max_pixels=max_pixels)
            # Missing provider MIME may be inferred, but a declared false type is
            # rejected. Search has no reliable declared MIME and uses None.
            if mime_type and mime_type.lower().split(";", 1)[0].strip() != normalized.mime_type:
                raise MediaValidationError()
        elif kind == "video_storyboard" and mime_type == "application/json":
            normalized = normalize_publish_evidence(raw, filename=filename, kind="platform_export",
                max_bytes=max_bytes, max_pixels=max_pixels)
            value = json.loads(normalized.data)
            if (not isinstance(value, dict) or not isinstance(value.get("shots"), list)
                    or len(value["shots"]) > 200 or any(not isinstance(s, dict) for s in value["shots"])):
                raise MediaValidationError()
            if any(phrase in text for _, text in publication_text_fields({"layout": value})
                   for phrase in forbidden_phrases if phrase):
                raise MediaValidationError("media_text_review_required")
        elif kind in {"video", "video_storyboard"} and mime_type == "video/mp4":
            return _video(raw, filename)
        else:
            raise MediaValidationError()
    except (PublishEvidenceError, ValueError, TypeError):
        raise MediaValidationError() from None
    return ValidatedMedia(normalized.data if normalize else raw, normalized.mime_type,
        normalized.extension, normalized.original_filename, normalized.source_sha256,
        normalized.width, normalized.height)
