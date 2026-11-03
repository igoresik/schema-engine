"""Check and run generated ``transform(df)`` code.

Two layers stand between model output and your machine:

1. A static check of the code before anything runs: allowlisted imports only, and
   no file, network, eval or dunder access.
2. A separate Python process with a timeout and no API keys in its environment,
   where file and network access are blocked while the transform runs.

This stops careless or buggy code. It is not a hard security boundary like a
container or VM.
"""

from __future__ import annotations

import ast

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
        "fromfile",
        "genfromtxt",
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
