"""Check and run generated ``transform(df)`` code.

Two layers stand between model output and your machine:

1. A static check of the code before anything runs: allowlisted imports only, and
   no file, network, eval or dunder access.
2. A separate Python process with a timeout and no API keys in its environment,
   where file and network access are blocked while the transform runs, both by patching
   the usual names and by an audit hook that refuses file, process and socket operations
   however the code reaches them.

On Windows the child also runs in a jail (see ``_jail``): a Low-integrity token that can't
write or delete your files, and a Job Object that blocks new processes and caps memory.
The audit hook is the only thing stopping reads and connections there, and the other
systems have no jail at all, so this is still not a container or VM.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from io import StringIO
from pathlib import Path

import pandas as pd

from schema_engine import _jail

ALLOWED_IMPORTS = frozenset(
    {
        "calendar",
        "collections",
        "datetime",
        "decimal",
        "functools",
        "itertools",
        "math",
        "numpy",
        "pandas",
        "re",
        "string",
        "typing",
        "unicodedata",
    }
)

BLOCKED_CALLS = frozenset(
    {
        "__import__",
        "breakpoint",
        "compile",
        "delattr",
        "eval",
        "exec",
        "exit",
        "getattr",
        "globals",
        "help",
        "input",
        "locals",
        "open",
        "quit",
        "setattr",
        "vars",
    }
)

# pandas/numpy names that read or write files, evaluate strings or reach C code.
BLOCKED_ATTRIBUTES = frozenset(
    {
        "ctypeslib",
        "eval",
        "ExcelFile",
        "ExcelWriter",
        "Formatter",
        "fromfile",
        "genfromtxt",
        "get_field",
        "get_value",
        "HDFStore",
        "io",
        "lib",
        "load",
        "loadtxt",
        "memmap",
        "query",
        "save",
        "savetxt",
        "savez",
        "savez_compressed",
        "tofile",
        "vformat",
    }
)

# Every other ``to_*`` / ``read_*`` name is file or database I/O.
ALLOWED_TO_METHODS = frozenset(
    {
        "to_datetime",
        "to_dict",
        "to_flat_index",
        "to_frame",
        "to_list",
        "to_numeric",
        "to_numpy",
        "to_period",
        "to_pydatetime",
        "to_series",
        "to_string",
        "to_timedelta",
        "to_timestamp",
    }
)


ERROR_LIMIT = 2000
"""Characters of error text kept, from the end, so retries stay cheap."""

# Everything else (API keys included) stays out of the child's environment.
_CHILD_ENV = ("PATH", "SYSTEMROOT", "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL")

SANDBOX_FLAG = "--sandbox"
"""How the desktop app's exe is told to act as the child process."""


@dataclass(frozen=True)
class SandboxResult:
    output: pd.DataFrame | None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def run_transform(code: str, df: pd.DataFrame, *, timeout: float = 30.0) -> SandboxResult:
    """Check ``code``, then run its ``transform(df)`` on a copy of ``df`` in a child process."""
    problems = check_code(code)
    if problems:
        listed = "\n".join(f"- {p}" for p in problems)
        return SandboxResult(None, f"Code rejected before running:\n{listed}")

    with tempfile.TemporaryDirectory(prefix="schema-engine-") as tmp:
        work = Path(tmp)
        jailed = _jail.WINDOWS and not os.environ.get("SCHEMA_ENGINE_NO_JAIL")
        try:
            if jailed:
                _jail.mark_low(work)  # before any file goes in, so they all inherit the Low label
            df.to_pickle(work / "input.pkl")
            (work / "transform.py").write_text(code, encoding="utf-8")
            env = {k: os.environ[k] for k in _CHILD_ENV if k in os.environ}
            returncode, stderr = _start(child_command(work), work, env, timeout, jailed)
        except subprocess.TimeoutExpired:
            return SandboxResult(None, f"TimeoutError: transform ran longer than {timeout:g}s")
        except _jail.JailError as exc:
            return SandboxResult(None, f"The sandbox could not start, so nothing was run: {exc}")
        if returncode == 0:
            output_file = work / "output.json"
            if not output_file.exists():
                return SandboxResult(None, "The transform finished without writing any output.")
            text = output_file.read_text(encoding="utf-8")
            try:
                # precise_float: the default parser changes the last digits,
                # 1.234567e-05 would come back as 1.2345669999999999e-05
                output = pd.read_json(StringIO(text), orient="table", precise_float=True)
                return SandboxResult(output)
            except (ValueError, OverflowError, NotImplementedError) as exc:
                # Read back as nanoseconds, so dates outside 1677-2262 fail here.
                # Timedeltas can't be read back from this JSON at all.
                error = f"{type(exc).__name__}: the output can't be read back: {exc}"
                return SandboxResult(None, error[-ERROR_LIMIT:])
        error_file = work / "error.txt"
        if error_file.exists():
            error = error_file.read_text(encoding="utf-8")
        else:
            error = stderr.strip() or f"sandbox exited with code {returncode}"
        return SandboxResult(None, error[-ERROR_LIMIT:])


def _start(
    command: list[str], work: Path, env: dict[str, str], timeout: float, jailed: bool
) -> tuple[int, str]:
    """Run the child, in the jail where there is one. Returns (exit code, its stderr)."""
    if not jailed:
        proc = subprocess.run(
            command, cwd=work, env=env, timeout=timeout, capture_output=True, text=True
        )
        return proc.returncode, proc.stderr
    with open(work / "stdout.txt", "wb") as out, open(work / "stderr.txt", "wb") as err:
        code = _jail.run(command, cwd=work, env=env, timeout=timeout, stdout=out, stderr=err)
    return code, (work / "stderr.txt").read_text(encoding="utf-8", errors="replace")


def child_command(work: Path) -> list[str]:
    # A frozen app has no python.exe to run, so it runs itself.
    if getattr(sys, "frozen", False):
        return [sys.executable, SANDBOX_FLAG, str(work)]
    return [sys.executable, "-I", "-m", "schema_engine._runner", str(work)]


def check_code(code: str) -> list[str]:
    """Return the reasons ``code`` may not run. An empty list means it passed."""
    try:
        tree = ast.parse(code, filename="transform.py")
    except SyntaxError as exc:
        return [f"SyntaxError on line {exc.lineno}: {exc.msg}"]

    problems: list[str] = []
    for node in ast.walk(tree):
        line = getattr(node, "lineno", "?")
        if isinstance(node, ast.Import):
            problems += [_import_problem(alias.name, line) for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                problems.append(f"line {line}: relative imports are not allowed")
                continue
            module = node.module or ""
            problems.append(_import_problem(module, line))
            if any(alias.name == "*" for alias in node.names):
                # it would hand over blocked names like fromfile without the check seeing them
                problems.append(f"line {line}: 'from {module} import *' is not allowed")
            problems += [_name_problem(alias.name, line) for alias in node.names]
        elif isinstance(node, ast.Attribute):
            problems.append(_name_problem(node.attr, line))
        elif isinstance(node, ast.Name) and (node.id in BLOCKED_CALLS or _dunder(node.id)):
            problems.append(f"line {line}: '{node.id}' is not allowed")

    if not any(isinstance(n, ast.FunctionDef) and n.name == "transform" for n in tree.body):
        problems.append("no top-level 'def transform(df)' found")
    return list(dict.fromkeys(p for p in problems if p))


def _import_problem(module: str, line: int | str) -> str:
    parts = module.split(".")
    if parts[0] not in ALLOWED_IMPORTS:
        return f"line {line}: import of '{module}' is not allowed"
    blocked = [p for p in parts[1:] if _blocked_name(p)]
    return f"line {line}: import of '{module}' is not allowed" if blocked else ""


def _name_problem(name: str, line: int | str) -> str:
    return f"line {line}: '{name}' is not allowed" if _blocked_name(name) else ""


def _blocked_name(name: str) -> bool:
    if _dunder(name) or name in BLOCKED_ATTRIBUTES or name.startswith("read_"):
        return True
    return name.startswith("to_") and name not in ALLOWED_TO_METHODS


def _dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")
