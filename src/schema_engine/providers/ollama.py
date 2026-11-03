"""Local inference through Ollama's native chat API (default http://localhost:11434)."""

from __future__ import annotations

from schema_engine.providers.base import Completion, HTTPProvider, Message, ProviderError, Usage


class OllamaProvider(HTTPProvider):
    def complete(
        self, messages: list[Message], *, max_tokens: int = 1024, temperature: float = 0.0
    ) -> Completion:
        payload = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        body, latency = self._post("/api/chat", payload)
        try:
            text = body["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise ProviderError("unexpected response shape from /api/chat") from exc
        return Completion(
            text=text,
            model=body.get("model", self.model),
            usage=Usage(int(body.get("prompt_eval_count", 0)), int(body.get("eval_count", 0))),
            latency_ms=latency,
        )
