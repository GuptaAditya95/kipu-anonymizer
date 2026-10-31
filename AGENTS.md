# AGENTS.md: standing instructions for the Pi coding agent

You are building the Kipu Pay anonymization service in this repo. **Read `docs/BUILD_SPEC.md` before any task.** It holds the data facts, the exact output contract, every module's spec and the acceptance tests. `TODO.md` tracks progress: tick items there when they're done.

## Project in one paragraph
Anonymize a Colombian fintech's corpus (2,028 chat messages, 26 docs, 6,243 table rows) so that no real person can be identified, while the data stays useful. Every person gets **one consistent, realistic pseudonym across all sources**. Quasi-identifiers are generalized. Orgs, places and business data are kept. It's served by FastAPI (`app/main.py`) with `/run` (full rebuild, background job) and `/anonymize` (live, ≤50 records, <60 s). Both share one core (`pipeline/core.py`).

## Hard rules (never break these)
1. **1:1 output:** every id in `data/raw/record_index.json` appears exactly once. Never drop or empty a record. Never remove a key, column or metadata key.
2. **Replace spans, never rewrite text.** Non-personal text must stay byte-identical.
3. **Deterministic pseudonyms:** `HMAC(SALT, stable_key)` plus the persisted map in `data/state/`. No `random` without a seed derived from the HMAC. Same input means the same output on every run.
4. **No PII libraries or services** (Presidio, spaCy NER models for PII, AWS Comprehend, etc.). These are banned by the challenge. Plain regex, our own lists, and `unicodedata` only.
5. **No LLM calls inside `/run` or `/anonymize`** except the optional Jev step (`pipeline/jev.py`), and only when `JEV_API_KEY` is set, with cache plus timeout.
6. **Never commit real data or secrets.** `data/` and `.env` are gitignored. Don't paste real names into code or tests. Use the sample dataset's people (they're fictional) for fixtures.
7. **The server must never crash.** Catch exceptions per record. Unknown fields pass through.
8. Python 3.11, type hints, small pure functions, a docstring on every module saying what it does and its status.

## How to work
- One task from `docs/BUILD_SPEC.md` §6 at a time. Write the tests first (`tests/`), then the code, then run:
  ```bash
  pytest -q
  python -m pipeline.run --offline          # uses data/raw (run once without --offline to download)
  node scripts/validar.mjs data/output --public data/raw
  python scripts/score_local.py data/output data/public/tests_public.json
  ```
- After each task: all of the above must pass. Update `TODO.md`. Commit with a clear message (author: the repo owner; no co-author lines).
- If something in the data contradicts the spec, **stop and write it in `knowledge/learned_from_runs.md`** with your hypothesis before changing code.
- Keep the README accurate when behaviour changes.
- No background bash in Pi: use `tmux` for the server (`tmux new -d -s api ./run.sh`).
