# Runs on real models

What happened when the engine ran against real models, and what changed because of it.
All runs used the synthetic files from `schema-engine samples` and a 5-row product list.
None of this is real customer data.

## 4 November 2026: small local models

Ollama 0.40.14 on a laptop GPU (8 GB), with `llama3.1` (8B, the default) and
`qwen2.5-coder:7b`, against the dirty sample and the product list, with and without both
presets.

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

## 6 November 2026: Groq, `openai/gpt-oss-120b`

Groq had removed `llama-3.3-70b-versatile`, the old default, so the first request failed
with `HTTP 404: The model ... does not exist`. The default is now `openai/gpt-oss-120b`.

### Version 0.2.1 prompt

The dirty sample as CSV and XLSX, the CSV twice more with `--preset sqlite`, and the
product list with `--preset shopify`. 4 of 5 runs passed, at 3,200 to 8,400 tokens each.
The Shopify run passed on the first try and kept the price column. Every run on the
orders sample failed its first try the same way, with
`to_datetime(..., infer_datetime_format=True)`, an argument pandas 3 removed. Three runs
fixed it on the second try. One SQLite run failed all 3 tries on the date column
(13,452 tokens).

A transform that runs is not always right. Reading the output showed:

- The Shopify run rounded weights to whole kilograms before turning them into grams
  (0.35 kg became 0 g).
- One SQLite run dropped the email column.
- Names and titles kept their original case, although the task asked to tidy them.
- In the first CSV run, `Y` and `n` in the stock column became empty.

### Pandas 3 notes (version 0.2.2)

Three tries at a fix, each on the same 4 orders runs:

1. **A rule in the system prompt** naming the removed APIs. No change: all 4 first tries
   still used `infer_datetime_format`, and 2 runs failed.
2. **A note after the task naming the date columns by position.** The old error went
   away, but the model then wrote `df.iloc[:, 3] = pd.to_datetime(...)`. Pandas 3 refuses
   that when the column's dtype changes, so both runs tried failed.
3. **The note names date columns by name**, and the position note says to replace a
   column with `df.isetitem(i, values)`. 4 of 4 passed, 2 on the first try, using 23,391
   tokens in total against 35,325 for the same 4 runs before. The two first-try
   failures left were one `applymap` and one repeated output column.

Version 0.2.2 ships the third one and keeps the system rule. One more run, used for the
README example, passed on the first try but turned the price `1436.72 USD` into an empty
cell.

These are small samples. The model is not fully repeatable even at temperature 0, so a
run can go better or worse than these.

## What to take from it

- Use a big model for real work. Small local models are fine for trying the tool on data
  that must stay on your machine, but expect to edit the saved transform.
- Notes next to the task, naming exact columns, work better than general rules in the
  system prompt.
- A transform that passes can still be wrong. Read the saved transform and check the
  output before you import it anywhere.
