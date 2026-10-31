"""Simulate the evaluator against a running service (local or deployed).

  python scripts/simulate_judge.py --service http://localhost:9000/v1 \
         --sources http://localhost:9100/v1 --token test [--raw data/raw]

Sequence (same as the real evaluator): GET /health -> POST /run -> poll GET /run/{id} ->
GET /output/* -> validator -> public tests -> POST /anonymize live checks:
  1. a new message naming a known client  -> same pseudonym as in output/
  2. a new person                         -> new, unique pseudonym
  3. two people with the same name, different cédulas -> two pseudonyms
  4. 50 records                           -> < 60 s
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ap = argparse.ArgumentParser()
ap.add_argument("--service", default="http://localhost:9000/v1")
ap.add_argument("--sources", default="http://localhost:9100/v1")
ap.add_argument("--token", default="test")
ap.add_argument("--raw", default="data/raw")
a = ap.parse_args()
ROOT = Path(__file__).resolve().parent.parent
H = {"Authorization": "Bearer judge-token"}
c = httpx.Client(timeout=120, headers=H)
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")


r = c.get(f"{a.service}/health")
check("health", r.status_code == 200 and r.json().get("status") == "ok", r.text[:120])

t0 = time.time()
r = c.post(f"{a.service}/run", json={"sources_url": a.sources, "token": a.token})
check("run accepted (202)", r.status_code == 202, r.text[:80])
job = r.json()["job_id"]
while True:
    st = c.get(f"{a.service}/run/{job}").json()
    if st["status"] in ("done", "failed"):
        break
    time.sleep(3)
check("run done", st["status"] == "done", json.dumps({k: st.get(k) for k in ("records", "cost", "leaks", "error")})[:300])
print("   changes:", json.dumps(st.get("changes"))[:300])
print("   stability:", st.get("stability"))

out = Path(tempfile.mkdtemp(prefix="judge_out_"))
for f in ("chats.jsonl", "docs.jsonl", "tables.jsonl", "entities.json"):
    (out / f).write_bytes(c.get(f"{a.service}/output/{f}").content)
pub = Path(tempfile.mkdtemp(prefix="judge_pub_"))
(pub / "record_index.json").write_bytes(
    httpx.get(f"{a.sources}/record_index", headers={"Authorization": f"Bearer {a.token}"}).content)
v = subprocess.run(["node", str(ROOT / "scripts" / "validar.mjs"), str(out), "--public", str(pub)],
                   capture_output=True, text=True)
check("validator", v.stdout.startswith("OK"), v.stdout.strip().splitlines()[0] if v.stdout else v.stderr[:200])
tests = Path(a.raw) / "tests_public.json"
if tests.exists():
    s = subprocess.run([sys.executable, str(ROOT / "scripts" / "score_local.py"), str(out), str(tests)],
                       capture_output=True, text=True)
    print(s.stdout)

# ---- live /anonymize
ents = json.loads((out / "entities.json").read_text("utf-8"))["entities"]
tables = [json.loads(l) for l in (Path(a.raw).parent / "output" / "tables.jsonl").read_text("utf-8").splitlines()] \
    if False else None
raw_clients = {}
import csv, io  # noqa: E401
rows = list(csv.DictReader(io.StringIO((Path(a.raw) / "tables" / "clientes.csv").read_bytes().decode("latin-1")), delimiter=";"))
known = rows[0]
known_ent = next(e for e in ents if f"clientes:{known['cliente_id']}" in e["records"] and not e.get("kept"))
msg = {"id": "live-1", "thread_id": "soporte-cobros:9999999999.000100", "author": known["nombre_completo"], "ts": "9999999999.000200",
       "text": f"Hola, soy {known['nombre_completo']}, mi correo es {known['correo']}.", "attachments": []}
t1 = time.time()
r = c.post(f"{a.service}/anonymize", json={"source": "chats", "records": [msg]})
rec = r.json()["records"][0]
check("live: known client keeps pseudonym", known_ent["pseudonyms"]["name"].split()[0] in rec["text"]
      and known["nombre_completo"] not in rec["text"] and known["correo"] not in rec["text"],
      f"-> {rec['text'][:120]} (expected {known_ent['pseudonyms']['name']})")

new = {"id": "live-2", "thread_id": "soporte-cobros:9999999999.000300", "author": "Gertrudis Malaver Quiñones",
       "ts": "9999999999.000300", "text": "Buenas, me llamo Gertrudis Malaver Quiñones y mi cédula es 52.987.654", "attachments": []}
r = c.post(f"{a.service}/anonymize", json={"source": "chats", "records": [new]})
body = r.json()
names_out = {e["pseudonyms"].get("name") for e in ents if not e.get("kept")} - {None}
new_names = [e["pseudonyms"]["name"] for e in body["entities"] if e["pseudonyms"].get("name")]
check("live: new person gets a new unique pseudonym", "Gertrudis" not in body["records"][0]["text"]
      and "52.987.654" not in body["records"][0]["text"] and new_names and not (set(new_names) & names_out),
      f"-> {body['records'][0]['text'][:120]}")

ns = [{"id": "live-3", "table": "clientes", "columns": {**known, "cliente_id": "CL-99901", "num_doc": "1000111222", "correo": "otra.persona@gmail.com"}},
      {"id": "live-4", "table": "clientes", "columns": {**known, "cliente_id": "CL-99902", "num_doc": "1000333444", "correo": "otra2@gmail.com"}}]
r = c.post(f"{a.service}/anonymize", json={"source": "tables", "records": ns})
n1, n2 = (x["columns"]["nombre_completo"] for x in r.json()["records"])
check("live: namesakes get two pseudonyms", n1 != n2 and known["nombre_completo"] not in (n1, n2), f"-> {n1} / {n2}")

batch = [dict(msg, id=f"live-b{i}") for i in range(50)]
t2 = time.time()
r = c.post(f"{a.service}/anonymize", json={"source": "chats", "records": batch})
dt = time.time() - t2
check("live: 50 records < 60 s", r.status_code == 200 and dt < 60, f"({dt:.1f}s)")

print(f"\n{sum(ok for _, ok in results)}/{len(results)} checks passed · run took {time.time() - t0:.0f}s · output in {out}")
