import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from schema_engine import __version__
from schema_engine.cli import _default_output, app
from schema_engine.io import load_dataset
from schema_engine.presets import EXPORT_PRESETS
from schema_engine.providers import AuthenticationError, FakeProvider, RateLimits
from schema_engine.samples import write_samples

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


GOOD = """```python
import pandas as pd


def transform(df):
    out = df.iloc[:, [0, 2]].copy()
    out.columns = ["order_id", "email"]
    out["order_id"] = out["order_id"].str.strip()
    return out
```"""


@pytest.fixture
def orders(tmp_path):
    write_samples(tmp_path, rows=12)
    return tmp_path / "dirty_orders.csv"


def as_text(df):
    return df.fillna("").astype(str).values.tolist()


def test_run_then_apply_without_the_model(clean_env, monkeypatch, orders):
    use_fake(monkeypatch, FakeProvider([GOOD]))
    result = runner.invoke(app, ["run", str(orders), "keep the order id and email"])
    assert result.exit_code == 0, result.output

    out = orders.with_name("dirty_orders_clean.csv")
    script = orders.with_name("dirty_orders_transform.py")
    cleaned = load_dataset(out)
    assert list(cleaned.columns) == ["order_id", "email"]
    assert len(cleaned) == len(load_dataset(orders))
    assert f"{len(cleaned)} rows -> {out}" in result.stdout
    assert f"transform -> {script}" in result.stdout
    assert "attempt 1: ok (" in result.stderr
    assert "tokens in total" in result.stderr

    text = script.read_text(encoding="utf-8")
    assert text.startswith("# schema-engine transform for dirty_orders.csv, made ")
    assert "#   keep the order id and email\n" in text
    assert "# Model: ollama/llama3.1, 1 attempt(s), " in text
    assert "schema-engine apply dirty_orders_transform.py dirty_orders.csv" in text
    assert text.endswith("    return out\n")

    again = orders.with_name("again.xlsx")
    result = runner.invoke(app, ["apply", str(script), str(orders), "-o", str(again)])
    assert result.exit_code == 0, result.output
    assert as_text(load_dataset(again)) == as_text(cleaned)


def test_run_retries_and_custom_paths(clean_env, monkeypatch, orders):
    broken = "```python\ndef transform(df):\n    return df[['nope']]\n```"
    use_fake(monkeypatch, FakeProvider([broken, GOOD]))
    out = orders.parent / "out" / "clean.xlsx"
    save = orders.parent / "scripts" / "orders.py"
    args = ["run", str(orders), "x", "-o", str(out), "--save", str(save), "-p", "none"]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    assert "attempt 1: failed, KeyError" in result.stderr
    assert "attempt 2: ok" in result.stderr
    assert out.exists()
    assert "2 attempt(s)" in save.read_text(encoding="utf-8")


def test_run_gives_up(clean_env, monkeypatch, orders):
    use_fake(monkeypatch, FakeProvider(["```python\ndef clean(df):\n    return df\n```"]))
    result = runner.invoke(app, ["run", str(orders), "x", "--attempts", "1"])
    assert result.exit_code == 1
    assert "No working transform after 1 attempts. Last error:" in result.stderr
    assert "no top-level 'def transform(df)' found" in result.stderr
    assert not orders.with_name("dirty_orders_clean.csv").exists()


def test_run_provider_error(clean_env, monkeypatch, orders):
    use_fake(monkeypatch, FakeProvider([AuthenticationError("HTTP 401: bad key")]))
    result = runner.invoke(app, ["run", str(orders), "x"])
    assert result.exit_code == 1
    assert "ollama request failed: HTTP 401: bad key" in result.stderr


def test_run_checks_before_spending_tokens(clean_env, monkeypatch, orders):
    fake = FakeProvider([GOOD])
    use_fake(monkeypatch, fake)
    result = runner.invoke(app, ["run", str(orders), "x", "-o", "out.json"])
    assert result.exit_code == 2
    assert "Can't write '.json' files" in result.stderr
    result = runner.invoke(app, ["run", str(orders), "x", "--provider", "nope"])
    assert result.exit_code == 2
    assert "Unknown provider 'nope'" in result.stderr
    assert fake.calls == []


def test_apply_reports_errors_and_excel_output(clean_env, orders, tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("import os\ndef transform(df):\n    return df\n", encoding="utf-8")
    result = runner.invoke(app, ["apply", str(bad), str(orders)])
    assert result.exit_code == 1
    assert "import of 'os' is not allowed" in result.stderr

    good = tmp_path / "good.py"
    good.write_text("def transform(df):\n    return df.iloc[:, :2]\n", encoding="utf-8")
    xlsx = orders.with_suffix(".xlsx")
    result = runner.invoke(app, ["apply", str(good), str(xlsx)])
    assert result.exit_code == 0, result.output
    assert orders.with_name("dirty_orders_clean.xlsx").exists()

    result = runner.invoke(app, ["apply", str(good), str(orders), "-o", "x.parquet"])
    assert result.exit_code == 2


@pytest.mark.parametrize(
    ("name", "expected"),
    [("a.csv", "a_clean.csv"), ("a.TSV", "a_clean.tsv"), ("a.xls", "a_clean.xlsx")],
)
def test_default_output(name, expected):
    assert _default_output(Path(name)) == Path(expected)
    assert _default_output(Path(name), EXPORT_PRESETS["shopify"]) == Path("a_clean.csv")


SHOPIFY = """```python
import pandas as pd


def transform(df):
    n = len(df)
    out = pd.DataFrame({"Title": ["Item"] * n, "URL handle": [f"item-{i}" for i in range(n)]})
    out["Status"] = "draft"
    return out
```"""


def test_run_and_apply_with_a_preset(clean_env, monkeypatch, orders):
    fake = FakeProvider([SHOPIFY.replace('"draft"', '"live"'), SHOPIFY])
    use_fake(monkeypatch, fake)
    result = runner.invoke(
        app, ["run", str(orders), "make it a shopify import", "--preset", "shopify"]
    )
    assert result.exit_code == 0, result.output
    assert "attempt 1: failed, Output doesn't fit the shopify format (1 problem)" in result.stderr
    assert "The output is a Shopify product import CSV." in fake.calls[0][1].content
    out = orders.with_name("dirty_orders_clean.csv")
    assert list(load_dataset(out).columns) == ["Title", "URL handle", "Status"]

    script = orders.with_name("dirty_orders_transform.py")
    text = script.read_text(encoding="utf-8")
    assert "apply dirty_orders_transform.py dirty_orders.csv --preset shopify\n" in text
    again = orders.with_name("again.csv")
    args = ["apply", str(script), str(orders), "-o", str(again), "--preset", "SHOPIFY"]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output

    script.write_text(text.replace('"draft"', '"live"'), encoding="utf-8")
    again.unlink()
    result = runner.invoke(app, args)
    assert result.exit_code == 1
    assert "- 'Status' must be active, draft or archived (rows 1, 2, 3, 4, 5 and" in result.stderr
    assert not again.exists()


def test_preset_checked_before_spending_tokens(clean_env, monkeypatch, orders):
    fake = FakeProvider([SHOPIFY])
    use_fake(monkeypatch, fake)
    result = runner.invoke(app, ["run", str(orders), "x", "--preset", "nope"])
    assert result.exit_code == 2
    assert "Unknown preset 'nope'. Choose one of: shopify." in result.stderr
    result = runner.invoke(app, ["run", str(orders), "x", "--preset", "shopify", "-o", "a.xlsx"])
    assert result.exit_code == 2
    assert "The shopify preset writes .csv files, not '.xlsx'." in result.stderr
    assert fake.calls == []


def test_write_errors_after_the_model_keep_the_transform(clean_env, monkeypatch, orders):
    clash = (
        "```python\ndef transform(df):\n"
        "    return df.iloc[:, :2].set_axis(['Id', 'id'], axis=1)\n```"
    )
    use_fake(monkeypatch, FakeProvider([clash]))
    result = runner.invoke(app, ["run", str(orders), "x", "-o", str(orders.with_name("o.sql"))])
    assert result.exit_code == 1
    assert "Can't write the SQLite table: duplicate column name: id" in result.stderr
    assert "transform -> " in result.stderr
    assert orders.with_name("dirty_orders_transform.py").exists()
    script = str(orders.with_name("dirty_orders_transform.py"))
    result = runner.invoke(
        app, ["apply", script, str(orders), "-o", str(orders.with_name("o.sql"))]
    )
    assert result.exit_code == 1
    assert "transform -> " not in result.stderr
