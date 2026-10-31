"""Stage 5 - Assign pseudonyms: deterministic, same category, persisted.

Status: SKELETON (core HMAC + map persistence implemented; generators are simple).

Rules (from the brief):
  - same person -> same pseudonym everywhere, across runs AND after the data grows
  - different people (even same name) -> different pseudonyms (check uniqueness)
  - same category: name of same gender/region, email like email (same provider + pattern),
    cedula with valid format far from original, valid +57 mobile, plausible address/date

TODO:
  - fill resources/names_*.json with Colombian first names (by gender) and surnames
  - email pattern detection (initial+surname, nombre.apellido, etc.)
  - cedula: same length and leading digits, but change the rest (see sample)
  - phone: keep 3xx prefix family, random rest
  - address / birth date / plate / passport generators
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
from pathlib import Path

RES = Path(__file__).resolve().parent.parent / "resources"


def _load(name: str, default):
    p = RES / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


class PseudonymMap:
    """entity stable key -> {"entity_id", "pseudonyms": {...}}. Saved to disk."""

    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.salt = os.environ.get("SALT", "change-me").encode()
        self.data: dict = {"entities": {}, "used_names": []}
        if path.exists():
            self.data = json.loads(path.read_text(encoding="utf-8"))
        self.used = set(self.data.get("used_names", []))
        self.female = _load("names_female.json", ["Sofía", "Marcela", "Doris", "Liliana"])
        self.male = _load("names_male.json", ["Hugo", "Andrés", "Mauricio", "Julián"])
        self.surnames = _load("surnames.json", ["Zambrano", "Pardo", "Lozano", "Cano"])

    def seed(self, key: str, counter: int = 0) -> int:
        mac = hmac.new(self.salt, f"{key}#{counter}".encode(), hashlib.sha256).digest()
        return int.from_bytes(mac[:8], "big")

    def name_for(self, key: str, gender: str = "F") -> str:
        names = self.female if gender == "F" else self.male
        for counter in range(1000):
            s = self.seed(key, counter)
            full = f"{names[s % len(names)]} {self.surnames[(s >> 16) % len(self.surnames)]}"
            if full not in self.used:
                return full
        raise RuntimeError("name pool exhausted - add more names to resources/")

    def get_or_create(self, key: str, gender: str = "F") -> dict:
        """Return the stored pseudonyms for this entity, creating them once."""
        with self.lock:
            ent = self.data["entities"].get(key)
            if ent:
                return ent
            name = self.name_for(key, gender)
            self.used.add(name)
            ent = {"entity_id": f"p_{len(self.data['entities']) + 1:04d}",
                   "pseudonyms": {"name": name}}
            self.data["entities"][key] = ent
            return ent

    def save(self) -> None:
        with self.lock:
            self.data["used_names"] = sorted(self.used)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)
