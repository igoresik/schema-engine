"""Load CSV/XLSX files into DataFrames without silently "fixing" them.

The goal is to hand the transform step the data as it really is. Headers are
kept verbatim (including duplicates and blanks, which pandas would otherwise
rename), CSV cells stay as text so leading zeros and odd formats survive, and
only truly empty cells become nulls. Placeholder strings like ``N/A`` are left
alone and reported by the profiler instead.
"""

from __future__ import annotations

import csv
import math
import sqlite3
from contextlib import closing
from datetime import date, datetime
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from schema_engine.presets import ExportPreset

CSV_SUFFIXES = {".csv", ".tsv", ".txt"}
EXCEL_SUFFIXES = {".xlsx", ".xlsm"}
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


def default_output(path: Path, target: ExportPreset | None = None) -> Path:
    """``NAME_clean`` next to ``path``, in a format that can be written."""
    suffix = target.suffix if target else path.suffix.lower()
    if suffix not in WRITE_SUFFIXES:
        suffix = ".xlsx" if suffix in EXCEL_SUFFIXES else ".csv"
    return path.with_name(f"{path.stem}_clean{suffix}")


def sqlite_script(df: pd.DataFrame, table: str) -> str:
    """``CREATE TABLE`` and ``INSERT`` statements in one transaction: ``sqlite3 my.db < file``.

    SQLite checks the ``CREATE TABLE`` first, so names it can't take (``Name`` next to
    ``name``) fail before anything is written. The values are written here rather than
    by ``iterdump``, because older SQLite dumps infinity as ``Inf``, which won't load.
    Dates become ISO 8601 text and bools 0/1.
    """
    name = _quote(table)
    columns = ",\n".join(f"  {_quote(str(c))} {_sql_type(col)}" for c, col in df.items())
    create = f"CREATE TABLE {name} (\n{columns}\n)"
    with closing(sqlite3.connect(":memory:")) as db:
        try:
            db.execute(create)
        except sqlite3.Error as exc:
            raise ValueError(f"Can't write the SQLite table: {exc}") from exc
    values = [[_literal(v) for v in _sql_values(col)] for _, col in df.items()]
    rows = (f"INSERT INTO {name} VALUES({','.join(row)});" for row in zip(*values, strict=True))
    return "\n".join(["BEGIN TRANSACTION;", f"{create};", *rows, "COMMIT;"]) + "\n"


def _literal(v: object) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, float):
        return repr(v) if math.isfinite(v) else ("9e999" if v > 0 else "-9e999")
    if isinstance(v, int):
        return str(v)
    if isinstance(v, bytes):
        return f"X'{v.hex()}'"
    text = str(v)
    if "\x00" in text:  # a NUL can't sit inside a text literal
        return f"CAST(X'{text.encode().hex()}' AS TEXT)"
    return "'" + text.replace("'", "''") + "'"


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
        read = partial(
            pd.read_csv,
            path,
            sep=delimiter,
            header=None,
            dtype=str,
            keep_default_na=False,
            na_values=[""],
            encoding=encoding,
            skip_blank_lines=True,
        )
        try:
            raw = read()
        except pd.errors.ParserError:
            # A row longer than the first one: widen to the longest row instead of giving up.
            with path.open(encoding=encoding, newline="") as f:
                width = max(len(row) for row in csv.reader(f, delimiter=delimiter))
            raw = read(names=range(width))
        return _promote_header(raw)
    raise ValueError(f"Could not decode {path.name}") from last_error


def _load_excel(path: Path, sheet: str | int) -> pd.DataFrame:
    try:
        raw = pd.read_excel(
            path, sheet_name=sheet, header=None, keep_default_na=False, na_values=[""]
        )
    except ImportError as exc:  # pandas picks the reader from the bytes, e.g. a renamed .xls
        raise ValueError(f"{path.name} isn't an .xlsx workbook. Save it as .xlsx or CSV.") from exc
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
