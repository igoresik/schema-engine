"""The compile loop: profile, prompt, model, sandbox, and retry with the error."""

from __future__ import annotations

import ast
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

import pandas as pd
from pandas.api.types import is_bool, is_bool_dtype, is_datetime64_any_dtype, is_numeric_dtype

from schema_engine.presets import ExportPreset
from schema_engine.privacy import NULL_TOKENS, PrivacyMode, mask_string
from schema_engine.profile import build_profile
from schema_engine.prompt import build_messages, extract_code, repair_message
from schema_engine.providers import LLMProvider, Message, Usage
from schema_engine.sandbox import run_transform

MAX_TOKENS = 4096
"""Reply budget per attempt. Reasoning models spend part of it thinking."""

MIN_SCRUB_LENGTH = 3
"""Shorter cell values are left in error text; they reveal little and match too much."""


@dataclass(frozen=True)
class Attempt:
    number: int
    code: str
    usage: Usage
    latency_ms: float
    error: str | None = None
    """What went back to the model. None means the transform ran and passed."""


@dataclass(frozen=True)
class Lost:
    column: str
    blanked: int
    filled: int
    """Cells with a real value in the input."""


@dataclass(frozen=True)
class CompileResult:
    attempts: list[Attempt]
    output: pd.DataFrame | None = None
    lost: list[Lost] = field(default_factory=list)
    """Set when no try kept every value, so the output came from the one that lost fewest."""
    picked: int = -1
    """Index of the attempt that made ``output``."""

    @property
    def ok(self) -> bool:
        return self.output is not None

    @property
    def code(self) -> str | None:
        return self.attempts[self.picked].code if self.attempts else None

    @property
    def usage(self) -> Usage:
        return sum((a.usage for a in self.attempts), Usage())


def compile_transform(
    df: pd.DataFrame,
    instruction: str,
    provider: LLMProvider,
    *,
    privacy_mode: PrivacyMode = PrivacyMode.MASKED,
    source: str = "dataset",
    max_attempts: int = 3,
    timeout: float = 30.0,
    temperature: float | None = None,
    preset: ExportPreset | None = None,
    on_attempt: Callable[[Attempt], None] | None = None,
) -> CompileResult:
    """Ask the model for ``transform(df)`` and run it, feeding errors back until it works.

    With a preset, an output that fails its check counts as an error too. So do
    values that parsing turned blank, but if every try loses some, the output that
    lost fewest is kept, with ``lost`` set.
    Provider errors (bad key, endpoint down) are raised, not retried here.
    """
    privacy_mode = PrivacyMode(privacy_mode)
    profile = build_profile(df, source=source, privacy_mode=privacy_mode)
    base = build_messages(profile, instruction, preset.rules if preset else "")
    messages = base
    attempts: list[Attempt] = []
    fallback: CompileResult | None = None
    for number in range(1, max_attempts + 1):
        completion = provider.complete(messages, max_tokens=MAX_TOKENS, temperature=temperature)
        code = extract_code(completion.text)
        result = run_transform(code, df, timeout=timeout)
        output, problems, lost = result.output, [], []
        if preset and output is not None:
            output, problems = preset.fit(output)
        error = None
        if result.error is not None:
            error = scrub_error(result.error, df, code, privacy_mode)
        elif preset and problems:
            scrub = _scrubber(df, code, privacy_mode)
            error = preset_error(preset.name, problems, output.columns, scrub)
        elif lost := lost_values(df, result.output):
            error = lost_error(lost)
        attempt = Attempt(number, code, completion.usage, completion.latency_ms, error)
        attempts.append(attempt)
        if on_attempt:
            on_attempt(attempt)
        if error is None:
            return CompileResult(attempts, output, picked=number - 1)
        if lost and (fallback is None or _blanked(lost) < _blanked(fallback.lost)):
            fallback = CompileResult(attempts, output, lost, number - 1)
        # Only the latest try goes back, so each retry costs about the same.
        messages = [*base, Message("assistant", completion.text), repair_message(error or "")]
    if fallback:
        return CompileResult(attempts, fallback.output, fallback.lost, fallback.picked)
    return CompileResult(attempts)


def lost_values(before: pd.DataFrame, after: pd.DataFrame) -> list[Lost]:
    """Output columns parsed to numbers, dates or bools that hold fewer values than before.

    That's where to_numeric/to_datetime(errors="coerce") quietly blank what they can't
    read. Text columns aren't checked, so a task that clears a column on purpose isn't
    flagged. Columns are matched by name, ignoring case and punctuation (``Unit Price``
    and ``unit_price``); names that repeat, and renamed columns, are skipped.
    Every dropped row is counted as one that held a value, so dropping duplicates or
    filtering rows never looks like a loss. The count is a lower bound.
    """
    keys_in = [_key(c) for c in before.columns]
    keys_out = [_key(c) for c in after.columns]
    dropped = max(len(before) - len(after), 0)
    lost = []
    for position, key in enumerate(keys_in):
        if not key or keys_in.count(key) != 1 or keys_out.count(key) != 1:
            continue
        out = after.iloc[:, keys_out.index(key)]
        if not _parsed(out):
            continue
        filled = int(before.iloc[:, position].map(_has_value).sum())
        blanked = filled - dropped - int(out.notna().sum())
        if blanked > 0:
            lost.append(Lost(str(before.columns[position]), blanked, filled))
    return lost


def lost_error(lost: list[Lost]) -> str:
    """What goes back to the model. Counts and column names only, never values."""
    lines = "\n".join(
        f"- '{x.column}': {x.blanked} of {x.filled} values became blank" for x in lost
    )
    total, columns = _blanked(lost), len(lost)
    return (
        f"{lines}\nRead every format listed in the column's value_shapes. Only placeholder "
        f"nulls should become blank.\n{total} value{'s' if total > 1 else ''} became blank "
        f"in {columns} column{'s' if columns > 1 else ''}"
    )


def _blanked(lost: list[Lost]) -> int:
    return sum(x.blanked for x in lost)


def _key(name: object) -> str:
    return re.sub(r"[^0-9a-z]+", "_", str(name).strip().lower()).strip("_")


def _parsed(column: pd.Series) -> bool:
    if is_numeric_dtype(column) or is_datetime64_any_dtype(column) or is_bool_dtype(column):
        return True
    values = column.dropna()
    return len(values) > 0 and bool(values.map(is_bool).all())


def _has_value(value: object) -> bool:
    if isinstance(value, str):
        text = value.strip().lower()
        return bool(text) and text not in NULL_TOKENS
    return pd.isna(value) is not True  # list cells make isna return an array


def preset_error(
    name: str, problems: list[str], columns: Iterable[object], scrub: Callable[[str], str]
) -> str:
    """List a preset's problems, summary last like a traceback.

    Only the quoted output column names get scrubbed: they can come from cell
    values, while the rest is fixed text that plain-text scrubbing would garble.
    """
    text = "\n".join(f"- {p}" for p in problems)
    for column in map(str, columns):
        hidden = scrub(column)
        if hidden != column:
            text = text.replace(f"'{column}'", f"'{hidden}'")
    count = f"{len(problems)} problem{'s' if len(problems) > 1 else ''}"
    return f"{text}\nOutput doesn't fit the {name} format ({count})"


def scrub_error(error: str, df: pd.DataFrame, code: str, mode: PrivacyMode) -> str:
    """Hide cell values quoted in an error message, unless raw mode allows them.

    Column names and strings the model wrote itself are kept, so it can still
    tell what went wrong. Best effort: values are matched as plain text.
    """
    return _scrubber(df, code, mode)(error)


def _scrubber(df: pd.DataFrame, code: str, mode: PrivacyMode) -> Callable[[str], str]:
    if mode is PrivacyMode.RAW:
        return lambda text: text
    keep = {str(c) for c in df.columns} | _string_constants(code)
    cells = {str(v) for v in pd.unique(df.to_numpy(dtype=object).ravel()) if not pd.isna(v)}
    values = {v for c in cells for v in (c, c.strip()) if len(v) >= MIN_SCRUB_LENGTH}
    ordered = sorted(values - keep, key=len, reverse=True)

    def scrub(text: str) -> str:
        for value in ordered:
            if value in text:
                text = text.replace(
                    value, mask_string(value) if mode is PrivacyMode.MASKED else "<value>"
                )
        return text

    return scrub


def _string_constants(code: str) -> set[str]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return set()
    return {
        n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }
