# schema-engine

[![CI](https://github.com/igoresik/schema-engine/actions/workflows/ci.yml/badge.svg)](https://github.com/igoresik/schema-engine/actions/workflows/ci.yml)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

**Clean messy spreadsheets with an LLM that never sees your rows.**

You say what to fix in plain words. The model gets a short profile of your columns
(names, types, formats, how many blanks) and writes a pandas function. That function runs
on your machine, on the whole file. You keep the code, so next month's export gets
cleaned again for free, without the model.

- **Your rows stay with you.** By default the model sees fake rows that only keep the
  shape of your values (`$1,240.50` becomes `$9,999.99`).
- **Cost doesn't grow with the file.** The prompt depends on the number of columns, not
  rows. A 50-row file and a 500,000-row file cost about the same.
- **It fixes its own mistakes.** If the code crashes, the error goes back to the model
  for another try.
- **You get code, not a black box.** Every run saves a plain Python file you can read,
  edit and commit.
- **Bring your own key.** Runs on Ollama (local, free), OpenAI or Groq.

## Example

`schema-engine samples` makes a deliberately messy orders file. Four of its rows:

| customer name | Order Date | Unit Price | In Stock? |
| --- | --- | --- | --- |
| `farah petrov` | `21.07.24` | `$75.28` | `TRUE` |
| `IVAN OKAFOR  ` | `10/15/2024` | `929.66` | `false` |
| `JIA OKAFOR` | `Sep 09, 2024` | `-` | `No` |
| `Goran Sato` | *(blank)* | `1436.72 USD` | `TRUE` |

```console
$ schema-engine run dirty_orders.csv "Title-case names, lowercase emails, parse dates and prices, make In Stock? true/false, drop duplicates" --provider groq
attempt 1: ok (3748 tokens, 5388 ms)
3748 tokens in total (1649 prompt, 2099 completion)
```

The same rows afterwards:

| customer name | Order Date | Unit Price | In Stock? |
| --- | --- | --- | --- |
| Farah Petrov | 2024-07-21 | 75.28 | True |
| Ivan Okafor | 2024-10-15 | 929.66 | False |
| Jia Okafor | 2024-09-09 | *(empty)* | False |
| Goran Sato | *(empty)* | *(empty)* | True |

Mostly right, but `1436.72 USD` lost its price. That's real output from
`openai/gpt-oss-120b`, and it's why the code is saved for you to check
(see [How well it works](#how-well-it-works)).

## Install

Needs Python 3.11 or newer.

```bash
git clone https://github.com/igoresik/schema-engine.git
cd schema-engine
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e .
```

## Quick start

1. Make the sample files and look at what the model would be sent:

   ```bash
   schema-engine samples samples/
   schema-engine profile samples/dirty_orders.csv
   ```

2. Pick a provider. [Ollama](https://ollama.com) runs on your machine with no key. For
   better results, get a free Groq key at [console.groq.com](https://console.groq.com)
   (API Keys, then Create API Key), copy `.env.example` to `.env` and paste the key after
   `GROQ_API_KEY=`.

3. Check that the provider answers:

   ```bash
   schema-engine test-connection --provider groq
   ```

4. Clean the file:

   ```bash
   schema-engine run samples/dirty_orders.csv "Tidy names and emails, parse dates and prices" --provider groq
   ```

5. Run the saved code on a new file, with no model and no tokens:

   ```bash
   schema-engine apply samples/dirty_orders_transform.py next_month.csv
   ```

Run the commands from the folder that holds your `.env`, since that's where it's read
from.

## Commands

| Command | What it does |
| --- | --- |
| `samples DIR` | Writes a synthetic messy CSV and XLSX to try things on |
| `profile FILE` | Prints the exact JSON the model would get, plus a token estimate |
| `test-connection` | Checks your provider and key with a one-token request |
| `run FILE "TASK"` | Has the model write a transform, runs it and saves the data and the code |
| `apply TRANSFORM FILE` | Runs a saved transform again. No model, no tokens |

Add `--help` to any command for all its options.

### `run`

`run` profiles the file, sends the profile and your task to the model, and runs the code
it writes in the [sandbox](#safety) against the full data. If the code fails, the error
goes back to the model, up to `--attempts` tries (default 3). Each try's token count is
printed as it happens, plus a total at the end.

When a try works, `run` writes two files next to the input:

- `NAME_clean.csv` (or `.tsv` / `.xlsx`, matching the input): the cleaned data. Change it
  with `-o`, which also takes `.sql` (see [SQLite output](#sqlite-output)).
- `NAME_transform.py`: the generated code, with a header saying which model wrote it and
  how many tokens it took. Change it with `--save`.

[examples/clean_orders.py](examples/clean_orders.py) is a hand-written transform for the
sample data, so you can try `apply` before setting up a provider.

## Export presets

`--preset` tells the model the target format and checks what comes out. When the output
doesn't fit, the problems go back to the model like any other error. Problems name
columns and row numbers, never cell values.

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

The Shopify column list comes from Shopify's help pages, checked on 4 November 2026.
Shopify still accepts its older headers, but the preset only writes the current ones.
Market columns (`Price / International`), metafields and packed dimensions aren't
supported yet.

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

## Privacy

`--privacy-mode` (`-p`) decides which row values the model may see:

| Mode | Row values sent |
| --- | --- |
| `masked` (default) | 3 fake rows that keep value shapes: `$1,240.50` → `$9,999.99`, `jane@corp.com` → `user@example.com`, `15/03/2024` → `DD/MM/YYYY` |
| `none` | None. Headers, types and column statistics only |
| `raw` | 3 real rows. Only for public data, or local models where nothing leaves your machine |

Every mode also includes column hints worked out locally over all rows: the kind of
value, blank and placeholder counts (`N/A`, `-`), untrimmed text, date order
(`DMY`/`MDY`/`YMD`/`inconsistent`) and header problems (spaces, blank or repeated names).
`schema-engine profile FILE -p MODE` shows exactly what gets sent.

Error messages sent back to the model have cell values replaced too, so a failed try
doesn't leak rows. This is best effort and matches values as plain text. `raw` mode sends
errors unchanged.

## Providers

Requests go straight from your machine to the provider with your own key. There is no
server in between.

| Provider | Default model | Key |
| --- | --- | --- |
| `ollama` (default) | `llama3.1` | None, runs locally |
| `openai` | `gpt-6-luna` | `OPENAI_API_KEY` |
| `groq` | `openai/gpt-oss-120b` | `GROQ_API_KEY` |

Use `--model` for any other model the provider has, for example
`--provider groq --model openai/gpt-oss-20b`.

Settings are read in this order, first match wins:

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

Keys in `.env` and `config.json` are plain text, so keep those files private and never
paste a key into an issue or chat. `.env` is already in `.gitignore`.

`test-connection` prints the settings it found with the key masked, sends a one-token
request and reports the time it took, tokens used and rate limit left. It exits with 1
when the request fails and 2 when something is missing from the settings.

## Safety

Generated code never runs inside the engine itself.

1. **Checked before it runs.** Imports must be on an allowlist (pandas, numpy, re,
   datetime, math and a few more). Reading or writing files, network access, `eval`,
   `exec`, `query`, `getattr` and dunder attributes are rejected.
2. **Run in a separate Python process** with a time limit (`--timeout`, default 30 s).
   That process gets no API keys. While the code runs, file and network access are
   blocked and its imports are checked again.
3. **Checked after it runs.** The code must return a table with unique, non-empty column
   names. Results come back as JSON.

This catches careless or buggy code. It is not a locked-down container or VM, so don't
use it to run code you would never trust.

## How well it works

| Model | Runs | Passed | Passed on the first try |
| --- | --- | --- | --- |
| `llama3.1` 8B and `qwen2.5-coder:7b` (Ollama, laptop) | about 20 | 1 | 1 |
| `openai/gpt-oss-120b` (Groq), version 0.2.1 | 5 | 4 | 1 |
| `openai/gpt-oss-120b` (Groq), version 0.2.2 | 5 | 5 | 3 |

"Passed" means the code ran and the output passed the checks. It doesn't mean every
value is right: runs also dropped a column, rounded weights to whole kilograms and lost
prices written like `1436.72 USD`. Use a big model for real work, read the saved
transform and check the output before you import it anywhere.

The full notes, and what changed because of them, are in
[docs/real-runs.md](docs/real-runs.md).

## How it works

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

| Module | Job |
| --- | --- |
| `io.py` | Loads CSV/XLSX without "fixing" anything (headers and text kept as they are); writes CSV, TSV, XLSX or a SQLite script |
| `profile.py`, `privacy.py` | Builds the column profile under the chosen privacy mode. This is all the model ever sees about the data |
| `prompt.py` | The rules the model must follow, notes about the file's dates and headers, pulling the code out of the reply, and the repair message after a failure |
| `providers/` | One interface, two clients: Ollama and OpenAI-compatible APIs. Retries rate limits and server errors |
| `config.py` | Reads settings in a fixed order and keeps API keys out of logs |
| `sandbox.py`, `_runner.py` | The check before, the separate process that runs the code, and the checks after |
| `presets.py` | Export presets: format rules for the model, safe fixes and the output check |
| `engine.py` | The loop: ask, run, send the error back, stop on success or after N tries |
| `cli.py` | `samples`, `profile`, `test-connection`, `run`, `apply` |

## Use it from Python

```python
from schema_engine import PrivacyMode, build_profile, load_dataset
from schema_engine.config import load_settings
from schema_engine.engine import compile_transform
from schema_engine.providers import create_provider

df = load_dataset("orders.xlsx")

# What the model would see, and roughly how many tokens it costs
profile = build_profile(df, source="orders.xlsx", privacy_mode=PrivacyMode.MASKED)
print(profile.to_json(indent=2), profile.estimated_tokens())

llm = create_provider(load_settings(provider="groq"))
result = compile_transform(df, "Parse the order dates", llm, source="orders.xlsx")
if result.ok:
    print(result.code, result.usage.total_tokens)
    result.output.to_csv("orders_clean.csv", index=False)
```

## Development

```bash
pip install -e ".[dev]"
ruff check . && ruff format --check .
pytest --cov
```

CI runs the same on Python 3.11, 3.12 and 3.13. Tests use a fake provider, so they never
need a key. The build plan is in [docs/PLAN.md](docs/PLAN.md).

## License

MIT, see [LICENSE](LICENSE).
