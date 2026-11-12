import sqlite3
from contextlib import closing
from datetime import date, datetime
from decimal import Decimal

import pandas as pd
import pytest

from schema_engine.io import UnsupportedFormatError, load_dataset, save_dataset, sqlite_script
from schema_engine.samples import write_samples

# The header that marks a pre-2007 .xls workbook
OLD_XLS = bytes.fromhex("d0cf11e0a1b11ae1") + bytes(504)


def test_csv_keeps_headers_text_and_placeholders(tmp_path):
    path = tmp_path / "a.csv"
    path.write_text(" id ,name,name,\n007,N/A,x,\n008,,y,\n", encoding="utf-8")
    df = load_dataset(path)
    assert list(df.columns) == [" id ", "name", "name", ""]
    assert df.iloc[0, 0] == "007"  # leading zeros survive
    assert df.iloc[0, 1] == "N/A"  # placeholder left for the profiler
    assert pd.isna(df.iloc[1, 1])  # real blank is null


def test_semicolon_delimiter_and_cp1252(tmp_path):
    path = tmp_path / "b.csv"
    path.write_bytes("Préis;Menge\n1,50;2\n".encode("cp1252"))
    df = load_dataset(path)
    assert list(df.columns) == ["Préis", "Menge"]
    assert df.iloc[0, 0] == "1,50"


def test_utf16_text_export(tmp_path):
    path = tmp_path / "u.txt"
    path.write_bytes("nome\tpreço\r\nLápis\t1,50\r\n".encode("utf-16"))
    df = load_dataset(path)
    assert list(df.columns) == ["nome", "preço"]
    assert df.iloc[0].tolist() == ["Lápis", "1,50"]


def test_csv_rows_longer_than_the_header(tmp_path):
    path = tmp_path / "r.csv"
    path.write_text("id,name\n1,Ann\n2,Bob,extra\n3\n", encoding="utf-8")
    df = load_dataset(path)
    assert list(df.columns) == ["id", "name", ""]
    assert df.iloc[1].tolist() == ["2", "Bob", "extra"]
    assert df.iloc[2, 1:].isna().all()


def test_tsv(tmp_path):
    path = tmp_path / "c.tsv"
    path.write_text("a\tb\n1\t2\n")
    assert list(load_dataset(path).columns) == ["a", "b"]


def test_excel_types_inferred(tmp_path):
    path = tmp_path / "d.xlsx"
    pd.DataFrame({"n": [1, 2], "when": pd.to_datetime(["2024-01-01", "2024-01-02"])}).to_excel(
        path, index=False
    )
    df = load_dataset(path)
    assert pd.api.types.is_integer_dtype(df["n"])
    assert pd.api.types.is_datetime64_any_dtype(df["when"])


def test_sample_files_round_trip(tmp_path):
    csv_path, xlsx_path = write_samples(tmp_path, rows=10)
    a, b = load_dataset(csv_path), load_dataset(xlsx_path)
    assert a.shape == b.shape
    assert list(a.columns) == list(b.columns)


def test_unsupported(tmp_path):
    path = tmp_path / "x.json"
    path.write_text("{}")
    with pytest.raises(UnsupportedFormatError):
        load_dataset(path)


def test_old_excel_format(tmp_path):
    with pytest.raises(UnsupportedFormatError, match=r"'\.xls'"):
        load_dataset(tmp_path / "old.xls")
    renamed = tmp_path / "old.xlsx"
    renamed.write_bytes(OLD_XLS)
    with pytest.raises(ValueError, match=r"old.xlsx isn't an .xlsx workbook"):
        load_dataset(renamed)


def test_empty_csv(tmp_path):
    path = tmp_path / "e.csv"
    path.write_text("")
    with pytest.raises(pd.errors.EmptyDataError):
        load_dataset(path)


@pytest.mark.parametrize("name", ["out.csv", "out.tsv", "nested/out.xlsx"])
def test_save_round_trip(tmp_path, name):
    df = pd.DataFrame({"id": ["007", "008"], "city": ["Cork", None]})
    path = tmp_path / name
    save_dataset(df, path)
    back = load_dataset(path)
    assert list(back.columns) == ["id", "city"]
    assert back.iloc[1, 0] == "008"
    assert pd.isna(back.iloc[1, 1])


def test_save_unsupported(tmp_path):
    with pytest.raises(UnsupportedFormatError, match=r"'\.json'"):
        save_dataset(pd.DataFrame({"a": [1]}), tmp_path / "out.json")


def test_sql_script_loads_into_sqlite(tmp_path):
    df = pd.DataFrame(
        {
            "id": pd.array([1, None], dtype="Int64"),
            "price": [9.99, float("inf")],
            "ok": [True, False],
            "day": pd.to_datetime(["2024-03-15", None]),
            "at": [pd.Timestamp("2024-03-15 10:30"), pd.Timestamp("2024-03-16")],
            'na"me': ["it's\nfine", None],
            "when": [date(2024, 1, 2), datetime(2024, 1, 2, 3, 4)],
            "big": [2**70, 5],
            "misc": [Decimal("1.10"), [1, 2]],
            "odd": [b"\x00\xff", "a\x00b"],
            "low": [-float("inf"), 1e-05],
        }
    )
    path = tmp_path / "nested" / "my orders.sql"
    save_dataset(df, path)
    script = path.read_text(encoding="utf-8")
    # Older SQLite would dump infinity as Inf, which doesn't load.
    assert "Inf" not in script
    with closing(sqlite3.connect(":memory:")) as db:
        db.executescript(script)
        ddl = db.execute("SELECT sql FROM sqlite_master").fetchone()[0]
        rows = db.execute('SELECT * FROM "my orders"').fetchall()
    types = ["INTEGER", "REAL", "INTEGER"] + ["TEXT"] * 7 + ["REAL"]
    names = ["id", "price", "ok", "day", "at", 'na""me', "when", "big", "misc", "odd", "low"]
    columns = ",\n".join(f'  "{n}" {t}' for n, t in zip(names, types, strict=True))
    assert ddl == f'CREATE TABLE "my orders" (\n{columns}\n)'
    first = (1, 9.99, 1, "2024-03-15", "2024-03-15 10:30:00", "it's\nfine", "2024-01-02")
    second = (None, float("inf"), 0, None, "2024-03-16 00:00:00", None, "2024-01-02 03:04:00")
    assert rows == [
        (*first, str(2**70), "1.10", b"\x00\xff", -float("inf")),
        (*second, "5", "[1, 2]", "a\x00b", 1e-05),
    ]


def test_sql_names_that_clash_in_sqlite(tmp_path):
    path = tmp_path / "t.sql"
    with pytest.raises(ValueError, match="duplicate column name"):
        save_dataset(pd.DataFrame({"Name": [1], "name": [2]}), path)
    assert not path.exists()
    assert "CREATE TABLE" in sqlite_script(pd.DataFrame({"a": []}), "empty")
