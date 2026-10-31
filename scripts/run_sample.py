"""Run the full pipeline on the sample dataset and compare with its ideal output.

  python scripts/run_sample.py [path/to/sample]      (default: data/sample)

Converts sample/input into the raw layout, runs the pipeline offline in a temporary DATA_DIR,
runs the official validator, the 13 sample tests and a similarity check vs expected_output.
"""
from __future__ import annotations

import difflib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sample = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "data" / "sample")
tmp = Path(tempfile.mkdtemp(prefix="kipu_sample_"))
os.environ["DATA_DIR"] = str(tmp)
raw = tmp / "raw"

# --- layout: sample/input -> raw
shutil.copytree(sample / "input" / "chats", raw / "chats")
(raw / "docs").mkdir(parents=True)
idx = json.loads((sample / "input" / "docs" / "index.json").read_text("utf-8"))
shutil.copy(sample / "input" / "docs" / "index.json", raw / "docs" / "index.json")
for d in idx:
    shutil.copy(sample / "input" / "docs" / d["filename"], raw / "docs" / f"{d['id']}.{d['format']}")
(raw / "tables").mkdir()
for f in (sample / "input" / "tables").iterdir():
    if f.suffix in (".csv", ".xml"):
        shutil.copy(f, raw / "tables" / f.name)
shutil.copy(sample / "record_index.json", raw / "record_index.json")

sys.path.insert(0, str(ROOT))
from pipeline import run as runmod  # noqa: E402

summary = runmod.run_pipeline(None, None, runmod.State(), offline=True)
print(json.dumps({k: summary[k] for k in ("records", "entities", "leaks", "seconds")}, ensure_ascii=False, indent=1))
out = tmp / "output"

print("\n== validator")
subprocess.run(["node", str(ROOT / "scripts" / "validar.mjs"), str(out), "--public", str(sample)])

print("\n== sample tests")
subprocess.run([sys.executable, str(ROOT / "scripts" / "score_local.py"), str(out), str(sample / "tests_sample.json")])

print("\n== similarity vs expected_output (non-personal text should be ~identical)")
exp = {}
for src in ("chats", "docs", "tables"):
    for line in (sample / "expected_output" / f"{src}.jsonl").read_text("utf-8").splitlines():
        r = json.loads(line)
        exp[r["id"]] = r
ratios = []
for src in ("chats", "docs", "tables"):
    for line in (out / f"{src}.jsonl").read_text("utf-8").splitlines():
        r = json.loads(line)
        e = exp.get(r["id"])
        if not e:
            continue
        a = json.dumps({k: v for k, v in r.items()}, ensure_ascii=False)
        b = json.dumps({k: v for k, v in e.items()}, ensure_ascii=False)
        ratio = difflib.SequenceMatcher(None, a, b).ratio()
        ratios.append(ratio)
        if ratio < 0.9:
            print(f"  {r['id'][:20]:20} {ratio:.2f}\n    ours: {a[:300]}\n    want: {b[:300]}")
print(f"  mean similarity {sum(ratios)/len(ratios):.3f} over {len(ratios)} records")
print(f"\noutput in {out}")
