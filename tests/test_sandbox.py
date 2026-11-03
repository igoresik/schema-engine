import pytest

from schema_engine.sandbox import check_code

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
