"""Stage 1 - Ingest: download every source from the sources API.

Status: IMPLEMENTED (tested against the real API on 2026-10-31).

Writes raw files under data/raw/ so stages can be re-run offline:
  data/raw/record_index.json
  data/raw/chats/channels.json, users.json, <canal>/<fecha>.json
  data/raw/docs/index.json, <id>.<ext>
  data/raw/tables/<name> (original bytes: clientes.csv is Latin-1)
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

MAX_RPM = 500  # API limit is 600/min per token; keep a margin


class SourcesClient:
    def __init__(self, base_url: str, token: str):
        self.base = base_url.rstrip("/")
        self.http = httpx.Client(
            headers={"Authorization": f"Bearer {token}"}, timeout=60, follow_redirects=True
        )
        self._calls: list[float] = []
        self.requests = 0

    def _throttle(self) -> None:
        now = time.time()
        self._calls = [t for t in self._calls if now - t < 60]
        if len(self._calls) >= MAX_RPM:
            time.sleep(60 - (now - self._calls[0]) + 0.1)
        self._calls.append(time.time())

    def get(self, path: str) -> httpx.Response:
        for attempt in range(5):
            self._throttle()
            self.requests += 1
            r = self.http.get(f"{self.base}/{path.lstrip('/')}")
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            return r
        r.raise_for_status()
        return r

    def json(self, path: str):
        return self.get(path).json()


def ingest(base_url: str, token: str, raw_dir: Path) -> dict:
    """Download everything. Returns a small summary dict."""
    c = SourcesClient(base_url, token)
    (raw_dir / "chats").mkdir(parents=True, exist_ok=True)
    (raw_dir / "docs").mkdir(parents=True, exist_ok=True)
    (raw_dir / "tables").mkdir(parents=True, exist_ok=True)

    def dump(obj, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")

    dump(c.json("record_index"), raw_dir / "record_index.json")

    # chats
    channels = c.json("chats/channels")
    dump(channels, raw_dir / "chats" / "channels.json")
    dump(c.json("chats/users"), raw_dir / "chats" / "users.json")
    n_days = 0
    for ch in channels:
        name = ch["name"]
        for day in c.json(f"chats/{name}"):
            dump(c.json(f"chats/{name}/{day}"), raw_dir / "chats" / name / f"{day}.json")
            n_days += 1

    # docs
    index = c.json("docs")
    dump(index, raw_dir / "docs" / "index.json")
    for d in index:
        r = c.get(f"docs/{d['id']}")
        (raw_dir / "docs" / f"{d['id']}.{d['format']}").write_bytes(r.content)

    # tables (keep original bytes; decoding happens in normalize)
    tables = c.json("tables")
    dump(tables, raw_dir / "tables" / "tables.json")
    for t in tables:
        r = c.get(f"tables/{t['name']}")
        (raw_dir / "tables" / t["name"]).write_bytes(r.content)

    return {"channels": len(channels), "chat_days": n_days, "docs": len(index),
            "tables": len(tables), "requests": c.requests}
