from __future__ import annotations

from .providers import MockProvider, OpenAICompatibleProvider, Provider
from .settings import Settings
from .provider_resources import ProviderResourceLimits


def build_text_provider(settings: Settings, override: str | None = None) -> Provider:
    provider = (override or settings.text_provider).strip().lower()
    if provider == "mock":
        return MockProvider()
    if provider == "openai-compatible":
        if (
            not settings.model_api_base
            or not settings.model_api_key
            or not settings.text_model
        ):
            raise ValueError("openai-compatible 的 API Base、API Key 或模型名未配置")
        result = OpenAICompatibleProvider(
            api_base=settings.model_api_base,
            api_key=settings.model_api_key,
            model=settings.text_model,
            timeout_seconds=settings.model_request_timeout_seconds,
            provider_name=provider,
            max_output_tokens=settings.model_max_output_tokens,
            output_limit_field=settings.model_output_limit_field,
            max_request_bytes=settings.model_max_request_bytes,
            max_response_bytes=settings.model_max_response_bytes,
        )
        result.resource_limits = ProviderResourceLimits.from_settings(settings)
        return result
    raise ValueError(f"不支持的文本模型 Provider: {provider}")
