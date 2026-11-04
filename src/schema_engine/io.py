"""Load CSV/XLSX files into DataFrames without silently "fixing" them.

The goal is to hand the transform step the data as it really is. Headers are
kept verbatim (including duplicates and blanks, which pandas would otherwise
rename), CSV cells stay as text so leading zeros and odd formats survive, and
only truly empty cells become nulls. Placeholder strings like ``N/A`` are left
alone and reported by the profiler instead.
"""

from __future__ import annotations

import csv
import sqlite3
from contextlib import closing
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

CSV_SUFFIXES = {".csv", ".tsv", ".txt"}
EXCEL_SUFFIXES = {".xlsx", ".xlsm", ".xls"}
WRITE_SUFFIXES = {".csv", ".tsv", ".txt", ".xlsx", ".sql"}
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


def save_dataset(df: pd.DataFrame, path: str | Path) -> None:
    """Write ``df`` as CSV, TSV, XLSX or a SQLite script, picked from the file suffix."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in WRITE_SUFFIXES:
        raise UnsupportedFormatError(
            f"Can't write '{suffix}' files. Use .csv, .tsv, .xlsx or .sql."
        )
    if suffix == ".sql":
        script = sqlite_script(df, table=path.stem)
    path.parent.mkdir(parents=True, exist_ok=True)
    if suffix == ".xlsx":
        df.to_excel(path, index=False)
    elif suffix == ".sql":
        path.write_text(script, encoding="utf-8")
    else:
        df.to_csv(path, index=False, sep="	" if suffix == ".tsv" else ",")


def sqlite_script(df: pd.DataFrame, table: str) -> str:
    """``CREATE TABLE`` and ``INSERT`` statements in one transaction: ``sqlite3 my.db < file``.

    The rows go through an in-memory database and come back with ``iterdump``, so
    SQLite does the quoting. Dates become ISO 8601 text and bools 0/1.
    """
    columns = ",\n".join(f"  {_quote(str(name))} {_sql_type(col)}" for name, col in df.items())
    rows = list(zip(*(_sql_values(col) for _, col in df.items()), strict=True))
    marks = ", ".join("?" * df.shape[1])
    with closing(sqlite3.connect(":memory:")) as db:
        try:
            db.execute(f"CREATE TABLE {_quote(table)} (\n{columns}\n)")
            db.executemany(f"INSERT INTO {_quote(table)} VALUES ({marks})", rows)
        except sqlite3.Error as exc:
            raise ValueError(f"Can't write the SQLite table: {exc}") from exc
        return "\n".join(db.iterdump()) + "\n"


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _sql_type(col: pd.Series) -> str:
    if pd.api.types.is_bool_dtype(col) or pd.api.types.is_integer_dtype(col):
        return "INTEGER"
    if pd.api.types.is_float_dtype(col):
        return "REAL"
    return "TEXT"


def _sql_values(col: pd.Series) -> list[object]:
    if pd.api.types.is_datetime64_any_dtype(col):
        known = col.dropna()
        # Same as to_csv: drop the time when every value is midnight.
        fmt = "%Y-%m-%d" if (known == known.dt.normalize()).all() else None
        return [
            None if pd.isna(v) else v.strftime(fmt) if fmt else v.isoformat(sep=" ") for v in col
        ]
    return [_sql_value(v) for v in col.astype(object)]


def _sql_value(v: object) -> object:
    if pd.isna(v) is True:  # `is True`: list cells give an array
        return None
    if isinstance(v, bool | np.bool_):
        return int(v)
    if isinstance(v, int | np.integer):
        return int(v) if -(2**63) <= v < 2**63 else str(v)
    if isinstance(v, float | np.floating):
        return float(v)
    if isinstance(v, str | bytes):
        return v
    if isinstance(v, datetime):
        return v.isoformat(sep=" ")
    if isinstance(v, date):
        return v.isoformat()
    return str(v)


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
