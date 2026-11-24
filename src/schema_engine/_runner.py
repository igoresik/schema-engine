"""Child process behind ``sandbox.run_transform``. Not meant to be run by hand.

``python -I -m schema_engine._runner WORKDIR`` reads ``input.pkl`` and
``transform.py`` from WORKDIR and writes ``output.json`` or ``error.txt``.
"""

from __future__ import annotations

import builtins
import linecache
import os
import socket
import sys
import traceback
import zoneinfo
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import pandas as pd

from schema_engine.sandbox import ALLOWED_IMPORTS

GENERATED = "generated_transform"
FILENAME = "transform.py"

# Audit events that generated code may never trigger, however it got there: starting
# programs, sockets, native code, and changing the file system.
_DENIED_EVENTS = (
    "socket.",
    "subprocess.",
    "ctypes.",
    "shutil.",
    "glob.",
    "os.exec",
    "os.spawn",
    "os.posix_spawn",
    "os.fork",
    "os.system",
    "os.startfile",
    "os.remove",
    "os.rename",
    "os.replace",
    "os.mkdir",
    "os.rmdir",
    "os.chmod",
    "os.chown",
    "os.truncate",
    "os.link",
    "os.symlink",
)
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC
_audit = {"on": False, "busy": False, "installed": False, "roots": ()}


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
        workdir / "output.json",
        orient="table",
        index=False,
        date_format="iso",
        double_precision=15,  # the default 10 turns 1.5e-11 into 0.0
    )
    return 0


@contextmanager
def _locked_down() -> Iterator[None]:
    """Block files, network and non-allowlisted imports while generated code runs."""
    real_import, real_open = builtins.__import__, builtins.open
    _install_audit()
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
    _audit["on"] = True
    try:
        yield
    finally:
        _audit["on"] = False
        builtins.__import__, builtins.open = real_import, real_open
        for obj, name, value in saved:
            setattr(obj, name, value)


def _install_audit() -> None:
    """Add the audit hook once; it can't be removed, so it only acts while ``_audit["on"]``.

    The patches above only cover the names a transform can reach directly. Python itself
    reports every file, process and socket operation as an audit event, whichever module
    it came through, so this closes the routes that get around the patches (io.open,
    os.system and the like).
    """
    if _audit["installed"]:
        return
    paths = {sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix, *sys.path}
    paths |= {*zoneinfo.TZPATH, getattr(sys, "_MEIPASS", ""), os.path.dirname(sys.executable)}
    _audit["roots"] = tuple({os.path.normcase(os.path.realpath(p)) for p in paths if p})
    sys.addaudithook(_audit_hook)
    _audit["installed"] = True


def _readable(path: object) -> bool:
    """Python's own files and time zone data, which imports and dates need during a transform."""
    if isinstance(path, int):  # an already open descriptor
        return True
    try:
        real = os.path.normcase(os.path.realpath(os.fsdecode(path)))
    except (TypeError, ValueError, OSError):
        return False
    return any(real == root or real.startswith(root + os.sep) for root in _audit["roots"])


def _listable(path: object) -> bool:
    return path is None or _readable(
        path
    )  # None is the current directory, which is the work folder


def _audit_hook(event: str, args: tuple) -> None:
    if not _audit["on"] or _audit["busy"]:
        return
    _audit["busy"] = True
    try:
        if event.startswith(_DENIED_EVENTS):
            raise PermissionError(f"{event} is blocked in the sandbox")
        if event == "open":
            path, mode, flags = args
            writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
                isinstance(flags, int) and flags & _WRITE_FLAGS
            )
            if writing or not _readable(path):
                raise PermissionError("file access is blocked in the sandbox")
        elif event in ("os.listdir", "os.scandir") and not _listable(args[0]):
            raise PermissionError("file access is blocked in the sandbox")
    finally:
        _audit["busy"] = False


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
