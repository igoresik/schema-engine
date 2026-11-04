import pandas as pd

from schema_engine.presets import EXPORT_PRESETS, SHOPIFY_COLUMNS, check_shopify, check_sqlite


def shopify_rows(**overrides):
    df = pd.DataFrame(
        {
            "Title": ["Black Sunglasses", None, "Café Mug"],
            "URL handle": ["black-sunglasses", "black-sunglasses", "café-mug"],
            "Option1 name": ["Color", "Color", None],
            "Option1 value": ["Black", "Brown", None],
            "Price": [9.99, 12.5, None],
            "Inventory quantity": pd.array([3, -1, None], dtype="Int64"),
            "Weight value (grams)": ["120", "120", ""],
            "Published on online store": ["true", "true", "false"],
            "Status": ["active", "active", "draft"],
        }
    )
    for column, values in overrides.items():
        df[column] = values
    return df


def test_valid_shopify_output():
    assert check_shopify(shopify_rows()) == []
    assert EXPORT_PRESETS["shopify"].check is check_shopify
    assert EXPORT_PRESETS["shopify"].suffix == ".csv"


def test_rules_list_every_column():
    rules = EXPORT_PRESETS["shopify"].rules
    assert all(column in rules for column in SHOPIFY_COLUMNS)
    assert "Option3 LinkedTo" in SHOPIFY_COLUMNS


def test_unknown_and_missing_columns():
    df = pd.DataFrame({"Handle": ["a"], "Variant Price": ["1"]})
    assert check_shopify(df) == [
        "'Handle' isn't a Shopify product column. Use the exact names from the list",
        "'Variant Price' isn't a Shopify product column. Use the exact names from the list",
        "missing column 'Title'",
        "missing column 'URL handle'",
    ]


def test_handles_and_titles():
    df = shopify_rows(
        **{"Title": [None, "Brown", "Mug"], "URL handle": ["Black Sunglasses", "", "mug--x"]}
    )
    assert check_shopify(df) == [
        "'URL handle' is empty in row 2",
        "'URL handle' must be lowercase letters, numbers and single dashes (rows 1, 3)",
        "'Title' is empty on the first row of a product (row 1)",
    ]


def test_value_formats_never_quote_cells():
    df = shopify_rows(
        Price=["$9.99", "12.50", None],
        **{
            "Inventory quantity": [3.0, None, 1.0],
            "Weight value (grams)": [120, 0, None],
            "Published on online store": [True, "TRUE", " "],
            "Status": ["Active", "draft", "gone"],
        },
    )
    problems = check_shopify(df)
    assert problems == [
        "'Published on online store' must be true or false (rows 1, 2)",
        "'Status' must be active, draft or archived (rows 1, 3)",
        "'Price' must be a plain number like 9.99 (row 1)",
        "'Inventory quantity' must be a whole number (rows 1, 3)",
        # None turns the column to floats, so 120 becomes 120.0.
        "'Weight value (grams)' must be a whole number of grams (rows 1, 2)",
    ]
    assert not any("$9.99" in p or "gone" in p for p in problems)


def test_row_list_is_capped():
    df = pd.DataFrame(
        {"Title": ["t"] * 8, "URL handle": [f"h{i}" for i in range(8)], "Status": ["x"] * 8}
    )
    assert check_shopify(df) == [
        "'Status' must be active, draft or archived (rows 1, 2, 3, 4, 5 and 3 more)"
    ]


def test_empty_output_passes():
    assert check_shopify(pd.DataFrame(columns=["Title", "URL handle"])) == []


def test_fit_fixes_whole_floats_and_bools_first():
    df = pd.DataFrame(
        {
            "Title": ["a", "b", "c"],
            "URL handle": ["a", "b", "c"],
            "Inventory quantity": [3.0, None, -1.0],
            "Weight value (grams)": [120.5, None, 1.0],
            "Gift card": [True, False, None],
            "Charge tax": [True, False, True],
            "Price": [9.0, 10.0, None],
        }
    )
    out, problems = EXPORT_PRESETS["shopify"].fit(df)
    assert str(out["Inventory quantity"].dtype) == "Int64"
    assert out["Inventory quantity"].iloc[[0, 2]].tolist() == [3, -1]
    assert out["Gift card"].iloc[:2].tolist() == ["true", "false"]
    assert pd.isna(out["Gift card"].iloc[2])
    assert out["Charge tax"].tolist() == ["true", "false", "true"]
    assert out["Price"].iloc[:2].tolist() == [9.0, 10.0]
    # 120.5 isn't whole, so the column stays as it was and the check reports it.
    assert problems == ["'Weight value (grams)' must be a whole number of grams (rows 1, 3)"]
    assert df["Charge tax"].tolist() == [True, False, True]


def test_sqlite_names():
    df = pd.DataFrame(columns=["order_id", "_x1", "Order Date", "1st", "émail"])
    rule = "must be snake_case: lowercase letters, digits and _, not starting with a digit"
    assert check_sqlite(df) == [f"'{name}' {rule}" for name in ("Order Date", "1st", "émail")]
    preset = EXPORT_PRESETS["sqlite"]
    assert preset.suffix == ".sql"
    assert preset.fit(df)[0] is df
    assert "order_date" in preset.rules
