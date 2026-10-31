"""Write the 4 output files and run the judge-equivalent leak sweep.

Status: IMPLEMENTED. The sweep uses pipeline.text.contains (a port of the judge's `contiene`).
"""
from __future__ import annotations

import json
from pathlib import Path

from pipeline.text import Haystack, contains


def write_output(out_dir: Path, records: dict[str, list[dict]], entities: list[dict]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for source in ("chats", "docs", "tables"):
        with open(out_dir / f"{source}.jsonl", "w", encoding="utf-8") as f:
            for r in records.get(source, []):
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    (out_dir / "entities.json").write_text(
        json.dumps({"entities": entities}, ensure_ascii=False, indent=1), encoding="utf-8")


def _flat(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    if isinstance(v, list):
        return [x for i in v for x in _flat(i)]
    if isinstance(v, dict):
        return [x for k, i in v.items() for x in [k, *_flat(i)]]
    return [str(v)]


def record_texts(out_dir: Path) -> dict[str, str]:
    texts = {}
    for source in ("chats", "docs", "tables"):
        p = out_dir / f"{source}.jsonl"
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    r = json.loads(line)
                    texts[r["id"]] = " | ".join(_flat({k: v for k, v in r.items() if k != "id"}))
    return texts


def single_capitalized(s: str) -> bool:
    return " " not in s and "@" not in s and not any(c.isdigit() for c in s) and s[:1].isupper()


def leak_sweep(out_dir: Path, entities: list[dict], limit: int = 50) -> list[dict]:
    """Originals (of non-kept people) still present anywhere in the output, judged like the
    validator: >=3 chars, single capitalized words skipped."""
    texts = record_texts(out_dir)
    whole = Haystack("\n".join(texts.values()))
    per = None
    leaks = []
    for e in entities:
        if e.get("kept"):
            continue
        for orig in e.get("originals", []):
            if len(orig) < 3 or single_capitalized(orig) or not whole.contains(orig):
                continue
            if per is None:
                per = {rid: Haystack(t) for rid, t in texts.items()}
            hits = [rid for rid, h in per.items() if h.contains(orig)][:3]
            leaks.append({"entity_id": e["entity_id"], "original": orig, "records": hits})
            if len(leaks) >= limit:
                return leaks
    return leaks
