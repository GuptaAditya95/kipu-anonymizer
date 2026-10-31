"""Human review step: promote Jev-learned occupation mappings into the versioned rules.

  python scripts/promote_learned.py            # show pending decisions
  python scripts/promote_learned.py --accept   # accept all pending: add to
                                               # resources/occupation_sector.json (exact) and
                                               # resources/occupation_keywords.json (keyword)
Then review the git diff and commit. Accepted entries stay deterministic forever.
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("DATA_DIR", ROOT / "data"))
learned_p = DATA / "state" / "learned_occupations.json"
exact_p = ROOT / "resources" / "occupation_sector.json"
kw_p = ROOT / "resources" / "occupation_keywords.json"

learned = json.loads(learned_p.read_text("utf-8")) if learned_p.exists() else {}
pending = {k: v for k, v in learned.items() if v.get("status") == "pending_review"}
if not pending:
    sys.exit("nothing pending")
for k, v in pending.items():
    print(f"{v['original']!r:35} -> {v['sector']:45} p={v['probability']}  keyword={v['proposed_keyword']!r}")

if "--accept" in sys.argv:
    exact = json.loads(exact_p.read_text("utf-8"))
    kw = json.loads(kw_p.read_text("utf-8"))
    known_kw = {r[0] for r in kw["rules"]}
    for k, v in pending.items():
        exact["map"][v["original"].strip().lower()] = v["sector"]
        if v["proposed_keyword"] and v["proposed_keyword"] not in known_kw:
            kw["rules"].append([v["proposed_keyword"], v["sector"]])
        v["status"] = "accepted"
    exact_p.write_text(json.dumps(exact, ensure_ascii=False, indent=1) + "\n", "utf-8")
    kw_p.write_text(json.dumps(kw, ensure_ascii=False, indent=1) + "\n", "utf-8")
    learned_p.write_text(json.dumps(learned, ensure_ascii=False, indent=1), "utf-8")
    print(f"\naccepted {len(pending)}; review `git diff resources/` and commit")
