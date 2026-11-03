import json

import pandas as pd

from schema_engine.privacy import PrivacyMode
from schema_engine.profile import CLASSIFY_LIMIT, build_profile
from schema_engine.samples import generate_dirty_dataframe


def _col(profile, name, nth=0):
    return [c for c in profile.columns if c.name == name][nth]


def test_profile_basic_counts(dirty_df):
    p = build_profile(dirty_df, source="/home/someone/secret/orders.csv")
    assert p.source == "orders.csv"
    assert p.row_count == len(dirty_df)
    assert p.column_count == dirty_df.shape[1]
    assert p.duplicate_rows >= 1
    assert p.privacy_mode is PrivacyMode.MASKED


def test_semantic_types_and_header_issues(dirty_df):
    p = build_profile(dirty_df)
    assert _col(p, "E-mail").semantic_type == "email"
    assert _col(p, "Order Date").semantic_type == "date"
    assert _col(p, "Unit Price").semantic_type == "currency"
    assert _col(p, "In Stock?").semantic_type == "boolean"
    assert _col(p, " Order ID").header_issues == ["whitespace"]
    assert _col(p, "").header_issues == ["unnamed"]
    assert _col(p, "").semantic_type == "empty"
    assert "duplicate" in _col(p, "Product", 1).header_issues


def test_placeholder_nulls_and_untrimmed_detected(dirty_df):
    p = build_profile(dirty_df)
    assert _col(p, "Notes").placeholder_nulls > 0
    assert _col(p, "customer name ").untrimmed_values > 0


def test_date_order_hint():
    df = pd.DataFrame({"d": ["25/12/2024", "13/01/2024", "01/02/2024"]})
    assert build_profile(df).columns[0].date_order == "DMY"
    df = pd.DataFrame({"d": ["25/12/2024", "12/25/2024"]})
    assert build_profile(df).columns[0].date_order == "inconsistent"


def test_mixed_column():
    df = pd.DataFrame({"x": ["a", "b", "1", "2", "a@b.io", "$3"]})
    assert build_profile(df).columns[0].semantic_type == "mixed"


def test_masked_mode_never_leaks_values(dirty_df):
    text = build_profile(dirty_df, privacy_mode="masked").to_json()
    for col in ("customer name ", "E-mail", "Unit Price"):
        for value in dirty_df[col].dropna().head(3):
            v = str(value).strip()
            if v and v.lower() not in {"n/a", "null", "-"}:
                assert v not in text, f"{v!r} leaked"


def test_none_mode_has_no_sample(dirty_df):
    p = build_profile(dirty_df, privacy_mode=PrivacyMode.NONE)
    data = json.loads(p.to_json())
    assert p.sample is None
    assert "sample" not in data
    text = p.to_json()
    assert not any(v in text for v in dirty_df["E-mail"].dropna() if "@" in v)


def test_raw_mode_keeps_values_but_truncates():
    df = pd.DataFrame({"t": ["x" * 200], "when": [pd.Timestamp("2024-05-01")]})
    p = build_profile(df, privacy_mode="raw")
    assert p.sample == [["x" * 64 + "…", "2024-05-01T00:00:00"]]


def test_sample_keeps_duplicate_headers_aligned(dirty_df):
    p = build_profile(dirty_df)
    assert all(len(row) == p.column_count for row in p.sample)
    assert len(p.sample) == 3


def test_token_cost_is_constant_in_row_count():
    small = build_profile(generate_dirty_dataframe(rows=50))
    large = build_profile(generate_dirty_dataframe(rows=CLASSIFY_LIMIT * 3))
    # Only digit counts in stats change; size must not scale with rows.
    assert abs(large.estimated_tokens() - small.estimated_tokens()) < 0.1 * small.estimated_tokens()


def test_numeric_and_typed_columns():
    df = pd.DataFrame(
        {
            "n": [1, 2, None],
            "ts": pd.to_datetime(["2024-01-01", None, "2024-02-01"]),
            "b": [True, False, True],
        }
    )
    p = build_profile(df)
    assert [c.semantic_type for c in p.columns] == ["number", "date", "boolean"]
    assert p.columns[0].null_count == 1
    assert p.sample[0] == [9.9, "YYYY-MM-DD", True]
    assert p.sample[1][1] is None


def test_empty_dataframe():
    p = build_profile(pd.DataFrame())
    assert p.row_count == 0
    assert p.columns == []
    assert p.duplicate_rows == 0
