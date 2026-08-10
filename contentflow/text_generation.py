from __future__ import annotations

from .providers import MockProvider, OpenAICompatibleProvider, Provider
from .settings import Settings


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
        return OpenAICompatibleProvider(
            api_base=settings.model_api_base,
            api_key=settings.model_api_key,
            model=settings.text_model,
            provider_name=provider,
        )
    raise ValueError(f"不支持的文本模型 Provider: {provider}")
