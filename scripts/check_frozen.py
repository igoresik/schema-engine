"""Runs transforms through the built exe's sandbox, the way the installed app does.

python scripts/check_frozen.py dist/SchemaEngine/SchemaEngine.exe
"""

import sys
from pathlib import Path

from schema_engine import sandbox
from schema_engine.samples import generate_dirty_dataframe

exe = Path(sys.argv[1]).resolve()
sys.frozen = True
sys.executable = str(exe)
df = generate_dirty_dataframe()
example = Path(__file__).resolve().parent.parent / "examples" / "clean_orders.py"

result = sandbox.run_transform(example.read_text(encoding="utf-8"), df, timeout=120)
assert result.output is not None, result.error
print(f"example transform: {len(result.output)} rows")

# passes the static check, so only the runtime guard can stop it
sneaky = 'def transform(df):\n    df.to_string(buf="x.txt")\n    return df\n'
blocked = sandbox.run_transform(sneaky, df, timeout=120)
assert blocked.error and "PermissionError" in blocked.error, blocked.error
print("file access blocked:", blocked.error.splitlines()[-1])
