from pathlib import Path

from schema_engine.sandbox import run_transform

EXAMPLE = Path(__file__).parents[1] / "examples" / "clean_orders.py"


def test_example_cleans_the_sample_data(dirty_df):
    result = run_transform(EXAMPLE.read_text(encoding="utf-8"), dirty_df)
    assert result.ok, result.error
    out = result.output
    assert list(out.columns) == [
        "order_id",
        "customer",
        "email",
        "order_date",
        "product",
        "unit_price",
        "currency",
        "qty",
        "in_stock",
        "notes",
    ]
    assert not out.duplicated().any()
    assert len(out) < len(dirty_df)  # the sample has duplicate rows
    assert out["order_id"].str.match(r"^ORD-\d{5}$").all()
    assert (out["customer"] == out["customer"].str.title()).all()
    assert set(out["currency"].dropna()) <= {"USD", "EUR"}
    assert str(out["order_date"].dtype).startswith("datetime64")
    assert str(out["qty"].dtype) == "Int64"
    assert str(out["in_stock"].dtype) == "boolean"
    assert not out["notes"].str.lower().isin(["n/a", "null", "-"]).any()
