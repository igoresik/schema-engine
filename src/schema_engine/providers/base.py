"""Strategy interface every LLM backend implements.

The rest of the engine only talks to ``LLMProvider``. Swapping a local model
for a cloud one is a config change, not a code change.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal

import httpx

Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True)
class Message:
    role: Role
    content: str


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.prompt_tokens + other.prompt_tokens,
            self.completion_tokens + other.completion_tokens,
        )


@dataclass(frozen=True)
class Completion:
    text: str
    model: str
    usage: Usage
    latency_ms: float


@dataclass(frozen=True)
class RateLimits:
    """Remaining quota as reported by response headers, when the provider sends them."""

    remaining_requests: int | None = None
    remaining_tokens: int | None = None
    reset_requests: str | None = None
    reset_tokens: str | None = None

    @classmethod
    def from_headers(cls, headers: Mapping[str, str]) -> RateLimits:
        def num(key: str) -> int | None:
            value = headers.get(key)
            try:
                return int(float(value)) if value is not None else None
            except ValueError:
                return None

        return cls(
            remaining_requests=num("x-ratelimit-remaining-requests"),
            remaining_tokens=num("x-ratelimit-remaining-tokens"),
            reset_requests=headers.get("x-ratelimit-reset-requests"),
            reset_tokens=headers.get("x-ratelimit-reset-tokens"),
        )


@dataclass(frozen=True)
class HealthReport:
    provider: str
    model: str
    base_url: str
    ok: bool
    latency_ms: float | None = None
    detail: str = ""
    rate_limits: RateLimits = field(default_factory=RateLimits)
    usage: Usage = field(default_factory=Usage)


class ProviderError(RuntimeError):
    """Base class for provider failures. Messages never include the API key."""


class AuthenticationError(ProviderError):
    """Key missing, wrong or lacking permission (HTTP 401/403)."""


class RateLimitError(ProviderError):
    """HTTP 429. ``retry_after`` is seconds, if the provider said."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class ModelNotFoundError(ProviderError):
    """The configured model does not exist on this endpoint."""


class ProviderUnavailableError(ProviderError):
    """Endpoint unreachable, timed out, or returned a 5xx."""


class LLMProvider(ABC):
    """A chat-completion backend."""

    name: str
    model: str
    base_url: str
    last_rate_limits: RateLimits = RateLimits()

    @abstractmethod
    def complete(
        self,
        messages: list[Message],
        *,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> Completion:
        """Return the model's reply. Raises a ``ProviderError`` subclass on failure."""

    def health_check(self) -> HealthReport:
        """Preflight: one-token request to prove reachability, auth and model."""
        try:
            completion = self.complete([Message("user", "ping")], max_tokens=1)
        except ProviderError as exc:
            return HealthReport(
                self.name, self.model, self.base_url, ok=False, detail=_describe(exc)
            )
        return HealthReport(
            self.name,
            self.model,
            self.base_url,
            ok=True,
            latency_ms=completion.latency_ms,
            detail="reachable, authenticated, model available",
            rate_limits=self.last_rate_limits,
            usage=completion.usage,
        )

    def close(self) -> None:  # noqa: B027  # pragma: no cover - optional hook
        """Release network resources."""


def _describe(exc: ProviderError) -> str:
    kind = {
        AuthenticationError: "authentication failed",
        RateLimitError: "rate limited",
        ModelNotFoundError: "model not found",
        ProviderUnavailableError: "endpoint unavailable",
    }.get(type(exc), "request failed")
    return f"{kind}: {exc}"


class HTTPProvider(LLMProvider):
    """Shared plumbing for HTTP backends: client, retries, error mapping."""

    def __init__(
        self,
        *,
        name: str,
        model: str,
        base_url: str,
        timeout: float = 60.0,
        max_retries: int = 2,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.name = name
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self._client = client or httpx.Client(timeout=timeout)
        self._sleep = sleep
        self.last_rate_limits = RateLimits()

    def _headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json"}

    def _post(self, path: str, payload: dict) -> tuple[dict, float]:
        """POST with bounded retries on 429/5xx/network errors. Returns (json, latency_ms)."""
        url = f"{self.base_url}{path}"
        attempt = 0
        while True:
            start = time.perf_counter()
            try:
                response = self._client.post(url, json=payload, headers=self._headers())
            except httpx.TimeoutException as exc:
                error: ProviderError = ProviderUnavailableError(f"timed out calling {url}")
                cause: Exception = exc
            except httpx.TransportError as exc:
                error = ProviderUnavailableError(f"could not reach {url} ({type(exc).__name__})")
                cause = exc
            else:
                latency = (time.perf_counter() - start) * 1000
                self.last_rate_limits = RateLimits.from_headers(response.headers)
                if response.is_success:
                    try:
                        return response.json(), latency
                    except ValueError as exc:
                        raise ProviderError(f"{url} returned non-JSON body") from exc
                error, cause = _error_for(response), None
            retryable = isinstance(error, RateLimitError | ProviderUnavailableError)
            if not retryable or attempt >= self.max_retries:
                raise error from cause
            delay = getattr(error, "retry_after", None) or 2**attempt
            self._sleep(min(delay, 30))
            attempt += 1

    def close(self) -> None:
        self._client.close()


def _error_for(response: httpx.Response) -> ProviderError:
    status = response.status_code
    message = f"HTTP {status}: {_error_message(response)}"
    if status in (401, 403):
        return AuthenticationError(message)
    if status == 404:
        return ModelNotFoundError(message)
    if status == 429:
        return RateLimitError(message, retry_after=_retry_after(response))
    if status >= 500:
        return ProviderUnavailableError(message)
    return ProviderError(message)


def _error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:200] or response.reason_phrase
    err = body.get("error", body) if isinstance(body, dict) else body
    if isinstance(err, dict):
        return str(err.get("message") or err)[:200]
    return str(err)[:200]


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None
