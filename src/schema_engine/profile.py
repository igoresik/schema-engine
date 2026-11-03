"""Data plane -> control plane boundary: turn a DataFrame into a metadata profile.

The profile is the only thing about a dataset that may be sent to an LLM. Its
size depends on the number of columns, never on the number of rows, so prompt
cost stays O(1) in dataset length.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from schema_engine.privacy import PrivacyMode, classify_value, date_order, mask_value

SAMPLE_ROWS = 3
"""Rows in the sample head (masked or raw modes)."""

CLASSIFY_LIMIT = 2000
"""Max non-null values per column inspected for pattern detection."""

RAW_TEXT_LIMIT = 64
"""Raw sample strings are truncated to this many characters."""

DOMINANT_SHARE = 0.6
"""A column gets a single semantic type when one pattern covers this share."""


@dataclass(frozen=True)
class ColumnProfile:
    name: str
    dtype: str
    semantic_type: str
    null_count: int
    null_pct: float
    unique_count: int
    patterns: dict[str, float] = field(default_factory=dict)
    placeholder_nulls: int = 0
    untrimmed_values: int = 0
    date_order: str | None = None
    header_issues: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DatasetProfile:
    source: str
    row_count: int
    column_count: int
    duplicate_rows: int
    privacy_mode: PrivacyMode
    columns: list[ColumnProfile]
    sample: list[list[Any]] | None
    """Sample head as rows of values in ``columns`` order.

    Lists rather than dicts so duplicate or blank headers survive, and column
    names are not repeated in every row (fewer tokens).
    """

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["privacy_mode"] = str(self.privacy_mode)
        if data["sample"] is None:
            del data["sample"]
        return data

    def to_json(self, indent: int | None = None) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False, default=str)

    def estimated_tokens(self) -> int:
        """Rough token count of the compact JSON (about 4 characters per token)."""
        return math.ceil(len(self.to_json()) / 4)


def build_profile(
    df: pd.DataFrame,
    *,
    source: str = "dataset",
    privacy_mode: PrivacyMode = PrivacyMode.MASKED,
    sample_rows: int = SAMPLE_ROWS,
) -> DatasetProfile:
    """Summarise ``df`` into metadata that is safe to share under ``privacy_mode``."""
    privacy_mode = PrivacyMode(privacy_mode)
    columns = [_profile_column(df, i) for i in range(df.shape[1])]
    return DatasetProfile(
        source=Path(source).name,  # never leak local directory structure
        row_count=int(df.shape[0]),
        column_count=int(df.shape[1]),
        duplicate_rows=int(df.duplicated().sum()) if df.shape[1] else 0,
        privacy_mode=privacy_mode,
        columns=columns,
        sample=_sample(df, privacy_mode, sample_rows),
    )


def _profile_column(df: pd.DataFrame, position: int) -> ColumnProfile:
    name = str(df.columns[position])
    series = df.iloc[:, position]
    total = len(series)
    null_mask = series.isna()
    non_null = series[~null_mask]
    probe = _probe(non_null)

    labels = Counter(classify_value(v) for v in probe)
    placeholder_share = labels.pop("null_token", 0)
    labels.pop("null", None)
    patterns = _shares(labels)
    semantic_type = _dominant(patterns)

    scale = len(non_null) / len(probe) if len(probe) else 0
    placeholders = round(placeholder_share * scale)
    untrimmed = sum(1 for v in probe if isinstance(v, str) and v != v.strip())

    order = None
    if semantic_type in ("date", "mixed"):
        orders = {o for v in probe if isinstance(v, str) and (o := date_order(v))}
        if len(orders) == 1:
            order = orders.pop()
        elif orders:
            order = "inconsistent"

    null_count = int(null_mask.sum())
    return ColumnProfile(
        name=name,
        dtype=str(series.dtype),
        semantic_type=semantic_type,
        null_count=null_count,
        null_pct=round(100 * null_count / total, 2) if total else 0.0,
        unique_count=int(non_null.nunique(dropna=True)),
        patterns=patterns,
        placeholder_nulls=placeholders,
        untrimmed_values=round(untrimmed * scale),
        date_order=order,
        header_issues=_header_issues(df.columns, position),
    )


def _probe(non_null: pd.Series) -> list[Any]:
    """Deterministic, evenly spaced subset so huge files profile in bounded time."""
    n = len(non_null)
    if n <= CLASSIFY_LIMIT:
        return non_null.tolist()
    step = n / CLASSIFY_LIMIT
    return [non_null.iloc[int(i * step)] for i in range(CLASSIFY_LIMIT)]


def _shares(labels: Counter[str]) -> dict[str, float]:
    total = sum(labels.values())
    if not total:
        return {}
    return {k: round(v / total, 2) for k, v in labels.most_common(3)}


def _dominant(patterns: dict[str, float]) -> str:
    if not patterns:
        return "empty"
    label, share = next(iter(patterns.items()))
    return label if share >= DOMINANT_SHARE else "mixed"


def _header_issues(columns: pd.Index, position: int) -> list[str]:
    raw = columns[position]
    name = str(raw)
    issues = []
    if name != name.strip():
        issues.append("whitespace")
    if not name.strip():
        issues.append("unnamed")
    normalised = [str(c).strip().lower() for c in columns]
    if normalised.count(name.strip().lower()) > 1:
        issues.append("duplicate")
    return issues


def _sample(df: pd.DataFrame, mode: PrivacyMode, rows: int) -> list[list[Any]] | None:
    if mode is PrivacyMode.NONE or rows <= 0:
        return None
    head = df.head(rows)
    convert = mask_value if mode is PrivacyMode.MASKED else _raw_value
    return [[convert(_py(val)) for val in row] for row in head.itertuples(index=False, name=None)]


def _py(value: Any) -> Any:
    """Normalise pandas/numpy scalars to plain Python values."""
    if value is None or value is pd.NaT:
        return None
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    if hasattr(value, "item"):  # numpy scalar
        value = value.item()
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _raw_value(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, str) and len(value) > RAW_TEXT_LIMIT:
        return value[:RAW_TEXT_LIMIT] + "…"
    return value
