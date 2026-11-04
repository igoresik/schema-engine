"""The compile loop: profile, prompt, model, sandbox, and retry with the error."""

from __future__ import annotations

import ast
from collections.abc import Callable, Iterable
from dataclasses import dataclass

import pandas as pd

from schema_engine.presets import ExportPreset
from schema_engine.privacy import PrivacyMode, mask_string
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
class CompileResult:
    attempts: list[Attempt]
    output: pd.DataFrame | None = None

    @property
    def ok(self) -> bool:
        return self.output is not None

    @property
    def code(self) -> str | None:
        return self.attempts[-1].code if self.attempts else None

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

    With a preset, an output that fails its check counts as an error too.
    Provider errors (bad key, endpoint down) are raised, not retried here.
    """
    privacy_mode = PrivacyMode(privacy_mode)
    profile = build_profile(df, source=source, privacy_mode=privacy_mode)
    base = build_messages(profile, instruction, preset.rules if preset else "")
    messages = base
    attempts: list[Attempt] = []
    for number in range(1, max_attempts + 1):
        completion = provider.complete(messages, max_tokens=MAX_TOKENS, temperature=temperature)
        code = extract_code(completion.text)
        result = run_transform(code, df, timeout=timeout)
        problems = preset.check(result.output) if preset and result.output is not None else []
        error = None
        if result.error is not None:
            error = scrub_error(result.error, df, code, privacy_mode)
        elif preset and problems:
            scrub = _scrubber(df, code, privacy_mode)
            error = preset_error(preset.name, problems, result.output.columns, scrub)
        attempt = Attempt(number, code, completion.usage, completion.latency_ms, error)
        attempts.append(attempt)
        if on_attempt:
            on_attempt(attempt)
        if error is None:
            return CompileResult(attempts, result.output)
        # Only the latest try goes back, so each retry costs about the same.
        messages = [*base, Message("assistant", completion.text), repair_message(error or "")]
    return CompileResult(attempts)


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
