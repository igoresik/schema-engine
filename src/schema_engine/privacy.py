"""Privacy tiers that decide which row-level information may reach the LLM.

The control plane only ever sees metadata. ``PrivacyMode`` controls whether a
small sample head is included and, if so, whether it is real or masked.
Masking is shape-preserving: it keeps the structural cues an LLM needs to write
parsing code (date order, separators, currency symbols, decimal places) while
replacing every actual value.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime
from enum import StrEnum
from typing import Any


class PrivacyMode(StrEnum):
    """What the LLM is allowed to receive about row values."""

    MASKED = "masked"
    """Default. Metadata plus a synthetic head that mirrors value formats."""

    NONE = "none"
    """Strict. Metadata only, zero row values."""

    RAW = "raw"
    """Opt-in. Metadata plus an untouched head. For public data or local models."""


NULL_TOKENS = frozenset({"n/a", "na", "null", "none", "nan", "nil", "-", "--", "?", "missing"})

EMAIL_TOKEN = "user@example.com"
URL_TOKEN = "https://example.com/path"

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
_URL_RE = re.compile(r"^(https?://|www\.)\S+$", re.IGNORECASE)
_BOOL_WORDS = {
    "true": "true",
    "false": "true",
    "yes": "yes",
    "no": "yes",
    "y": "y",
    "n": "y",
    "t": "t",
    "f": "t",
}
# Numeric dates such as 2024-03-15, 15/03/2024, 3.15.24, optionally with a time.
_NUMERIC_DATE_RE = re.compile(
    r"^(?P<a>\d{1,4})(?P<sep>[-/.])(?P<b>\d{1,2})(?P=sep)(?P<c>\d{2,4})"
    r"(?P<time>[T ]\d{1,2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:?\d{2})?)?$"
)
_MONTH_NAMES = (
    "january|february|march|april|may|june|july|august|september|october|november|december|"
    "jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec"
)
_MONTH_NAME_RE = re.compile(rf"\b({_MONTH_NAMES})\b", re.IGNORECASE)
_CURRENCY_CODES = (
    "USD|EUR|GBP|JPY|CNY|INR|CAD|AUD|NZD|CHF|SEK|NOK|DKK|PLN|CZK|HUF|RUB|UAH|"
    "BRL|MXN|ZAR|TRY|SGD|HKD|KRW|AED|SAR|ILS|RSD|HRK|BAM"
)
_CURRENCY = rf"(?:[$€£¥₹]|(?:{_CURRENCY_CODES})\b)"
# Amounts and plain numbers: optional currency symbol/code, digits with separators.
_AMOUNT_RE = re.compile(
    rf"^(?P<pre>[-+(]?\s*{_CURRENCY}?\s*-?)"
    r"(?P<num>\d[\d,.\s']*\d|\d)"
    rf"(?P<post>\s*(?:%|{_CURRENCY})?\)?)$"
)
_CURRENCY_RE = re.compile(_CURRENCY)


def mask_value(value: Any) -> Any:
    """Return a synthetic stand-in for ``value`` that keeps its structural shape.

    Nulls stay null so the LLM still sees where blanks occur.
    """
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, bool):
        return True
    if isinstance(value, int):
        return _mask_digits(str(value))
    if isinstance(value, float):
        return float(_mask_digits(repr(value))) if math.isfinite(value) else value
    if isinstance(value, datetime):
        return "YYYY-MM-DD HH:MM:SS" if _has_time(value) else "YYYY-MM-DD"
    if isinstance(value, date):
        return "YYYY-MM-DD"
    if isinstance(value, str):
        return mask_string(value)
    # Unknown objects (Decimal, lists, ...) fall back to the string-shape rule.
    return mask_string(str(value))


def mask_string(text: str) -> str:
    """Mask a string value. Leading/trailing whitespace is preserved as a cue."""
    stripped = text.strip()
    if not stripped:
        return text
    lead = text[: len(text) - len(text.lstrip())]
    trail = text[len(text.rstrip()) :]
    return f"{lead}{_mask_core(stripped)}{trail}"


def _mask_core(s: str) -> str:
    if s.lower() in NULL_TOKENS:
        # Placeholder blanks carry no personal data, and the LLM needs to see
        # them to write correct null handling.
        return s
    if _EMAIL_RE.match(s):
        return EMAIL_TOKEN
    if _URL_RE.match(s):
        return URL_TOKEN
    lowered = s.lower()
    if lowered in _BOOL_WORDS:
        return _match_case(_BOOL_WORDS[lowered], s)
    m = _NUMERIC_DATE_RE.match(s)
    if m:
        return _numeric_date_token(m)
    if _MONTH_NAME_RE.search(s) and any(ch.isdigit() for ch in s):
        # "March 5, 2024" -> "Month D, YYYY"-style shape.
        return _mask_digits(_MONTH_NAME_RE.sub("Month", s))
    if _AMOUNT_RE.match(s):
        return _mask_digits(s)
    return _mask_text(s)


def classify_value(value: Any) -> str:
    """Label a single value with a coarse semantic type.

    Used locally by the profiler to describe columns without sending values.
    Returns one of: null, null_token, boolean, number, currency, date, email,
    url, text.
    """
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, date):
        return "date"
    s = str(value).strip()
    if not s:
        return "null_token"  # whitespace-only cell: blank in disguise
    lowered = s.lower()
    if lowered in NULL_TOKENS:
        return "null_token"
    if _EMAIL_RE.match(s):
        return "email"
    if _URL_RE.match(s):
        return "url"
    if lowered in _BOOL_WORDS:
        return "boolean"
    if _NUMERIC_DATE_RE.match(s) or (_MONTH_NAME_RE.search(s) and _has_digit(s) and len(s) <= 30):
        return "date"
    m = _AMOUNT_RE.match(s)
    if m:
        return "currency" if _CURRENCY_RE.search(m["pre"] + m["post"]) else "number"
    return "text"


def date_order(text: str) -> str | None:
    """Return 'YMD', 'DMY', 'MDY' or None (ambiguous / not a numeric date)."""
    m = _NUMERIC_DATE_RE.match(text.strip())
    if not m:
        return None
    if len(m["a"]) == 4:
        return "YMD"
    first, second = int(m["a"]), int(m["b"])
    if first > 12 >= second:
        return "DMY"
    if second > 12 >= first:
        return "MDY"
    return None


def _has_digit(s: str) -> bool:
    return any(ch.isdigit() for ch in s)


def _numeric_date_token(m: re.Match[str]) -> str:
    a, b, c, sep = m["a"], m["b"], m["c"], m["sep"]
    if len(a) == 4:
        parts = ["YYYY", "MM", "DD"]
    else:
        year = "YYYY" if len(c) == 4 else "YY"
        first, second = int(a), int(b)
        if first > 12 >= second:
            parts = ["DD", "MM", year]
        elif second > 12 >= first:
            parts = ["MM", "DD", year]
        else:
            # Order cannot be told from this value alone; the column profile's
            # date_order hint (computed over all rows locally) resolves it.
            parts = ["NN", "NN", year]
    token = sep.join(parts)
    if m["time"]:
        token += m["time"][0] + "HH:MM" + (":SS" if m["time"].count(":") >= 2 else "")
    return token


def _mask_digits(s: str) -> str:
    return re.sub(r"\d", "9", s)


def _mask_text(s: str) -> str:
    """Generic fallback: letters become x/X, digits 9, punctuation stays.

    Long values are truncated so free text never inflates token usage.
    """
    out = []
    for ch in s[:24]:
        if ch.isdigit():
            out.append("9")
        elif ch.isalpha():
            out.append("X" if ch.isupper() else "x")
        else:
            out.append(ch)
    if len(s) > 24:
        out.append("…")
    return "".join(out)


def _match_case(token: str, original: str) -> str:
    if original.isupper():
        return token.upper()
    if original[:1].isupper():
        return token.capitalize()
    return token


def _has_time(value: datetime) -> bool:
    return (value.hour, value.minute, value.second, value.microsecond) != (0, 0, 0, 0)
