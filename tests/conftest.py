import pandas as pd
import pytest

from schema_engine.samples import generate_dirty_dataframe


@pytest.fixture
def dirty_df() -> pd.DataFrame:
    return generate_dirty_dataframe(rows=40, seed=3)
