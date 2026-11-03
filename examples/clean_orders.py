# Example transform for the sample data, written by hand.
# It has the same shape as the files `schema-engine run` saves, so you can try the
# sandbox without an LLM or API key:
#
#   schema-engine samples samples/
#   schema-engine apply examples/clean_orders.py samples/dirty_orders.csv -o orders_clean.csv

import re
from datetime import datetime

import pandas as pd

NULLS = {"", "-", "--", "?", "missing", "n/a", "na", "nan", "nil", "none", "null"}
DATE_FORMATS = ("%Y-%m-%d", "%b %d, %Y", "%d.%m.%y")
YES = {"yes", "y", "true", "1"}
NO = {"no", "n", "false", "0"}


def text(series: pd.Series) -> pd.Series:
    """Trimmed strings, with blanks and placeholder nulls turned into real nulls."""
    s = series.astype("string").str.strip()
    return s.mask(s.str.lower().isin(NULLS))


def parse_date(value):
    if pd.isna(value):
        return pd.NaT
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass
    match = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", value)
    if match:
        first, second, year = (int(g) for g in match.groups())
        if first > 12:
            return datetime(year, second, first)
        if second > 12:
            return datetime(year, first, second)
    return pd.NaT  # 05/02/2024 could be May or February, so leave it empty


def currency(value):
    if pd.isna(value):
        return pd.NA
    if "€" in value:
        return "EUR"
    if "$" in value or "USD" in value.upper():
        return "USD"
    return pd.NA


def amount(value):
    if pd.isna(value):
        return float("nan")
    digits = re.sub(r"[^0-9.]", "", value)
    return float(digits) if digits else float("nan")


def flag(value):
    if pd.isna(value):
        return pd.NA
    key = value.lower()
    return True if key in YES else False if key in NO else pd.NA


def transform(df: pd.DataFrame) -> pd.DataFrame:
    # Headers are messy (blank and repeated), so pick columns by position.
    price = text(df.iloc[:, 5])
    out = pd.DataFrame(
        {
            "order_id": text(df.iloc[:, 0]),
            "customer": text(df.iloc[:, 1]).str.title(),
            "email": text(df.iloc[:, 2]).str.lower(),
            "order_date": pd.to_datetime(text(df.iloc[:, 3]).map(parse_date)),
            "product": text(df.iloc[:, 4]),
            "unit_price": price.map(amount).astype("float64"),
            "currency": price.map(currency).astype("string"),
            "qty": pd.to_numeric(text(df.iloc[:, 6]), errors="coerce").astype("Int64"),
            "in_stock": text(df.iloc[:, 7]).map(flag).astype("boolean"),
            "notes": text(df.iloc[:, 8]),
        }
    )
    return out.drop_duplicates().reset_index(drop=True)
