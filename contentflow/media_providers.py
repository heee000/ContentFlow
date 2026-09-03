from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import quote, urljoin, urlparse

import httpx

from .filenames import safe_filename
from .network_validation import normalize_exact_host
from .settings import Settings

MEDIA_CONTRACT_VERSION = "1"
MEDIA_CONTRACT_VERSION_HEADER = "ContentFlow-Media-Version"
_MAX_ERROR_RESPONSE_BYTES = 64 * 1024
_RESPONSE_ENVELOPE_OVERHEAD_BYTES = 64 * 1024


def media_provider_profile_fingerprint(settings: Settings, kind: str) -> str:
    """Return a non-secret identity for the configured media target."""

    normalized_kind = "image" if kind == "image" else "video"
    provider = (
        settings.image_provider
        if normalized_kind == "image"
        else settings.video_provider
    )
    profile = {
        "contract_version": MEDIA_CONTRACT_VERSION,
        "kind": normalized_kind,
        "provider": provider,
        "api_base": (
            (settings.media_api_base or "").rstrip("/") if provider == "http" else ""
        ),
        "model": (
            settings.image_model if normalized_kind == "image" else settings.video_model
        )
        if provider == "http"
        else "",
    }
    canonical = json.dumps(profile, sort_keys=True, separators=(",", ":"))
    return f"cfp-{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


class MediaProviderError(RuntimeError):
    """Stable, redacted failure raised by the external media contract."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        status_code: int | None = None,
        retry_after_seconds: int | None = None,
        provider_request_id: str | None = None,
        provider_request_id_source: str | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        self.provider_request_id = provider_request_id
        self.provider_request_id_source = provider_request_id_source


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
        idempotency_key: str,
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
        idempotency_key: str,
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
    """Vendor-neutral adapter for ContentFlow Media Contract v1."""

    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        if not settings.media_api_base or not settings.media_api_key:
            raise ValueError(
                "HTTP 素材 Provider 需要 CONTENTFLOW_MEDIA_API_BASE 和 "
                "CONTENTFLOW_MEDIA_API_KEY"
            )
        if (
            not isinstance(settings.media_api_key, str)
            or not 1 <= len(settings.media_api_key) <= 4096
            or not settings.media_api_key.isascii()
            or any(
                not 0x21 <= ord(char) <= 0x7E
                for char in settings.media_api_key
            )
        ):
            raise MediaProviderError(
                "HTTP 素材 Provider API Key 格式无效",
                retryable=False,
            )
        self._validate_api_base(
            settings.media_api_base,
            require_https=settings.production,
        )
        normalized_hosts = [
            normalize_exact_host(host)
            for host in settings.media_download_allowed_hosts
        ]
        if not normalized_hosts or any(host is None for host in normalized_hosts):
            raise MediaProviderError(
                "HTTP 素材 Provider 需要非空的精确下载域名允许列表",
                retryable=False,
            )
        self.settings = settings
        self.client = client
        self.base_url = settings.media_api_base.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {settings.media_api_key}",
            "Content-Type": "application/json",
            MEDIA_CONTRACT_VERSION_HEADER: MEDIA_CONTRACT_VERSION,
        }

    def generate(
        self,
        *,
        kind: str,
        prompt: str,
        metadata: dict[str, Any],
        idempotency_key: str,
    ) -> MediaGeneration:
        self._validate_prompt(prompt)
        request_headers = self._generation_headers(idempotency_key)
        parameters = self._generation_parameters(metadata)
        if kind == "image":
            if not self._valid_model_name(self.settings.image_model):
                raise MediaProviderError(
                    "HTTP 图片 Provider 模型名格式无效",
                    retryable=False,
                )
            width, height = _image_size(str(metadata.get("ratio") or "1:1"))
            body = self._request_json(
                "POST",
                f"{self.base_url}/images/generations",
                headers=request_headers,
                payload={
                    "model": self.settings.image_model,
                    "prompt": prompt,
                    "size": f"{width}x{height}",
                    "parameters": parameters,
                },
            )
            return self._image_result(body)
        if kind not in {"video", "video_storyboard"}:
            raise MediaProviderError(
                "HTTP 素材 Provider 收到不支持的素材类型",
                retryable=False,
            )
        if not self._valid_model_name(self.settings.video_model):
            raise MediaProviderError(
                "HTTP 视频 Provider 模型名格式无效",
                retryable=False,
            )
        body = self._request_json(
            "POST",
            f"{self.base_url}/videos/generations",
            headers=request_headers,
            payload={
                "model": self.settings.video_model,
                "prompt": prompt,
                "parameters": parameters,
            },
        )
        return self._video_result(body)

    def poll(self, external_task_id: str) -> MediaGeneration:
        if not self._valid_bounded_text(
            external_task_id,
            minimum=1,
            maximum=255,
        ) or not external_task_id.strip():
            raise MediaProviderError(
                "HTTP 视频任务 ID 格式无效",
                retryable=False,
            )
        body = self._request_json(
            "GET",
            f"{self.base_url}/videos/generations/{quote(external_task_id, safe='')}",
            headers=self.headers,
        )
        return self._video_result(body, fallback_id=external_task_id)

    @property
    def _max_success_response_bytes(self) -> int:
        encoded_limit = ((self.settings.max_upload_bytes + 2) // 3) * 4
        inline_limit = encoded_limit + _RESPONSE_ENVELOPE_OVERHEAD_BYTES
        return min(
            self.settings.media_provider_max_response_bytes,
            inline_limit,
        )

    @staticmethod
    def _validate_api_base(value: str, *, require_https: bool) -> None:
        message = (
            "HTTP 素材 Provider Base 必须是无凭据、query 或 fragment 的 "
            "HTTP(S) URL"
        )
        if not isinstance(value, str) or any(
            ord(char) <= 0x20 or ord(char) == 0x7F for char in value
        ):
            raise MediaProviderError(message, retryable=False)
        try:
            value.encode("utf-8")
            parsed = urlparse(value)
            parsed.port
        except ValueError as error:
            raise MediaProviderError(message, retryable=False) from error
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or normalize_exact_host(parsed.hostname) is None
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise MediaProviderError(message, retryable=False)
        if require_https and parsed.scheme != "https":
            raise MediaProviderError(
                "生产 HTTP 素材 Provider Base 必须使用 HTTPS",
                retryable=False,
            )

    def _response_limit(self, response: httpx.Response) -> int:
        if 200 <= response.status_code < 300:
            return self._max_success_response_bytes
        return _MAX_ERROR_RESPONSE_BYTES

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self.client is not None:
            return self._request_json_with_client(
                self.client,
                method,
                url,
                headers=headers,
                payload=payload,
            )
        with httpx.Client(timeout=90, follow_redirects=False) as client:
            return self._request_json_with_client(
                client,
                method,
                url,
                headers=headers,
                payload=payload,
            )

    def _request_json_with_client(
        self,
        client: httpx.Client,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, Any] | None,
    ) -> dict[str, Any]:
        try:
            request = client.build_request(
                method,
                url,
                headers=headers,
                json=payload,
            )
            response = client.send(
                request,
                stream=True,
                follow_redirects=False,
            )
        except httpx.TimeoutException:
            raise MediaProviderError(
                "HTTP 素材服务请求超时",
                retryable=True,
            ) from None
        except httpx.RequestError:
            raise MediaProviderError(
                "HTTP 素材服务网络请求失败",
                retryable=True,
            ) from None
        try:
            try:
                raw = self._read_response_bytes(response)
            except httpx.TimeoutException:
                raise MediaProviderError(
                    "HTTP 素材服务响应读取超时",
                    retryable=True,
                ) from None
            except httpx.RequestError:
                raise MediaProviderError(
                    "HTTP 素材服务响应读取失败",
                    retryable=True,
                ) from None
            body = self._response_body(response, raw)
            self._raise_for_status(response, body)
            return body
        finally:
            response.close()

    def _read_response_bytes(self, response: httpx.Response) -> bytes:
        response_limit = self._response_limit(response)
        content_length = response.headers.get("Content-Length", "").strip()
        if content_length.isdigit() and (
            len(content_length) > 20 or int(content_length) > response_limit
        ):
            raise MediaProviderError(
                "HTTP 素材响应超过大小限制",
                retryable=False,
            )
        content = bytearray()
        for chunk in response.iter_bytes():
            if len(content) + len(chunk) > response_limit:
                raise MediaProviderError(
                    "HTTP 素材响应超过大小限制",
                    retryable=False,
                )
            content.extend(chunk)
        return bytes(content)

    @staticmethod
    def _valid_bounded_text(
        value: Any,
        *,
        minimum: int,
        maximum: int,
        ascii_only: bool = False,
    ) -> bool:
        if not isinstance(value, str) or not minimum <= len(value) <= maximum:
            return False
        if ascii_only and not value.isascii():
            return False
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            return False
        return all(
            ord(char) >= 0x20 and ord(char) != 0x7F
            for char in value
        )

    @classmethod
    def _valid_model_name(cls, value: Any) -> bool:
        return cls._valid_bounded_text(
            value,
            minimum=1,
            maximum=200,
        ) and value == value.strip()

    @staticmethod
    def _validate_prompt(prompt: str) -> None:
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 50_000:
            raise MediaProviderError(
                "HTTP 素材 Prompt 格式无效",
                retryable=False,
            )

        try:
            prompt.encode("utf-8")
        except UnicodeEncodeError:
            raise MediaProviderError(
                "HTTP 素材 Prompt 格式无效",
                retryable=False,
            ) from None

    @staticmethod
    def _generation_parameters(metadata: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(metadata, dict):
            raise MediaProviderError(
                "HTTP 素材 metadata 格式无效",
                retryable=False,
            )
        allowed = ("ratio", "aspect_ratio", "duration_seconds", "shots")
        parameters = {key: metadata[key] for key in allowed if key in metadata}
        ratios = {"3:4", "4:3", "1:1", "9:16", "16:9"}
        for key in ("ratio", "aspect_ratio"):
            if key in parameters and (
                not isinstance(parameters[key], str)
                or parameters[key] not in ratios
            ):
                raise MediaProviderError(
                    "HTTP 素材画面比例无效",
                    retryable=False,
                )
        duration = parameters.get("duration_seconds")
        if duration is not None and (
            isinstance(duration, bool)
            or not isinstance(duration, int)
            or not 1 <= duration <= 600
        ):
            raise MediaProviderError(
                "HTTP 视频时长参数无效",
                retryable=False,
            )
        shots = parameters.get("shots")
        if shots is not None:
            if not isinstance(shots, list) or len(shots) > 100:
                raise MediaProviderError(
                    "HTTP 视频分镜参数无效",
                    retryable=False,
                )
            allowed_shot_fields = {
                "time",
                "visual",
                "voiceover",
                "subtitle",
            }
            for shot in shots:
                if isinstance(shot, str):
                    valid = HTTPMediaProvider._valid_bounded_text(
                        shot,
                        minimum=1,
                        maximum=5000,
                    )
                elif isinstance(shot, dict):
                    valid = (
                        bool(shot)
                        and shot.keys() <= allowed_shot_fields
                        and all(
                            HTTPMediaProvider._valid_bounded_text(
                                value,
                                minimum=1,
                                maximum=5000,
                            )
                            for value in shot.values()
                        )
                    )
                else:
                    valid = False
                if not valid:
                    raise MediaProviderError(
                        "HTTP 视频分镜参数无效",
                        retryable=False,
                    )
            try:
                serialized = json.dumps(
                    shots,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            except (TypeError, UnicodeEncodeError, ValueError):
                raise MediaProviderError(
                    "HTTP 视频分镜参数无效",
                    retryable=False,
                ) from None
            if len(serialized) > 256 * 1024:
                raise MediaProviderError(
                    "HTTP 视频分镜参数超过大小限制",
                    retryable=False,
                )
        return parameters

    def _generation_headers(self, idempotency_key: str) -> dict[str, str]:
        if not isinstance(idempotency_key, str):
            raise MediaProviderError(
                "HTTP 素材幂等键格式无效",
                retryable=False,
            )
        normalized = idempotency_key.strip()
        if (
            normalized != idempotency_key
            or not 8 <= len(normalized) <= 128
            or not normalized.isascii()
            or any(not 0x20 <= ord(char) <= 0x7E for char in normalized)
        ):
            raise MediaProviderError(
                "HTTP 素材幂等键格式无效",
                retryable=False,
            )
        return {**self.headers, "Idempotency-Key": normalized}

    @staticmethod
    def _retry_after_seconds(response: httpx.Response) -> int | None:
        value = response.headers.get("Retry-After", "").strip()
        if not value.isascii() or not value.isdigit():
            return None
        if len(value) > 10:
            return 300
        return min(300, max(1, int(value)))

    @classmethod
    def _raise_for_status(
        cls,
        response: httpx.Response,
        body: dict[str, Any],
    ) -> None:
        if 200 <= response.status_code < 300:
            return
        retryable = response.status_code in {408, 425, 429} or (
            500 <= response.status_code < 600
        )
        cls._validate_error_detail(
            body,
            expected_retryable=retryable,
            top_level=True,
        )
        disposition = "暂时" if retryable else "永久"
        raise MediaProviderError(
            f"HTTP 素材服务返回{disposition}错误（状态码 {response.status_code}）",
            retryable=retryable,
            status_code=response.status_code,
            retry_after_seconds=(
                cls._retry_after_seconds(response) if retryable else None
            ),
            provider_request_id=body.get("request_id"),
            provider_request_id_source=(
                "body.request_id" if body.get("request_id") else None
            ),
        )

    @staticmethod
    def _response_body(
        response: httpx.Response,
        raw: bytes,
    ) -> dict[str, Any]:
        response_version = response.headers.get(MEDIA_CONTRACT_VERSION_HEADER)
        if response_version != MEDIA_CONTRACT_VERSION:
            raise MediaProviderError(
                "HTTP 素材响应缺少兼容的 ContentFlow Media Contract 版本",
                retryable=False,
            )
        content_type = response.headers.get("Content-Type", "")
        media_type = content_type.split(";", 1)[0].strip().lower()
        if media_type != "application/json":
            raise MediaProviderError(
                "HTTP 素材响应 Content-Type 必须是 application/json",
                retryable=False,
            )
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise MediaProviderError(
                "HTTP 素材响应不是有效 JSON",
                retryable=False,
            ) from error
        if not isinstance(body, dict):
            raise MediaProviderError(
                "HTTP 素材响应顶层必须是对象",
                retryable=False,
            )
        return body

    @staticmethod
    def _validate_closed_object(
        value: Any,
        *,
        allowed: set[str],
        required: set[str] | frozenset[str] = frozenset(),
        label: str,
    ) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise MediaProviderError(
                f"HTTP 素材{label}必须是对象",
                retryable=False,
            )
        if not required <= value.keys() or not value.keys() <= allowed:
            raise MediaProviderError(
                f"HTTP 素材{label}字段不符合 Media Contract v1",
                retryable=False,
            )
        return value

    @classmethod
    def _validate_error_detail(
        cls,
        value: Any,
        *,
        expected_retryable: bool | None = None,
        top_level: bool = False,
    ) -> dict[str, Any]:
        if top_level:
            body = cls._validate_closed_object(
                value,
                allowed={"request_id", "error"},
                required={"error"},
                label="错误响应",
            )
            request_id = body.get("request_id")
            if "request_id" in body and not cls._valid_bounded_text(
                request_id,
                minimum=1,
                maximum=255,
            ):
                raise MediaProviderError(
                    "HTTP 素材错误响应 request_id 无效",
                    retryable=False,
                )
            value = body["error"]
        detail = cls._validate_closed_object(
            value,
            allowed={"code", "message", "retryable"},
            required={"code", "message", "retryable"},
            label="错误详情",
        )
        code = detail["code"]
        message = detail["message"]
        retryable = detail["retryable"]
        if (
            not cls._valid_bounded_text(
                code,
                minimum=1,
                maximum=80,
                ascii_only=True,
            )
            or any(
                not (char.isalnum() or char in "._-")
                for char in code
            )
        ):
            raise MediaProviderError(
                "HTTP 素材错误码无效",
                retryable=False,
            )
        if not cls._valid_bounded_text(
            message,
            minimum=1,
            maximum=500,
        ):
            raise MediaProviderError(
                "HTTP 素材错误消息无效",
                retryable=False,
            )
        if not isinstance(retryable, bool) or (
            expected_retryable is not None and retryable is not expected_retryable
        ):
            raise MediaProviderError(
                "HTTP 素材错误重试语义与状态码不一致",
                retryable=False,
            )
        return detail

    @classmethod
    def _result_item(
        cls,
        body: dict[str, Any],
        *,
        kind: str,
    ) -> dict[str, Any]:
        cls._validate_closed_object(
            body,
            allowed={"request_id", "requestId", "data"},
            required={"data"},
            label="成功响应",
        )
        if "request_id" in body and "requestId" in body:
            raise MediaProviderError(
                "HTTP 素材成功响应 request ID 字段冲突",
                retryable=False,
            )
        for key in ("request_id", "requestId"):
            request_id = body.get(key)
            if key in body and not cls._valid_bounded_text(
                request_id,
                minimum=1,
                maximum=255,
            ):
                raise MediaProviderError(
                    "HTTP 素材成功响应 request_id 无效",
                    retryable=False,
                )
        data = body["data"]
        if kind == "图片" and isinstance(data, list):
            if len(data) != 1 or not isinstance(data[0], dict):
                raise MediaProviderError(
                    "HTTP 图片响应 data 必须包含一个对象",
                    retryable=False,
                )
            return data[0]
        if isinstance(data, dict):
            return data
        raise MediaProviderError(
            f"HTTP {kind}响应 data 格式无效",
            retryable=False,
        )

    @staticmethod
    def _request_metadata(body: dict[str, Any]) -> dict[str, Any]:
        request_id = body.get("request_id") or body.get("requestId")
        return {"request_id": request_id} if request_id else {}

    def _download_url(self, item: dict[str, Any]) -> str | None:
        value = item.get("url") or item.get("download_url")
        if value is None:
            return None
        try:
            _validate_download_url(
                value,
                tuple(self.settings.media_download_allowed_hosts),
                require_https=self.settings.production,
            )
        except ValueError:
            raise MediaProviderError(
                "HTTP 素材响应包含不安全或无效的下载地址",
                retryable=False,
            ) from None
        return value

    @classmethod
    def _validate_optional_media_labels(cls, item: dict[str, Any]) -> None:
        mime_type = item.get("mime_type")
        if mime_type is not None and (
            not cls._valid_bounded_text(
                mime_type,
                minimum=1,
                maximum=120,
                ascii_only=True,
            )
            or mime_type.count("/") != 1
            or not all(mime_type.split("/", 1))
            or any(char.isspace() for char in mime_type)
        ):
            raise MediaProviderError(
                "HTTP 素材响应 mime_type 无效",
                retryable=False,
            )
        filename = item.get("filename")
        if filename is None:
            return
        if not cls._valid_bounded_text(
            filename,
            minimum=1,
            maximum=255,
        ):
            raise MediaProviderError(
                "HTTP 素材响应 filename 无效",
                retryable=False,
            )
        try:
            clean_name = safe_filename(filename)
        except ValueError:
            raise MediaProviderError(
                "HTTP 素材响应 filename 无效",
                retryable=False,
            ) from None
        if clean_name != filename:
            raise MediaProviderError(
                "HTTP 素材响应 filename 必须是安全 basename",
                retryable=False,
            )

    def _image_result(self, body: dict[str, Any]) -> MediaGeneration:
        item = self._result_item(body, kind="图片")
        self._validate_closed_object(
            item,
            allowed={"b64_json", "url", "download_url", "mime_type", "filename"},
            label="图片结果",
        )
        sources = [
            key for key in ("b64_json", "url", "download_url") if key in item
        ]
        if len(sources) != 1:
            raise MediaProviderError(
                "HTTP 图片响应必须且只能包含一种媒体来源",
                retryable=False,
            )
        self._validate_optional_media_labels(item)
        encoded = item.get("b64_json")
        if isinstance(encoded, str) and encoded:
            encoded_limit = ((self.settings.max_upload_bytes + 2) // 3) * 4
            if len(encoded) > encoded_limit:
                raise MediaProviderError(
                    "HTTP 图片响应超过大小限制",
                    retryable=False,
                )
            try:
                content = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error) as error:
                raise MediaProviderError(
                    "HTTP 图片响应包含无效 base64 数据",
                    retryable=False,
                ) from error
            if len(content) > self.settings.max_upload_bytes:
                raise MediaProviderError(
                    "HTTP 图片响应超过大小限制",
                    retryable=False,
                )
            return MediaGeneration(
                status="ready",
                content=content,
                mime_type=item.get("mime_type") or "image/png",
                filename=item.get("filename") or "generated-image.png",
                metadata=self._request_metadata(body),
            )
        url = self._download_url(item)
        if not url:
            raise MediaProviderError(
                "HTTP 图片响应缺少有效媒体来源",
                retryable=False,
            )
        return MediaGeneration(
            status="ready",
            download_url=url,
            mime_type=item.get("mime_type") or "image/png",
            filename=item.get("filename") or "generated-image.png",
            metadata=self._request_metadata(body),
        )

    def _video_result(
        self,
        body: dict[str, Any],
        *,
        fallback_id: str | None = None,
    ) -> MediaGeneration:
        item = self._result_item(body, kind="视频")
        self._validate_closed_object(
            item,
            allowed={
                "id",
                "task_id",
                "status",
                "url",
                "download_url",
                "mime_type",
                "filename",
                "error",
            },
            required={"status"},
            label="视频结果",
        )
        self._validate_optional_media_labels(item)
        raw_status = item["status"]
        if not isinstance(raw_status, str):
            raise MediaProviderError(
                "HTTP 视频响应状态无效",
                retryable=False,
            )
        status = raw_status

        for key in ("id", "task_id"):
            value = item.get(key)
            if value is not None and not self._valid_bounded_text(
                value,
                minimum=1,
                maximum=255,
            ):
                raise MediaProviderError(
                    "HTTP 视频响应包含无效任务 ID",
                    retryable=False,
                )
        identifiers = [
            item[key]
            for key in ("id", "task_id")
            if key in item
        ]
        if len(set(identifiers)) > 1:
            raise MediaProviderError(
                "HTTP 视频响应任务 ID 不一致",
                retryable=False,
            )
        item_task_id = identifiers[0] if identifiers else None
        task_id = item_task_id or fallback_id

        url_fields = [key for key in ("url", "download_url") if key in item]
        if len(url_fields) > 1:
            raise MediaProviderError(
                "HTTP 视频响应不得包含多个下载地址",
                retryable=False,
            )
        url = self._download_url(item)
        metadata = self._request_metadata(body)

        if status in {"queued", "pending", "processing", "running"}:
            if not item_task_id or url_fields or "error" in item:
                raise MediaProviderError(
                    "HTTP 视频活动状态载荷无效",
                    retryable=False,
                )
            return MediaGeneration(
                status="processing",
                external_task_id=task_id,
                metadata=metadata,
            )

        if status in {"ready", "completed", "succeeded"}:
            if len(url_fields) != 1 or not url or "error" in item:
                raise MediaProviderError(
                    "HTTP 视频完成响应载荷无效",
                    retryable=False,
                )
            return MediaGeneration(
                status="ready",
                external_task_id=task_id,
                download_url=url,
                mime_type=item.get("mime_type") or "video/mp4",
                filename=item.get("filename") or "generated-video.mp4",
                metadata=metadata,
            )

        if status in {"failed", "cancelled", "expired"}:
            if url_fields or "error" not in item:
                raise MediaProviderError(
                    "HTTP 视频失败终态载荷无效",
                    retryable=False,
                )
            self._validate_error_detail(item["error"], expected_retryable=False)
            raise MediaProviderError(
                "HTTP 视频任务返回失败终态",
                retryable=False,
            )

        raise MediaProviderError(
            "HTTP 视频任务返回未知或缺失状态",
            retryable=False,
        )

def build_media_provider(settings: Settings, kind: str) -> MediaProvider:
    provider_name = (
        settings.image_provider if kind == "image" else settings.video_provider
    )
    if provider_name == "mock":
        return MockMediaProvider()
    if provider_name == "http":
        return HTTPMediaProvider(settings)
    raise ValueError(f"不支持的素材生成 provider: {provider_name}")


def _validate_download_url(
    url: str,
    allowed_hosts: tuple[str, ...],
    *,
    require_https: bool = False,
) -> None:
    if (
        not isinstance(url, str)
        or not 1 <= len(url) <= 2048
        or any(ord(char) <= 0x20 or ord(char) == 0x7F for char in url)
    ):
        raise ValueError("素材下载地址必须是有效的 HTTP(S) URL")
    try:
        url.encode("utf-8")
        parsed = urlparse(url)
        port = parsed.port
    except ValueError as error:
        raise ValueError("素材下载地址必须是有效的 HTTP(S) URL") from error
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.fragment
    ):
        raise ValueError("素材下载地址必须是有效的 HTTP(S) URL")
    if require_https and parsed.scheme != "https":
        raise ValueError("生产素材下载地址必须使用 HTTPS")
    if require_https and port not in {None, 443}:
        raise ValueError("生产素材下载地址只允许默认 HTTPS 端口")
    if parsed.username or parsed.password:
        raise ValueError("素材下载地址不得包含 URL 凭据")
    normalized_host_values = [
        normalize_exact_host(host)
        for host in allowed_hosts
    ]
    if any(host is None for host in normalized_host_values):
        raise ValueError("素材下载域名允许列表包含无效主机名")
    normalized_hosts = {
        host for host in normalized_host_values if host is not None
    }
    if not normalized_hosts:
        raise ValueError("素材下载地址需要非空的精确域名允许列表")
    if parsed.hostname.lower() not in normalized_hosts:
        raise ValueError("素材下载地址不在允许的域名列表中")


def download_generated_media(
    generation: MediaGeneration,
    *,
    client: httpx.Client | None = None,
    max_bytes: int = 100 * 1024 * 1024,
    allowed_hosts: tuple[str, ...] = (),
    require_https: bool = False,
    max_redirects: int = 5,
) -> bytes:
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError("素材下载大小上限无效")
    if (
        isinstance(max_redirects, bool)
        or not isinstance(max_redirects, int)
        or not 0 <= max_redirects <= 20
    ):
        raise ValueError("素材下载重定向上限无效")
    if generation.content is not None:
        if not isinstance(generation.content, bytes):
            raise ValueError("素材生成内容格式无效")
        if len(generation.content) > max_bytes:
            raise ValueError("模型生成素材超过大小限制")
        return generation.content
    if not generation.download_url:
        raise ValueError("素材生成结果没有内容或下载地址")
    http = client or httpx.Client(timeout=120, follow_redirects=False)
    owns_client = client is None
    current_url = generation.download_url
    redirect_statuses = {301, 302, 303, 307, 308}
    try:
        for redirect_count in range(max_redirects + 1):
            _validate_download_url(
                current_url,
                allowed_hosts,
                require_https=require_https,
            )
            try:
                response_context = http.stream(
                    "GET",
                    current_url,
                    follow_redirects=False,
                )
                with response_context as response:
                    if response.status_code in redirect_statuses:
                        location = response.headers.get("Location")
                        if not location:
                            raise ValueError("素材下载重定向缺少 Location")
                        if redirect_count >= max_redirects:
                            raise ValueError("素材下载重定向次数超过限制")
                        next_url = urljoin(str(response.url), location)
                        _validate_download_url(
                            next_url,
                            allowed_hosts,
                            require_https=require_https,
                        )
                        current_url = next_url
                        continue
                    if not 200 <= response.status_code < 300:
                        retryable = response.status_code in {408, 425, 429} or (
                            500 <= response.status_code < 600
                        )
                        disposition = "暂时" if retryable else "永久"
                        raise MediaProviderError(
                            f"素材下载服务返回{disposition}错误"
                            f"（状态码 {response.status_code}）",
                            retryable=retryable,
                            status_code=response.status_code,
                            retry_after_seconds=(
                                HTTPMediaProvider._retry_after_seconds(response)
                                if retryable
                                else None
                            ),
                        )
                    content_length = response.headers.get(
                        "Content-Length",
                        "",
                    ).strip()
                    if content_length.isdigit() and (
                        len(content_length) > 20
                        or int(content_length) > max_bytes
                    ):
                        raise ValueError("模型生成素材超过大小限制")
                    content = bytearray()
                    for chunk in response.iter_bytes():
                        if len(content) + len(chunk) > max_bytes:
                            raise ValueError("模型生成素材超过大小限制")
                        content.extend(chunk)
                    return bytes(content)
            except httpx.TimeoutException:
                raise MediaProviderError(
                    "素材下载请求超时",
                    retryable=True,
                ) from None
            except httpx.RequestError:
                raise MediaProviderError(
                    "素材下载网络请求失败",
                    retryable=True,
                ) from None
        raise ValueError("素材下载重定向次数超过限制")
    finally:
        if owns_client:
            http.close()
