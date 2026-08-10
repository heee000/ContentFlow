from __future__ import annotations

import base64
import binascii
import io
import json
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import quote, urlparse

import httpx

from .settings import Settings


@dataclass(slots=True)
class MediaGeneration:
    status: str
    content: bytes | None = None
    mime_type: str | None = None
    filename: str | None = None
    external_task_id: str | None = None
    download_url: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class MediaProvider(Protocol):
    def generate(
        self,
        *,
        kind: str,
        prompt: str,
        metadata: dict[str, Any],
    ) -> MediaGeneration: ...

    def poll(self, external_task_id: str) -> MediaGeneration: ...


def _image_size(ratio: str) -> tuple[int, int]:
    return {
        "3:4": (900, 1200),
        "4:3": (1200, 900),
        "1:1": (1024, 1024),
        "9:16": (720, 1280),
        "16:9": (1280, 720),
    }.get(ratio, (1024, 1024))


class MockMediaProvider:
    """Offline provider that produces honest, inspectable placeholder assets."""

    def generate(
        self,
        *,
        kind: str,
        prompt: str,
        metadata: dict[str, Any],
    ) -> MediaGeneration:
        if kind == "image":
            from PIL import Image, ImageDraw, ImageFont

            width, height = _image_size(str(metadata.get("ratio") or "1:1"))
            image = Image.new("RGB", (width, height), "#0f62fe")
            draw = ImageDraw.Draw(image)
            band_height = max(160, height // 4)
            draw.rectangle(
                (0, height - band_height, width, height),
                fill="#161616",
            )
            draw.rectangle((0, 0, 18, height), fill="#78a9ff")
            font = ImageFont.load_default()
            label = "ContentFlow\nOffline preview asset\nReplace with model output in production"
            draw.multiline_text(
                (48, height - band_height + 36),
                label,
                fill="white",
                font=font,
                spacing=12,
            )
            output = io.BytesIO()
            image.save(output, format="PNG", optimize=True)
            return MediaGeneration(
                status="ready",
                content=output.getvalue(),
                mime_type="image/png",
                filename="preview.png",
                metadata={
                    "mock": True,
                    "prompt_excerpt": prompt[:240],
                    "width": width,
                    "height": height,
                },
            )

        storyboard = {
            "mode": "offline_storyboard",
            "notice": "这不是生成视频，仅用于离线验收工作流。",
            "prompt": prompt,
            "shots": metadata.get("shots") or [],
            "duration_seconds": metadata.get("duration_seconds"),
        }
        return MediaGeneration(
            status="ready",
            content=json.dumps(
                storyboard,
                ensure_ascii=False,
                indent=2,
            ).encode("utf-8"),
            mime_type="application/json",
            filename="storyboard.json",
            metadata={"mock": True, "artifact_type": "storyboard"},
        )

    def poll(self, external_task_id: str) -> MediaGeneration:
        raise ValueError(f"离线素材任务不需要轮询: {external_task_id}")


class HTTPMediaProvider:
    """Vendor-neutral HTTP adapter using the ContentFlow media contract."""

    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        if not settings.media_api_base or not settings.media_api_key:
            raise ValueError(
                "HTTP 素材 Provider 需要 CONTENTFLOW_MEDIA_API_BASE 和 "
                "CONTENTFLOW_MEDIA_API_KEY"
            )
        self.settings = settings
        self.client = client or httpx.Client(timeout=90)
        self.base_url = settings.media_api_base.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {settings.media_api_key}",
            "Content-Type": "application/json",
        }

    def generate(
        self,
        *,
        kind: str,
        prompt: str,
        metadata: dict[str, Any],
    ) -> MediaGeneration:
        if kind == "image":
            if not self.settings.image_model:
                raise ValueError("HTTP 图片 Provider 缺少 CONTENTFLOW_IMAGE_MODEL")
            width, height = _image_size(str(metadata.get("ratio") or "1:1"))
            response = self.client.post(
                f"{self.base_url}/images/generations",
                headers=self.headers,
                json={
                    "model": self.settings.image_model,
                    "prompt": prompt,
                    "size": f"{width}x{height}",
                    "metadata": metadata,
                },
            )
            response.raise_for_status()
            return self._image_result(self._response_body(response))
        if kind != "video":
            raise ValueError(f"HTTP 素材 Provider 不支持 kind={kind}")
        if not self.settings.video_model:
            raise ValueError("HTTP 视频 Provider 缺少 CONTENTFLOW_VIDEO_MODEL")
        response = self.client.post(
            f"{self.base_url}/videos/generations",
            headers=self.headers,
            json={
                "model": self.settings.video_model,
                "prompt": prompt,
                "metadata": metadata,
            },
        )
        response.raise_for_status()
        return self._video_result(self._response_body(response))

    def poll(self, external_task_id: str) -> MediaGeneration:
        if not external_task_id.strip():
            raise ValueError("HTTP 视频任务 ID 不能为空")
        response = self.client.get(
            f"{self.base_url}/videos/generations/{quote(external_task_id, safe='')}",
            headers=self.headers,
        )
        response.raise_for_status()
        return self._video_result(
            self._response_body(response), fallback_id=external_task_id
        )

    @staticmethod
    def _response_body(response: httpx.Response) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError as error:
            raise RuntimeError("HTTP 素材响应不是有效 JSON") from error
        if not isinstance(body, dict):
            raise RuntimeError("HTTP 素材响应顶层必须是对象")
        return body

    @staticmethod
    def _result_item(body: dict[str, Any]) -> dict[str, Any]:
        data = body.get("data")
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return data[0]
        if isinstance(data, dict):
            return data
        return body

    @staticmethod
    def _request_metadata(body: dict[str, Any]) -> dict[str, Any]:
        request_id = body.get("request_id") or body.get("requestId")
        return {"request_id": str(request_id)} if request_id else {}

    def _image_result(self, body: dict[str, Any]) -> MediaGeneration:
        item = self._result_item(body)
        encoded = item.get("b64_json")
        if isinstance(encoded, str) and encoded:
            try:
                content = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error) as error:
                raise RuntimeError("HTTP 图片响应包含无效 base64 数据") from error
            return MediaGeneration(
                status="ready",
                content=content,
                mime_type=str(item.get("mime_type") or "image/png"),
                filename=str(item.get("filename") or "generated-image.png"),
                metadata=self._request_metadata(body),
            )
        url = item.get("url") or item.get("download_url")
        if not isinstance(url, str) or not url:
            raise RuntimeError("HTTP 图片响应缺少 url、download_url 或 b64_json")
        return MediaGeneration(
            status="ready",
            download_url=url,
            mime_type=str(item.get("mime_type") or "image/png"),
            filename=str(item.get("filename") or "generated-image.png"),
            metadata=self._request_metadata(body),
        )

    def _video_result(
        self,
        body: dict[str, Any],
        *,
        fallback_id: str | None = None,
    ) -> MediaGeneration:
        item = self._result_item(body)
        status = str(item.get("status") or body.get("status") or "").lower()
        task_id = item.get("id") or item.get("task_id") or fallback_id
        url = item.get("url") or item.get("download_url")
        metadata = self._request_metadata(body)
        if status in {"queued", "pending", "processing", "running"} or (
            not status and task_id and not url
        ):
            if not task_id:
                raise RuntimeError("HTTP 视频响应缺少异步任务 ID")
            return MediaGeneration(
                status="processing",
                external_task_id=str(task_id),
                metadata=metadata,
            )
        if status in {"ready", "completed", "succeeded"} or (
            not status and isinstance(url, str) and url
        ):
            if not isinstance(url, str) or not url:
                raise RuntimeError("HTTP 视频完成响应缺少下载地址")
            return MediaGeneration(
                status="ready",
                external_task_id=str(task_id) if task_id else fallback_id,
                download_url=url,
                mime_type=str(item.get("mime_type") or "video/mp4"),
                filename=str(item.get("filename") or "generated-video.mp4"),
                metadata=metadata,
            )
        raise RuntimeError(f"HTTP 视频任务返回未知状态: {status or 'missing'}")


def build_media_provider(settings: Settings, kind: str) -> MediaProvider:
    provider_name = (
        settings.image_provider if kind == "image" else settings.video_provider
    )
    if provider_name == "mock":
        return MockMediaProvider()
    if provider_name == "http":
        return HTTPMediaProvider(settings)
    raise ValueError(f"不支持的素材生成 provider: {provider_name}")


def _validate_download_url(url: str, allowed_hosts: tuple[str, ...]) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("素材下载地址必须是有效的 HTTP(S) URL")
    if parsed.username or parsed.password:
        raise ValueError("素材下载地址不得包含 URL 凭据")
    normalized_hosts = {host.strip().lower() for host in allowed_hosts if host.strip()}
    if normalized_hosts and parsed.hostname.lower() not in normalized_hosts:
        raise ValueError("素材下载地址不在允许的域名列表中")


def download_generated_media(
    generation: MediaGeneration,
    *,
    client: httpx.Client | None = None,
    max_bytes: int = 100 * 1024 * 1024,
    allowed_hosts: tuple[str, ...] = (),
) -> bytes:
    if generation.content is not None:
        if len(generation.content) > max_bytes:
            raise ValueError("模型生成素材超过大小限制")
        return generation.content
    if not generation.download_url:
        raise ValueError("素材生成结果没有内容或下载地址")
    _validate_download_url(generation.download_url, allowed_hosts)
    http = client or httpx.Client(timeout=120, follow_redirects=True)
    with http.stream("GET", generation.download_url) as response:
        for hop in (*response.history, response):
            _validate_download_url(str(hop.url), allowed_hosts)
        response.raise_for_status()
        chunks: list[bytes] = []
        size = 0
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > max_bytes:
                raise ValueError("模型生成素材超过大小限制")
            chunks.append(chunk)
    return b"".join(chunks)
