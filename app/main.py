"""HTTP service - contract in api/participante.openapi.yaml.

Status: IMPLEMENTED (endpoints, background jobs, atomic output, cold start).
The anonymization logic itself lives in pipeline/core.py (shared with /run).

Notes from the event contract:
  - every call carries "Authorization: Bearer <token>": accepted, never used to reject.
  - must be reachable from 19:00 until demo day ends -> always-on host.
"""
from __future__ import annotations

import threading
import time
import traceback
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from pipeline import jev
from pipeline.run import OUT, State, run_pipeline

TEAM = "kipu-anonymizer"
VERSION = "0.1.0"

app = FastAPI(title="Kipu Pay anonymizer")
STATE = State()          # loads the persisted pseudonym map on boot (cold start safe)
JOBS: dict[str, dict] = {}
RUN_LOCK = threading.Lock()
CURRENT: dict = {"job_id": None}


class RunRequest(BaseModel):
    sources_url: str
    token: str


class AnonymizeRequest(BaseModel):
    source: str
    records: list[dict]


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


@app.get("/v1/health")
def health():
    models = ["rules"] + ([f"openrouter/typesafe/{jev.MODEL}"] if jev.enabled() else [])
    return {"status": "ok", "team": TEAM, "version": VERSION, "models": models}


def _worker(job_id: str, req: RunRequest):
    job = JOBS[job_id]
    job.update(status="running", started_at=now())
    t0 = time.time()
    try:
        with RUN_LOCK:
            summary = run_pipeline(req.sources_url, req.token, STATE)
        job.update(status="done", records=summary["records"], changes=summary.get("changes"),
                   stability=summary.get("stability"), leaks=len(summary.get("leaks", [])))
        job["_jev"] = summary.get("jev", {})
    except Exception as e:  # never kill the server
        job.update(status="failed", error=f"{e}\n{traceback.format_exc()[-1500:]}")
    finally:
        job["finished_at"] = now()
        j = job.pop("_jev", {}) or {}
        job["cost"] = {"tokens_in": int(j.get("tokens_in", 0)), "tokens_out": 0,
                       "seconds": round(time.time() - t0, 1)}


@app.post("/v1/run", status_code=202)
def run(req: RunRequest):
    cur = CURRENT["job_id"]
    if cur and JOBS[cur]["status"] in ("queued", "running"):
        return {"job_id": cur}  # idempotent: one rebuild at a time
    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {"status": "queued", "records": {}, "cost": {}}
    CURRENT["job_id"] = job_id
    threading.Thread(target=_worker, args=(job_id, req), daemon=True).start()
    return {"job_id": job_id}


@app.get("/v1/run/{job_id}")
def run_status(job_id: str):
    if job_id not in JOBS:
        raise HTTPException(404, "job not found")
    return JOBS[job_id]


FILES = {"chats.jsonl": "application/x-ndjson", "docs.jsonl": "application/x-ndjson",
         "tables.jsonl": "application/x-ndjson", "entities.json": "application/json"}


@app.get("/v1/output/{archivo}")
def get_output(archivo: str):
    if archivo not in FILES:
        raise HTTPException(404, "unknown file")
    p = OUT / archivo
    if not p.exists():
        raise HTTPException(404, "no completed run")
    return FileResponse(p, media_type=FILES[archivo])


@app.post("/v1/anonymize")
def anonymize(req: AnonymizeRequest):
    if req.source not in ("chats", "docs", "tables"):
        raise HTTPException(422, "source must be chats, docs or tables")
    if len(req.records) > 50:
        raise HTTPException(422, "max 50 records")
    for r in req.records:
        if "id" not in r:
            raise HTTPException(422, "record without id")
    for r in req.records:
        r.pop("_uid", None)
    try:
        with STATE.lock:
            out, entities = STATE.processor.run({req.source: req.records}, live=True)
            STATE.save()  # new people from live calls persist too
    except Exception as e:  # never crash: return records untouched would leak, so fail loudly
        raise HTTPException(500, f"anonymize failed: {e}")
    records = out.get(req.source, [])
    return {"records": records, "entities": entities}


@app.get("/v1/entities")
def entities():
    p = OUT / "entities.json"
    if not p.exists():
        return JSONResponse({"entities": []})
    return FileResponse(p, media_type="application/json")
