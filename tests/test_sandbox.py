import builtins
import socket
import subprocess
import sys

import pandas as pd
import pytest

from schema_engine import _runner
from schema_engine.sandbox import check_code, child_command, run_transform

GOOD = """
import re
from datetime import datetime

import numpy as np
import pandas as pd
from pandas.api.types import is_numeric_dtype


def transform(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    to_drop = [c for c in out.columns if not c.strip()]
    out = out.drop(columns=to_drop)
    out["when"] = pd.to_datetime(out["when"], errors="coerce")
    out["n"] = pd.to_numeric(out["n"].str.replace(r"[^0-9.]", "", regex=True))
    out["big"] = np.where(out["n"] > 10, "yes", "no")
    return out
"""


def test_clean_code_passes():
    assert check_code(GOOD) == []


@pytest.mark.parametrize(
    ("code", "problem"),
    [
        ("import os", "import of 'os'"),
        ("import subprocess as sp", "import of 'subprocess'"),
        ("from pathlib import Path", "import of 'pathlib'"),
        ("from pandas.io import common", "import of 'pandas.io'"),
        ("from pandas import read_csv", "'read_csv'"),
        ("from . import x", "relative imports"),
        ("x = pd.read_csv('a.csv')", "'read_csv'"),
        ("df.to_csv('out.csv')", "'to_csv'"),
        ("df.to_pickle('p')", "'to_pickle'"),
        ("pd.read_html('http://x')", "'read_html'"),
        ("df.query('a > 1')", "'query'"),
        ("pd.eval('1+1')", "'eval'"),
        ("np.load('x.npy')", "'load'"),
        ("open('x')", "'open'"),
        ("eval('1')", "'eval'"),
        ("exec('1')", "'exec'"),
        ("getattr(df, 'x')", "'getattr'"),
        ("__import__('os')", "'__import__'"),
        ("().__class__.__bases__", "'__class__'"),
        ("x = __builtins__", "'__builtins__'"),
    ],
)
def test_blocked_code(code, problem):
    problems = check_code(f"def transform(df):\n    return df\n\n{code}\n")
    assert any(problem in p for p in problems), problems


def test_reports_line_numbers_once():
    code = "import os\nimport os\ndef transform(df):\n    return df\n"
    assert check_code(code) == [
        "line 1: import of 'os' is not allowed",
        "line 2: import of 'os' is not allowed",
    ]


def test_needs_transform_function():
    assert check_code("def clean(df):\n    return df\n") == [
        "no top-level 'def transform(df)' found"
    ]


def test_syntax_error():
    assert check_code("def transform(df)\n    return df\n") == [
        "SyntaxError on line 1: expected ':'"
    ]


# --- running code -------------------------------------------------------------


def write_job(workdir, code, df=None):
    (df if df is not None else pd.DataFrame({"a": [" x "]})).to_pickle(workdir / "input.pkl")
    (workdir / "transform.py").write_text(code, encoding="utf-8")


def run_in_process(workdir, code, df=None):
    """Run the child's main() here, so its guards are tested past the static check."""
    write_job(workdir, code, df)
    code_rc = _runner.main(workdir)
    error = workdir / "error.txt"
    return code_rc, error.read_text(encoding="utf-8") if error.exists() else None


def test_run_transform_returns_typed_output(dirty_df):
    code = """
import pandas as pd


def transform(df):
    out = df.iloc[:, [0, 5, 6]].copy()
    out.columns = ["order_id", "price", "qty"]
    out["order_id"] = out["order_id"].str.strip()
    out["price"] = pd.to_numeric(out["price"].str.replace(r"[^0-9.]", "", regex=True))
    out["qty"] = pd.to_numeric(out["qty"], errors="coerce").astype("Int64")
    return out.set_index("order_id", drop=False)
"""
    result = run_transform(code, dirty_df)
    assert result.ok, result.error
    assert list(result.output.columns) == ["order_id", "price", "qty"]
    assert len(result.output) == len(dirty_df)
    assert result.output["price"].dtype == "float64"
    assert str(result.output["qty"].dtype) == "Int64"
    assert list(result.output.index[:2]) == [0, 1]


def test_run_transform_rejects_before_running(dirty_df):
    result = run_transform("import os\ndef transform(df):\n    return df\n", dirty_df)
    assert not result.ok
    assert result.output is None
    assert result.error == "Code rejected before running:\n- line 1: import of 'os' is not allowed"


def test_run_transform_reports_only_generated_frames(dirty_df):
    result = run_transform("def transform(df):\n    return df.iloc[:, 99]\n", dirty_df)
    assert result.error.startswith("Traceback (generated code only):")
    assert 'File "transform.py", line 2, in transform' in result.error
    assert "return df.iloc[:, 99]" in result.error
    assert result.error.endswith("IndexError: single positional indexer is out-of-bounds")
    assert "_runner.py" not in result.error


def test_run_transform_timeout(dirty_df):
    result = run_transform(
        "def transform(df):\n    while True:\n        pass\n", dirty_df, timeout=2
    )
    assert result.error == "TimeoutError: transform ran longer than 2s"


def test_child_gets_no_api_keys_and_crashes_are_reported(monkeypatch, dirty_df):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
    seen = {}

    def fake_run(args, **kwargs):
        seen.update(kwargs, args=args)
        return subprocess.CompletedProcess(args, 1, "", "Fatal Python error: boom\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = run_transform("def transform(df):\n    return df\n", dirty_df)
    assert result.error == "Fatal Python error: boom"
    assert "OPENAI_API_KEY" not in seen["env"]
    assert "PATH" in seen["env"]
    assert seen["args"][:4] == [sys.executable, "-I", "-m", "schema_engine._runner"]

    monkeypatch.setattr(
        subprocess, "run", lambda args, **kw: subprocess.CompletedProcess(args, 9, "", "")
    )
    assert run_transform("def transform(df):\n    return df\n", dirty_df).error == (
        "sandbox exited with code 9"
    )


def test_frozen_app_runs_itself_as_the_child(monkeypatch, tmp_path):
    assert child_command(tmp_path)[1:] == ["-I", "-m", "schema_engine._runner", str(tmp_path)]
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "C:/App/SchemaEngine.exe")
    assert child_command(tmp_path) == ["C:/App/SchemaEngine.exe", "--sandbox", str(tmp_path)]


def test_runtime_blocks_files_network_and_imports(tmp_path):
    rc, error = run_in_process(
        tmp_path, 'def transform(df):\n    df.to_string(buf="x.txt")\n    return df\n'
    )
    assert rc == 1
    assert error.endswith("PermissionError: file and network access are blocked in the sandbox")
    assert not (tmp_path / "x.txt").exists()

    _, error = run_in_process(tmp_path, "def transform(df):\n    import socket\n    return df\n")
    assert error.endswith("ImportError: import of 'socket' is not allowed")

    # Guards are lifted once the transform is done.
    assert builtins.open.__name__ == "open"
    assert builtins.__import__.__name__ == "__import__"
    assert socket.socket.connect.__name__ == "connect"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("return 5", "TypeError: transform must return a DataFrame, got int"),
        (
            'return pd.DataFrame([[1, 2]], columns=["a", "a"])',
            "ValueError: output column names must be unique, duplicated: ['a']",
        ),
        (
            'return pd.DataFrame([[1]], columns=[" "])',
            "ValueError: output column names must be non-empty strings, got [' ']",
        ),
        ("raise SystemExit(3)", "SystemExit: 3"),
    ],
)
def test_output_checks(tmp_path, body, message):
    rc, error = run_in_process(tmp_path, f"import pandas as pd\ndef transform(df):\n    {body}\n")
    assert rc == 1
    assert error.endswith(message)


def test_dates_that_cant_be_read_back_go_to_the_model(dirty_df):
    code = (
        "import pandas as pd\n"
        "def transform(df):\n"
        "    return pd.DataFrame({'d': pd.to_datetime(['1470-01-01'])})\n"
    )
    result = run_transform(code, dirty_df)
    assert result.output is None
    assert "Out of bounds" in result.error


def test_stdlib_helpers_that_import_lazily_still_work(tmp_path):
    code = (
        "from datetime import datetime\n"
        "def transform(df):\n"
        "    when = datetime.strptime('21.07.24', '%d.%m.%y')\n"
        "    return df.assign(when=when, raw=len('café'.encode('cp1252')))\n"
    )
    assert run_in_process(tmp_path, code) == (0, None)


def test_runner_success_writes_output(tmp_path):
    rc, error = run_in_process(tmp_path, "def transform(df):\n    return df.assign(b=1)\n")
    assert (rc, error) == (0, None)
    assert '"b":1' in (tmp_path / "output.json").read_text(encoding="utf-8")
