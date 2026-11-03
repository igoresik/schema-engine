"""The compile loop: profile, prompt, model, sandbox, and retry with the error."""

from __future__ import annotations

import ast
from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd

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
    on_attempt: Callable[[Attempt], None] | None = None,
) -> CompileResult:
    """Ask the model for ``transform(df)`` and run it, feeding errors back until it works.

    Provider errors (bad key, endpoint down) are raised, not retried here.
    """
    privacy_mode = PrivacyMode(privacy_mode)
    profile = build_profile(df, source=source, privacy_mode=privacy_mode)
    base = build_messages(profile, instruction)
    messages = base
    attempts: list[Attempt] = []
    for number in range(1, max_attempts + 1):
        completion = provider.complete(messages, max_tokens=MAX_TOKENS, temperature=temperature)
        code = extract_code(completion.text)
        result = run_transform(code, df, timeout=timeout)
        error = None if result.ok else scrub_error(result.error or "", df, code, privacy_mode)
        attempt = Attempt(number, code, completion.usage, completion.latency_ms, error)
        attempts.append(attempt)
        if on_attempt:
            on_attempt(attempt)
        if result.ok:
            return CompileResult(attempts, result.output)
        # Only the latest try goes back, so each retry costs about the same.
        messages = [*base, Message("assistant", completion.text), repair_message(error or "")]
    return CompileResult(attempts)


def scrub_error(error: str, df: pd.DataFrame, code: str, mode: PrivacyMode) -> str:
    """Hide cell values quoted in an error message, unless raw mode allows them.

    Column names and strings the model wrote itself are kept, so it can still
    tell what went wrong. Best effort: values are matched as plain text.
    """
    if mode is PrivacyMode.RAW:
        return error
    keep = {str(c) for c in df.columns} | _string_constants(code)
    cells = {str(v) for v in pd.unique(df.to_numpy(dtype=object).ravel()) if not pd.isna(v)}
    values = {v for c in cells for v in (c, c.strip()) if len(v) >= MIN_SCRUB_LENGTH}
    for value in sorted(values - keep, key=len, reverse=True):
        if value in error:
            error = error.replace(
                value, mask_string(value) if mode is PrivacyMode.MASKED else "<value>"
            )
    return error


def _string_constants(code: str) -> set[str]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return set()
    return {
        n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }
