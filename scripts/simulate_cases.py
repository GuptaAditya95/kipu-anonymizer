"""Inject difficult cases into a COPY of the output and run the verifier on it.

  python scripts/simulate_cases.py            # uses data/output + data/state, writes to data/sim_review/

Each injected snippet is a leftover the pipeline could plausibly miss (or a trap that is NOT PII).
The script reports, per case, whether the verifier raised it and what the reasoning proposed,
so it doubles as a labeled test set for tuning the checks (recall on PII, false alarms on non-PII).
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pipeline.env import load_env  # noqa: E402
load_env()
DATA = Path(os.environ.get("DATA_DIR", ROOT / "data"))

CASES = [  # (label, is_pii, text appended to a chat message)
    ("nickname_of_client", True, "Gracias, le aviso a la Mafe para que pague hoy."),
    ("rare_foreign_name", True, "La transferencia la hizo Siobhan O'Neill desde Dublín."),
    ("caps_name_statement", True, "TITULAR: MARIA FERNANDA LOPEZ GIRALDO"),
    ("third_party_relative", True, "mi tía Gloria Patiño también tiene cuenta con ustedes"),
    ("obfuscated_email", True, "escríbanme a laura.mejia [at] gmail [dot] com por favor"),
    ("account_number", True, "consígnenme a la cuenta de ahorros 4512 8890 3321, gracias"),
    ("address_short", True, "vivo en la Cra 7 # 45-12 apto 301"),
    ("initial_surname", True, "Transferencia recibida de J. Castrillón"),
    ("bare_cedula_words", True, "mi documento es el 1.020.304.050 por si lo necesitan"),
    ("brand_like_name", False, "me tomé un tinto en Juan Valdez antes de ir al banco"),
    ("place_like_name", False, "nos vemos en la Plaza Botero a las 3"),
    ("public_figure", False, "como dice Shakira, las caderas no mienten jaja"),
    ("amount_not_id", False, "el cupo aprobado es de 8.000.000 pesos"),
    ("internal_id", False, "el caso quedó como PQR-2026-0451 y la cuenta CL-12538"),
]


def main():
    src_out, src_state = DATA / "output", DATA / "state"
    sim = DATA / "sim_review"
    if sim.exists():
        shutil.rmtree(sim)
    shutil.copytree(src_out, sim / "output")
    (sim / "state").mkdir(parents=True)
    for f in ("pseudonym_map.json",):
        shutil.copy(src_state / f, sim / "state" / f)
    lines = (sim / "output" / "chats.jsonl").read_text("utf-8").splitlines()
    targets = {}
    for i, (label, _, snippet) in enumerate(CASES):
        r = json.loads(lines[100 + i * 37])
        r["text"] = r["text"] + " " + snippet
        lines[100 + i * 37] = json.dumps(r, ensure_ascii=False)
        targets[r["id"]] = (label, snippet)
    (sim / "output" / "chats.jsonl").write_text("\n".join(lines) + "\n", "utf-8")

    from pipeline import knowledge, verify
    from pipeline.run import State
    st = State()
    res = verify.verify(sim / "output", sim / "state", st.processor.reg, knowledge.load())
    by_rid = {}
    for c in res["cases"]:
        by_rid.setdefault(c["record_id"], []).append(c)
    tp = fn = fp = tn = 0
    print(f"{'case':24} {'PII?':5} {'raised':7} {'span':34} {'reasoning':10} proposal")
    for label, is_pii, snippet in CASES:
        rid = next(k for k, v in targets.items() if v[0] == label)
        hits = [c for c in by_rid.get(rid, []) if c["span"] and c["span"] in snippet or
                (c["span"] and snippet.find(c["span"].split()[0]) >= 0)]
        raised = bool(hits)
        c = hits[0] if hits else None
        ai = (c or {}).get("reasoning") or {}
        fix = ai.get("proposed_fix", {})
        print(f"{label:24} {str(is_pii):5} {str(raised):7} {(c or {}).get('span', '')[:34]:34} "
              f"{ai.get('verdict', ''):10} {fix.get('action', '')} {fix.get('entity_key') or ''}")
        tp += is_pii and raised
        fn += is_pii and not raised
        fp += (not is_pii) and raised
        tn += (not is_pii) and not raised
    print(f"\nrecall on injected PII: {tp}/{tp + fn} · false alarms on traps: {fp}/{fp + tn}")
    print(f"cases written to {sim / 'state' / 'cases.json'}  ({len(res['cases'])} total incl. real ones)")


if __name__ == "__main__":
    main()
