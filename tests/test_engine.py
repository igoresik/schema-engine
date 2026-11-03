import pandas as pd
import pytest

from schema_engine.engine import MAX_TOKENS, CompileResult, compile_transform, scrub_error
from schema_engine.privacy import PrivacyMode
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
