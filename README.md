# Kipu Pay corpus anonymizer

Solution for the Reto Frontier challenge **"Vende los datos sin vender a la gente"**: turn Kipu Pay's support chats, internal documents and customer tables into a corpus that is **still useful for training customer-service models** while **no real person can be identified**.

- Every natural person gets **one consistent, realistic pseudonym** across chats, docs and tables.
- Quasi-identifiers (neighborhood + occupation + year) are **generalized, not deleted**.
- Organizations, places, brands, public figures and business data are **kept**.
- Served as an HTTP API that rebuilds the output in **< 2 h** and anonymizes new records live in **< 60 s / 50 records**.

Built with the **Pi coding agent**. At request time the service runs **deterministic code, not a runtime agent** (see `DECISIONS.md`).

## Deliverables

| Deliverable | Where |
|---|---|
| Pipeline + service | `pipeline/`, `app/` |
| Script that starts the service | `run.sh` |
| Decisions (PII, what we kept, cost) | `DECISIONS.md` |
| Design document (1 page) | `docs/Design_Document.pdf` |
| Architecture document + diagram | `docs/Architecture_Document.pdf`, `docs/architecture_diagram.png` |
| 5-minute demo script | `docs/DEMO.md` |
| Public URL | registered in `/evento/reto` of Frontier |

## API (contract: `api/participante.openapi.yaml`)

| Endpoint | What it does |
|---|---|
| `GET /v1/health` | `{status, team, version, models}` |
| `POST /v1/run` | `{sources_url, token}` → `202 {job_id}`; rebuilds `output/` from scratch in the background |
| `GET /v1/run/{job_id}` | `queued / running / done / failed`, record counts, cost in tokens and seconds |
| `GET /v1/output/{file}` | `chats.jsonl`, `docs.jsonl`, `tables.jsonl`, `entities.json` of the last completed run |
| `POST /v1/anonymize` | `{source, records}` (≤ 50) → same ids anonymized + entities involved, same pseudonyms as `output/` |
| `GET /v1/entities` | full dictionary of the last completed run (judge only) |

Every call may carry `Authorization: Bearer <token>`; it is accepted and never used to reject a request.

## Repository layout

```
.
├── app/
│   └── main.py              # FastAPI service: endpoints, background jobs, atomic output
├── pipeline/
│   ├── ingest.py            # 1. download sources (throttled < 600 req/min)       [done]
│   ├── normalize.py         # 2. raw -> output schema (chats, docs, tables)        [done]
│   ├── registry.py          # 3. person registry: clientes, empleados, chat users  [todo]
│   ├── detect.py            # 4. regex + registry match + name list + keep-list    [draft]
│   ├── jev.py               # 4b. optional Jev typed classification of leftovers   [stretch]
│   ├── pseudonyms.py        # 5. HMAC deterministic pseudonyms + persisted map     [skeleton]
│   ├── generalize.py        # 6b. occupation -> sector, barrio -> barrio, year      [skeleton]
│   ├── core.py              # 4-6 shared by /run and /anonymize                    [pass-through]
│   ├── output.py            # 7. write 4 files + leak sweep                        [done/simple]
│   └── run.py               # orchestrator (also a CLI)
├── resources/               # name lists, surnames, keep-list, occupation sectors, barrios
├── scripts/
│   ├── fetch_sources.sh     # download sample + public tests + record_index
│   ├── validate.sh          # official validator on sample and on our output
│   ├── validar.mjs          # official validator (Node 20+)
│   └── score_local.py       # local scorer for public/sample tests
├── tests/test_smoke.py      # API contract smoke tests
├── api/                     # OpenAPI contracts (sources API, participant API)
├── docs/                    # design + architecture documents, demo script
├── data/                    # (gitignored) raw sources, output, pseudonym map
├── run.sh  Dockerfile  requirements.txt  .env.example
├── DECISIONS.md
└── PLAN.md                  # build plan and timeline (incl. 15:00 incident)
```

## Quick start

```bash
cp .env.example .env            # put your sources token and a SALT
./run.sh                        # service on http://localhost:9000/v1/health

# or run the pipeline directly while developing
python3 -m pipeline.run         # downloads into data/raw, writes data/output
python3 -m pipeline.run --offline
./scripts/fetch_sources.sh && ./scripts/validate.sh
pytest -q
```

## Deploy

Any always-on host (Railway / Render / Fly) using the `Dockerfile`. **Mount a persistent volume at `/data`** so the pseudonym map survives restarts, and set `SALT` once. Never change it after the first evaluation run, or every pseudonym changes. Must stay reachable from 19:00 until demo day ends.

## Data handling

Real sources contain personal data and are **never committed** (`data/` is gitignored). The sources token lives in `.env` or arrives in the `/run` request body.
