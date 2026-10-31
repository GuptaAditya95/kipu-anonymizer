"""Jev (TypeSafe System One decision model) through OpenRouter.

Status: IMPLEMENTED (client + cache). Disabled unless OPENROUTER_API_KEY is set.
Run `python scripts/jev_probe.py` once to confirm the key works and to see the exact
response shape for "choice" questions; adjust _parse_choice if it differs.

Why Jev: it returns a TYPED decision with probabilities (no free text), it's fast, and
output tokens are free. We only use it where rules can't decide, and every answer is cached,
so the same question is never asked twice: decisions stay deterministic across runs.

OpenRouter docs (verified 2026-10-31):
  POST https://openrouter.ai/api/v1/systemone
  Authorization: Bearer $OPENROUTER_API_KEY
  body: {"model": "jev-1.13" | "jev-latest", "state": <text or JSON>,
         "questions": {"<name>": {"type": "choice", "instructions": "...",
                                  "criteria": {"<option>": "<description>", ...}},
                       "<name2>": {"type": "noul", "instructions": "..."}}}
  response: {"model", "answers": {"<name>": {...}}, "usage": {"input_tokens", "cost"}}
  noul answer: {"type": "noul", "noul": 0.98}
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path

import httpx

URL = os.environ.get("JEV_URL", "https://openrouter.ai/api/v1/systemone")
MODEL = os.environ.get("JEV_MODEL", "jev-1.13")
TIMEOUT = float(os.environ.get("JEV_TIMEOUT", "4"))
MIN_CONFIDENCE = float(os.environ.get("JEV_MIN_CONFIDENCE", "0.6"))


def enabled() -> bool:
    return bool(os.environ.get("OPENROUTER_API_KEY"))


ENABLED = enabled()


class JevCache:
    """Persistent cache: question hash -> answer. Lives on the data volume."""

    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.data: dict = json.loads(path.read_text("utf-8")) if path.exists() else {}
        self.calls = 0
        self.tokens_in = 0
        self.cost = 0.0

    @staticmethod
    def key(*parts: str) -> str:
        return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:24]

    def get(self, k: str):
        return self.data.get(k)

    def put(self, k: str, v) -> None:
        with self.lock:
            self.data[k] = v
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), "utf-8")
            tmp.replace(self.path)


def _post(state, questions: dict, cache: JevCache | None) -> dict | None:
    try:
        r = httpx.post(URL, timeout=TIMEOUT, json={"model": MODEL, "state": state,
                                                  "questions": questions},
                       headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"})
        r.raise_for_status()
        body = r.json()
        if cache is not None:
            u = body.get("usage", {})
            cache.calls += 1
            cache.tokens_in += int(u.get("input_tokens", 0) or 0)
            cache.cost += float(u.get("cost", 0) or 0)
        return body.get("answers", {})
    except Exception:
        return None  # never break the pipeline: callers fall back to rules


def _parse_choice(ans: dict) -> tuple[str, float] | None:
    """Defensive parser: accepts {"choice": "x", "probability": p},
    {"choice": "x", "probabilities": {...}}, or {"probabilities": {...}}."""
    if not isinstance(ans, dict):
        return None
    probs = ans.get("probabilities") or ans.get("scores")
    pick = ans.get("choice") or ans.get("value")
    if isinstance(pick, dict):
        pick = pick.get("key") or pick.get("value")
    if isinstance(probs, dict) and probs:
        if not pick:
            pick = max(probs, key=probs.get)
        return str(pick), float(probs.get(pick, 0))
    if pick:
        return str(pick), float(ans.get("probability", ans.get("confidence", 1.0)))
    return None


def choose(instructions: str, state, options: dict[str, str],
           cache: JevCache | None = None) -> tuple[str, float] | None:
    """Ask Jev to pick one option. Returns (option_key, probability) or None.
    Cached by (instructions, state, options)."""
    k = JevCache.key("choice", instructions, json.dumps(state, ensure_ascii=False, sort_keys=True),
                     json.dumps(options, ensure_ascii=False, sort_keys=True))
    if cache is not None and (hit := cache.get(k)) is not None:
        return tuple(hit) if hit else None
    if not enabled():
        return None
    answers = _post(state, {"q": {"type": "choice", "instructions": instructions,
                                  "criteria": options}}, cache)
    res = _parse_choice(answers.get("q")) if answers else None
    if res and res[0] not in options:
        res = None
    if cache is not None and answers is not None:
        cache.put(k, list(res) if res else [])
    return res


def yes_probability(instructions: str, state, cache: JevCache | None = None) -> float | None:
    """Ask a yes/no (noul) question. Returns probability of yes or None."""
    k = JevCache.key("noul", instructions, json.dumps(state, ensure_ascii=False, sort_keys=True))
    if cache is not None and (hit := cache.get(k)) is not None:
        return hit
    if not enabled():
        return None
    answers = _post(state, {"q": {"type": "noul", "instructions": instructions}}, cache)
    val = None
    if answers and isinstance(answers.get("q"), dict):
        val = answers["q"].get("noul")
    if cache is not None and val is not None:
        cache.put(k, float(val))
    return None if val is None else float(val)


def classify_span(span: str, sentence: str, cache: JevCache | None = None):
    """Leftover capitalized span: person / organization / place / other."""
    return choose(
        "In this Spanish customer-support text, what is the highlighted span?",
        {"span": span, "sentence": sentence},
        {"person": "A natural person's name or surname",
         "organization": "A company, bank, brand, government body or product",
         "place": "A city, neighborhood, street, landmark or region",
         "other": "Anything else (common word, title, acronym)"},
        cache)
