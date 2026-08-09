from __future__ import annotations

from .embeddings import dashscope_compatible_base
from .providers import MockProvider, OpenAICompatibleProvider, Provider
from .settings import Settings


def build_text_provider(settings: Settings, override: str | None = None) -> Provider:
    provider = (override or settings.text_provider).strip().lower()
    if provider == "mock":
        return MockProvider()
    if provider in {"openai-compatible", "dashscope"}:
        api_base = (
            dashscope_compatible_base(settings)
            if provider == "dashscope"
            else settings.model_api_base
        )
        api_key = (
            settings.dashscope_api_key
            if provider == "dashscope"
            else settings.model_api_key
        )
        if not api_base or not api_key:
            raise ValueError(f"{provider} 的 API Base 或 API Key 未配置")
        return OpenAICompatibleProvider(
            api_base=api_base,
            api_key=api_key,
            model=settings.text_model,
            provider_name=provider,
        )
    raise ValueError(f"不支持的文本模型 Provider: {provider}")
