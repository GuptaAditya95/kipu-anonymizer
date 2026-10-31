"""Person registry: who exists, with every form of their identity.

Status: IMPLEMENTED.

Ground truth comes from the structured tables, so namesakes stay separate:
  clientes.csv  -> key cl:<cliente_id>
  empleados.xml -> key kp:<KP-id>
  users.json    -> linked to an employee (kipupay.co email / name) or a client
                   (email, or unique full name, or a cedula/email they typed in chat);
                   otherwise key uid:<uid>
People found only in free text get keys later (txt:<name>, em:<email>, ...).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from pipeline.text import digits, fold, slug

RES = Path(__file__).resolve().parent.parent / "resources"
PARTICLES = {"de", "del", "la", "las", "los", "y"}


def _load(name):
    return json.loads((RES / name).read_text(encoding="utf-8"))


FEMALE = {fold(n) for n in _load("names_female.json")}
MALE = {fold(n) for n in _load("names_male.json")}
FIRST_NAMES = FEMALE | MALE


def gender_of(first: str) -> str:
    f = fold(first.split()[0]) if first else ""
    if f in FEMALE:
        return "F"
    if f in MALE:
        return "M"
    return "F" if f.endswith("a") else "M"


def split_name(full: str) -> tuple[list[str], list[str]]:
    """Colombian convention: last two tokens are surnames when there are 3+ tokens
    (particles like 'de la' stick to the following surname)."""
    toks = full.split()
    groups: list[str] = []
    buf = ""
    for t in toks:
        if t.lower() in PARTICLES:
            buf = (buf + " " + t).strip()
            continue
        groups.append((buf + " " + t).strip() if buf else t)
        buf = ""
    if len(groups) <= 1:
        return groups, []
    if len(groups) == 2:
        return groups[:1], groups[1:]
    # 3+ tokens: if the 2nd token is a known first name, assume 2 first names
    if len(groups) == 3 and fold(groups[1]) in FIRST_NAMES and fold(groups[2]) not in FIRST_NAMES:
        return groups[:2], groups[2:]
    return groups[:-2], groups[-2:]


@dataclass
class Person:
    key: str
    kind: str                         # client | employee | chat_user | mentioned
    full_name: str = ""
    first: list[str] = field(default_factory=list)
    surnames: list[str] = field(default_factory=list)
    gender: str = "F"
    cedulas: set[str] = field(default_factory=set)
    emails: set[str] = field(default_factory=set)
    phones: set[str] = field(default_factory=set)
    addresses: set[str] = field(default_factory=set)
    births: set[str] = field(default_factory=set)
    handles: set[str] = field(default_factory=set)     # slack name, pqrs agente username
    nicknames: set[str] = field(default_factory=set)   # slack display_name
    uids: set[str] = field(default_factory=set)
    occupation: str = ""
    barrio: str = ""
    city: str = ""
    since_year: str = ""

    def set_name(self, full: str) -> None:
        self.full_name = full.strip()
        self.first, self.surnames = split_name(self.full_name)
        if self.first:
            self.gender = gender_of(self.first[0])

    def name_forms(self) -> dict[str, str]:
        """folded form -> role. Roles: full, first_s1, first1_s1, first_all, first1, s1."""
        f, s = self.first, self.surnames
        out = {}
        if not f:
            return out
        if s:
            out.setdefault(fold(" ".join(f + s)), "full")
            out.setdefault(fold(" ".join(f + s[:1])), "first_s1")
            out.setdefault(fold(" ".join(f[:1] + s[:1])), "first1_s1")
            if len(s) > 1:
                out.setdefault(fold(" ".join(f[:1] + s)), "first1_full")
        out.setdefault(fold(" ".join(f)), "first_all")
        out.setdefault(fold(f[0]), "first1")
        return out

    def multi_token_forms(self) -> dict[str, str]:
        return {k: v for k, v in self.name_forms().items() if " " in k}

    def slug_forms(self) -> dict[str, str]:
        out = {}
        for k, role in self.multi_token_forms().items():
            out[slug(k, "_")] = role
            out[slug(k, "-")] = role
        return out


class Registry:
    def __init__(self):
        self.people: dict[str, Person] = {}
        self.by_form: dict[str, set[str]] = {}       # folded multi-token name -> keys
        self.by_id: dict[str, str] = {}               # cedula digits / phone digits / email -> key
        self.by_uid: dict[str, str] = {}
        self.by_handle: dict[str, str] = {}
        self.client_ids: dict[str, str] = {}          # CL-xxx -> key

    # ----------------------------------------------------------- building
    def add(self, p: Person) -> Person:
        self.people[p.key] = p
        return p

    def index(self) -> None:
        self.by_form.clear()
        self.by_id.clear()
        for k, p in self.people.items():
            for form in list(p.multi_token_forms()) + list(p.slug_forms()):
                self.by_form.setdefault(form, set()).add(k)
            for c in p.cedulas:
                self.by_id.setdefault(digits(c), k)
            for ph in p.phones:
                d = digits(ph)
                self.by_id.setdefault(d[-10:], k)
            for e in p.emails:
                self.by_id.setdefault(e.lower(), k)
            for h in p.handles:
                self.by_handle.setdefault(h.lower(), k)
            for u in p.uids:
                self.by_uid[u] = k

    def build(self, records: dict[str, list[dict]], users: dict[str, dict],
              chats_by_user: dict[str, list[str]] | None = None) -> None:
        for r in records.get("tables", []):
            c, t = r["columns"], r["table"]
            if t == "clientes":
                p = Person(key=f"cl:{c.get('cliente_id')}", kind="client")
                p.set_name(c.get("nombre_completo", ""))
                for attr, col in (("cedulas", "num_doc"), ("emails", "correo"), ("phones", "celular"),
                                  ("addresses", "direccion"), ("births", "fecha_nacimiento")):
                    if c.get(col):
                        getattr(p, attr).add(c[col].strip().lower() if attr == "emails" else c[col].strip())
                p.occupation, p.barrio, p.city = c.get("ocupacion", ""), c.get("barrio", ""), c.get("ciudad", "")
                p.since_year = (c.get("fecha_vinculacion") or "")[:4]
                self.add(p)
                self.client_ids[c.get("cliente_id", "")] = p.key
            elif t == "empleados":
                p = Person(key=f"kp:{c.get('id')}", kind="employee")
                p.set_name(c.get("nombre", ""))
                for attr, col in (("cedulas", "documento"), ("emails", "correo"), ("phones", "celular"),
                                  ("births", "fecha_nacimiento")):
                    if c.get(col):
                        getattr(p, attr).add(c[col].strip().lower() if attr == "emails" else c[col].strip())
                for e in p.emails:
                    p.handles.add(e.split("@")[0])
                self.add(p)
        self.index()
        name_index = {}
        for k, p in self.people.items():
            name_index.setdefault(fold(p.full_name), []).append(k)
            if p.kind == "employee" and p.first and p.surnames:
                name_index.setdefault(fold(f"{p.first[0]} {p.surnames[0]}"), []).append(k)

        for uid, u in users.items():
            if u.get("is_bot"):
                continue
            prof = u.get("profile", {})
            real = u.get("real_name") or prof.get("real_name") or ""
            email = (prof.get("email") or "").lower()
            key = self.by_id.get(email) if email else None
            if not key:
                cands = name_index.get(fold(real), []) or sorted(
                    k for k in self.by_form.get(fold(real), set()) if self.people[k].kind == "client")
                if len(cands) == 1:
                    key = cands[0]
                elif len(cands) > 1 and chats_by_user:
                    texts = " ".join(chats_by_user.get(uid, []))
                    hits = {self.by_id.get(d) for d in re.findall(r"\d[\d.\s]{5,14}\d", texts)
                            for d in [digits(d)] if self.by_id.get(d) in cands}
                    hits |= {self.by_id.get(e.lower()) for e in re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", texts)
                             if self.by_id.get(e.lower()) in cands}
                    hits.discard(None)
                    if len(hits) == 1:
                        key = hits.pop()
            if key:
                p = self.people[key]
            else:
                p = self.add(Person(key=f"uid:{uid}", kind="chat_user"))
                p.set_name(real)
            p.uids.add(uid)
            if email:
                p.emails.add(email)
            if prof.get("phone"):
                p.phones.add(prof["phone"])
            if u.get("name"):
                p.handles.add(u["name"])
            dn = prof.get("display_name") or ""
            if dn and fold(dn) not in {fold(x) for x in p.first} and " " not in dn and len(dn) >= 3:
                p.nicknames.add(dn)
        self.index()

    # ----------------------------------------------------------- lookup
    def all_real_name_forms(self) -> set[str]:
        return {f for p in self.people.values() for f in p.multi_token_forms()}

    def all_real_values(self) -> set[str]:
        out = set()
        for p in self.people.values():
            out |= p.cedulas | p.emails | p.phones | p.addresses | p.births
        return out

    def first_names(self) -> set[str]:
        return {fold(p.first[0]) for p in self.people.values() if p.first}
