import json

import pytest
from typer.testing import CliRunner

from schema_engine import __version__
from schema_engine.cli import app
from schema_engine.providers import AuthenticationError, FakeProvider, RateLimits

runner = CliRunner()


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_samples_then_profile(tmp_path):
    result = runner.invoke(app, ["samples", str(tmp_path), "--rows", "12"])
    assert result.exit_code == 0, result.output
    csv_path = tmp_path / "dirty_orders.csv"
    assert csv_path.exists()

    result = runner.invoke(app, ["profile", str(csv_path), "--privacy-mode", "none", "--compact"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout.splitlines()[0])
    assert data["privacy_mode"] == "none"
    assert "sample" not in data


def test_profile_xlsx_sheet_index(tmp_path):
    runner.invoke(app, ["samples", str(tmp_path), "--rows", "5"])
    result = runner.invoke(app, ["profile", str(tmp_path / "dirty_orders.xlsx"), "--sheet", "0"])
    assert result.exit_code == 0, result.output
    assert '"privacy_mode": "masked"' in result.stdout


def test_profile_unsupported_type(tmp_path):
    path = tmp_path / "data.json"
    path.write_text("{}")
    result = runner.invoke(app, ["profile", str(path)])
    assert result.exit_code == 2


@pytest.fixture
def clean_env(tmp_path, monkeypatch):
    """No real keys, .env or user config leak into test-connection tests."""
    for name in ("OPENAI_API_KEY", "GROQ_API_KEY", "OLLAMA_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    for name in ("PROVIDER", "MODEL", "BASE_URL", "TIMEOUT"):
        monkeypatch.delenv(f"SCHEMA_ENGINE_{name}", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)


def use_fake(monkeypatch, fake):
    monkeypatch.setattr("schema_engine.cli.create_provider", lambda settings: fake)


def test_test_connection_ok(clean_env, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-1234567890abcd")
    fake = FakeProvider(["p"])
    fake.last_rate_limits = RateLimits(remaining_requests=29, remaining_tokens=5999)
    use_fake(monkeypatch, fake)

    result = runner.invoke(app, ["test-connection", "--provider", "groq", "--model", "m1"])
    assert result.exit_code == 0, result.output
    assert "provider: groq" in result.stdout
    assert "model: m1" in result.stdout
    assert "api_key: gsk…abcd" in result.stdout
    assert "1234567890" not in result.output
    assert "ok: 0 ms, 1 tokens used" in result.stdout
    assert "rate limit left: 29 requests, 5999 tokens" in result.stdout
    assert len(fake.calls) == 1


def test_test_connection_failure(clean_env, monkeypatch):
    use_fake(monkeypatch, FakeProvider([AuthenticationError("HTTP 401: bad key")]))
    result = runner.invoke(app, ["test-connection"])
    assert result.exit_code == 1
    assert "provider: ollama" in result.stdout
    assert "failed: authentication failed: HTTP 401: bad key" in result.stderr


def test_test_connection_missing_key(clean_env):
    result = runner.invoke(app, ["test-connection", "--provider", "openai"])
    assert result.exit_code == 2
    assert "No API key for 'openai'" in result.stderr
