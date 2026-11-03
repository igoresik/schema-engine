"""Scripted provider for tests and offline demos. Never touches the network."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from schema_engine.providers.base import Completion, LLMProvider, Message, ProviderError, Usage

Reply = str | ProviderError | Callable[[list[Message]], str]


class FakeProvider(LLMProvider):
    """Returns queued replies in order and records every request.

    A reply can be a string, an exception to raise, or a function of the
    messages. When the queue runs out the last reply is repeated.
    """

    def __init__(self, replies: Iterable[Reply] = ("ok",), *, model: str = "fake-model") -> None:
        self.name = "fake"
        self.model = model
        self.base_url = "fake://local"
        self._replies = list(replies) or ["ok"]
        self.calls: list[list[Message]] = []

    def complete(
        self, messages: list[Message], *, max_tokens: int = 1024, temperature: float | None = None
    ) -> Completion:
        self.calls.append(list(messages))
        index = min(len(self.calls) - 1, len(self._replies) - 1)
        reply = self._replies[index]
        if isinstance(reply, ProviderError):
            raise reply
        text = reply(messages) if callable(reply) else reply
        prompt_tokens = sum(len(m.content) for m in messages) // 4
        return Completion(text, self.model, Usage(prompt_tokens, len(text) // 4), latency_ms=0.0)
