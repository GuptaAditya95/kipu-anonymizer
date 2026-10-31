"""Reasoning over review cases: a chat model through OpenRouter looks at the case and all its
connected datapoints and returns a verdict, a proposed fix and a question for the human.

Status: IMPLEMENTED. Falls back to a deterministic heuristic when OPENROUTER_API_KEY is not set or
the call fails, so the dashboard always has a proposal. Answers are cached per case.

Model: REASONING_MODEL (default below; check the exact slug in OpenRouter's model list).
Only MASKED text is sent (our replacements are placeholders), plus the suspicious span itself.
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import os
from pathlib import Path

import httpx

URL = os.environ.get("REASONING_URL", "https://openrouter.ai/api/v1/chat/completions")
MODEL = os.environ.get("REASONING_MODEL", "anthropic/claude-sonnet-4.5")
WORKERS = int(os.environ.get("REASONING_WORKERS", "6"))
TIMEOUT = float(os.environ.get("REASONING_TIMEOUT", "60"))

SECTORS_HINT = "Allowed occupation sectors are in resources/occupation_sectors.json."
SYSTEM = """You review the output of a PII anonymization pipeline for a Colombian fintech (Spanish text).
Our own replacements are shown as bracketed placeholders like [NOMBRE_1], [CEDULA], [EMAIL] and are fine.
You receive one suspicious span that the pipeline may have MISSED, the masked text around it and connected
datapoints (thread neighbours, other records with the same text, registry matches, people in the record).
Decide whether the span is personal data of a natural person. Organizations, brands, banks, places and
public figures are NOT personal data. Return ONLY a JSON object:
{"verdict": "pii" | "not_pii" | "uncertain",
 "kind": "person_name" | "nickname" | "id_number" | "contact" | "address" | "organization" | "place" | "occupation" | "other",
 "confidence": 0.0-1.0,
 "explanation": "one or two sentences citing the datapoints you used",
 "proposed_fix": {"action": "person_name" | "link" | "keep" | "identifier" | "occupation" | "none",
                  "value": "exact text to act on", "entity_key": "registry key if linking to a known person",
                  "sector": "only for occupation"},
 "question": "one short yes/no or choice question for the human reviewer"}"""


def heuristic(case: dict) -> dict:
    """Deterministic fallback used without a key (and as a sanity baseline)."""
    reason, span = case.get("reason"), case.get("span", "")
    reg = case.get("context", {}).get("registry", {})
    same = case.get("context", {}).get("same_text_elsewhere", {}).get("count", 0)
    if reg.get("in_keep_list"):
        return _r("not_pii", "organization", 0.8, "Span is on the keep-list.", "none", span,
                  "Confirm this is an organization/place and not a person?")
    if reason in ("possible_id_number",):
        return _r("pii", "id_number", 0.7, "A 6-12 digit number left in free text, not near money words or an internal id.",
                  "identifier", span, f"Is '{span}' a personal ID/phone number that must be replaced?")
    if reason in ("possible_contact", "possible_address"):
        return _r("pii", "contact" if "contact" in reason else "address", 0.75,
                  "Contact/address pattern not covered by our replacements.", "identifier", span,
                  f"Is '{span}' a real person's contact or address?")
    if reason == "surname_next_to_placeholder":
        return _r("pii", "person_name", 0.8, "A capitalized word right after one of our name placeholders: "
                  "likely a surname we left behind (partial replacement).", "person_name", span,
                  f"Is '{span}' the rest of a person's name (surname)?")
    if reason == "possible_nickname":
        return _r("uncertain", "nickname", 0.5, "Article + capitalized word ('la X', 'el X'): Colombian way to refer to a person by nickname.",
                  "link" if reg.get("people_with_this_first_name") else "person_name", span,
                  f"Is '{span}' a nickname of a person in this conversation?",
                  (case.get("context", {}).get("people_in_record") or [{}])[0].get("key"))
    if reason == "possible_name_unknown":
        return _r("uncertain", "person_name", 0.45, "Two capitalized words mid-sentence that are not in our name lists or keep-list (possibly a foreign name).",
                  "person_name", span, f"Is '{span}' a person's name?")
    if reason in ("possible_name", "possible_name_caps"):
        known = reg.get("known_person_form") or []
        if known:
            return _r("pii", "person_name", 0.8, f"Matches a registry person form ({known[0]}) but was left in place.",
                      "link", span, f"Is '{span}' the same person as {known[0]}?", known[0])
        conf = 0.75 if reg.get("first_token_is_first_name") and reg.get("has_surname_token") else 0.55
        return _r("pii" if conf > 0.6 else "uncertain", "person_name", conf,
                  f"Capitalized sequence starting with a known first name; seen in {same} other record(s).",
                  "person_name", span, f"Is '{span}' a real person's name?")
    if reason == "possible_first_name":
        ppl = reg.get("people_with_this_first_name") or []
        if len(ppl) == 1:
            return _r("uncertain", "nickname", 0.5, f"Single first name; exactly one known person has it ({ppl[0]}).",
                      "link", span, f"Does '{span}' refer to {ppl[0]}?", ppl[0])
        return _r("uncertain", "person_name", 0.4, f"Single first name; {len(ppl)} known people share it.",
                  "none", span, f"Is '{span}' a person's name here (and who)?")
    return _r("uncertain", "other", 0.3, "Flagged by the model score only.", "none", span,
              "Does this text still contain personal data?")


def _r(verdict, kind, conf, expl, action, value, question, entity_key=None, sector=None):
    return {"verdict": verdict, "kind": kind, "confidence": conf, "explanation": expl,
            "proposed_fix": {"action": action, "value": value, "entity_key": entity_key, "sector": sector},
            "question": question, "source": "heuristic"}


def _call(case: dict) -> dict | None:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        return None
    payload = {k: case[k] for k in ("span", "reason", "source", "field", "jev_p", "all_hits")}
    payload["masked_window"] = case["context"].get("window")
    payload["connected"] = {k: v for k, v in case["context"].items() if k != "window"}
    try:
        r = httpx.post(URL, timeout=TIMEOUT, headers={"Authorization": f"Bearer {key}"}, json={
            "model": MODEL, "temperature": 0, "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]})
        r.raise_for_status()
        txt = r.json()["choices"][0]["message"]["content"]
        start, end = txt.find("{"), txt.rfind("}")
        out = json.loads(txt[start:end + 1])
        out["source"] = MODEL
        return out
    except Exception:
        return None


def reason_all(cases: list[dict], state_dir: Path, progress=None) -> None:
    cache_p = state_dir / "reasoning_cache.json"
    cache = json.loads(cache_p.read_text("utf-8")) if cache_p.exists() else {}

    def work(c):
        if c["case_id"] in cache:
            return c["case_id"], cache[c["case_id"]]
        return c["case_id"], (_call(c) or heuristic(c))

    with cf.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for n, (cid, res) in enumerate(ex.map(work, cases), 1):
            cache[cid] = res
            if progress:
                progress(n, len(cases))
    for c in cases:
        c["reasoning"] = cache.get(c["case_id"])
    from pipeline.fileio import write_json
    write_json(cache_p, cache)
