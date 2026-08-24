from __future__ import annotations

import hashlib
import json
from typing import Any
from urllib.parse import urlparse

import httpx

from .media_providers import MediaProviderError
from .network_validation import normalize_exact_host
from .settings import Settings


_OPEN_LICENSES = {"cc0", "pdm", "by", "by-sa"}
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_WIKIMEDIA_PAGE_HOSTS = {"commons.wikimedia.org"}
_CREATIVE_COMMONS_HOSTS = {"creativecommons.org"}


class ImageSearchError(MediaProviderError):
    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message, retryable=retryable)


def _bounded_text(value: Any, maximum: int) -> str:
    if not isinstance(value, str):
        return ""
    normalized = value.strip()
    try:
        normalized.encode("utf-8")
    except UnicodeEncodeError:
        return ""
    return normalized[:maximum]


def _safe_url(
    value: Any,
    *,
    allowed_hosts: set[str] | None = None,
) -> str:
    if not isinstance(value, str) or len(value) > 4096:
        return ""
    try:
        parsed = urlparse(value)
        parsed.port
    except ValueError:
        return ""
    host = normalize_exact_host(parsed.hostname or "")
    if (
        parsed.scheme != "https"
        or not host
        or parsed.username
        or parsed.password
        or parsed.fragment
        or (allowed_hosts is not None and host not in allowed_hosts)
    ):
        return ""
    return value


class OpenverseImageSearchProvider:
    """Search openly licensed Wikimedia images through the Openverse API."""

    provider_name = "openverse"

    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self.settings = settings
        self.client = client
        self.base_url = settings.openverse_api_base.rstrip("/")
        self.allowed_download_hosts = set(
            settings.image_search_download_allowed_hosts
        )

    def search(self, *, query: str, limit: int | None = None) -> list[dict[str, Any]]:
        normalized_query = _bounded_text(query, 500)
        if not normalized_query:
            raise ImageSearchError("图片搜索词为空", retryable=False)
        page_size = min(
            max(1, limit or self.settings.image_search_result_limit),
            self.settings.image_search_result_limit,
        )
        owns_client = self.client is None
        client = self.client or httpx.Client(timeout=30, follow_redirects=False)
        try:
            try:
                with client.stream(
                    "GET",
                    f"{self.base_url}/images/",
                    params={
                        "q": normalized_query,
                        "page_size": page_size * 3,
                        "source": "wikimedia",
                        "license": "cc0,pdm,by,by-sa",
                        "mature": "false",
                    },
                    headers={
                        "Accept": "application/json",
                        "User-Agent": "ContentFlow/0.2 (open-media-search)",
                    },
                    follow_redirects=False,
                ) as response:
                    if response.status_code == 429:
                        raise ImageSearchError("图片搜索服务限流", retryable=True)
                    if response.status_code >= 500:
                        raise ImageSearchError("图片搜索服务暂时不可用", retryable=True)
                    if response.status_code != 200:
                        raise ImageSearchError("图片搜索请求被拒绝", retryable=False)
                    content_length = response.headers.get("Content-Length", "")
                    if content_length.isdigit() and int(content_length) > _MAX_RESPONSE_BYTES:
                        raise ImageSearchError("图片搜索响应超过大小限制", retryable=False)
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        if len(raw) + len(chunk) > _MAX_RESPONSE_BYTES:
                            raise ImageSearchError(
                                "图片搜索响应超过大小限制",
                                retryable=False,
                            )
                        raw.extend(chunk)
                    try:
                        body = json.loads(bytes(raw).decode("utf-8"))
                    except (UnicodeDecodeError, ValueError):
                        raise ImageSearchError(
                            "图片搜索响应不是有效 JSON",
                            retryable=False,
                        ) from None
            except httpx.TimeoutException:
                raise ImageSearchError("图片搜索请求超时", retryable=True) from None
            except httpx.RequestError:
                raise ImageSearchError("图片搜索网络请求失败", retryable=True) from None
        finally:
            if owns_client:
                client.close()

        results = body.get("results") if isinstance(body, dict) else None
        if not isinstance(results, list):
            raise ImageSearchError("图片搜索响应结构无效", retryable=False)
        candidates = []
        for raw_item in results:
            if not isinstance(raw_item, dict):
                continue
            license_name = _bounded_text(raw_item.get("license"), 40).lower()
            if license_name not in _OPEN_LICENSES:
                continue
            download_url = _safe_url(
                raw_item.get("url"),
                allowed_hosts=self.allowed_download_hosts,
            )
            if not download_url:
                continue
            thumbnail = _safe_url(
                raw_item.get("thumbnail"),
                allowed_hosts=self.allowed_download_hosts,
            )
            candidate_id = hashlib.sha256(download_url.encode("utf-8")).hexdigest()[:24]
            candidate = {
                "id": candidate_id,
                "title": _bounded_text(raw_item.get("title"), 300)
                or "未命名开放授权图片",
                "creator": _bounded_text(raw_item.get("creator"), 200) or "未知作者",
                "creator_url": _safe_url(
                    raw_item.get("creator_url"),
                    allowed_hosts=_WIKIMEDIA_PAGE_HOSTS,
                ),
                "license": license_name,
                "license_version": _bounded_text(
                    raw_item.get("license_version"), 20
                ),
                "license_url": _safe_url(
                    raw_item.get("license_url"),
                    allowed_hosts=_CREATIVE_COMMONS_HOSTS,
                ),
                "source": _bounded_text(raw_item.get("source"), 80),
                "provider": _bounded_text(raw_item.get("provider"), 80),
                "landing_url": _safe_url(
                    raw_item.get("foreign_landing_url"),
                    allowed_hosts=_WIKIMEDIA_PAGE_HOSTS,
                ),
                "download_url": download_url,
                "thumbnail_url": thumbnail,
                "width": (
                    raw_item.get("width")
                    if isinstance(raw_item.get("width"), int)
                    else None
                ),
                "height": (
                    raw_item.get("height")
                    if isinstance(raw_item.get("height"), int)
                    else None
                ),
            }
            candidates.append(candidate)
            if len(candidates) >= page_size:
                break
        if not candidates:
            raise ImageSearchError(
                "没有找到满足商业使用、可修改和安全下载限制的图片",
                retryable=False,
            )
        return candidates


def build_image_search_provider(
    settings: Settings,
    client: httpx.Client | None = None,
) -> OpenverseImageSearchProvider:
    if settings.image_search_provider != "openverse":
        raise ImageSearchError("当前环境未启用图片搜索 Provider", retryable=False)
    return OpenverseImageSearchProvider(settings, client=client)
