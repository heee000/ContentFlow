from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from typing import Any, Protocol

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
    ) -> MediaGeneration:
        ...

    def poll(self, external_task_id: str) -> MediaGeneration:
        ...


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


class DashScopeMediaProvider:
    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        if not settings.dashscope_api_key:
            raise ValueError("使用 DashScope 素材模型需要 CONTENTFLOW_DASHSCOPE_API_KEY")
        self.settings = settings
        self.client = client or httpx.Client(timeout=90)
        self.base_url = self._base_url(settings.dashscope_region)
        self.headers = {
            "Authorization": f"Bearer {settings.dashscope_api_key}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _base_url(region: str) -> str:
        if region.lower() in {"singapore", "intl", "international"}:
            return "https://dashscope-intl.aliyuncs.com"
        return "https://dashscope.aliyuncs.com"

    def generate(
        self,
        *,
        kind: str,
        prompt: str,
        metadata: dict[str, Any],
    ) -> MediaGeneration:
        if kind == "image":
            response = self.client.post(
                f"{self.base_url}/api/v1/services/aigc/multimodal-generation/generation",
                headers=self.headers,
                json={
                    "model": self.settings.dashscope_image_model,
                    "input": {"messages": [{"role": "user", "content": [{"text": prompt}]}]},
                    "parameters": {
                        "size": self._wan_size(str(metadata.get("ratio") or "1:1")),
                        "n": 1,
                    },
                },
            )
            response.raise_for_status()
            body = response.json()
            result = ((body.get("output") or {}).get("choices") or [{}])[0]
            content = (result.get("message") or {}).get("content") or []
            image_url = next(
                (
                    part.get("image")
                    for part in content
                    if isinstance(part, dict) and part.get("image")
                ),
                None,
            )
            if not image_url:
                raise RuntimeError(f"DashScope 图片响应缺少下载地址: {body}")
            return MediaGeneration(
                status="ready",
                download_url=image_url,
                mime_type="image/png",
                filename="wan-image.png",
                metadata={"request_id": body.get("request_id")},
            )

        response = self.client.post(
            f"{self.base_url}/api/v1/services/aigc/video-generation/video-synthesis",
            headers={**self.headers, "X-DashScope-Async": "enable"},
            json={
                "model": self.settings.dashscope_video_model,
                "input": {"prompt": prompt},
                "parameters": {
                    "size": self._wan_video_size(
                        str(metadata.get("ratio") or "16:9")
                    ),
                },
            },
        )
        response.raise_for_status()
        body = response.json()
        task_id = (body.get("output") or {}).get("task_id")
        if not task_id:
            raise RuntimeError(f"DashScope 视频响应缺少 task_id: {body}")
        return MediaGeneration(
            status="processing",
            external_task_id=task_id,
            metadata={"request_id": body.get("request_id")},
        )

    def poll(self, external_task_id: str) -> MediaGeneration:
        response = self.client.get(
            f"{self.base_url}/api/v1/tasks/{external_task_id}",
            headers=self.headers,
        )
        response.raise_for_status()
        body = response.json()
        output = body.get("output") or {}
        status = str(output.get("task_status") or "").upper()
        if status in {"PENDING", "RUNNING"}:
            return MediaGeneration(status="processing", external_task_id=external_task_id)
        if status != "SUCCEEDED":
            raise RuntimeError(
                f"DashScope 视频任务失败: {output.get('message') or status or body}"
            )
        url = output.get("video_url") or (output.get("results") or [{}])[0].get(
            "url"
        )
        if not url:
            raise RuntimeError(f"DashScope 视频完成但缺少下载地址: {body}")
        return MediaGeneration(
            status="ready",
            external_task_id=external_task_id,
            download_url=url,
            mime_type="video/mp4",
            filename="wan-video.mp4",
            metadata={"request_id": body.get("request_id")},
        )

    @staticmethod
    def _wan_size(ratio: str) -> str:
        return {
            "3:4": "1104*1472",
            "4:3": "1472*1104",
            "1:1": "1328*1328",
            "9:16": "928*1664",
            "16:9": "1664*928",
        }.get(ratio, "1328*1328")

    @staticmethod
    def _wan_video_size(ratio: str) -> str:
        return {
            "9:16": "720*1280",
            "1:1": "960*960",
            "16:9": "1280*720",
        }.get(ratio, "1280*720")


def build_media_provider(settings: Settings, kind: str) -> MediaProvider:
    provider_name = settings.image_provider if kind == "image" else settings.video_provider
    if provider_name == "mock":
        return MockMediaProvider()
    if provider_name in {"dashscope", "wan"}:
        return DashScopeMediaProvider(settings)
    raise ValueError(f"不支持的素材生成 provider: {provider_name}")


def download_generated_media(
    generation: MediaGeneration,
    *,
    client: httpx.Client | None = None,
    max_bytes: int = 100 * 1024 * 1024,
) -> bytes:
    if generation.content is not None:
        return generation.content
    if not generation.download_url:
        raise ValueError("素材生成结果没有内容或下载地址")
    http = client or httpx.Client(timeout=120, follow_redirects=True)
    with http.stream("GET", generation.download_url) as response:
        response.raise_for_status()
        chunks: list[bytes] = []
        size = 0
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > max_bytes:
                raise ValueError("模型生成素材超过 100MB 限制")
            chunks.append(chunk)
    return b"".join(chunks)
