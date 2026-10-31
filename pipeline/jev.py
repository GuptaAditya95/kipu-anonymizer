"""Stage 4b (OPTIONAL, stretch) - Jev typed classification of leftover spans.

Status: NOT STARTED. Disabled unless JEV_API_KEY is set.

"Code finds, Jev decides, code replaces":
  input : leftover capitalized span + its sentence (not resolved by detect)
  ask   : type in {person, organization, place, other} with probability
  result: person (high) -> new entity; org/place (high) -> keep-list;
          low confidence -> mask if name-like, log for review
  cache : answers keyed by (span, context hash) in the pseudonym map -> deterministic
  never on the /anonymize hot path without cache; hard timeout; on failure skip.

Endpoint per TypeSafe docs: POST https://api.typesafe.ai/v1/systemone (model "jev-latest").
Verify the request format in the official docs before implementing.
"""
from __future__ import annotations

import os

ENABLED = bool(os.environ.get("JEV_API_KEY"))


def classify(span: str, context: str) -> tuple[str, float] | None:
    """TODO. Return (label, probability) or None when disabled/failed."""
    return None
