"""Child process behind ``sandbox.run_transform``. Not meant to be run by hand.

``python -I -m schema_engine._runner WORKDIR`` reads ``input.pkl`` and
``transform.py`` from WORKDIR and writes ``output.json`` or ``error.txt``.
"""

from __future__ import annotations

import builtins
import linecache
import socket
import sys
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import pandas as pd

from schema_engine.sandbox import ALLOWED_IMPORTS

GENERATED = "generated_transform"
FILENAME = "transform.py"


def main(workdir: Path) -> int:
    df = pd.read_pickle(workdir / "input.pkl")  # written by the parent, trusted
    code = (workdir / FILENAME).read_text(encoding="utf-8")
    linecache.cache[FILENAME] = (len(code), None, code.splitlines(True), FILENAME)
    try:
        with _locked_down():
            namespace = {"__name__": GENERATED}
            exec(compile(code, FILENAME, "exec"), namespace)
            result = namespace["transform"](df.copy())
        _check_output(result)
    except BaseException as exc:  # the parent wants every failure, SystemExit included
        (workdir / "error.txt").write_text(_format_error(exc), encoding="utf-8")
        return 1
    _plain_dates(result).reset_index(drop=True).to_json(
        workdir / "output.json", orient="table", index=False, date_format="iso"
    )
    return 0


@contextmanager
def _locked_down() -> Iterator[None]:
    """Block files, network and non-allowlisted imports while generated code runs."""
    real_import, real_open = builtins.__import__, builtins.open
    targets = [
        (socket, "create_connection"),
        (socket, "getaddrinfo"),
        (socket.socket, "connect"),
        (socket.socket, "connect_ex"),
        (socket.socket, "bind"),
    ]
    saved = [(obj, name, getattr(obj, name)) for obj, name in targets]

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        # Only imports made by the generated code are limited; pandas' own lazy
        # imports still work.
        if (globals or {}).get("__name__") == GENERATED and not _allowed_at_runtime(name):
            raise ImportError(f"import of '{name}' is not allowed")
        return real_import(name, globals, locals, fromlist, level)

    def blocked(*args, **kwargs):
        raise PermissionError("file and network access are blocked in the sandbox")

    builtins.__import__ = guarded_import
    builtins.open = blocked
    for obj, name, _ in saved:
        setattr(obj, name, blocked)
    try:
        yield
    finally:
        builtins.__import__, builtins.open = real_import, real_open
        for obj, name, value in saved:
            setattr(obj, name, value)


def _allowed_at_runtime(name: str) -> bool:
    top = name.split(".")[0]
    # C code like datetime.strptime or str.encode imports private helpers
    # (_strptime, encodings.*) on the caller's behalf. The static check already
    # stops generated code from importing these itself.
    return top in ALLOWED_IMPORTS or top.startswith("_") or top == "encodings"


def _check_output(result: object) -> None:
    if not isinstance(result, pd.DataFrame):
        raise TypeError(f"transform must return a DataFrame, got {type(result).__name__}")
    names = list(result.columns)
    bad = [n for n in names if not isinstance(n, str) or not n.strip()]
    if bad:
        raise ValueError(f"output column names must be non-empty strings, got {bad!r}")
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise ValueError(f"output column names must be unique, duplicated: {dupes!r}")


def _plain_dates(df: pd.DataFrame) -> pd.DataFrame:
    """``.dt.date`` gives date objects, which to_json writes as midnight timestamps."""
    for i in range(df.shape[1]):
        col = df.iloc[:, i]
        if col.dtype == object:
            df.isetitem(i, col.map(lambda v: v.isoformat() if type(v) is date else v))
    return df


def _format_error(exc: BaseException) -> str:
    """Traceback limited to the generated code, so the model sees its own lines."""
    frames = [f for f in traceback.extract_tb(exc.__traceback__) if f.filename == FILENAME]
    lines = ["Traceback (generated code only):\n", *traceback.format_list(frames)] if frames else []
    lines += traceback.format_exception_only(exc)
    return "".join(lines).rstrip()


if __name__ == "__main__":  # pragma: no cover - exercised through run_transform
    sys.exit(main(Path(sys.argv[1])))
