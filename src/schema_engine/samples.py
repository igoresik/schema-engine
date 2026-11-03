"""Synthetic dirty datasets for tests and demos.

Every value is generated from a seeded RNG, so no real personal data is ever
needed to exercise the engine. The mess is deliberate and realistic: untrimmed
headers, duplicate and blank column names, placeholder nulls, mixed date
formats, currency strings, inconsistent casing and duplicate rows.
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

FIRST_NAMES = ["Ana", "Ben", "Chloe", "Dmitri", "Elif", "Farah", "Goran", "Hana", "Ivan", "Jia"]
LAST_NAMES = ["Novak", "Okafor", "Petrov", "Quinn", "Rossi", "Sato", "Torres", "Ueda", "Vega"]
PRODUCTS = ["Desk Lamp", "USB-C Cable", "Notebook A5", "Wireless Mouse", "Coffee Mug", "Tote Bag"]
NULL_PLACEHOLDERS = ["", "N/A", "null", "-", "  "]

HEADERS = [
    " Order ID",
    "customer name ",
    "E-mail",
    "Order Date",
    "Product",
    "Unit Price",
    "Qty",
    "In Stock?",
    "Notes",
    "",
    "Product",
]


def generate_dirty_dataframe(rows: int = 50, seed: int = 7) -> pd.DataFrame:
    """Build a messy order-export table with ``rows`` rows (plus a few duplicates)."""
    rng = random.Random(seed)
    start = date(2024, 1, 1)
    records = []
    for i in range(rows):
        first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
        day = start + timedelta(days=rng.randint(0, 365))
        product = rng.choice(PRODUCTS)
        price = rng.uniform(3, 1500)
        records.append(
            [
                f"ORD-{1000 + i:05d}",
                _messy_case(rng, f"{first} {last}") + rng.choice(["", " ", "  "]),
                _maybe_null(rng, f"{first}.{last}{rng.randint(1, 99)}@example.org".lower()),
                _maybe_null(rng, _messy_date(rng, day)),
                product,
                _maybe_null(rng, _messy_price(rng, price)),
                _maybe_null(rng, str(rng.randint(1, 20))),
                rng.choice(["yes", "No", "Y", "n", "TRUE", "false"]),
                _maybe_null(rng, rng.choice(["gift wrap", "leave at door", "fragile"]), p=0.7),
                "",
                product.upper() if rng.random() < 0.5 else product,
            ]
        )
    for idx in rng.sample(range(rows), k=max(1, rows // 20)):
        records.append(list(records[idx]))
    return pd.DataFrame(records, columns=HEADERS)


def write_samples(out_dir: str | Path, rows: int = 50, seed: int = 7) -> list[Path]:
    """Write ``dirty_orders.csv`` and ``dirty_orders.xlsx`` to ``out_dir``."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    df = generate_dirty_dataframe(rows, seed)
    csv_path = out / "dirty_orders.csv"
    xlsx_path = out / "dirty_orders.xlsx"
    df.to_csv(csv_path, index=False)
    df.to_excel(xlsx_path, index=False)
    return [csv_path, xlsx_path]


def _maybe_null(rng: random.Random, value: str, p: float = 0.12) -> str:
    return rng.choice(NULL_PLACEHOLDERS) if rng.random() < p else value


def _messy_case(rng: random.Random, text: str) -> str:
    return rng.choice([text, text.upper(), text.lower()])


def _messy_date(rng: random.Random, d: date) -> str:
    fmt = rng.choice(["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%b %d, %Y", "%d.%m.%y"])
    return d.strftime(fmt)


def _messy_price(rng: random.Random, price: float) -> str:
    style = rng.randrange(4)
    if style == 0:
        return f"${price:,.2f}"
    if style == 1:
        return f"{price:.2f} USD"
    if style == 2:
        return f"€{price:,.2f}".replace(",", " ")
    return f"{price:.2f}"
