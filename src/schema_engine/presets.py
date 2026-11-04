"""Export presets: a target format the model must write, and a check of what it wrote."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ExportPreset:
    name: str
    suffix: str
    """File type the preset writes."""
    rules: str
    """Added to the task so the model knows the target format."""
    check: Callable[[pd.DataFrame], list[str]]
    """Problems with an output. They go back to the model, so they never quote cell values."""


# Current header names from Shopify's product CSV docs (help.shopify.com, checked 2026-11-04).
SHOPIFY_COLUMNS = (
    "Title",
    "URL handle",
    "Description",
    "Vendor",
    "Product category",
    "Type",
    "Tags",
    "Published on online store",
    "Status",
    "SKU",
    "Barcodes",
    *(f"Option{n} {part}" for n in (1, 2, 3) for part in ("name", "value", "LinkedTo")),
    "Price",
    "Compare-at price",
    "Cost per item",
    "Charge tax",
    "Inventory tracker",
    "Inventory quantity",
    "Continue selling when out of stock",
    "Weight value (grams)",
    "Weight unit for display",
    "Requires shipping",
    "Fulfillment service",
    "Product image URL",
    "Image position",
    "Image alt text",
    "Variant image URL",
    "Gift card",
    "SEO title",
    "SEO description",
)

_TRUE_FALSE = (r"true|false", "the text true or false")
_PRICE = (r"\d+(?:\.\d+)?", "a plain number like 9.99")
SHOPIFY_FORMATS = {
    "Published on online store": _TRUE_FALSE,
    "Charge tax": _TRUE_FALSE,
    "Requires shipping": _TRUE_FALSE,
    "Gift card": _TRUE_FALSE,
    "Status": (r"active|draft|archived", "active, draft or archived"),
    "Price": _PRICE,
    "Compare-at price": _PRICE,
    "Cost per item": _PRICE,
    "Inventory quantity": (r"-?\d+", "a whole number"),
    "Weight value (grams)": (r"\d+", "a whole number of grams"),
    "Image position": (r"[1-9]\d*", "a whole number from 1"),
    "Continue selling when out of stock": (r"deny|continue", "deny or continue"),
    "Inventory tracker": (
        r"shopify|shipwire|amazon_marketplace_web",
        "shopify, shipwire or amazon_marketplace_web",
    ),
    "Weight unit for display": (r"g|kg|lb|oz", "g, kg, lb or oz"),
}

_HANDLE_RE = re.compile(r"[^\W_]+(?:-[^\W_]+)*")

SHOPIFY_RULES = f"""\
The output is a Shopify product import CSV.
- Use only these exact column names and leave out the ones you don't need: \
{", ".join(SHOPIFY_COLUMNS)}.
- Title and URL handle are required. Every row needs a URL handle: lowercase letters, \
numbers and single dashes, made from the title (Black Sunglasses -> black-sunglasses).
- Extra variant or image rows of a product repeat its URL handle and leave Title, \
Description, Vendor and Tags blank.
- Published on online store, Charge tax, Requires shipping, Gift card: the text true or false.
- Status: active, draft or archived. Continue selling when out of stock: deny or continue.
- Price, Compare-at price, Cost per item: plain numbers like 9.99, no currency symbol.
- Inventory quantity, Weight value (grams), Image position: whole numbers. Use a nullable \
Int64 column or text, because a float column writes 5125.0.
- Leave unknown cells empty (None), never a placeholder like N/A.
"""


def check_shopify(df: pd.DataFrame) -> list[str]:
    columns = [str(c) for c in df.columns]
    problems = [
        f"'{c}' isn't a Shopify product column. Use the exact names from the list"
        for c in columns
        if c not in SHOPIFY_COLUMNS
    ]
    problems += [f"missing column '{c}'" for c in ("Title", "URL handle") if c not in columns]
    if problems:
        return problems

    # Text as the CSV will hold it; `is True` keeps list cells from breaking isna.
    text = df.astype(object).map(lambda v: "" if pd.isna(v) is True else str(v))
    blank = text.map(lambda v: not v.strip())
    handles = text["URL handle"]
    if blank["URL handle"].any():
        problems.append(f"'URL handle' is empty in {_rows(blank['URL handle'])}")
    bad_handle = ~blank["URL handle"] & ~handles.map(
        lambda h: bool(_HANDLE_RE.fullmatch(h)) and h == h.lower()
    )
    if bad_handle.any():
        problems.append(
            f"'URL handle' must be lowercase letters, numbers and single dashes "
            f"({_rows(bad_handle)})"
        )
    untitled = ~handles.duplicated() & ~blank["URL handle"] & blank["Title"]
    if untitled.any():
        problems.append(f"'Title' is empty on the first row of a product ({_rows(untitled)})")
    for column, (pattern, expected) in SHOPIFY_FORMATS.items():
        if column in columns:
            fits = text[column].map(lambda v, p=pattern: bool(re.fullmatch(p, v)))
            bad = ~blank[column] & ~fits
            if bad.any():
                problems.append(f"'{column}' must be {expected} ({_rows(bad)})")
    return problems


def _rows(mask: pd.Series) -> str:
    rows = (np.flatnonzero(mask.to_numpy()) + 1).tolist()
    shown = ", ".join(map(str, rows[:5]))
    more = f" and {len(rows) - 5} more" if len(rows) > 5 else ""
    return f"row{'s' if len(rows) > 1 else ''} {shown}{more}"


EXPORT_PRESETS = {
    "shopify": ExportPreset("shopify", ".csv", SHOPIFY_RULES, check_shopify),
}
