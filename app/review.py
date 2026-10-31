"""Review dashboard + API (internal; not part of the judge's contract).

Status: IMPLEMENTED.
  GET  /v1/review?key=...                 dashboard page
  GET  /v1/review/cases?key=...           cases + stats
  POST /v1/review/verify?key=...          (re)build cases from the last output (background)
  POST /v1/review/cases/{id}/answer?key=  human answer -> knowledge.json (applied by the next /run)
  GET  /v1/review/knowledge?key=...       what the next run will apply

The page shows real snippets, so it requires REVIEW_KEY (env). Without REVIEW_KEY set, the
dashboard is disabled (403), so nothing personal is exposed through the public tunnel by default.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from pipeline import knowledge, verify

router = APIRouter()
STATUS = {"running": False, "progress": "", "error": ""}


def _check(key: str | None):
    want = os.environ.get("REVIEW_KEY")
    if not want or key != want:
        raise HTTPException(403, "review dashboard disabled or wrong key (set REVIEW_KEY)")


def run_verify(state, out_dir: Path, state_dir: Path):
    if STATUS["running"]:
        return
    STATUS.update(running=True, progress="masking + residual check", error="")
    try:
        verify.verify(out_dir, state_dir, state.processor.reg, knowledge.load(),
                      progress=lambda n, t: STATUS.update(progress=f"reasoning {n}/{t}"))
        STATUS["progress"] = "done"
    except Exception as e:  # never affects the judge's endpoints
        STATUS["error"] = str(e)
    finally:
        STATUS["running"] = False


class Answer(BaseModel):
    action: str                     # person_name | link | keep | identifier | occupation | dismiss
    value: str | None = None
    entity_key: str | None = None
    sector: str | None = None
    note: str | None = None
    reviewer: str | None = "human"


def make_router(get_state, out_dir: Path, state_dir: Path) -> APIRouter:
    @router.get("/v1/review", response_class=HTMLResponse)
    def page(key: str | None = Query(None)):
        _check(key)
        return HTMLResponse((Path(__file__).parent / "review.html").read_text("utf-8"))

    @router.get("/v1/review/cases")
    def cases(key: str | None = Query(None)):
        _check(key)
        data = verify.load_cases(state_dir)
        data["status"] = STATUS
        data["sectors"] = json.loads((Path(__file__).resolve().parent.parent / "resources" /
                                      "occupation_sectors.json").read_text("utf-8"))["sectors"]
        return data

    @router.post("/v1/review/verify")
    def trigger(key: str | None = Query(None)):
        _check(key)
        threading.Thread(target=run_verify, args=(get_state(), out_dir, state_dir), daemon=True).start()
        return {"started": True}

    @router.post("/v1/review/cases/{case_id}/answer")
    def answer(case_id: str, a: Answer, key: str | None = Query(None)):
        _check(key)
        data = verify.load_cases(state_dir)
        case = next((c for c in data.get("cases", []) if c["case_id"] == case_id), None)
        if not case:
            raise HTTPException(404, "case not found")
        if a.action != "dismiss":
            knowledge.record_answer(case, a.model_dump())
        case["status"] = "answered"
        case["answer"] = a.model_dump()
        from pipeline.fileio import write_json
        write_json(state_dir / verify.CASES, data)
        return {"ok": True}

    @router.get("/v1/review/knowledge")
    def kn(key: str | None = Query(None)):
        _check(key)
        return knowledge.load()

    return router
