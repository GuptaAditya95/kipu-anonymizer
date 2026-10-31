"""Deterministic, same-category pseudonyms, persisted across runs.

Status: IMPLEMENTED.

Every entity has a stable key (cl:CL-123, kp:KP-005, uid:U.., txt:<name>, ...).
seed = HMAC_SHA256(SALT, key|purpose|counter) drives every choice, and the result is stored in
the map (DATA_DIR/state/pseudonym_map.json). A person keeps the same pseudonym across runs,
restarts and the mid-event data growth. Different entities never share a name or an email,
and a pseudonym never equals a real name form (that would look like a leak).

Formats follow the sample's ideal output:
  name    same gender, 1-2 first names + 2 surnames (name = first + surname1)
  email   same provider, same pattern as the original (mcastano@ -> szambrano@)
  cedula  same length, same first 2 digits, the rest changed (>= 4 positions differ)
  phone   valid CO mobile, same first 2 digits, same separators/+57 as the original
  address same street type, new numbers; birth date shifted 1-4 years; plate/passport same shape
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import os
import re
import threading
from pathlib import Path

from pipeline.text import fold, strip_accents

RES = Path(__file__).resolve().parent.parent / "resources"


def _load(name):
    return json.loads((RES / name).read_text(encoding="utf-8"))


class PseudonymMap:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        self.salt = os.environ.get("SALT", "dev-salt-change-me").encode()
        self.female = _load("names_female.json")
        self.male = _load("names_male.json")
        self.surnames = _load("surnames.json")
        self.data = {"entities": {}, "next_id": 1}
        if path.exists():
            self.data = json.loads(path.read_text(encoding="utf-8"))
        self.reserved: set[str] = set()   # folded real name forms that pseudonyms must avoid
        self.reserved_values: set[str] = set()  # canonical real ids (emails, digits, dates)
        self._index()

    def _index(self):
        self.used_names = {fold(e["first"] + " " + e["s1"]) for e in self.data["entities"].values()}
        self.used_values = {v for e in self.data["entities"].values() for v in e["values"].values()}

    # ------------------------------------------------------------- seeds
    def seed(self, key: str, purpose: str, counter: int = 0) -> int:
        mac = hmac.new(self.salt, f"{key}|{purpose}|{counter}".encode(), hashlib.sha256).digest()
        return int.from_bytes(mac[:8], "big")

    def reserve(self, real_forms: set[str], real_values: set[str] = frozenset()) -> None:
        self.reserved |= {fold(f) for f in real_forms}
        self.reserved_values |= {_canon(v) for v in real_values}

    # ------------------------------------------------------------- entities
    def get(self, key: str) -> dict | None:
        return self.data["entities"].get(key)

    def entity(self, key: str, gender: str = "F", n_first: int = 1,
               avoid: set[str] | None = None) -> dict:
        """Return the entity profile for `key`, creating it once."""
        with self.lock:
            ent = self.data["entities"].get(key)
            if ent and not self._name_collides(ent):
                return ent
            old_ent = ent  # None, or an old pseudonym that now equals a newly arrived real name
            names = self.female if gender == "F" else self.male
            avoid_f = {fold(a) for a in (avoid or set())}
            for c in range(5000):
                s = self.seed(key, "name", c)
                k = 2 if (n_first >= 2 or s % 3 == 0) else 1
                first = [names[(s >> (8 * i)) % len(names)] for i in range(k)]
                if len(set(first)) < k:
                    continue
                s1 = self.surnames[(s >> 20) % len(self.surnames)]
                s2 = self.surnames[(s >> 36) % len(self.surnames)]
                fn = " ".join(first)
                full_short = fold(f"{fn} {s1}")
                cand = {"first": fn, "s1": s1, "s2": s2}
                if (s1 == s2 or full_short in self.used_names or self._name_collides(cand)
                        or fold(s1) in avoid_f or fold(s2) in avoid_f):
                    continue
                break
            # id from a hash of the stable key: identical on any fresh rebuild, order-independent
            eid = "p_" + hashlib.sha256(self.salt + key.encode()).hexdigest()[:10]
            ent = {"entity_id": eid, "gender": gender,
                   "first": fn, "s1": s1, "s2": s2, "values": old_ent["values"] if old_ent else {}}
            self.data["next_id"] += 1
            self.data["entities"][key] = ent
            self.used_names.add(full_short)
            return ent

    def _name_collides(self, ent) -> bool:
        """True if any multi-word form we would write equals a real person's name form."""
        f1 = ent["first"].split()[0]
        forms = {f"{ent['first']} {ent['s1']}", f"{ent['first']} {ent['s1']} {ent['s2']}",
                 f"{f1} {ent['s1']}", f"{f1} {ent['s1']} {ent['s2']}"}
        return any(fold(x) in self.reserved for x in forms)

    @staticmethod
    def name(ent) -> str:
        return f"{ent['first']} {ent['s1']}"

    @staticmethod
    def full(ent) -> str:
        return f"{ent['first']} {ent['s1']} {ent['s2']}"

    # ------------------------------------------------------------- values
    def value(self, key: str, kind: str, original: str, make) -> str:
        """Stable pseudonym for (entity, kind, original value)."""
        ent = self.data["entities"][key]
        vk = f"{kind}|{original}"
        with self.lock:
            if vk in ent["values"]:
                old = ent["values"][vk]
                if _canon(old) not in self.reserved_values:  # still safe -> keep (stability)
                    return old
                self.used_values.discard(old)  # collides with newly arrived real data -> redo
            for c in range(200):
                v = make(self.seed(key, vk, c))
                if c and "@" in v:  # pattern-based emails ignore the seed: add digits on retries
                    loc, _, dom = v.partition("@")
                    v = f"{loc}{self.seed(key, vk, c) % 90 + 10}@{dom}"
                if v != original and v not in self.used_values and _canon(v) not in self.reserved_values:
                    break
            ent["values"][vk] = v
            self.used_values.add(v)
            return v

    def save(self) -> None:
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)


def _canon(v: str) -> str:
    v = v.strip().lower()
    d = re.sub(r"\D", "", v)
    if "@" in v or not d or re.search(r"[a-z]", v) and len(d) < 6:
        return fold(v)
    return d[-10:] if len(d) >= 6 and not re.match(r"^\d{4}-\d{2}-\d{2}$", v) else v


# ----------------------------------------------------------------- generators
def _reformat(template: str, new_digits: str) -> str:
    """Put new digits into the original's layout (keeps dots, spaces, +57, dashes)."""
    it = iter(new_digits)
    return "".join(next(it, "0") if ch.isdigit() else ch for ch in template)


def gen_cedula(original: str, seed: int) -> str:
    d = re.sub(r"\D", "", original)
    keep = d[:2]
    rest = "".join(str((seed >> (3 * i)) % 10) for i in range(len(d) - 2))
    new = keep + rest
    if sum(a != b for a, b in zip(new, d)) < 4:
        new = keep + rest[::-1]
    return _reformat(original, new)


def gen_phone(original: str, seed: int) -> str:
    d = re.sub(r"\D", "", original)
    cc = d[:2] if (len(d) == 12 and d.startswith("57")) else ""
    core = d[len(cc):]
    keep = core[:2] if core.startswith("3") else "31"
    rest = "".join(str((seed >> (3 * i)) % 10) for i in range(len(core) - 2))
    return _reformat(original, cc + keep + rest)


def gen_digits_like(original: str, seed: int) -> str:
    d = re.sub(r"\D", "", original)
    return _reformat(original, d[:1] + "".join(str((seed >> (3 * i)) % 10) for i in range(len(d) - 1)))


def gen_email(original: str, ent: dict, orig_parts: dict, seed: int) -> str:
    local, _, domain = original.partition("@")
    pf = fold(ent["first"].split()[0]).replace(" ", "")
    ps1 = fold(ent["s1"]).replace(" ", "")
    ps2 = fold(ent["s2"]).replace(" ", "")
    of, os1, os2 = (fold(orig_parts.get(k, "")).replace(" ", "") for k in ("first", "s1", "s2"))
    new = None
    low = local.lower()
    if of and os1:
        if low.startswith(of[0] + os1) and not low.startswith(of):
            new = pf[0] + ps1 + low[len(of[0] + os1):].replace(os2, ps2) if os2 else pf[0] + ps1 + low[len(of[0] + os1):]
        elif of in low or os1 in low:
            cand = low
            for o, pnew in ((of, pf), (os1, ps1), (os2, ps2)):
                if o and o in cand:
                    cand = cand.replace(o, pnew)
            if cand != low:
                new = cand
    if not new:
        tail = re.sub(r"\D", "", low)
        sep = "." if "." in low else ("_" if "_" in low else "")
        new = (pf + sep + ps1) if sep else (pf[0] + ps1)
        if tail:
            new += str(seed % 90 + 10)
    new = strip_accents(new)
    if new == low:
        new += str(seed % 9 + 1)
    return f"{new}@{domain}"


def spell_email(email: str) -> str:
    local, _, domain = email.partition("@")
    return f"{local} arroba " + " punto ".join(domain.split("."))


def gen_address(original: str, seed: int) -> str:
    m = re.match(r"\s*([A-Za-zÁÉÍÓÚáéíóú\.]+)", original)
    tipo = m.group(1) if m else "Calle"
    a = seed % 180 + 1
    letter = ["", "", "A", "B", "C"][(seed >> 8) % 5]
    b, c = (seed >> 12) % 120 + 1, (seed >> 20) % 99 + 1
    extra = ["", "", f" apto {(seed >> 28) % 1500 + 101}", f" casa {(seed >> 28) % 90 + 1}",
             f" of {(seed >> 28) % 900 + 101}"][(seed >> 40) % 5]
    return f"{tipo} {a}{letter} # {b}-{c}{extra}"


def gen_birth(original: str, seed: int) -> str:
    try:
        d = dt.date.fromisoformat(original)
    except ValueError:
        return original
    years = (seed % 4 + 1) * (1 if (seed >> 4) % 2 else -1)
    month, day = (seed >> 8) % 12 + 1, (seed >> 16) % 28 + 1
    return dt.date(max(1940, min(2008, d.year + years)), month, day).isoformat()


def gen_plate(original: str, seed: int) -> str:
    letters = "BCDFGHJKLMNPRSTVWXYZ"
    new = "".join(letters[(seed >> (5 * i)) % len(letters)] for i in range(3))
    nums = "".join(str((seed >> (4 * i + 20)) % 10) for i in range(3))
    sep = original[3] if len(original) > 6 and not original[3].isalnum() else ""
    return f"{new}{sep}{nums}"


def gen_passport(original: str, seed: int) -> str:
    n = len(re.sub(r"\D", "", original))
    return original[:2] + "".join(str((seed >> (3 * i)) % 10) for i in range(n))
