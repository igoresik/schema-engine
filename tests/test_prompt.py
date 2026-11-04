import json

import pandas as pd

from schema_engine.privacy import PrivacyMode
from schema_engine.profile import build_profile
from schema_engine.prompt import SYSTEM_PROMPT, build_messages, extract_code, repair_message


def test_messages_carry_profile_and_task(dirty_df):
    profile = build_profile(dirty_df, source="orders.csv", privacy_mode=PrivacyMode.NONE)
    system, user = build_messages(profile, "  Split names into first and last.  ")
    assert (system.role, system.content) == ("system", SYSTEM_PROMPT)
    assert user.role == "user"
    task, _, payload = user.content.partition("\n\nData profile (JSON):\n")
    # The sample repeats a header, so the position note follows the task.
    assert task.startswith("Task: Split names into first and last.\n\nSome column names ")
    assert json.loads(payload)["source"] == "orders.csv"
    assert "sample" not in json.loads(payload)


def test_blank_and_repeated_headers_are_named_by_position():
    df = pd.DataFrame([[1, 2, 3, 4]], columns=["a", "Product", "", "product "])
    profile = build_profile(df, source="x.csv", privacy_mode=PrivacyMode.NONE)
    _, user = build_messages(profile, "Tidy up", "Rules.")
    task = user.content.partition("\n\nData profile (JSON):\n")[0]
    assert task == (
        "Task: Tidy up\n\nRules.\n\nSome column names are blank or repeat, so df[name] can't "
        "pick one column. Select these by position: df.iloc[:, 1] ('Product'), "
        "df.iloc[:, 2] (''), df.iloc[:, 3] ('product ')."
    )
    clean = build_profile(df[["a"]], source="x.csv", privacy_mode=PrivacyMode.NONE)
    assert "by position" not in build_messages(clean, "x")[1].content


def test_system_prompt_lists_the_rules():
    assert "pandas, re, string, typing, unicodedata" in SYSTEM_PROMPT
    assert "n/a" in SYSTEM_PROMPT
    assert "df.iloc[:, i]" in SYSTEM_PROMPT


def test_repair_message():
    message = repair_message("KeyError: 'x'")
    assert message.role == "user"
    assert "KeyError: 'x'" in message.content


def test_extract_code_prefers_the_transform_block():
    reply = (
        "Here you go:\n```\npip install pandas\n```\n"
        "```python\nimport pandas as pd\n\ndef transform(df):\n    return df\n```\nDone."
    )
    assert extract_code(reply) == "import pandas as pd\n\ndef transform(df):\n    return df\n"


def test_extract_code_fallbacks():
    assert extract_code("```py\nx = 1\n```") == "x = 1\n"
    assert extract_code("```PYTHON \ny = 2\n```") == "y = 2\n"
    assert extract_code("def transform(df):\n    return df\n\n") == (
        "def transform(df):\n    return df\n"
    )
