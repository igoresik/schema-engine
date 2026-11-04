# Build plan

Agreed 2026-11-03. Each step lands as its own pull request.

1. **Skeleton and metadata extraction** (done, PR #1). Python 3.11+ package, typer CLI,
   pytest + ruff + GitHub Actions. CSV/XLSX loader, `DatasetProfile`, privacy tiers,
   synthetic dirty sample generator.
2. **Provider layer (BYOK)** (done, PR #2). `LLMProvider` interface; Ollama and one
   OpenAI-compatible client (OpenAI, Groq) over plain `httpx`. Config from `.env` and
   `~/.config/schema-engine/config.json`. `test-connection` sends a one-token request.
   Tests use a fake provider so CI never needs keys.
3. **Code generation, sandbox, self-healing** (done, PR #3). Constrained prompt and code extraction;
   generated `transform(df)` runs in a subprocess with a timeout, import allowlist and no
   network. Tracebacks go back to the model for up to N retries. Token usage logged per run.
4. **Docs and polish** (done, PR #4). Architecture diagram and module map, version 0.2.0.
   Real local models tried on 2026-11-04 (see the README). Still open: a coverage badge,
   which needs the repo to be public or a coverage service.
5. **Export presets** (done, version 0.2.1). `--preset shopify` (Shopify product CSV) and
   `--preset sqlite`, plus `.sql` output: a SQLite script with `CREATE TABLE` and the rows.

Later: a run on a bigger model (Groq key), optional FastAPI wrapper. Shopify market,
metafield and packed-dimension columns if a real import needs them.

## Constraints

- Raw rows never reach the LLM unless the user opts in with `--privacy-mode raw`.
- Prompt size depends on column count only, never row count.
- No hardcoded secrets. Keys come from `.env` or the user config file.
- No LangChain / LlamaIndex. Plain HTTP or official SDK calls only.

## Privacy tiers (`--privacy-mode`)

| Mode | What the LLM receives | Use for |
| --- | --- | --- |
| `masked` (default) | Headers, dtypes, null stats, unique counts, detected patterns, and a 3-row synthetic head that keeps value shapes (`$1,240.50` → `$9,999.99`, emails → `user@example.com`, dates → `DD/MM/YYYY`). | General use, confidential or PII spreadsheets where format inference matters. |
| `none` (strict) | Headers, dtypes, null stats, unique counts and detected patterns. Zero row values. | Highly regulated data where even value shapes are restricted. |
| `raw` (opt-in) | Headers, dtypes, stats and an untouched 3-row head (long strings truncated). | Public datasets, or local Ollama runs where data never leaves the machine. |

Masking uses lightweight regex heuristics in `schema_engine/privacy.py`, with no faker
dependency. Column-level hints (`semantic_type`, `date_order`, `placeholder_nulls`,
`untrimmed_values`) are computed locally over the full column, so the model still learns
for example that a date column is day-first, even in `none` mode.
