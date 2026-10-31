"""Incident detection: snapshot of the sources each run, diff against the previous run,
and pseudonym stability check (old people must keep exactly the same pseudonyms).

Status: IMPLEMENTED.
"""
from __future__ import annotations

import json
from pathlib import Path


def take(raw: Path, records: dict) -> dict:
    snap = {"counts": {k: len(v) for k, v in records.items() if isinstance(v, list)}}
    chats = raw / "chats"
    snap["channels"] = sorted(p.name for p in chats.iterdir() if p.is_dir()) if chats.exists() else []
    snap["chat_days"] = sum(1 for _ in chats.glob("*/*.json")) if chats.exists() else 0
    snap["docs"] = sorted(r["id"] + ":" + r.get("format", "") for r in records.get("docs", []))
    tables = {}
    for r in records.get("tables", []):
        t = tables.setdefault(r["table"], {"rows": 0, "columns": set()})
        t["rows"] += 1
        t["columns"] |= set(r.get("columns", {}))
    snap["tables"] = {k: {"rows": v["rows"], "columns": sorted(v["columns"])} for k, v in tables.items()}
    return snap


def diff(prev: dict | None, cur: dict) -> dict:
    if not prev:
        return {"first_run": True}
    d = {"counts": {k: cur["counts"].get(k, 0) - prev["counts"].get(k, 0) for k in cur["counts"]},
         "new_channels": sorted(set(cur["channels"]) - set(prev["channels"])),
         "new_chat_days": cur["chat_days"] - prev["chat_days"],
         "new_docs": sorted(set(cur["docs"]) - set(prev["docs"])),
         "new_tables": sorted(set(cur["tables"]) - set(prev["tables"])),
         "new_columns": {t: sorted(set(v["columns"]) - set(prev["tables"].get(t, {}).get("columns", [])))
                         for t, v in cur["tables"].items()
                         if set(v["columns"]) - set(prev["tables"].get(t, {}).get("columns", []))},
         "removed_docs": sorted(set(prev["docs"]) - set(cur["docs"]))}
    d["changed"] = any(v for k, v in d.items() if k != "counts") or any(d["counts"].values())
    return d


def stability(prev_entities: dict | None, cur_entities: list[dict]) -> dict:
    """Share of previous people whose pseudonyms are unchanged (by entity_id)."""
    if not prev_entities:
        return {"first_run": True}
    prev = {e["entity_id"]: e.get("pseudonyms", {}) for e in prev_entities.get("entities", []) if not e.get("kept")}
    cur = {e["entity_id"]: e.get("pseudonyms", {}) for e in cur_entities if not e.get("kept")}
    same = sum(1 for k, v in prev.items() if k in cur and all(cur[k].get(t) == x for t, x in v.items()
                                                                  if t in cur[k]))
    missing = [k for k in prev if k not in cur]
    return {"previous_people": len(prev), "unchanged": same,
            "pct": round(100 * same / len(prev), 2) if prev else 100.0, "missing": missing[:20]}


def load(state_dir: Path) -> dict | None:
    p = state_dir / "snapshot.json"
    return json.loads(p.read_text("utf-8")) if p.exists() else None


def save(state_dir: Path, snap: dict) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "snapshot.json").write_text(json.dumps(snap, ensure_ascii=False, indent=1), "utf-8")
