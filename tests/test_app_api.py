import time
from pathlib import Path

import httpx
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from schema_engine.app import api
from schema_engine.io import load_dataset
from schema_engine.providers import AuthenticationError, FakeProvider, HealthReport

SAME = "def transform(df):\n    return df\n"
CLEAN = Path(__file__).parents[1] / "examples" / "clean_orders.py"
GOOD = f"```python\n{CLEAN.read_text(encoding='utf-8')}\n```"


class Dialogs:
    def __init__(self, answers):
        self.answers = list(answers)
        self.asked = []

    def open(self, kind):
        self.asked.append(("open", kind))
        return self.answers.pop(0)

    def save(self, kind, suggested):
        self.asked.append(("save", kind, suggested.name))
        return self.answers.pop(0)


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "DATA_DIR", tmp_path / "data")
    return tmp_path / "data"


def make_client(tmp_path, *, fake=None, dialogs=None):
    app = api.create_app(
        dialogs=dialogs,
        provider_factory=lambda settings: fake or FakeProvider([GOOD]),
        config_file=tmp_path / "config.json",
    )
    return TestClient(app)


def wait(client, run_id):
    for _ in range(300):
        run = client.get(f"/api/runs/{run_id}").json()
        if run["status"] != "running":
            return run
        time.sleep(0.05)
    raise AssertionError("run never finished")


def test_info_and_health(tmp_path):
    client = make_client(tmp_path)
    assert client.get("/api/health").json() == {"ok": True}
    info = client.get("/api/info").json()
    assert info["desktop"] is False
    assert {p["name"] for p in info["presets"]} == {"shopify", "sqlite"}
    assert info["privacy_modes"] == ["masked", "none", "raw"]


def test_other_sites_and_hosts_are_blocked(tmp_path):
    client = make_client(tmp_path)
    evil = {"origin": "https://evil.example"}
    assert client.post("/api/files/sample", headers=evil).status_code == 403
    blocked = client.post("/api/files/sample", headers={"sec-fetch-site": "cross-site"})
    assert blocked.status_code == 403
    assert client.get("/api/health", headers={"host": "evil.example"}).status_code == 400


def test_sample_profile_run_and_save(tmp_path, data_dir):
    client = make_client(tmp_path)
    opened = client.post("/api/files/sample").json()
    assert opened["name"] == "dirty_orders.csv"
    assert opened["table"]["total"] == 52
    assert len(opened["table"]["rows"]) == 52

    masked = client.get(f"/api/files/{opened['id']}/profile").json()
    strict = client.get(f"/api/files/{opened['id']}/profile?privacy=none").json()
    assert masked["profile"]["privacy_mode"] == "masked"
    assert "sample" not in strict["profile"]
    assert strict["tokens"] < masked["tokens"]

    started = client.post("/api/runs", json={"file": opened["id"], "instruction": "dedupe"})
    run = wait(client, started.json()["id"])
    assert run["status"] == "done"
    # Every try blanks 5 dates, so the fewest-lost one is kept, with a warning.
    assert [a["summary"] for a in run["attempts"]] == ["5 values became blank in 1 column"] * 3
    assert run["usage"]["total"] > 0
    out = run["output"]
    assert out["lost"] == [{"column": "Order Date", "blanked": 5, "filled": 45}]
    assert run["kept"] == 1
    assert out["table"]["total"] == 50
    assert out["table"]["rows"][0][:4] == [
        "ORD-01000",
        "Farah Petrov",
        "farah.petrov47@example.org",
        "2024-07-21",
    ]
    assert out["suggested"] == {
        "data": "dirty_orders_clean.csv",
        "transform": "dirty_orders_transform.py",
    }

    target = tmp_path / "clean.xlsx"
    save = f"/api/outputs/{out['id']}/save"
    saved = client.post(save, json={"kind": "data", "path": str(target)})
    assert saved.json() == {"path": str(target)}
    assert len(load_dataset(target)) == 50
    script = tmp_path / "t.py"
    client.post(f"/api/outputs/{out['id']}/save", json={"kind": "transform", "path": str(script)})
    text = script.read_text(encoding="utf-8")
    assert text.startswith("# schema-engine transform for dirty_orders.csv")
    assert "#   dedupe" in text
    assert "def transform(df: pd.DataFrame)" in text

    # Without a dialog or a path, it goes next to the input, like the CLI.
    client.post(f"/api/outputs/{out['id']}/save", json={"kind": "data"})
    assert (data_dir / "samples" / "dirty_orders_clean.csv").exists()


def test_bad_saves(tmp_path, data_dir):
    client = make_client(tmp_path)
    opened = client.post("/api/files/sample").json()
    applied = client.post(
        "/api/apply",
        json={"file": opened["id"], "code": CLEAN.read_text(encoding="utf-8"), "preset": "sqlite"},
    ).json()
    assert applied["preset"] == "sqlite"
    assert applied["suggested"]["data"] == "dirty_orders_clean.sql"
    save = f"/api/outputs/{applied['id']}/save"
    wrong = client.post(save, json={"kind": "data", "path": str(tmp_path / "a.csv")})
    assert wrong.status_code == 400
    assert "saved as .sql" in wrong.json()["detail"]
    assert client.post(save, json={"kind": "data", "path": str(tmp_path / "a.sql")}).is_success

    plain = client.post(
        "/api/apply", json={"file": opened["id"], "code": CLEAN.read_text(encoding="utf-8")}
    ).json()
    odd = client.post(
        f"/api/outputs/{plain['id']}/save", json={"kind": "data", "path": str(tmp_path / "a.pdf")}
    )
    assert "Can't save .pdf" in odd.json()["detail"]
    same_name = client.post(
        "/api/apply",
        json={
            "file": opened["id"],
            "code": SAME.replace("return df", "return df.iloc[:, :0].assign(a=1, A=2)"),
        },
    ).json()
    clash = client.post(
        f"/api/outputs/{same_name['id']}/save",
        json={"kind": "data", "path": str(tmp_path / "b.sql")},
    )
    assert clash.status_code == 400
    unknown = client.post("/api/outputs/nope/save", json={"kind": "data"})
    assert unknown.status_code == 404


def test_run_retries_then_fails(tmp_path, data_dir):
    broken = "```python\ndef transform(df):\n    return df.nope()\n```"
    client = make_client(tmp_path, fake=FakeProvider([broken]))
    opened = client.post("/api/files/sample").json()
    run_id = client.post(
        "/api/runs", json={"file": opened["id"], "instruction": "x", "attempts": 2}
    ).json()["id"]
    run = wait(client, run_id)
    assert run["status"] == "failed"
    assert run["error"] == "No working transform after 2 tries."
    assert [a["summary"] for a in run["attempts"]] == [
        "AttributeError: 'DataFrame' object has no attribute 'nope'"
    ] * 2
    assert run["output"] is None
    assert run["kept"] is None
    assert client.get("/api/runs/nope").status_code == 404


def test_provider_errors(tmp_path, data_dir):
    client = make_client(tmp_path, fake=FakeProvider([AuthenticationError("HTTP 401: bad key")]))
    opened = client.post("/api/files/sample").json()
    run_id = client.post("/api/runs", json={"file": opened["id"], "instruction": "x"}).json()["id"]
    run = wait(client, run_id)
    assert run["error"] == "ollama request failed: HTTP 401: bad key"

    # A cloud provider with no key fails before anything starts.
    real = api.create_app(config_file=tmp_path / "config.json")
    with TestClient(real) as other:
        opened = other.post("/api/files/sample").json()
        missing = other.post(
            "/api/runs", json={"file": opened["id"], "instruction": "x", "provider": "groq"}
        )
        assert missing.status_code == 400
        assert "GROQ_API_KEY" in missing.json()["detail"]
        assert other.post("/api/settings/groq/test").json()["ok"] is False
    assert client.post("/api/runs", json={"file": "gone", "instruction": "x"}).status_code == 404
    mine = client.post("/api/files/sample").json()
    bad = client.post("/api/runs", json={"file": mine["id"], "instruction": "x", "preset": "csv+"})
    assert bad.json()["detail"] == "Unknown format 'csv+'."


def test_apply_with_dialogs_and_errors(tmp_path, data_dir):
    write = tmp_path / "orders.csv"
    pd.DataFrame({"a": ["1", "2"]}).to_csv(write, index=False)
    out_path = tmp_path / "picked.csv"
    dialogs = Dialogs([str(write), str(CLEAN), None, str(out_path), None])
    client = make_client(tmp_path, dialogs=dialogs)
    assert client.get("/api/info").json()["desktop"] is True

    opened = client.post("/api/files/open", json={}).json()
    assert opened["path"] == str(write)
    crashed = client.post("/api/apply", json={"file": opened["id"]})
    assert crashed.status_code == 422
    assert "IndexError" in crashed.json()["detail"]
    assert client.post("/api/apply", json={"file": opened["id"]}).json() is None

    applied = client.post("/api/apply", json={"file": opened["id"], "code": SAME}).json()
    save = f"/api/outputs/{applied['id']}/save"
    assert client.post(save, json={"kind": "data"}).json() == {"path": str(out_path)}
    assert client.post(save, json={"kind": "transform"}).json() is None
    assert dialogs.asked == [
        ("open", "data"),
        ("open", "transform"),
        ("open", "transform"),
        ("save", "data", "orders_clean.csv"),
        ("save", "transform", "orders_transform.py"),
    ]

    rejected = client.post("/api/apply", json={"file": opened["id"], "code": "import os\n" + SAME})
    assert "Code rejected" in rejected.json()["detail"]
    shopify = client.post(
        "/api/apply",
        json={"file": opened["id"], "code": SAME, "preset": "shopify"},
    )
    assert "shopify" in shopify.json()["detail"]


def test_open_errors_without_dialogs(tmp_path, data_dir):
    client = make_client(tmp_path)
    assert client.post("/api/files/open", json={}).status_code == 400
    missing = client.post("/api/files/open", json={"path": str(tmp_path / "nope.csv")})
    assert missing.status_code == 400
    odd = tmp_path / "notes.pdf"
    odd.write_text("x", encoding="utf-8")
    assert client.post("/api/files/open", json={"path": str(odd)}).status_code == 400
    renamed = tmp_path / "old.xlsx"
    renamed.write_bytes(bytes.fromhex("d0cf11e0a1b11ae1") + bytes(504))
    assert client.post("/api/files/open", json={"path": str(renamed)}).status_code == 400
    assert client.post("/api/apply", json={"file": "x", "code": "y"}).status_code == 404
    opened = client.post("/api/files/sample").json()
    assert client.post("/api/apply", json={"file": opened["id"]}).status_code == 400
    assert client.get("/api/files/nope/profile").status_code == 404


def test_cancelled_open_dialog(tmp_path, data_dir):
    client = make_client(tmp_path, dialogs=Dialogs([None]))
    assert client.post("/api/files/open", json={}).json() is None


def test_dropped_excel_file_and_sheets(tmp_path, data_dir, monkeypatch):
    book = tmp_path / "book.xlsx"
    with pd.ExcelWriter(book) as writer:
        pd.DataFrame({"when": pd.to_datetime(["2024-01-02", None]), "n": [1, 2]}).to_excel(
            writer, sheet_name="Dates", index=False
        )
        pd.DataFrame({"at": pd.to_datetime(["2024-01-02 10:30"])}).to_excel(
            writer, sheet_name="Times", index=False
        )
    client = make_client(tmp_path)
    dropped = client.post("/api/files/drop?name=book.xlsx", content=book.read_bytes()).json()
    assert dropped["path"] is None
    assert dropped["sheets"] == ["Dates", "Times"]
    assert dropped["table"]["rows"] == [["2024-01-02", 1], [None, 2]]
    copy = next(data_dir.glob("dropped/*/book.xlsx"))
    times = client.post("/api/files/open", json={"path": str(copy), "sheet": "Times"}).json()
    assert times["sheet"] == "Times"
    assert times["table"]["rows"] == [["2024-01-02 10:30:00"]]
    second = client.post("/api/files/open", json={"path": str(copy), "sheet": "1"}).json()
    assert second["sheet"] == 1
    assert client.post("/api/files/drop?name=", content=b"x").status_code == 400

    # A dropped file is a copy in the data folder, so saving suggests Documents.
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    (tmp_path / "home" / "Documents").mkdir(parents=True)
    applied = client.post("/api/apply", json={"file": dropped["id"], "code": SAME}).json()
    saved = client.post(f"/api/outputs/{applied['id']}/save", json={"kind": "data"}).json()
    assert saved == {"path": str(tmp_path / "home" / "Documents" / "book_clean.xlsx")}


def test_open_sheet_named_like_a_number(tmp_path, data_dir):
    book = tmp_path / "years.xlsx"
    with pd.ExcelWriter(book) as writer:
        pd.DataFrame({"year": [2023]}).to_excel(writer, sheet_name="2023", index=False)
        pd.DataFrame({"year": [2024]}).to_excel(writer, sheet_name="2024", index=False)
    client = make_client(tmp_path)
    opened = client.post("/api/files/open", json={"path": str(book), "sheet": "2024"})
    assert opened.status_code == 200, opened.text
    assert opened.json()["sheet"] == "2024"
    assert opened.json()["table"]["rows"] == [[2024]]


def test_settings_round_trip(tmp_path, memory_keyring, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("SCHEMA_ENGINE_PROVIDER", raising=False)
    monkeypatch.delenv("SCHEMA_ENGINE_MODEL", raising=False)
    client = make_client(tmp_path)
    start = client.get("/api/settings").json()
    assert start["default"] == "ollama"
    groq = next(p for p in start["providers"] if p["name"] == "groq")
    assert groq == {
        "name": "groq",
        "needs_key": True,
        "key_env": "GROQ_API_KEY",
        "default_model": "openai/gpt-oss-120b",
        "model": "openai/gpt-oss-120b",
        "default_base_url": "https://api.groq.com/openai/v1",
        "base_url": "https://api.groq.com/openai/v1",
        "key": None,
        "key_in_app": False,
    }

    saved = client.put(
        "/api/settings/groq", json={"model": "qwen/qwen3.8-27b", "api_key": " gsk-1234567890 "}
    ).json()
    assert saved["model"] == "qwen/qwen3.8-27b"
    assert saved["key"] == "gsk…7890"
    assert saved["key_in_app"] is True
    assert memory_keyring.saved == {("schema-engine", "groq"): "gsk-1234567890"}

    kept = client.put("/api/settings/groq", json={"model": "openai/gpt-oss-120b"}).json()
    assert kept["key_in_app"] is True
    assert '"model"' not in (tmp_path / "config.json").read_text(encoding="utf-8")
    forgot = client.put("/api/settings/groq", json={"api_key": ""}).json()
    assert forgot["key"] is None

    assert client.put("/api/settings", json={"provider": "groq"}).json()["default"] == "groq"
    assert client.put("/api/settings", json={"provider": "ollama"}).json()["default"] == "ollama"
    assert client.put("/api/settings", json={"provider": "grok"}).status_code == 400
    assert client.put("/api/settings/grok", json={}).status_code == 400


def test_connection_test(tmp_path):
    class Healthy(FakeProvider):
        def health_check(self):
            return HealthReport("ollama", "llama3.1", "x", True, 12.0)

    client = make_client(tmp_path, fake=Healthy())
    report = client.post("/api/settings/ollama/test").json()
    assert report == {"ok": True, "detail": "", "latency_ms": 12.0, "tokens": 0}


def test_ollama_models(tmp_path, monkeypatch):
    client = make_client(tmp_path)
    request = httpx.Request("GET", "http://localhost:11434/api/tags")
    reply = httpx.Response(200, json={"models": [{"name": "llama3.1:latest"}]}, request=request)
    monkeypatch.setattr(api.httpx, "get", lambda url, timeout: reply)
    assert client.get("/api/ollama").json() == {"running": True, "models": ["llama3.1:latest"]}

    def down(url, timeout):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(api.httpx, "get", down)
    assert client.get("/api/ollama").json() == {"running": False, "models": []}


def test_serves_the_built_ui(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<p>ui</p>", encoding="utf-8")
    monkeypatch.setattr(api, "STATIC", tmp_path)
    client = make_client(tmp_path)
    assert client.get("/").text == "<p>ui</p>"
