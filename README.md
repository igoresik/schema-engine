# schema-engine

[![CI](https://github.com/igoresik/schema-engine/actions/workflows/ci.yml/badge.svg)](https://github.com/igoresik/schema-engine/actions/workflows/ci.yml)

A cost-aware AI ETL engine. An LLM acts as a code generator: it sees **only metadata**
about a dataset and writes a Pandas transformation, which then runs locally against the
full data. Prompt cost is O(1) in row count, and rows never leave your machine unless you
opt in.

```
 ┌───────────────── data plane (local) ─────────────────┐      ┌── control plane ──┐
 │ CSV/XLSX ─► DataFrame ─► profile (+ privacy mode) ───┼─────►│ LLM writes        │
 │                │                                     │      │ transform(df)     │
 │                └──► sandboxed exec ◄─────────────────┼──────┤                   │
 │                         │  error? traceback ─────────┼─────►│ self-heal         │
 │                         ▼                            │      └───────────────────┘
 │                  clean DataFrame ─► export            │
 └──────────────────────────────────────────────────────┘
```

Licensed MIT. Status: step 1 of 4 (metadata extraction and privacy tiers). See [docs/PLAN.md](docs/PLAN.md).

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

schema-engine samples samples/                       # synthetic dirty CSV + XLSX
schema-engine profile samples/dirty_orders.csv       # what the LLM would see (masked)
schema-engine profile samples/dirty_orders.csv -p none
```

`profile` prints the exact JSON payload that would be sent to the model, plus an
estimated token count on stderr.

## Privacy modes

| Mode | Row values sent |
| --- | --- |
| `masked` (default) | 3 synthetic rows that keep value shapes: `$1,240.50` → `$9,999.99`, `jane@corp.com` → `user@example.com`, `15/03/2024` → `DD/MM/YYYY` |
| `none` | None. Headers, dtypes and column statistics only |
| `raw` | 3 real rows (opt-in, for public data or local models) |

Every mode also includes column hints computed locally over all rows: semantic type,
null and placeholder-null counts (`N/A`, `-`, blanks), untrimmed values, date order
(`DMY`/`MDY`/`YMD`/`inconsistent`) and header problems (whitespace, blank, duplicate).

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
