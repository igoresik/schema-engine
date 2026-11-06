import pandas as pd
import pytest

from schema_engine.engine import (
    MAX_TOKENS,
    CompileResult,
    Lost,
    compile_transform,
    lost_values,
    preset_error,
    scrub_error,
)
from schema_engine.presets import EXPORT_PRESETS
from schema_engine.privacy import PrivacyMode, mask_string
from schema_engine.providers import AuthenticationError, FakeProvider, Usage

GOOD = """```python
import pandas as pd


def transform(df):
    out = df.iloc[:, [0, 2]].copy()
    out.columns = ["order_id", "email"]
    out["order_id"] = out["order_id"].str.strip()
    return out
```"""

MISSING_COLUMN = """```python
def transform(df):
    return df[["order_id"]]
```"""

LEAKS_A_VALUE = """```python
def transform(df):
    float(df.iloc[0, 1])
    return df
```"""


class RecordingFake(FakeProvider):
    """FakeProvider that also keeps the request options."""

    def complete(self, messages, *, max_tokens=1024, temperature=None):
        self.options = (max_tokens, temperature)
        return super().complete(messages, max_tokens=max_tokens, temperature=temperature)


def test_first_try_success(dirty_df):
    fake = RecordingFake([GOOD])
    seen = []
    result = compile_transform(
        dirty_df, "keep id and email", fake, temperature=0.0, on_attempt=seen.append
    )
    assert result.ok
    assert list(result.output.columns) == ["order_id", "email"]
    assert result.output["order_id"].iloc[0] == "ORD-01000"
    assert [a.error for a in result.attempts] == [None]
    assert seen == result.attempts
    assert result.code.startswith("import pandas as pd")
    assert result.usage == result.attempts[0].usage
    assert fake.options == (MAX_TOKENS, 0.0)
    assert [m.role for m in fake.calls[0]] == ["system", "user"]
    assert "keep id and email" in fake.calls[0][1].content


def test_self_heals_with_the_error(dirty_df):
    fake = FakeProvider([MISSING_COLUMN, GOOD])
    result = compile_transform(dirty_df, "keep id and email", fake)
    assert result.ok
    assert len(result.attempts) == 2
    assert "KeyError" in result.attempts[0].error
    retry = fake.calls[1]
    assert [m.role for m in retry] == ["system", "user", "assistant", "user"]
    assert retry[2].content == MISSING_COLUMN
    assert "KeyError" in retry[3].content
    assert result.usage == result.attempts[0].usage + result.attempts[1].usage


def test_gives_up_after_max_attempts(dirty_df):
    fake = FakeProvider(["```python\nimport os\ndef transform(df):\n    return df\n```"])
    result = compile_transform(dirty_df, "anything", fake, max_attempts=3)
    assert not result.ok
    assert result.output is None
    assert len(result.attempts) == 3
    assert result.attempts[-1].error.startswith("Code rejected before running:")
    # Each retry only carries the latest attempt, so prompts don't grow.
    assert [len(call) for call in fake.calls] == [2, 4, 4]
    assert result.code == "import os\ndef transform(df):\n    return df\n"


def test_error_sent_back_hides_cell_values(dirty_df):
    fake = FakeProvider([LEAKS_A_VALUE, GOOD])
    compile_transform(dirty_df, "x", fake, max_attempts=2)
    repair = fake.calls[1][3].content
    real = dirty_df.iloc[0, 1]
    assert "could not convert string to float: 'XXXXXX XXXX  '" in repair
    assert real.strip() not in repair


def test_provider_errors_are_raised(dirty_df):
    with pytest.raises(AuthenticationError):
        compile_transform(dirty_df, "x", FakeProvider([AuthenticationError("bad key")]))


def test_no_attempts_result():
    empty = CompileResult([])
    assert (empty.ok, empty.code, empty.usage) == (False, None, Usage())


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (PrivacyMode.MASKED, "can't parse 'Xxxx Xxx', '$9,999.99' or 'Al' in 'city'"),
        (PrivacyMode.NONE, "can't parse '<value>', '<value>' or 'Al' in 'city'"),
        (PrivacyMode.RAW, "can't parse 'Cork Pub', '$1,470.32' or 'Al' in 'city'"),
    ],
)
def test_scrub_error(mode, expected):
    df = pd.DataFrame({"city": ["  Cork Pub ", None, "Al"], "price": ["$1,470.32", "7", None]})
    code = "def transform(df):\n    return df['city']\n"
    error = "can't parse 'Cork Pub', '$1,470.32' or 'Al' in 'city'"
    assert scrub_error(error, df, code, mode) == expected


def test_scrub_keeps_what_the_model_wrote():
    df = pd.DataFrame({"d": ["%d/%m/%Y", "21/07/2024"]})
    code = (
        "import pandas as pd\ndef transform(df):\n    pd.to_datetime(df['d'], format='%d/%m/%Y')\n"
    )
    error = "time data '21/07/2024' doesn't match format '%d/%m/%Y'"
    assert scrub_error(error, df, code, PrivacyMode.MASKED) == (
        "time data 'DD/MM/YYYY' doesn't match format '%d/%m/%Y'"
    )
    assert scrub_error(error, df, "not python (", PrivacyMode.NONE) == (
        "time data '<value>' doesn't match format '<value>'"
    )


NAMES_A_COLUMN_FROM_A_CELL = """```python
import pandas as pd


def transform(df):
    out = pd.DataFrame({"Title": df["name"], "URL handle": ["black-sunglasses", "b"]})
    out[df["email"].iloc[0]] = 1
    return out
```"""

BAD_STATUS = NAMES_A_COLUMN_FROM_A_CELL.replace(
    'out[df["email"].iloc[0]] = 1', 'out["Status"] = "live"'
)
GOOD_STATUS = BAD_STATUS.replace('"live"', '"draft"')


def test_preset_check_failures_are_retried():
    df = pd.DataFrame({"name": ["Black Sunglasses", "active"], "email": ["jane@corp.com", None]})
    fake = FakeProvider([NAMES_A_COLUMN_FROM_A_CELL, BAD_STATUS, GOOD_STATUS])
    result = compile_transform(df, "x", fake, preset=EXPORT_PRESETS["shopify"])
    assert result.ok
    assert list(result.output["Status"]) == ["draft", "draft"]
    assert "\n\nThe output is a Shopify product import CSV." in fake.calls[0][1].content

    first, second, _ = result.attempts
    assert first.error == (
        f"- '{mask_string('jane@corp.com')}' isn't a Shopify product column. "
        "Use the exact names from the list\nOutput doesn't fit the shopify format (1 problem)"
    )
    assert "jane@corp.com" not in fake.calls[1][3].content
    # "active" is also a cell value; fixed text must not be scrubbed.
    assert second.error == (
        "- 'Status' must be active, draft or archived (rows 1, 2)\n"
        "Output doesn't fit the shopify format (1 problem)"
    )


def test_preset_error_lists_every_problem():
    assert preset_error("x", ["'a' bad", "b"], ["a"], lambda text: text) == (
        "- 'a' bad\n- b\nOutput doesn't fit the x format (2 problems)"
    )


PRICES = pd.DataFrame({"id": ["a", "b", "c", "d"], "Unit Price": ["$1.50", "2 USD", "-", "3"]})


def _prices(pattern):
    return f"""```python
import pandas as pd


def transform(df):
    out = df.copy()
    out["Unit Price"] = pd.to_numeric(
        out["Unit Price"].str.replace(r"{pattern}", "", regex=True), errors="coerce"
    )
    return out
```"""


LOSES_USD = _prices(r"\$")
KEEPS_ALL = _prices(r"[^0-9.]")


def test_values_that_go_blank_go_back_to_the_model():
    fake = FakeProvider([LOSES_USD, KEEPS_ALL])
    result = compile_transform(PRICES, "parse prices", fake)
    assert result.ok and not result.lost and result.picked == 1
    first = result.attempts[0].error
    assert first.startswith("- 'Unit Price': 1 of 3 values became blank\n")
    assert first.endswith("1 value became blank in 1 column")
    assert "value_shapes" in fake.calls[1][-1].content
    assert result.output["Unit Price"].tolist()[:2] == [1.5, 2.0]


def test_the_try_that_lost_fewest_values_is_kept():
    crash = "```python\ndef transform(df):\n    return df[['nope']]\n```"
    fake = FakeProvider([LOSES_USD, crash])
    result = compile_transform(PRICES, "parse prices", fake, max_attempts=2)
    assert result.ok
    assert result.lost == [Lost("Unit Price", 1, 3)]
    assert result.picked == 0
    assert result.code == result.attempts[0].code


def test_lost_values_only_checks_parsed_columns_it_can_match():
    before = pd.DataFrame(
        {
            "Unit Price": ["1", "2", "x", "4"],
            "Note": ["a", "b", "c", "d"],
            "When": ["2024-01-01"] * 4,
            "In Stock?": ["Y", "n", "yes", "N/A"],
        }
    )
    after = pd.DataFrame(
        {
            "unit_price": [1.0, 2.0, None, 4.0],  # "x" lost
            "note": [None] * 4,  # text, so clearing it on purpose is fine
            "date": [pd.NaT] * 4,  # renamed, so not matched
            "in_stock": [True, None, True, None],  # "N/A" was a placeholder
        }
    )
    assert lost_values(before, after) == [Lost("Unit Price", 1, 4), Lost("In Stock?", 1, 3)]
    assert lost_values(before.iloc[:, :1], after.iloc[[0, 1], :1]) == []  # dropped rows
    # Dropping only the filled rows (duplicates, a filter) isn't a loss either.
    kept_blanks = pd.DataFrame({"n": [None, None]}, dtype=float)
    assert lost_values(pd.DataFrame({"n": ["1", "2", "3", "", ""]}), kept_blanks) == []
    twice = pd.DataFrame([["1", "x"]], columns=["n", "N"])
    assert lost_values(twice, pd.DataFrame({"n": [None]})) == []
    numbers = pd.DataFrame({"qty": [1.0, None, 3.0]})  # Excel cells come in as numbers
    assert lost_values(numbers, pd.DataFrame({"qty": [1.0, None, None]})) == [Lost("qty", 1, 2)]
