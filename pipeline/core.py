"""Shared core used by BOTH /run (batch) and /anonymize (live).

Stages 4-6: detect -> resolve -> pseudonymize -> replace/generalize.
Keeping one code path guarantees a person gets the same pseudonym in output/ and live.

Status: SKELETON - currently a pass-through (returns records unchanged) so the service,
the API contract and the validator can be tested end to end before detection exists.
DO NOT spend an evaluation run on the pass-through: it leaks everything.
"""
from __future__ import annotations

import copy


def anonymize_records(source: str, records: list[dict], state) -> tuple[list[dict], list[dict]]:
    """Return (anonymized_records, entities_involved).

    state: object with .registry and .pmap (PseudonymMap)
    TODO:
      1. for every string field (author, text, attachments[].filename, title, body,
         metadata values, filename, columns values): detect spans
      2. resolve each span to a Person (strong ids first: cedula/email/phone/cliente_id;
         then unambiguous name; same name + different id -> new entity)
      3. state.pmap.get_or_create(person.key, person.gender)
      4. replace spans in place keeping the form (first->first, CAPS->CAPS, slug->slug,
         dotted cedula->dotted); generalize quasi-identifiers
      5. collect entities with originals, pseudonyms, records
    """
    out = copy.deepcopy(records)
    entities: list[dict] = []
    return out, entities
