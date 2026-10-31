"""Stage 4 - Detect PII spans in every string field.

Status: SKELETON (regex patterns drafted, not yet tuned/tested).

Layers, in order:
  a. regex for structured identifiers (below)
  b. registry matching on normalized text, longest form first
  c. Colombian first-name list for unknown third parties -> new entities
  d. keep-list (resources/keep_list.json): orgs, banks, merchants, places, public figures.
     A keep-list term is skipped UNLESS it sits inside a matched person name ("Santander").

TODO:
  - tune each regex against the real data and the 13 sample tests
  - normalization identical to scripts/validar.mjs (NFKC, casefold, accents, confusables)
"""
from __future__ import annotations

import re
from dataclasses import dataclass

PATTERNS = {
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),
    "email_spelled": re.compile(r"\b[\w.]+ arroba [\w]+ punto [\w]+(?: punto [\w]+)?\b", re.I),
    "cedula_dots": re.compile(r"\b\d{1,3}(?:\.\d{3}){2,3}\b"),
    "digits_long": re.compile(r"(?<!\d)\d{7,10}(?!\d)"),          # cedula or phone, classify by context
    "phone_co": re.compile(r"(?:\+?57[\s-]?)?3\d{2}[\s-]?\d{3}[\s-]?\d{4}\b"),
    "plate": re.compile(r"\b[A-Z]{3}[\s-]?\d{3}\b"),
    "passport": re.compile(r"\b[A-Z]{2}\d{6,7}\b"),
    "address": re.compile(
        r"\b(?:Calle|Cl|Carrera|Cra|Kr|Diagonal|Dg|Transversal|Tv|Avenida|Av)\.?\s*\d+[A-Z]?"
        r"(?:\s*Bis)?(?:\s*Sur)?\s*#\s*\d+[A-Z]?\s*-\s*\d+[^,;\n]*", re.I),
    "birth_iso": re.compile(r"\b(?:19|20)\d{2}-\d{2}-\d{2}\b"),   # only if matches a known birth date
    "slack_mention": re.compile(r"<@[A-Z0-9]+>"),
}


@dataclass
class Span:
    start: int
    end: int
    text: str
    kind: str            # email, cedula, phone, name, address, ...
    entity_key: str = ""  # filled by resolve


def detect_regex(text: str) -> list[Span]:
    spans = []
    for kind, rx in PATTERNS.items():
        for m in rx.finditer(text):
            spans.append(Span(m.start(), m.end(), m.group(0), kind))
    return spans


def detect(text: str, registry=None, keep_list=None) -> list[Span]:
    """TODO: combine regex + registry + name list, drop keep-list hits, resolve overlaps
    (longest span wins)."""
    return detect_regex(text)
