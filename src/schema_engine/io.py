"""Load CSV/XLSX files into DataFrames without silently "fixing" them.

The goal is to hand the transform step the data as it really is. Headers are
kept verbatim (including duplicates and blanks, which pandas would otherwise
rename), CSV cells stay as text so leading zeros and odd formats survive, and
only truly empty cells become nulls. Placeholder strings like ``N/A`` are left
alone and reported by the profiler instead.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd

CSV_SUFFIXES = {".csv", ".tsv", ".txt"}
EXCEL_SUFFIXES = {".xlsx", ".xlsm", ".xls"}
ENCODINGS = ("utf-8-sig", "cp1252", "latin-1")


class UnsupportedFormatError(ValueError):
    """Raised for file types the loader does not handle."""


def load_dataset(path: str | Path, *, sheet: str | int = 0) -> pd.DataFrame:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in CSV_SUFFIXES:
        return _load_csv(path)
    if suffix in EXCEL_SUFFIXES:
        return _load_excel(path, sheet)
    raise UnsupportedFormatError(f"Unsupported file type '{suffix}'. Use CSV or XLSX.")


def _load_csv(path: Path) -> pd.DataFrame:
    last_error: Exception | None = None
    for encoding in ENCODINGS:
        try:
            text_sample = path.read_text(encoding=encoding)[:65536]
        except UnicodeDecodeError as exc:
            last_error = exc
            continue
        delimiter = _sniff_delimiter(text_sample, path.suffix.lower())
        raw = pd.read_csv(
            path,
            sep=delimiter,
            header=None,
            dtype=str,
            keep_default_na=False,
            na_values=[""],
            encoding=encoding,
            skip_blank_lines=True,
        )
        return _promote_header(raw)
    raise ValueError(f"Could not decode {path.name}") from last_error


def _load_excel(path: Path, sheet: str | int) -> pd.DataFrame:
    raw = pd.read_excel(path, sheet_name=sheet, header=None, keep_default_na=False, na_values=[""])
    df = _promote_header(raw)
    return df.infer_objects()


def _sniff_delimiter(sample: str, suffix: str) -> str:
    if suffix == ".tsv":
        return "\t"
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def _promote_header(raw: pd.DataFrame) -> pd.DataFrame:
    if raw.empty:
        return pd.DataFrame()
    header = ["" if pd.isna(h) else str(h) for h in raw.iloc[0].tolist()]
    df = raw.iloc[1:].reset_index(drop=True)
    df.columns = header
    return df
