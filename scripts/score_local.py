"""Local scorer: checks our output against the 5 public tests (and the 13 sample tests
when run on the sample). Status: SKELETON - implements leak / false-positive checks;
homonym and consistency checks are TODO.

  python scripts/score_local.py data/output data/public/tests_public.json
"""
from __future__ import annotations

import json
import sys
import unicodedata
from pathlib import Path


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    return s.casefold()


def load(out: Path) -> dict:
    recs = {}
    for src in ("chats", "docs", "tables"):
        for line in (out / f"{src}.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                recs[r["id"]] = r
    return recs


def main(out_dir: str, tests_path: str) -> None:
    recs = load(Path(out_dir))
    alltext = norm("\n".join(json.dumps(r, ensure_ascii=False) for r in recs.values()))
    passed = 0
    tests = json.loads(Path(tests_path).read_text(encoding="utf-8"))
    for t in tests:
        fam, payload = t["family"], t.get("payload", [])
        rec = recs.get((t.get("locator") or {}).get("record") or "", {})
        rtext = norm(json.dumps(rec, ensure_ascii=False))
        if fam in ("directa", "oculta", "indirecta"):
            ok = all(norm(p) not in alltext for p in payload) if fam == "directa" else \
                 all(norm(p) not in rtext for p in payload)
        elif fam == "falso_positivo":
            ok = all(p in json.dumps(rec, ensure_ascii=False) for p in payload)
        else:
            ok = None  # TODO homonimos / consistencia
        passed += ok is True
        print(f"{t['id']:8} {fam:15} {t['mechanism']:35} {'PASS' if ok else 'TODO' if ok is None else 'FAIL'}")
    print(f"\n{passed}/{len(tests)} passing (TODO checks not counted)")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
