# schema-engine

[![CI](https://github.com/igoresik/schema-engine/actions/workflows/ci.yml/badge.svg)](https://github.com/igoresik/schema-engine/actions/workflows/ci.yml)

A cost-aware AI ETL engine. An LLM acts as a code generator: it sees **only metadata**
about a dataset and writes a Pandas transformation, which then runs locally against the
full data. Prompt cost is O(1) in row count, and rows never leave your machine unless you
opt in.

```
 ┌─────────────────── your machine ───────────────────┐       ┌─ LLM provider ──┐
 │ CSV/XLSX ─► DataFrame ─► profile (privacy mode) ───┼──────►│ writes          │
 │    │                                               │       │ transform(df)   │
 │    │            static check ◄─────────────────────┼───────┤                 │
 │    │                 │                             │       │                 │
 │    └─► sandbox process (full data) ─► error? ──────┼──────►│ fixes it        │
 │                      │              (cell values   │       │ (up to N tries) │
 │                      │               hidden)       │       └─────────────────┘
 │                      ▼                             │
 │      clean data + NAME_transform.py                │
 │                      │                             │
 │                      ▼                             │
 │      apply on new files: no LLM, 0 tokens          │
 └────────────────────────────────────────────────────┘
```

Licensed MIT. Status: version 0.2.1, works end to end. `gpt-oss-120b` on Groq passed 4 of
5 real runs, small local models rarely do (see [Tried on real models](#tried-on-real-models)). See
[docs/PLAN.md](docs/PLAN.md).

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

schema-engine samples samples/                       # synthetic dirty CSV + XLSX
schema-engine profile samples/dirty_orders.csv       # what the LLM would see (masked)
schema-engine profile samples/dirty_orders.csv -p none
schema-engine apply examples/clean_orders.py samples/dirty_orders.csv -o clean.csv
schema-engine test-connection                        # check your LLM provider
schema-engine run samples/dirty_orders.csv "Tidy names and emails, parse dates and prices"
```

`profile` prints the exact JSON payload that would be sent to the model, plus an
estimated token count on stderr.

## Generate and run a transform

```bash
schema-engine run samples/dirty_orders.csv "Tidy names and emails, parse dates and prices, drop duplicates"
```

`run` profiles the file, sends the profile and your instruction to the model, and runs
the code it writes in the sandbox against the full data. If the code fails, the error
goes back to the model for a fix, up to `--attempts` tries (default 3). Each try's
token count is printed as it happens, plus a total at the end.

When a try works, `run` writes two files next to the input:

- `NAME_clean.csv` (or `.tsv` / `.xlsx`, matching the input): the cleaned data. Change
  it with `-o`, which also takes `.sql` (see [SQLite output](#sqlite-output)).
- `NAME_transform.py`: the generated code, with a header saying which model wrote it and
  how many tokens it took. Change it with `--save`.

The saved transform is plain Python. Read it, edit it, commit it, and run it again on
next month's export without the model:

```bash
schema-engine apply samples/dirty_orders_transform.py next_month.csv -o next_month_clean.csv
```

`apply` costs no tokens. [examples/clean_orders.py](examples/clean_orders.py) is a
hand-written transform for the sample data, so you can try `apply` before setting up a
provider.

## Export presets

`--preset` tells the model the target format and checks what comes out. When the output
doesn't fit, the problems go back to the model like any other error, so it gets another
try. Problems name columns and row numbers, never cell values.

```bash
schema-engine run products.xlsx "Make this a Shopify import, Active means published" --preset shopify
schema-engine apply products_transform.py next_month.xlsx --preset shopify
```

| Preset | Writes | Checks |
| --- | --- | --- |
| `shopify` | `NAME_clean.csv` for Shopify's product import | Only Shopify's current column names (`URL handle`, not the old `Handle`); Title and URL handle present; handles in lowercase letters, numbers and dashes; true/false, status, price and whole-number columns in Shopify's format |
| `sqlite` | `NAME_clean.sql`, a SQLite script | Column names in snake_case (`order_date`). The model is also told to avoid SQL keywords and give each column one real type |

Before the check, the Shopify preset fixes what no model should have to think about:
whole numbers stored as floats (`14.0`, which pandas makes when one cell is empty) go back
to integers, and Python bools become `true`/`false`.

### SQLite output

Any `-o something.sql`, with or without a preset, writes one script: a `CREATE TABLE`
named after the file, then the rows as `INSERT`s in a single transaction.

```bash
schema-engine run orders.csv "Tidy it up" --preset sqlite -o orders.sql
sqlite3 shop.db < orders.sql
```

Whole numbers and bools become `INTEGER` columns (bools as 0/1), decimals `REAL`, and the
rest `TEXT`. SQLite has no date type, so dates are stored as ISO 8601 text
(`2024-03-15`, or `2024-03-15 10:30:00` when a column has times), which SQLite's date
functions read. SQLite treats `Name` and `name` as the same column, so such output fails
with a clear error.

The Shopify column list comes from Shopify's help pages, checked on 4 November 2026.
Shopify still accepts its older headers, but the preset only writes the current ones.
Market columns (`Price / International`), metafields and packed dimensions aren't
supported yet.

## Sandbox

Generated code is never run in the engine's own process.

1. **Static check before anything runs.** Imports must be on an allowlist (pandas,
   numpy, re, datetime, math and a few more). File and network I/O (`open`,
   `read_*`, `to_csv` and friends), `eval`, `exec`, `query`, `getattr` and dunder
   attributes are rejected.
2. **A separate Python process** with a time limit (`--timeout`, default 30 s). Its
   environment holds no API keys. While the transform runs, file and network access are
   blocked and imports from the generated code are checked again.
3. **Output checks.** The transform must return a DataFrame with unique, non-empty
   column names. Results come back as JSON, never as a pickle.

This stops careless or buggy code. It is not a hard security boundary like a container
or VM, so don't point it at code you would never trust.

Error messages sent back to the model have cell values replaced (masked shapes, or
`<value>` in `none` mode), so a failed try doesn't leak rows. Column names and strings
the model wrote itself are kept. This is best effort and matches values as plain text.
`raw` mode sends errors unchanged.

## Privacy modes

| Mode | Row values sent |
| --- | --- |
| `masked` (default) | 3 synthetic rows that keep value shapes: `$1,240.50` → `$9,999.99`, `jane@corp.com` → `user@example.com`, `15/03/2024` → `DD/MM/YYYY` |
| `none` | None. Headers, dtypes and column statistics only |
| `raw` | 3 real rows (opt-in, for public data or local models) |

Every mode also includes column hints computed locally over all rows: semantic type,
null and placeholder-null counts (`N/A`, `-`, blanks), untrimmed values, date order
(`DMY`/`MDY`/`YMD`/`inconsistent`) and header problems (whitespace, blank, duplicate).

## Providers (bring your own key)

Requests go straight from your machine to the provider with your own key. There is no
proxy in between.

| Provider | Default model | Key |
| --- | --- | --- |
| `ollama` (default) | `llama3.1` | None, runs locally |
| `openai` | `gpt-6-luna` | `OPENAI_API_KEY` |
| `groq` | `openai/gpt-oss-120b` | `GROQ_API_KEY` |

Settings are resolved in this order, first match wins:

1. Command flags: `--provider`, `--model`, `--base-url`
2. Environment variables: `SCHEMA_ENGINE_PROVIDER`, `SCHEMA_ENGINE_MODEL`,
   `SCHEMA_ENGINE_BASE_URL`, `SCHEMA_ENGINE_TIMEOUT`, the provider's key variable and
   `OLLAMA_BASE_URL`
3. A `.env` file in the current folder (copy [.env.example](.env.example))
4. `~/.config/schema-engine/config.json` (or `$XDG_CONFIG_HOME/schema-engine/config.json`)
5. The defaults in the table above

```json
{
  "provider": "groq",
  "providers": {
    "groq": { "api_key": "gsk_...", "model": "openai/gpt-oss-120b" },
    "ollama": { "base_url": "http://localhost:11434" }
  },
  "timeout": 60
}
```

Keys in `.env` or `config.json` are stored as plain text, so keep those files private.
`.env` is already in `.gitignore`.

```bash
schema-engine test-connection
schema-engine test-connection --provider groq --model openai/gpt-oss-20b
```

`test-connection` prints the resolved settings with the key masked, sends a one-token
request and reports latency, tokens used and remaining rate limit. It exits with 1 when
the request fails and 2 when the configuration is incomplete.

## How it works

| Module | Job |
| --- | --- |
| `io.py` | Loads CSV/XLSX without "fixing" anything (headers and text kept as they are); writes CSV, TSV, XLSX or a SQLite script |
| `profile.py`, `privacy.py` | Builds the column profile under the chosen privacy mode. This is all the model ever sees about the data |
| `prompt.py` | The rules the model must follow, pulling the code out of its reply, and the repair message after a failure |
| `providers/` | One interface, two clients: Ollama and OpenAI-compatible APIs. Retries rate limits and server errors |
| `config.py` | Reads settings in a fixed order and keeps API keys out of logs |
| `sandbox.py`, `_runner.py` | Static check, the separate process that runs the code, and the output checks |
| `presets.py` | Export presets: format rules for the model, safe fixes and the output check |
| `engine.py` | The loop: ask, run, send the error back, stop on success or after N tries |
| `cli.py` | `profile`, `samples`, `test-connection`, `run`, `apply` |

Cost stays flat as files grow: the prompt depends on the number of columns, never on the
number of rows, and a saved transform can be reused for free with `apply`.

## Tried on real models

On 4 November 2026 the engine ran on Ollama 0.40.14 (laptop GPU, 8 GB) with `llama3.1`
(8B, the default) and `qwen2.5-coder:7b`, against the dirty sample and a 5-row product
list, with and without both presets.

The engine side worked: requests, token counts, errors and preset problems going back to
the model, and the retries. The models were the weak part. About 20 runs, most with 3
tries, gave one transform that passed (llama3.1, Shopify preset, first try), and that one
still left out the price column. Typical mistakes were pandas 1.x calls that pandas 3
removed (`applymap`, `DataFrame.append`), a list passed as one date format, and columns
picked by names that don't exist.

Three fixes came out of it:

- A transform that returns dates outside 1677-2262 no longer crashes `run`. The error
  goes back to the model.
- When headers are blank or repeated, the prompt names their exact positions
  (`df.iloc[:, 4]`). That stopped the `'DataFrame' object has no attribute 'str'` error
  that kept coming back in the first runs.
- The Shopify preset turns whole-number floats back into integers itself, after a run
  where the only problems left were whole numbers written as `14.0`.

On 6 November 2026 it ran on Groq with `openai/gpt-oss-120b`: the dirty sample as CSV and
XLSX, the CSV twice more with `--preset sqlite`, and a 5-row product list with
`--preset shopify`. 4 of 5 runs passed, at 3,200 to 8,400 tokens each. The Shopify run
passed on the first try and kept the price column. Every run on the orders sample failed
its first try the same way, with `to_datetime(..., infer_datetime_format=True)`, an
argument pandas 3 removed. Three runs fixed it on the second try. One SQLite run failed
all 3 tries on the date column (13,452 tokens).

A transform that runs is not always right. Reading the output showed:

- The Shopify run rounded weights to whole kilograms before turning them into grams
  (0.35 kg became 0 g).
- One SQLite run dropped the email column.
- Names and titles kept their original case, although the task asked to tidy them.
- In the first CSV run, `Y` and `n` in the stock column became empty.

For real work, use a bigger model, for example `groq` with `openai/gpt-oss-120b`.
Small local models are fine for trying the tool on data that must stay on your machine,
but expect to edit the saved transform. With any model, read the saved transform and check
the output before you import it anywhere.

## Development

```bash
ruff check . && ruff format --check .
pytest --cov
```

## Library use

```python
from schema_engine import build_profile, load_dataset, PrivacyMode

df = load_dataset("orders.xlsx")
profile = build_profile(df, source="orders.xlsx", privacy_mode=PrivacyMode.MASKED)
print(profile.to_json(indent=2), profile.estimated_tokens())
```

```python
from schema_engine.config import load_settings
from schema_engine.providers import Message, create_provider

llm = create_provider(load_settings(provider="ollama"))
print(llm.complete([Message("user", "Say hi")]).text)

from schema_engine import load_dataset
from schema_engine.engine import compile_transform

df = load_dataset("orders.xlsx")
result = compile_transform(df, "Parse the order dates", llm, source="orders.xlsx")
if result.ok:
    print(result.code, result.usage.total_tokens)
    result.output.to_csv("orders_clean.csv", index=False)
```
