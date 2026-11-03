from datetime import date, datetime

import pytest

from schema_engine.privacy import EMAIL_TOKEN, classify_value, date_order, mask_value


@pytest.mark.parametrize(
    ("raw", "masked"),
    [
        ("$1,240.50", "$9,999.99"),
        ("1240.50 USD", "9999.99 USD"),
        ("€1 240,50", "€9 999,99"),
        ("-42", "-99"),
        ("12.5%", "99.9%"),
        ("jane.doe@corp.com", EMAIL_TOKEN),
        ("https://corp.com/u/jane", "https://example.com/path"),
        ("2024-03-15", "YYYY-MM-DD"),
        ("15/03/2024", "DD/MM/YYYY"),
        ("03/15/2024", "MM/DD/YYYY"),
        ("03/04/2024", "NN/NN/YYYY"),
        ("15.03.24", "DD.MM.YY"),
        ("2024-03-15T10:30:00Z", "YYYY-MM-DDTHH:MM:SS"),
        ("2024-03-15 10:30", "YYYY-MM-DD HH:MM"),
        ("March 5, 2024", "Month 9, 9999"),
        ("Yes", "Yes"),
        ("no", "yes"),
        ("FALSE", "TRUE"),
        ("N/A", "N/A"),
        ("null", "null"),
        ("Jane Doe", "Xxxx Xxx"),
        ("ORD-01000", "XXX-99999"),
        ("  padded ", "  xxxxxx "),
        ("", ""),
    ],
)
def test_mask_string_shapes(raw, masked):
    assert mask_value(raw) == masked


def test_mask_long_text_is_truncated():
    out = mask_value("a" * 100)
    assert out == "x" * 24 + "…"


@pytest.mark.parametrize(
    ("raw", "masked"),
    [
        (None, None),
        (float("nan"), None),
        (True, True),
        (False, True),
        (1234, "9999"),
        (-7, "-9"),
        (12.5, 99.9),
        (float("inf"), float("inf")),
        (date(2024, 1, 2), "YYYY-MM-DD"),
        (datetime(2024, 1, 2), "YYYY-MM-DD"),
        (datetime(2024, 1, 2, 9, 30), "YYYY-MM-DD HH:MM:SS"),
    ],
)
def test_mask_non_string_values(raw, masked):
    assert mask_value(raw) == masked


def test_mask_unknown_object_uses_string_rule():
    from decimal import Decimal

    assert mask_value(Decimal("10.25")) == "99.99"


@pytest.mark.parametrize(
    ("value", "label"),
    [
        (None, "null"),
        (float("nan"), "null"),
        ("   ", "null_token"),
        ("N/A", "null_token"),
        (True, "boolean"),
        ("y", "boolean"),
        (3, "number"),
        ("3,000", "number"),
        ("$3,000", "currency"),
        ("3000 EUR", "currency"),
        ("ORD-123", "text"),
        (date(2024, 1, 1), "date"),
        ("01/02/2024", "date"),
        ("Jan 5, 2024", "date"),
        ("a@b.io", "email"),
        ("www.site.org", "url"),
        ("hello world", "text"),
    ],
)
def test_classify_value(value, label):
    assert classify_value(value) == label


@pytest.mark.parametrize(
    ("text", "order"),
    [
        ("2024-01-02", "YMD"),
        ("25/12/2024", "DMY"),
        ("12/25/2024", "MDY"),
        ("01/02/2024", None),
        ("hello", None),
    ],
)
def test_date_order(text, order):
    assert date_order(text) == order
