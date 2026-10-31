"""Human-approved knowledge that the next run applies.

Status: IMPLEMENTED.

Stored at DATA_DIR/state/knowledge.json (persists with the data, never in git because it can
contain real names). Answers from the review dashboard land here; State.build applies it.

{
  "person_names": [{"text": "Siobhan O'Neill", "entity_key": null}],   # always a person
  "links":        [{"text": "Mafe", "entity_key": "cl:CL-123"}],        # a form of a known person
  "keep":         ["Café Velvet"],                                       # org/place/brand, not PII
  "identifiers":  [{"text": "AB-99887", "kind": "identifier"}],          # literal ids to replace
  "occupations":  {"astrólogo": "trabajador independiente"},
  "answers":      [ ...audit trail of every human decision... ]
}
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

DATA = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
PATH = DATA / "state" / "knowledge.json"
_LOCK = threading.Lock()
EMPTY = {"person_names": [], "links": [], "keep": [], "identifiers": [], "occupations": {}, "answers": []}


def load(path: Path | None = None) -> dict:
    p = path or PATH
    if p.exists():
        k = json.loads(p.read_text("utf-8"))
        return {**{key: (v.copy() if hasattr(v, "copy") else v) for key, v in EMPTY.items()}, **k}
    return json.loads(json.dumps(EMPTY))


def save(k: dict, path: Path | None = None) -> None:
    p = path or PATH
    from pipeline.fileio import write_json
    with _LOCK:
        write_json(p, k)


def record_answer(case: dict, answer: dict, path: Path | None = None) -> dict:
    """Turn a human answer on a case into knowledge. answer = {action, value?, entity_key?, sector?, note?}"""
    k = load(path)
    action, value = answer.get("action"), (answer.get("value") or case.get("span") or "").strip()
    if action == "person_name" and value:
        if not any(x["text"] == value for x in k["person_names"]):
            k["person_names"].append({"text": value, "entity_key": answer.get("entity_key")})
    elif action == "link" and value and answer.get("entity_key"):
        k["links"].append({"text": value, "entity_key": answer["entity_key"]})
    elif action == "keep" and value:
        if value not in k["keep"]:
            k["keep"].append(value)
    elif action == "identifier" and value:
        if not any(x["text"] == value for x in k["identifiers"]):
            k["identifiers"].append({"text": value, "kind": answer.get("kind", "identifier")})
    elif action == "occupation" and value and answer.get("sector"):
        k["occupations"][value.lower()] = answer["sector"]
    k["answers"].append({"case_id": case.get("case_id"), "record_id": case.get("record_id"),
                         "span": case.get("span"), "action": action, "value": value,
                         "entity_key": answer.get("entity_key"), "sector": answer.get("sector"),
                         "ai_verdict": (case.get("reasoning") or {}).get("verdict"),
                         "note": answer.get("note", ""), "reviewer": answer.get("reviewer", "human"),
                         "ts": time.strftime("%Y-%m-%dT%H:%M:%S")})
    save(k, path)
    return k


def apply_to_registry(reg, k: dict) -> None:
    """Called after Registry.build: human-confirmed names and links become name forms."""
    from pipeline.text import fold
    for item in k.get("links", []):
        p = reg.people.get(item.get("entity_key"))
        if not p:
            continue
        t = item["text"].strip()
        if " " in t:
            reg.by_form.setdefault(fold(t), set()).add(p.key)
        else:
            p.nicknames.add(t)
    for item in k.get("person_names", []):
        t = item["text"].strip()
        if " " in t:
            key = item.get("entity_key") if item.get("entity_key") in reg.people else f"txt:{fold(t)}"
            reg.by_form.setdefault(fold(t), set()).add(key)
