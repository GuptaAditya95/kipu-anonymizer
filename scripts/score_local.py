"""Local scorer for the public / sample tests (approximation of the judge).

  python scripts/score_local.py <output_dir> <tests.json>

Families: directa, oculta, indirecta, falso_positivo, homonimos, consistencia.
Uses pipeline.text.contains (port of the judge's matching).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.output import _flat  # noqa: E402
from pipeline.text import contains, slug  # noqa: E402


def field_text(rec: dict, field: str | None) -> str:
    if not field or field in ("any", "raw", "*"):
        return " | ".join(_flat({k: v for k, v in rec.items() if k != "id"}))
    if field.startswith("columns."):
        col = field.split(".", 1)[1]
        cols = rec.get("columns", {})
        return " | ".join(_flat(cols if col == "*" else cols.get(col)))
    return " | ".join(_flat(rec.get(field)))


def main(out_dir: str, tests_path: str) -> int:
    out = Path(out_dir)
    recs = {}
    for src in ("chats", "docs", "tables"):
        for line in (out / f"{src}.jsonl").read_text("utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                recs[r["id"]] = r
    ents = json.loads((out / "entities.json").read_text("utf-8"))["entities"]
    whole = "\n".join(field_text(r, None) for r in recs.values())
    tests = json.loads(Path(tests_path).read_text("utf-8"))
    passed = 0
    for t in tests:
        fam, payload = t["family"], t.get("payload", [])
        loc = t.get("locator") or {}
        rec = recs.get(loc.get("record") or "", {})
        rtext = field_text(rec, loc.get("field")) if rec else ""
        if fam == "directa":
            ok = all(not contains(whole, p) for p in payload)
        elif fam == "oculta":
            ok = all(not contains(rtext, p) for p in payload)
        elif fam == "indirecta":
            ok = not all(contains(field_text(rec, None), p) for p in payload)
        elif fam == "falso_positivo":
            ok = all(p in field_text(rec, None) for p in payload)
        elif fam == "homonimos":
            short = [" ".join(p.split()[:2]) for p in payload]
            names = {e["pseudonyms"].get("name") for e in ents if not e.get("kept")
                     and any(o in payload or o in short or any(o.startswith(x + " ") for x in short)
                             for o in e["originals"])}
            ok = len(names) >= 2
        elif fam == "consistencia":
            forms = t.get("lookup_forms", [])
            hit = [e for e in ents if not e.get("kept") and set(e["originals"]) & set(forms)]
            if not hit and not forms:
                ok = None
            else:
                nm = hit[0]["pseudonyms"].get("name", "") if hit else ""
                forms_ok = [nm, nm.split()[0] if nm else "", slug(nm)]
                ok = len(hit) == 1 and all(any(f and contains(field_text(recs[r], None), f) for f in forms_ok)
                                           for r in hit[0]["records"] if r in recs and nm) \
                    and not any(contains(whole, f) for f in forms if len(f) >= 3)
        else:
            ok = None
        passed += ok is True
        status = "PASS" if ok else ("SKIP" if ok is None else "FAIL")
        print(f"{t['id']:10} {fam:15} {t['mechanism'][:38]:38} {status}")
    print(f"\n{passed}/{len(tests)} passing")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
