"""Generalization of quasi-identifiers, keeping the data trainable.

Status: IMPLEMENTED (barrio, occupation with Jev fallback and learning, year phrases).

Design goals:
  - Barrio (table): replaced by ANOTHER barrio of the SAME ZONE of the same city
    (resources/barrios_by_city.json), so the geography stays realistic for training.
    Deterministic: the choice comes from a seed. From the sample: Granada -> Santa Mónica
    (both north Cali).
  - Barrio (free text): replaced by the CITY ("en Granada" -> "en Cali"), as the sample does.
  - Occupation -> sector from a CLOSED list (resources/occupation_sectors.json):
      1. exact map         resources/occupation_sector.json
      2. learned map       DATA_DIR/state/learned_occupations.json (Jev decisions, persisted)
      3. keyword rules     resources/occupation_keywords.json
      4. Jev choice        among the existing sectors (only if OPENROUTER_API_KEY is set)
                           -> confident answer is stored in the learned map + a proposed
                              keyword, for human review (scripts/promote_learned.py)
      5. fallback          "trabajador independiente" (logged as a miss for the review report)
  - Year in identifying phrases ("entré en 2025") -> "hace unos años".
"""
from __future__ import annotations

import json
import os
import re
import threading
import unicodedata
from pathlib import Path

from pipeline import jev

RES = Path(__file__).resolve().parent.parent / "resources"
DATA = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
LEARNED = DATA / "state" / "learned_occupations.json"
FALLBACK_SECTOR = "trabajador independiente"


def _load(name):
    return json.loads((RES / name).read_text(encoding="utf-8"))


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFD", s.strip().lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


# ------------------------------------------------------------------ barrios
class Barrios:
    def __init__(self):
        raw = _load("barrios_by_city.json")
        self.zones = {city: zones for city, zones in raw.items() if not city.startswith("_")}
        self.index = {}  # (city_fold, barrio_fold) -> (city, zone)
        for city, zones in self.zones.items():
            for zone, barrios in zones.items():
                for b in barrios:
                    self.index[(_fold(city), _fold(b))] = (city, zone)

    def all_barrios(self) -> set[str]:
        return {b for zones in self.zones.values() for bs in zones.values() for b in bs}

    def nearby(self, barrio: str, city: str, seed: int) -> str:
        """Another barrio of the same zone; else same city; else unchanged."""
        hit = self.index.get((_fold(city), _fold(barrio)))
        if hit:
            c, zone = hit
            options = [b for b in self.zones[c][zone] if _fold(b) != _fold(barrio)]
        else:
            c = next((k for k in self.zones if _fold(k) == _fold(city)), None)
            options = ([b for bs in self.zones[c].values() for b in bs if _fold(b) != _fold(barrio)]
                       if c else [])
        return options[seed % len(options)] if options else barrio


def barrio_in_text(text: str, barrio: str, city: str) -> str:
    """Free text: the barrio becomes the city ('en Granada' -> 'en Cali')."""
    return re.sub(rf"(?<!\w){re.escape(barrio)}(?!\w)", city, text)


# ------------------------------------------------------------------ occupations
class Occupations:
    def __init__(self, jev_cache: "jev.JevCache | None" = None):
        self.exact = {_fold(k): v for k, v in _load("occupation_sector.json")["map"].items()}
        self.sectors = _load("occupation_sectors.json")["sectors"]
        self.keywords = [(_fold(k), v) for k, v in _load("occupation_keywords.json")["rules"]]
        self.lock = threading.Lock()
        self.learned = json.loads(LEARNED.read_text("utf-8")) if LEARNED.exists() else {}
        self.jev_cache = jev_cache
        self.misses: list[str] = []

    def to_sector(self, occupation: str) -> tuple[str, str]:
        """Return (sector, source) where source in exact|learned|keyword|jev|fallback."""
        key = _fold(occupation)
        if not key:
            return occupation, "empty"
        if key in self.exact:
            return self.exact[key], "exact"
        if key in self.learned:
            return self.learned[key]["sector"], "learned"
        for kw, sector in self.keywords:
            if kw in key:
                return sector, "keyword"
        res = jev.choose(
            "Which sector best generalizes this Colombian occupation? Pick the closest.",
            {"occupation": occupation},
            {s: s for s in self.sectors},
            self.jev_cache,
        )
        if res and res[1] >= jev.MIN_CONFIDENCE:
            self._learn(key, occupation, res[0], res[1])
            return res[0], "jev"
        self.misses.append(occupation)
        return FALLBACK_SECTOR, "fallback"

    def _learn(self, key: str, original: str, sector: str, prob: float) -> None:
        """Persist the decision and propose a keyword (first word stem) for review."""
        stem = key.split()[0][:-1] if len(key.split()[0]) > 5 else key.split()[0]
        with self.lock:
            self.learned[key] = {"original": original, "sector": sector,
                                 "probability": round(prob, 3), "proposed_keyword": stem,
                                 "status": "pending_review"}
            LEARNED.parent.mkdir(parents=True, exist_ok=True)
            tmp = LEARNED.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.learned, ensure_ascii=False, indent=1), "utf-8")
            tmp.replace(LEARNED)


# ------------------------------------------------------------------ years
YEAR_PHRASE = re.compile(
    r"\b(entr[eé] con ustedes en|entr[eé] en|desde|me vincul[eé] en|abr[ií] la cuenta en)\s+(20\d{2})\b",
    re.I)


def generalize_years(text: str) -> str:
    """'entré con ustedes en 2025' -> 'entré con ustedes en hace unos años' (as in the sample)."""
    return YEAR_PHRASE.sub(lambda m: f"{m.group(1)} hace unos años", text)
