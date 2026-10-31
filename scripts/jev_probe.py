"""Check that Jev works through OpenRouter and print the raw response shapes.

  OPENROUTER_API_KEY=sk-or-... python scripts/jev_probe.py

Prints the raw JSON for one "choice" and one "noul" question. If the choice answer shape
differs from what pipeline/jev.py::_parse_choice expects, adjust the parser.
"""
import json
import os
import sys

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import jev  # noqa: E402

key = os.environ.get("OPENROUTER_API_KEY")
if not key:
    sys.exit("set OPENROUTER_API_KEY")

body = {
    "model": jev.MODEL,
    "state": {"occupation": "auxiliar de vuelo"},
    "questions": {
        "sector": {"type": "choice",
                   "instructions": "Which sector best generalizes this Colombian occupation?",
                   "criteria": {"profesional de la aviación": "aviation",
                                "profesional de la salud": "health",
                                "trabajador del comercio": "commerce"}},
        "is_person": {"type": "noul", "instructions": "Is 'Santander' here a person's surname?"},
    },
}
r = httpx.post(jev.URL, json=body, timeout=20, headers={"Authorization": f"Bearer {key}"})
print(r.status_code)
print(json.dumps(r.json(), indent=1, ensure_ascii=False))
ans = r.json().get("answers", {})
print("\nparsed choice:", jev._parse_choice(ans.get("sector")))
