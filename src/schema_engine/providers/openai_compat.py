"""OpenAI-compatible chat completions (OpenAI, Groq, OpenRouter, Gemini).

Requests go straight from this machine to the provider with the user's own
bearer token; there is no proxy in between.
"""

from __future__ import annotations

from typing import Any

from schema_engine.providers.base import Completion, HTTPProvider, Message, ProviderError, Usage


class OpenAICompatibleProvider(HTTPProvider):
    def __init__(self, *, api_key: str, token_param: str = "max_tokens", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._api_key = api_key
        self._token_param = token_param

    def __repr__(self) -> str:  # never show the key
        return f"OpenAICompatibleProvider(name={self.name!r}, model={self.model!r})"

    def _headers(self) -> dict[str, str]:
        return {**super()._headers(), "Authorization": f"Bearer {self._api_key}"}

    def complete(
        self, messages: list[Message], *, max_tokens: int = 1024, temperature: float = 0.0
    ) -> Completion:
        payload = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
            self._token_param: max_tokens,
        }
        body, latency = self._post("/chat/completions", payload)
        try:
            text = body["choices"][0]["message"].get("content") or ""
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            raise ProviderError("unexpected response shape from chat/completions") from exc
        usage = body.get("usage") or {}
        return Completion(
            text=text,
            model=body.get("model", self.model),
            usage=Usage(int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))),
            latency_ms=latency,
        )
