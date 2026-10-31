# Kipu Pay corpus anonymizer

Solution for the Reto Frontier challenge **"Vende los datos sin vender a la gente"**. Kipu Pay, a Colombian fintech, wants to sell its support chats, internal documents and customer tables to train customer-service models. The deal only closes if the data **stays useful** and **no real person can be identified**.

This service rebuilds an anonymized 1:1 copy of the corpus from the challenge's sources API and anonymizes new records live:

- **One consistent, realistic pseudonym per person** across chats, docs and tables (same gender, valid cédula/phone/email formats), stable across runs and when new data arrives.
- **Replace, never rewrite:** only the personal span changes, so business facts stay intact.
- **Generalize quasi-identifiers** (occupation → sector, barrio → a nearby barrio or the city, year → "hace unos años") instead of deleting them.
- **Keep** organizations, places, brands and public figures (Bancolombia, Nequi, Plaza Botero…).
- A **self-check** refuses to ship output where a real value can still be found.
- A **verification + human-review loop** (Jev decision model + reasoning model + dashboard) turns doubtful cases into knowledge that the next run applies.

## Results

| | Before the incident | After the Págalo incident |
|---|---|---|
| Records (chats / docs / table rows) | 2,028 / 26 / 6,243 | 2,999 / 34 / 7,823 |
| Official validator | OK | OK |
| Leak sweep (judge-equivalent) | 0 | 0 |
| Public tests / sample tests | 5/5 · 13/13 | 5/5 · 13/13 |
| Pseudonym stability vs previous run | — | 99%+ |
| Full rebuild time | ~11–35 s | ~20–80 s |
| LLM tokens for the core pipeline | 0 | 0 |

Verification on 14 injected difficult cases: **9/9 leaks raised, 0/5 false alarms**. See `DECISIONS.md` (choices and costs) and `INCIDENT.md` (the data growth at 15:00).

## Quick start

Requirements: **Python 3.11+**. Node 20+ is only needed for the official validator.

```bash
git clone https://github.com/GuptaAditya95/kipu-anonymizer && cd kipu-anonymizer
cp .env.example .env          # Windows: copy .env.example .env
# edit .env: set SALT to a long random string (python -c "import secrets;print(secrets.token_hex(32))")
```

Start the service:

| macOS / Linux | Windows (cmd) |
|---|---|
| `./run.sh` | `run.bat` |

It listens on `http://localhost:9000/v1`. Settings are read from `.env`, or from environment variables, which take precedence.

### Try it without the challenge token

```bash
pip install -r requirements.txt
pytest -q                                   # 18 tests, no data or network needed

curl localhost:9000/v1/health
curl -X POST localhost:9000/v1/anonymize -H "Content-Type: application/json" -d '{
  "source": "chats",
  "records": [{"id": "m1", "thread_id": "soporte:1", "author": "Ana Lucía Marulanda", "ts": "1",
               "text": "Hola, soy Ana Lucía Marulanda, mi cédula es 32.355.343 y mi correo amarulanda@gmail.com",
               "attachments": []}]}'
```

The second call returns the record with a realistic pseudonym, a valid-format fake cédula and a matching fake email, plus the entities involved.

### Full run with the sources API

Put `SOURCES_URL` and `SOURCES_TOKEN` (from `/evento/reto` in Frontier) in `.env`, then:

```bash
python -m pipeline.run                      # downloads into data/raw, writes data/output (~30 s)
python -m pipeline.run --offline            # re-run on data/raw without downloading
node scripts/validar.mjs data/output --public data/raw
python scripts/score_local.py data/output data/raw/tests_public.json
```

The run summary is printed and also saved to `data/state/last_run.json`. It includes `records`, `changes` (what grew since the last run), `stability`, `leaks` and `self_check_passes`.

### Simulate the evaluator locally

This serves your downloaded data as a mock sources API, then calls the service exactly like the judge: `/health` → `/run` → poll → `/output` → validator → live `/anonymize` checks.

```bash
python scripts/mock_sources.py --raw data/raw --port 9100 &     # add --subset to simulate "before the incident"
python scripts/simulate_judge.py --service http://localhost:9000/v1 --sources http://localhost:9100/v1
```

## API (contract: `api/participante.openapi.yaml`)

| Endpoint | What it does |
|---|---|
| `GET /v1/health` | `{status, team, version, models}` |
| `POST /v1/run` | `{sources_url, token}` → `202 {job_id}`; rebuilds the output from scratch in the background |
| `GET /v1/run/{job_id}` | `queued / running / done / failed`, record counts, cost, `changes`, `stability` |
| `GET /v1/output/{file}` | `chats.jsonl`, `docs.jsonl`, `tables.jsonl`, `entities.json` of the last completed run |
| `POST /v1/anonymize` | `{source, records}` (≤ 50) → same ids anonymized + entities, same pseudonyms as the output |
| `GET /v1/entities` | full dictionary of the last completed run |

Every call may carry `Authorization: Bearer <token>`; it is accepted and never used to reject a request.

## Verification and human review

After each `/run` finishes (in the background, so the judge never waits):

1. **Mask** our own replacements as `[NOMBRE_1]`, `[CEDULA]`, `[EMAIL]`… so anything that still looks personal is something we missed.
2. **Residual check (code):** leftover names, CAPS names, a surname right after a placeholder, `J. Castrillón`-style initials, nicknames (`la Mafe`), ID-like numbers, IPs, obfuscated emails, addresses.
3. **Jev** (TypeSafe decision model via OpenRouter) scores P(real PII left) on free text: 16 parallel workers, cached, deduplicated.
4. **Decision:** a code hit, or P > 0.7 → high priority; 0.2–0.7 or a weak hit → low; otherwise pass. Jev never clears a code hit.
5. **Reasoning model** (via OpenRouter; deterministic heuristic fallback) reviews each case with its connected datapoints (thread, same text elsewhere, registry matches, people in the record) and proposes a fix plus a question.
6. **Dashboard** at `http://localhost:9000/v1/review?key=REVIEW_KEY`: a human accepts or answers.
7. Answers go to `data/state/knowledge.json`, and the **next `/run` applies them**.

```bash
python scripts/simulate_cases.py       # inject 14 difficult cases and measure the verifier
python scripts/jev_probe.py            # check your OPENROUTER_API_KEY reaches Jev
```

## Configuration (`.env`)

| Variable | Required | Purpose |
|---|---|---|
| `SALT` | **yes** | Secret for deterministic pseudonyms. **Set once; changing it changes every pseudonym.** |
| `DATA_DIR` | no (`./data`) | Raw sources, output, pseudonym map and state. Keep it between runs. |
| `SOURCES_URL`, `SOURCES_TOKEN` | for CLI runs | Sources API (the judge sends its own in `/run`) |
| `OPENROUTER_API_KEY` | no | Enables Jev + the reasoning model. Without it everything works rules-only. |
| `REVIEW_KEY` | no | Enables the review dashboard (disabled when empty; it shows real snippets) |
| `REASONING_MODEL` | no | OpenRouter model slug for case reasoning |
| `VERIFY`, `VERIFY_LOW`, `VERIFY_HIGH`, `VERIFY_WORKERS` | no | Verification switch and thresholds |
| `PORT` | no (`9000`) | HTTP port |

## Deploying

Any always-on machine with Python works. During the hackathon it ran on a laptop behind a free Cloudflare tunnel:

```bash
cloudflared tunnel --url http://localhost:9000      # prints https://<random>.trycloudflare.com
```

Register `https://<random>.trycloudflare.com/v1` in Frontier. A `Dockerfile` is included for container hosts; mount a persistent volume at `/data`. Keep `SALT` and `DATA_DIR` the same across restarts so pseudonyms stay stable.

## Repository layout

```
app/
  main.py          HTTP service (contract endpoints, background jobs, atomic output swap)
  review.py        review API (key-protected)
  review.html      review dashboard
pipeline/
  ingest.py        download sources (throttled, retries)
  normalize.py     raw -> output schema; tables discovered from the API; never drops an id
  registry.py      people from clientes/empleados/chat users, every form of their identity
  core.py          detection, resolution, replacement (shared by /run and /anonymize)
  process.py       chats by thread, docs, table rows; builds entities.json
  pseudonyms.py    HMAC deterministic pseudonyms, same category, persisted map
  generalize.py    nearby barrios by zone, occupation -> sector (Jev fallback), year phrases
  snapshot.py      source snapshot/diff (incident detection) and pseudonym stability
  output.py        writes the 4 files; judge-equivalent leak sweep
  verify.py        masked verification -> review cases
  reasoning.py     reasoning model over cases (+ heuristic fallback)
  knowledge.py     human answers -> knowledge applied by the next run
  jev.py           Jev client through OpenRouter (cached)
  text.py          judge-equivalent normalization, slugs, form-preserving style
  fileio.py        atomic JSON writes that also work on Windows
  env.py           .env loader
  run.py           full-run orchestrator (CLI + used by the API)
resources/         name lists, surnames, keep-list, occupation sectors, barrios by zone
scripts/           validator, local scorer, mock sources, judge simulator, case simulator, probes
tests/             unit + API tests
api/               OpenAPI contracts (sources API, participant API)
docs/              design & architecture (PDF + markdown), demo script, build history
DECISIONS.md       what we treated as PII, what we kept and why, costs, design choices
INCIDENT.md        the Págalo data growth: detection, adaptation, results
run.sh / run.bat   start the service · Dockerfile · requirements.txt · .env.example
```

## Data handling

Real sources contain personal data and are **never committed**: `data/` and `.env` are gitignored. The review dashboard is off unless `REVIEW_KEY` is set. Rotate any API key that was shared during the event.
