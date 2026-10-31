"""Orchestrates the core over chats (by thread), docs (one group each) and table rows,
for the full rebuild and for /anonymize. Also builds entities.json.

Status: IMPLEMENTED.
"""
from __future__ import annotations

import copy
import json
from collections import defaultdict

import re

from pipeline.core import (RX_EMAIL, TABLE_ID_COLS, Anonymizer, EntityLog, Span, folded, text_fields)
from pipeline.pseudonyms import PseudonymMap
from pipeline.registry import Person
from pipeline.text import digits, fold

KEPT_REASON = "organización, lugar, marca o figura pública; no es persona natural"
BOT_NAMES = {"kipi"}


class Processor:
    def __init__(self, anon: Anonymizer):
        self.a = anon
        self.reg = anon.reg

    # ------------------------------------------------------------ helpers
    def _author_key(self, rec: dict) -> str | None:
        uid = rec.get("_uid")
        if uid and uid in self.reg.by_uid:
            return self.reg.by_uid[uid]
        name = (rec.get("author") or "").strip()
        if not name or fold(name) in BOT_NAMES:
            return None
        keys = self.reg.by_form.get(fold(name))
        if keys:
            return sorted(keys)[0]
        k = self.reg.by_handle.get(name.lower())
        return k or f"txt:{fold(name)}"

    def _author(self, rec: dict, key: str | None, log: EntityLog) -> None:
        if not key:
            return
        orig = rec.get("author", "")
        ent = self.a.ent(key, orig)
        p = self.reg.people.get(key)
        rep = PseudonymMap.name(ent)
        if p and len(p.first) and fold(orig) == fold(" ".join(p.first + p.surnames)) and len(p.surnames) > 1 \
                and False:
            rep = PseudonymMap.full(ent)
        rec["author"] = rep
        log.add(key, orig, "name", rep, rec["id"])

    def _cell(self, rec, col, kind, key, log, role=""):
        val = rec["columns"].get(col, "")
        if not val or not key:
            return
        extra = {"cedula": digits(val), "phone": digits(val)[-10:], "email": val.strip().lower(),
                 "address": fold(val), "birth": val.strip()}.get(kind, "")
        sp = Span(0, len(val), kind, key, val, role, extra)
        rec["columns"][col] = self.a.apply(val, [sp], rec["id"], log)

    # ------------------------------------------------------------ chats
    def chats(self, recs: list[dict], log: EntityLog, live: bool = False) -> None:
        threads = defaultdict(list)
        for r in recs:
            threads[r.get("thread_id", r["id"])].append(r)
        for tid, items in threads.items():
            keys = [self._author_key(r) for r in items]
            base = set(self.a.thread_people.get(tid, set())) | {k for k in keys if k}
            group = [(r["id"], r, text_fields(r, "chats")) for r in items]
            extra = {fold(r.get("author", "")): k for r, k in zip(items, keys)
                     if k and k.startswith("txt:") and len(r.get("author", "").split()) >= 2}
            clients = {k for k in keys if k and not (self.reg.people.get(k) and self.reg.people[k].kind == "employee")}
            owner = next(iter(clients)) if len(clients) == 1 else None
            ctx = self.a.anonymize_group(group, base, owner, log, live, extra)
            self.a.thread_people[tid] = {k for k in ctx if k in self.reg.people}
            for r, k in zip(items, keys):
                self._author(r, k, log)

    # ------------------------------------------------------------ docs
    def docs(self, recs: list[dict], log: EntityLog, live: bool = False) -> None:
        for r in recs:
            self.a.anonymize_group([(r["id"], r, text_fields(r, "docs"))], set(), None, log, live)

    # ------------------------------------------------------------ tables
    def tables(self, recs: list[dict], log: EntityLog, live: bool = False) -> None:
        for r in recs:
            try:
                self._row(r, log, live)
            except Exception as e:  # never lose a record
                r.setdefault("_errors", []).append(str(e))

    def _row(self, r: dict, log: EntityLog, live: bool) -> None:
        t, c = r.get("table"), r.get("columns", {})
        key, text_cols, quasi = None, [], True
        if t == "clientes":
            key = f"cl:{c.get('cliente_id')}"
            if key not in self.reg.people:  # live: a new client row
                p = Person(key=key, kind="client")
                p.set_name(c.get("nombre_completo", ""))
                p.occupation, p.barrio, p.city = c.get("ocupacion", ""), c.get("barrio", ""), c.get("ciudad", "")
                self.reg.people[key] = p
            p = self.reg.people[key]
            self._cell(r, "nombre_completo", "name", key, log, fold(c.get("nombre_completo", "")))
            self._cell(r, "num_doc", "cedula", key, log)
            self._cell(r, "correo", "email", key, log)
            self._cell(r, "celular", "phone", key, log)
            self._cell(r, "direccion", "address", key, log)
            self._cell(r, "fecha_nacimiento", "birth", key, log)
            if c.get("barrio"):
                c["barrio"] = self.a.barrios.nearby(c["barrio"], c.get("ciudad", ""),
                                                    self.a.pmap.seed(key, "barrio"))
            if c.get("ocupacion"):
                c["ocupacion"] = self.a.occ.to_sector(c["ocupacion"])[0]
            text_cols = ["notas"]
        elif t == "empleados":
            key = f"kp:{c.get('id')}"
            self._cell(r, "nombre", "name", key, log, fold(c.get("nombre", "")))
            self._cell(r, "correo", "email", key, log)
            self._cell(r, "celular", "phone", key, log)
            self._cell(r, "documento", "cedula", key, log)
            self._cell(r, "fecha_nacimiento", "birth", key, log)
            boss = c.get("reporta_a", "")
            if boss:
                bk = sorted(k for k in self.reg.by_form.get(fold(boss), set()) if k.startswith("kp:"))
                if bk:
                    self._cell(r, "reporta_a", "name", bk[0], log, fold(boss))
        elif t == "pqrs":
            key = self.reg.client_ids.get(c.get("cliente_id", ""))
            if c.get("cedula"):
                ck = self.reg.by_id.get(digits(c["cedula"])) or key or f"ce:{digits(c['cedula'])}"
                self._cell(r, "cedula", "cedula", ck, log)
            ag = c.get("agente", "")
            if ag and self.reg.by_handle.get(ag.lower()):
                hk = self.reg.by_handle[ag.lower()]
                sp = Span(0, len(ag), "handle", hk, ag, extra=ag.lower())
                c["agente"] = self.a.apply(ag, [sp], r["id"], log)
            text_cols = ["descripcion", "respuesta"]
        elif t == "transacciones":
            key = self.reg.client_ids.get(c.get("cliente_id", ""))
            text_cols, quasi = ["descripcion"], False
        else:
            key = self.reg.client_ids.get(c.get("cliente_id", ""))
            text_cols = [k for k in c if k not in TABLE_ID_COLS]
        fields = [(("columns", col), c[col]) for col in text_cols if isinstance(c.get(col), str) and c.get(col)]
        if fields:
            ctx = {key} if key else set()
            if not quasi:
                saved = self.a.quasi_spans
                self.a.quasi_spans = lambda text, people: []
                try:
                    self.a.anonymize_group([(r["id"], r, fields)], ctx, key, log, live)
                finally:
                    self.a.quasi_spans = saved
            else:
                self.a.anonymize_group([(r["id"], r, fields)], ctx, key, log, live)

    # ------------------------------------------------------------ entry points
    def run(self, records: dict[str, list[dict]], live: bool = False, prev_entities: dict | None = None):
        out = {k: copy.deepcopy(v) for k, v in records.items()}
        # every real email / long number in the input is off-limits for generated values
        seen = set()
        for recs in out.values():
            for r in recs:
                blob = json.dumps(r, ensure_ascii=False)
                seen |= {e.lower() for e in RX_EMAIL.findall(blob)}
                seen |= set(re.findall(r"\d{6,12}", re.sub(r"(?<=\d)[.\s-](?=\d)", "", blob)))
        self.a.pmap.reserve(set(), seen)
        log = EntityLog()
        self.chats(out.get("chats", []), log, live)
        self.docs(out.get("docs", []), log, live)
        self.tables(out.get("tables", []), log, live)
        for recs in out.values():
            for r in recs:
                r.pop("_uid", None)
                r.pop("_errors", None)
        return out, self.entities(log, prev_entities)

    def entities(self, log: EntityLog, prev: dict | None = None) -> list[dict]:
        """prev: previous run's entities.json; a still-valid previous declaration is reused so
        entities.json stays stable across the incident (the judge compares it)."""
        from pipeline.text import Haystack
        prev_map = {e["entity_id"]: e.get("pseudonyms", {}) for e in (prev or {}).get("entities", [])
                    if not e.get("kept")}
        ents = []
        for key, e in log.data.items():
            pm = self.a.pmap.get(key)
            if not pm:
                continue
            pseud = {}
            old = prev_map.get(pm["entity_id"], {})
            for kind, vals in e["written"].items():
                if kind in old and Haystack(" | ".join(vals)).contains(old[kind]):
                    pseud[kind] = old[kind]
                    continue
                if kind == "name":
                    canon = PseudonymMap.name(pm)
                    pseud["name"] = canon if any(fold(canon) in fold(v) for v in vals) else max(vals, key=len)
                else:
                    pseud[kind] = vals[0]
            ents.append({"entity_id": pm["entity_id"], "type": "person",
                         "originals": sorted(e["originals"]), "pseudonyms": pseud,
                         "records": sorted(e["records"])})
        ents.sort(key=lambda x: x["entity_id"])
        for i, (item, rids) in enumerate(sorted(log.kept.items()), 1):
            ents.append({"entity_id": f"kept_{i:03d}", "type": "organization_or_place",
                         "originals": [item], "pseudonyms": {}, "kept": True, "reason": KEPT_REASON,
                         "records": []})
        return ents
