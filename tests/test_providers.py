import json

import httpx
import pytest

from schema_engine.config import ConfigError, Settings
from schema_engine.providers import (
    AuthenticationError,
    FakeProvider,
    Message,
    ModelNotFoundError,
    OllamaProvider,
    OpenAICompatibleProvider,
    ProviderError,
    ProviderUnavailableError,
    RateLimitError,
    RateLimits,
    Usage,
    create_provider,
)

KEY = "sk-test-1234567890abcd"
PING = [Message("user", "ping")]


class Recorder:
    """httpx transport that replays scripted responses and keeps every request."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        reply = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(reply, Exception):
            raise reply
        return reply

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))

    def body(self, index: int = -1) -> dict:
        return json.loads(self.requests[index].content)


def openai_reply(text="hi", **extra):
    body = {
        "model": "gpt-6-luna",
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": 7, "completion_tokens": 1},
        **extra,
    }
    return httpx.Response(200, json=body)


def make_openai(recorder, sleeps=None, **kwargs):
    return OpenAICompatibleProvider(
        api_key=KEY,
        name="openai",
        model="gpt-6-luna",
        base_url="https://api.openai.com/v1/",
        client=recorder.client(),
        sleep=(sleeps.append if sleeps is not None else lambda _: None),
        **kwargs,
    )


def make_ollama(recorder):
    return OllamaProvider(
        name="ollama", model="llama3.1", base_url="http://localhost:11434", client=recorder.client()
    )


def test_openai_request_and_reply():
    rec = Recorder(openai_reply("hello"))
    result = make_openai(rec).complete(PING, max_tokens=5)

    request = rec.requests[0]
    assert str(request.url) == "https://api.openai.com/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert rec.body() == {
        "model": "gpt-6-luna",
        "messages": [{"role": "user", "content": "ping"}],
        "max_completion_tokens": 5,
    }
    assert (result.text, result.model, result.usage) == ("hello", "gpt-6-luna", Usage(7, 1))
    assert result.latency_ms >= 0


def test_openai_sends_temperature_only_when_set():
    rec = Recorder(openai_reply())
    make_openai(rec).complete(PING, temperature=0.0)
    assert rec.body()["temperature"] == 0.0


def test_openai_empty_content_is_empty_text():
    # Reasoning models can spend a tiny budget on thinking and return no content.
    rec = Recorder(
        httpx.Response(200, json={"choices": [{"message": {"content": None}}], "usage": None})
    )
    result = make_openai(rec).complete(PING, max_tokens=1)
    assert (result.text, result.model, result.usage) == ("", "gpt-6-luna", Usage())


def test_openai_unexpected_shape():
    rec = Recorder(httpx.Response(200, json={"choices": []}))
    with pytest.raises(ProviderError, match="unexpected response shape"):
        make_openai(rec).complete(PING)


def test_openai_repr_hides_key():
    assert KEY not in repr(make_openai(Recorder(openai_reply())))


def test_ollama_request_and_reply():
    reply = {
        "model": "llama3.1",
        "message": {"role": "assistant", "content": "pong"},
        "prompt_eval_count": 11,
        "eval_count": 2,
    }
    rec = Recorder(httpx.Response(200, json=reply))
    result = make_ollama(rec).complete(PING, max_tokens=3)

    assert str(rec.requests[0].url) == "http://localhost:11434/api/chat"
    assert "authorization" not in rec.requests[0].headers
    assert rec.body() == {
        "model": "llama3.1",
        "messages": [{"role": "user", "content": "ping"}],
        "stream": False,
        "options": {"num_predict": 3},
    }
    assert (result.text, result.usage) == ("pong", Usage(11, 2))

    make_ollama(rec).complete(PING, temperature=0.2)
    assert rec.body()["options"] == {"num_predict": 1024, "temperature": 0.2}


def test_ollama_unexpected_shape():
    rec = Recorder(httpx.Response(200, json={"done": True}))
    with pytest.raises(ProviderError, match="unexpected response shape"):
        make_ollama(rec).complete(PING)


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (400, ProviderError),
        (401, AuthenticationError),
        (403, AuthenticationError),
        (404, ModelNotFoundError),
    ],
)
def test_client_errors_are_mapped_and_not_retried(status, error):
    rec = Recorder(httpx.Response(status, json={"error": {"message": "nope"}}))
    with pytest.raises(error, match=f"HTTP {status}: nope") as info:
        make_openai(rec).complete(PING)
    assert type(info.value) is error
    assert len(rec.requests) == 1
    assert KEY not in str(info.value)


def test_error_message_from_text_or_status():
    rec = Recorder(httpx.Response(400, text="plain failure"))
    with pytest.raises(ProviderError, match="plain failure"):
        make_openai(rec).complete(PING)
    rec = Recorder(httpx.Response(400))
    with pytest.raises(ProviderError, match="Bad Request"):
        make_openai(rec).complete(PING)
    rec = Recorder(httpx.Response(400, json={"error": "flat message"}))
    with pytest.raises(ProviderError, match="flat message"):
        make_openai(rec).complete(PING)
    rec = Recorder(httpx.Response(400, json=["odd"]))
    with pytest.raises(ProviderError, match="odd"):
        make_openai(rec).complete(PING)


def test_rate_limit_waits_for_retry_after_then_succeeds():
    sleeps = []
    rec = Recorder(httpx.Response(429, headers={"retry-after": "3"}), openai_reply("ok"))
    assert make_openai(rec, sleeps).complete(PING).text == "ok"
    assert sleeps == [3.0]


def test_server_errors_back_off_then_give_up():
    sleeps = []
    rec = Recorder(httpx.Response(503, text="busy"))
    with pytest.raises(ProviderUnavailableError, match="HTTP 503"):
        make_openai(rec, sleeps, max_retries=2).complete(PING)
    assert len(rec.requests) == 3
    assert sleeps == [1, 2]


def test_retry_wait_is_capped():
    sleeps = []
    rec = Recorder(httpx.Response(429, headers={"retry-after": "600"}), openai_reply())
    make_openai(rec, sleeps).complete(PING)
    assert sleeps == [30]


def test_bad_retry_after_falls_back_to_backoff():
    sleeps = []
    rec = Recorder(httpx.Response(429, headers={"retry-after": "later"}), openai_reply())
    make_openai(rec, sleeps).complete(PING)
    assert sleeps == [1]


def test_network_failures():
    rec = Recorder(httpx.ConnectTimeout("slow"))
    with pytest.raises(ProviderUnavailableError, match="timed out"):
        make_openai(rec, max_retries=0).complete(PING)
    rec = Recorder(httpx.ConnectError("refused"), openai_reply("back"))
    assert make_openai(rec).complete(PING).text == "back"


def test_non_json_success_body():
    rec = Recorder(httpx.Response(200, text="<html>"))
    with pytest.raises(ProviderError, match="non-JSON"):
        make_openai(rec).complete(PING)


def test_rate_limit_headers():
    headers = {
        "x-ratelimit-remaining-requests": "99",
        "x-ratelimit-remaining-tokens": "149990.0",
        "x-ratelimit-reset-requests": "1s",
        "x-ratelimit-reset-tokens": "6ms",
    }
    provider = make_openai(Recorder(httpx.Response(200, headers=headers, json={})))
    with pytest.raises(ProviderError):
        provider.complete(PING)
    assert provider.last_rate_limits == RateLimits(99, 149990, "1s", "6ms")
    assert RateLimits.from_headers({"x-ratelimit-remaining-requests": "n/a"}) == RateLimits()


def test_health_check_reports_success_and_failure():
    headers = {"x-ratelimit-remaining-requests": "5"}
    ok = make_openai(Recorder(openai_reply(""))).health_check()
    assert ok.ok
    assert ok.usage.total_tokens == 8

    rec = Recorder(httpx.Response(200, headers=headers, json=openai_reply().json()))
    report = make_openai(rec).health_check()
    assert report.rate_limits.remaining_requests == 5
    assert rec.body()["max_completion_tokens"] == 1

    bad = make_openai(Recorder(httpx.Response(401, json={"error": {"message": "bad key"}})))
    report = bad.health_check()
    assert not report.ok
    assert report.detail == "authentication failed: HTTP 401: bad key"


@pytest.mark.parametrize(
    ("error", "label"),
    [
        (RateLimitError("slow down"), "rate limited"),
        (ModelNotFoundError("gone"), "model not found"),
        (ProviderUnavailableError("down"), "endpoint unavailable"),
        (ProviderError("odd"), "request failed"),
    ],
)
def test_health_check_labels(error, label):
    report = FakeProvider([error]).health_check()
    assert not report.ok
    assert report.detail.startswith(f"{label}: ")


def test_close_closes_client():
    provider = make_openai(Recorder(openai_reply()))
    provider.close()
    assert provider._client.is_closed


def test_create_provider_picks_strategy():
    ollama = create_provider(Settings("ollama", "llama3.1", "http://localhost:11434", None))
    assert isinstance(ollama, OllamaProvider)
    groq = create_provider(Settings("groq", "m", "https://api.groq.com/openai/v1", "gsk"))
    assert isinstance(groq, OpenAICompatibleProvider)
    assert groq.name == "groq"
    with pytest.raises(ConfigError, match="GROQ_API_KEY"):
        create_provider(Settings("groq", "m", "u", None))


def test_fake_provider_scripts_replies():
    fake = FakeProvider(["one", lambda msgs: msgs[-1].content.upper()])
    assert fake.complete(PING).text == "one"
    assert fake.complete([Message("user", "again")]).text == "AGAIN"
    assert fake.complete([Message("user", "last")]).text == "LAST"  # last reply repeats
    assert len(fake.calls) == 3
    assert FakeProvider([]).complete(PING).text == "ok"


def test_usage_adds_up():
    assert Usage(3, 4) + Usage(1, 1) == Usage(4, 5)
    assert Usage(3, 4).total_tokens == 7
