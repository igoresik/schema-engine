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

Licensed MIT. Status: step 2 of 4 (provider layer, bring your own key). See [docs/PLAN.md](docs/PLAN.md).

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

schema-engine samples samples/                       # synthetic dirty CSV + XLSX
schema-engine profile samples/dirty_orders.csv       # what the LLM would see (masked)
schema-engine profile samples/dirty_orders.csv -p none
schema-engine test-connection                        # check your LLM provider
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

## Providers (bring your own key)

Requests go straight from your machine to the provider with your own key. There is no
proxy in between.

| Provider | Default model | Key |
| --- | --- | --- |
| `ollama` (default) | `llama3.1` | None, runs locally |
| `openai` | `gpt-6-luna` | `OPENAI_API_KEY` |
| `groq` | `llama-3.3-70b-versatile` | `GROQ_API_KEY` |

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
    "groq": { "api_key": "gsk_...", "model": "llama-3.3-70b-versatile" },
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
```
