import pandas as pd
import pytest

from schema_engine.io import UnsupportedFormatError, load_dataset
from schema_engine.samples import write_samples


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


def test_empty_csv(tmp_path):
    path = tmp_path / "e.csv"
    path.write_text("")
    with pytest.raises(pd.errors.EmptyDataError):
        load_dataset(path)
