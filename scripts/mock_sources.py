"""Mock of the challenge's sources API, served from a local raw dump (data/raw).

  python scripts/mock_sources.py --raw data/raw --port 9100 [--subset]

--subset simulates the state BEFORE the 15:00 incident: hides the 'general' channel and the
last 6 documents (and filters record_index accordingly). Restart without --subset to simulate
the data growth. Requires the Bearer header like the real API.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse, Response

ap = argparse.ArgumentParser()
ap.add_argument("--raw", default="data/raw")
ap.add_argument("--port", type=int, default=9100)
ap.add_argument("--subset", action="store_true")
args = ap.parse_args()
RAW = Path(args.raw)
HIDDEN_CHANNELS = {"general"} if args.subset else set()
docs_index = json.loads((RAW / "docs" / "index.json").read_text("utf-8"))
HIDDEN_DOCS = {d["id"] for d in docs_index[-6:]} if args.subset else set()

app = FastAPI()


def auth(a: str | None):
    if not a or not a.startswith("Bearer "):
        raise HTTPException(401, "missing token")


def j(path: Path):
    return json.loads(path.read_text("utf-8"))


@app.get("/v1/record_index")
def record_index(authorization: str | None = Header(None)):
    auth(authorization)
    idx = j(RAW / "record_index.json")
    hidden_msgs = {m["client_msg_id"] for ch in HIDDEN_CHANNELS
                   for f in (RAW / "chats" / ch).glob("*.json") for m in j(f) if "client_msg_id" in m}
    idx["chats"] = [i for i in idx["chats"] if i not in hidden_msgs]
    idx["docs"] = [i for i in idx["docs"] if i not in HIDDEN_DOCS]
    return idx


@app.get("/v1/chats/channels")
def channels(authorization: str | None = Header(None)):
    auth(authorization)
    return [c for c in j(RAW / "chats" / "channels.json") if c["name"] not in HIDDEN_CHANNELS]


@app.get("/v1/chats/users")
def users(authorization: str | None = Header(None)):
    auth(authorization)
    return j(RAW / "chats" / "users.json")


@app.get("/v1/chats/{canal}")
def dates(canal: str, authorization: str | None = Header(None)):
    auth(authorization)
    d = RAW / "chats" / canal
    if canal in HIDDEN_CHANNELS or not d.is_dir():
        raise HTTPException(404, "no existe")
    return sorted(p.stem for p in d.glob("*.json"))


@app.get("/v1/chats/{canal}/{fecha}")
def day(canal: str, fecha: str, authorization: str | None = Header(None)):
    auth(authorization)
    p = RAW / "chats" / canal / f"{fecha}.json"
    if canal in HIDDEN_CHANNELS or not p.exists():
        raise HTTPException(404, "no existe")
    return j(p)


@app.get("/v1/docs")
def docs(authorization: str | None = Header(None)):
    auth(authorization)
    return [d for d in docs_index if d["id"] not in HIDDEN_DOCS]


@app.get("/v1/docs/{doc_id}")
def doc(doc_id: str, authorization: str | None = Header(None)):
    auth(authorization)
    d = next((d for d in docs_index if d["id"] == doc_id and doc_id not in HIDDEN_DOCS), None)
    if not d:
        raise HTTPException(404, "no existe")
    ctype = {"pdf": "application/pdf", "html": "text/html; charset=utf-8", "md": "text/markdown; charset=utf-8"}
    return Response((RAW / "docs" / f"{doc_id}.{d['format']}").read_bytes(), media_type=ctype[d["format"]])


@app.get("/v1/tables")
def tables(authorization: str | None = Header(None)):
    auth(authorization)
    return j(RAW / "tables" / "tables.json")


@app.get("/v1/tables/{name}")
def table(name: str, authorization: str | None = Header(None)):
    auth(authorization)
    p = RAW / "tables" / name
    if not p.exists():
        raise HTTPException(404, "no existe")
    return Response(p.read_bytes(), media_type="text/csv" if name.endswith(".csv") else "application/xml")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
