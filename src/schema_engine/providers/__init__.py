"""LLM provider strategies and the factory that picks one from settings."""

from __future__ import annotations

import httpx

from schema_engine.config import Settings, require_key
from schema_engine.providers.base import (
    AuthenticationError,
    Completion,
    HealthReport,
    LLMProvider,
    Message,
    ModelNotFoundError,
    ProviderError,
    ProviderUnavailableError,
    RateLimitError,
    RateLimits,
    Usage,
)
from schema_engine.providers.fake import FakeProvider
from schema_engine.providers.ollama import OllamaProvider
from schema_engine.providers.openai_compat import OpenAICompatibleProvider


def create_provider(settings: Settings, *, client: httpx.Client | None = None) -> LLMProvider:
    """Build the provider described by ``settings``."""
    common = {
        "name": settings.provider,
        "model": settings.model,
        "base_url": settings.base_url,
        "timeout": settings.timeout,
        "max_retries": settings.max_retries,
        "client": client,
    }
    if settings.preset.kind == "ollama":
        return OllamaProvider(**common)
    key = require_key(settings)
    assert key is not None  # require_key raises otherwise
    return OpenAICompatibleProvider(
        api_key=key,
        **common,
    )


__all__ = [
    "AuthenticationError",
    "Completion",
    "FakeProvider",
    "HealthReport",
    "LLMProvider",
    "Message",
    "ModelNotFoundError",
    "OllamaProvider",
    "OpenAICompatibleProvider",
    "ProviderError",
    "ProviderUnavailableError",
    "RateLimitError",
    "RateLimits",
    "Usage",
    "create_provider",
]
