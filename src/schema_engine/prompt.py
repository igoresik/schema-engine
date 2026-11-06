"""What the model is told, and how its code is pulled out of the reply."""

from __future__ import annotations

import re

import pandas as pd

from schema_engine.privacy import NULL_TOKENS
from schema_engine.profile import DatasetProfile
from schema_engine.providers import Message
from schema_engine.sandbox import ALLOWED_IMPORTS

SYSTEM_PROMPT = f"""\
You write one Python function that cleans a table with pandas.

Rules:
- Reply with a single ```python code block and nothing else.
- Define `transform(df: pd.DataFrame) -> pd.DataFrame` at the top level and return a \
new DataFrame. Put imports at the top of the block.
- Allowed imports: {", ".join(sorted(ALLOWED_IMPORTS))}. Nothing else.
- No file, network or system access. Do not use eval, exec, query, getattr, open or \
dunder attributes. No pandas read_* functions or to_csv-style writers (converters like \
to_datetime and to_numeric are fine).
- You see a profile of the data, not the data. The code must work for every row: \
handle nulls, placeholder nulls ({", ".join(sorted(NULL_TOKENS))}), stray whitespace and \
the mixed formats the profile reports.
- Sample rows may be synthetic. They show formats only, so never hardcode their values.
- Columns are listed in table order. Names can be blank or repeated, so select by \
position with df.iloc[:, i] when a name is not unique.
- Output column names must be unique, non-empty strings.
- The code runs on pandas {pd.__version__}. Avoid old APIs it removed or deprecated: \
to_datetime(infer_datetime_format=...), DataFrame.applymap (use DataFrame.map) and \
DataFrame.append (use pd.concat).
"""


def build_messages(profile: DatasetProfile, instruction: str, rules: str = "") -> list[Message]:
    """``rules`` are a preset's output format, placed after the task."""
    task = f"Task: {instruction.strip()}"
    for extra in (rules.strip(), _position_note(profile), _date_note(profile)):
        if extra:
            task += f"\n\n{extra}"
    user = f"{task}\n\nData profile (JSON):\n{profile.to_json()}"
    return [Message("system", SYSTEM_PROMPT), Message("user", user)]


def _position_note(profile: DatasetProfile) -> str:
    # Small models skip the general rule; naming the exact positions works better.
    risky = [
        f"df.iloc[:, {i}] ({c.name!r})"
        for i, c in enumerate(profile.columns)
        if {"duplicate", "unnamed"} & set(c.header_issues)
    ]
    if not risky:
        return ""
    return (
        "Some column names are blank or repeat, so df[name] can't pick one column. "
        f"Select these by position: {', '.join(risky)}. To replace one, use "
        "df.isetitem(i, values): df.iloc[:, i] = values can't change the column's dtype."
    )


def _date_note(profile: DatasetProfile) -> str:
    # gpt-oss-120b passed infer_datetime_format on every first try despite the system rule.
    dates = [repr(c.name) for c in profile.columns if c.date_order]
    if not dates:
        return ""
    return (
        f"Dates in {', '.join(dates)}: pd.to_datetime in pandas {pd.__version__} has no "
        'infer_datetime_format argument. Use format="mixed" for mixed formats.'
    )


def repair_message(error: str) -> Message:
    return Message(
        "user",
        f"That code failed:\n\n{error}\n\nFix it. Reply with the full corrected code block only.",
    )


_BLOCK_RE = re.compile(r"```[ \t]*(?:python|py)?[ \t]*\n(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_code(reply: str) -> str:
    """The code block that defines ``transform``, else the first block, else the reply."""
    blocks = _BLOCK_RE.findall(reply)
    for block in blocks:
        if "def transform" in block:
            return block.strip() + "\n"
    return (blocks[0] if blocks else reply).strip() + "\n"
