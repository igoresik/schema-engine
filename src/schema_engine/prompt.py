"""What the model is told, and how its code is pulled out of the reply."""

from __future__ import annotations

import re

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
"""


def build_messages(profile: DatasetProfile, instruction: str) -> list[Message]:
    user = f"Task: {instruction.strip()}\n\nData profile (JSON):\n{profile.to_json()}"
    return [Message("system", SYSTEM_PROMPT), Message("user", user)]


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
