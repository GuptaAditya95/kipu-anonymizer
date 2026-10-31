"""Stage 7 - Write the 4 output files and run the leak sweep.

Status: write IMPLEMENTED; leak sweep SIMPLE (normalized substring search).

TODO: make the leak sweep use exactly the judge's normalization (port contiene() from
scripts/validar.mjs: confusables, invisible chars, digit separators, +57 prefix).
"""
from __future__ import annotations

import json
import unicodedata
from pathlib import Path


def write_output(out_dir: Path, records: dict[str, list[dict]], entities: list[dict]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for source in ("chats", "docs", "tables"):
        with open(out_dir / f"{source}.jsonl", "w", encoding="utf-8") as f:
            for r in records.get(source, []):
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    (out_dir / "entities.json").write_text(
        json.dumps({"entities": entities}, ensure_ascii=False, indent=1), encoding="utf-8")


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    return s.casefold()


def leak_sweep(out_dir: Path, entities: list[dict], limit: int = 50) -> list[dict]:
    """Find original forms (non-kept entities) still present anywhere in the output."""
    text = ""
    for source in ("chats", "docs", "tables"):
        p = out_dir / f"{source}.jsonl"
        if p.exists():
            text += _norm(p.read_text(encoding="utf-8"))
    leaks = []
    for e in entities:
        if e.get("kept"):
            continue
        for orig in e.get("originals", []):
            if len(orig) >= 4 and _norm(orig) in text:
                leaks.append({"entity_id": e.get("entity_id"), "original": orig})
                if len(leaks) >= limit:
                    return leaks
    return leaks
