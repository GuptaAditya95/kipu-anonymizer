"""Anonymization core shared by /run (batch) and /anonymize (live).

Status: IMPLEMENTED.

Per group of records (a chat thread, a document, a table row):
  pass 1  strong detections (registry names, slugs, ids) -> context people of the group
  pass 2  all detections, including context-only forms (first names, nicknames,
          "señor <surname>", handles), quasi-identifiers of context clients
  replace spans right-to-left, keeping the original's form (CAPS, accents, slug, layout)
Every replacement is recorded per entity (originals found, pseudonyms written, records)
to build entities.json honestly.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from pipeline import jev
from pipeline.generalize import Barrios, Occupations
from pipeline.pseudonyms import (PseudonymMap, _reformat, gen_address, gen_birth, gen_cedula,
                                 gen_email, gen_passport, gen_phone, gen_plate, spell_email)
from pipeline.registry import FIRST_NAMES, Person, Registry, gender_of, split_name
from pipeline.text import digits, fold, slug, strip_accents, style_like

RES = Path(__file__).resolve().parent.parent / "resources"
KEEP = [k for k in json.loads((RES / "keep_list.json").read_text("utf-8"))["items"]]
KEEP_FOLD = {fold(k) for k in KEEP}
RX_KEEP = re.compile(r"(?<![\w])(" + "|".join(map(re.escape, sorted(KEEP, key=len, reverse=True))) + r")(?![\w])")
SURNAMES = {fold(s) for s in json.loads((RES / "surnames.json").read_text("utf-8"))}
GENERIC_EMAIL = re.compile(r"^(soporte|info|contacto|notificaciones|cobranza|cobros|ayuda|no-?reply|"
                           r"pqrs?|legal|seguridad|fraude|riesgo|servicio|clientes|admin|ventas|"
                           r"facturacion|talento|rrhh|nomina|comunicaciones|prensa|privacidad|"
                           r"datos|compliance|auditoria|tesoreria|operaciones|hola|team|equipo)\b", re.I)

RX_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
RX_SPELLED = re.compile(r"\b([\w.]+)\s+arroba\s+([\w-]+)((?:\s+punto\s+[\w-]+)+)", re.I)
RX_PHONE = re.compile(r"(?<![\d])(?:\+?57[\s.-]?)?3\d{2}[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)")
RX_DOTTED = re.compile(r"(?<![\d.,])\d{1,3}(?:\.\d{3}){2,3}(?![\d]|\.\d)")
RX_DIGITS = re.compile(r"(?<![\d.,\-])\d{6,10}(?![\d]|[.,]\d)")
RX_PASSPORT = re.compile(r"\b[A-Z]{2}\d{6,7}\b")
RX_PLATE = re.compile(r"\b(?!COP|USD|EUR|INC|PQR|TX|CL|KP)[A-Z]{3}[\s-]?\d{3}\b")
RX_ISO = re.compile(r"\b(?:19|20)\d{2}-\d{2}-\d{2}\b")
RX_ADDRESS = re.compile(
    r"\b(?:Calle|Cl|Cll|Carrera|Cra|Kr|Kra|Diagonal|Dg|Transversal|Tv|Avenida|Av|Autopista)\.?\s*"
    r"\d+[A-Z]?(?:\s*Bis)?(?:\s*[A-Z])?(?:\s*Sur)?\s*(?:#|No\.?|N°)\s*\d+[A-Z]?\s*-\s*\d+"
    r"(?:\s*(?:Sur|Este))?(?:\s*(?:apto|apartamento|casa|of|oficina|int|torre|bl|bloque)\.?\s*\d+)?",
    re.I)
RX_CAP_SEQ = re.compile(
    r"(?<![\w@.])([A-ZÁÉÍÓÚÑ][a-záéíóúñü]+(?:\s+(?:de\s+la\s+|de\s+los\s+|del\s+|de\s+)?"
    r"[A-ZÁÉÍÓÚÑ][a-záéíóúñü]+){1,3})(?![\w])")
RX_CAPS_SEQ = re.compile(r"(?<![\w])([A-ZÁÉÍÓÚÑ]{3,}(?:\s+[A-ZÁÉÍÓÚÑ]{2,}){1,3})(?![\w])")
CEDULA_CUE = re.compile(r"(c[ée]dula|\bcc\b|c\.c\.|documento|identificaci[oó]n|\bdoc\b|\bnit\b|n[uú]mero de id)", re.I)
PHONE_CUE = re.compile(r"(celular|cel\b|tel[eé]fono|whats|llamar|n[uú]mero)", re.I)
HONORIFIC = r"(?:señor|señora|sr\.?|sra\.?|don|doña|estimado señor|estimada señora|apreciado señor|apreciada señora)"
PRIORITY = {"email": 9, "email_spelled": 9, "address": 8, "cedula": 7, "phone": 7, "passport": 7,
            "plate": 6, "birth": 6, "slug": 5, "name": 5, "handle": 4, "nickname": 3, "first": 3,
            "surname": 3, "occupation": 2, "barrio": 2, "year": 1}

TABLE_ID_COLS = {"cliente_id", "tx_id", "radicado", "id", "tipo_doc", "ciudad", "fecha_vinculacion",
                 "fecha", "monto", "tipo", "canal", "estado", "cargo", "ingreso", "documento_tipo"}


@dataclass
class Span:
    start: int
    end: int
    kind: str
    key: str
    orig: str
    role: str = ""
    extra: str = ""


_FOLD: dict[str, str] = {}


def _fold_char(c: str) -> str:
    r = _FOLD.get(c)
    if r is None:
        r = _FOLD[c] = (strip_accents(c).lower() or c)[:1]
    return r


_FOLDED_CACHE: dict[str, str] = {}


def folded(text: str) -> str:
    """Lowercase, accentless copy with the same length (offsets map 1:1 to the original)."""
    r = _FOLDED_CACHE.get(text)
    if r is None:
        r = "".join(map(_fold_char, text))
        if len(_FOLDED_CACHE) > 50000:
            _FOLDED_CACHE.clear()
        _FOLDED_CACHE[text] = r
    return r


class EntityLog:
    """Collects what was found/written per entity -> entities.json."""

    def __init__(self):
        self.data: dict[str, dict] = {}
        self.kept: dict[str, set] = {}

    def add(self, key, orig, kind, value, rid):
        e = self.data.setdefault(key, {"originals": set(), "written": {}, "records": set()})
        e["originals"].add(orig)
        e["records"].add(rid)
        e["written"].setdefault(kind, []).append(value)

    def keep(self, item, rid):
        self.kept.setdefault(item, set()).add(rid)


class Anonymizer:
    def __init__(self, registry: Registry, pmap: PseudonymMap, jev_cache=None):
        self.reg = registry
        self.pmap = pmap
        self.jev_cache = jev_cache
        self.barrios = Barrios()
        self.occ = Occupations(jev_cache)
        self.pmap.reserve(registry.all_real_name_forms(), registry.all_real_values())
        self.first_names = FIRST_NAMES | registry.first_names()
        self.thread_people: dict[str, set[str]] = {}
        self.prior_keys = set(pmap.data["entities"])  # people that existed in the previous run
        self._compile()

    # ---------------------------------------------------------------- setup
    def _compile(self):
        forms = sorted(self.reg.by_form, key=len, reverse=True)
        name_forms = [f for f in forms if " " in f]
        slug_forms = [f for f in forms if " " not in f]
        self.rx_names = (re.compile(r"(?<![\w])(" + "|".join(
            r"\s+".join(map(re.escape, f.split())) for f in name_forms) + r")(?![\w])")
            if name_forms else None)
        self.rx_slugs = (re.compile(r"(?<![a-z0-9])(" + "|".join(map(re.escape, slug_forms)) + r")(?![a-z0-9])")
                         if slug_forms else None)
        self.address_index = {fold(a): k for k, p in self.reg.people.items() for a in p.addresses}
        handles = sorted((h for h in self.reg.by_handle if len(h) >= 6 and re.search(r"[a-z]", h)), key=len, reverse=True)
        self.rx_handles = (re.compile(r"(?<![\w.-])(" + "|".join(map(re.escape, handles)) + r")(?![\w@-]|\.\w)")
                           if handles else None)
        self.birth_index = {b: k for k, p in self.reg.people.items() for b in p.births}

    def person(self, key) -> Person | None:
        return self.reg.people.get(key)

    def ent(self, key: str, name_hint: str = ""):
        p = self.person(key)
        if p:
            return self.pmap.entity(key, p.gender, max(1, len(p.first)), set(p.surnames))
        first, sur = split_name(name_hint) if name_hint else ([], [])
        return self.pmap.entity(key, gender_of(first[0]) if first else "F", max(1, len(first)), set(sur))

    # ---------------------------------------------------------------- detection
    def strong_spans(self, text: str) -> list[Span]:
        """Registry names/slugs + identifiers. Keys may be lists (namesakes) -> resolved later."""
        spans: list[Span] = []
        f = folded(text)
        if self.rx_names:
            for m in self.rx_names.finditer(f):
                form = re.sub(r"\s+", " ", m.group(1))
                spans.append(Span(m.start(), m.end(), "name", "|".join(sorted(self.reg.by_form[form])),
                                  text[m.start():m.end()], form))
        if self.rx_slugs:
            for m in self.rx_slugs.finditer(f):
                spans.append(Span(m.start(), m.end(), "slug", "|".join(sorted(self.reg.by_form[m.group(1)])),
                                  text[m.start():m.end()], m.group(1)))
        if self.rx_handles:
            low = text.lower()
            for m in self.rx_handles.finditer(low):
                k = self.reg.by_handle.get(m.group(1))
                if k:
                    spans.append(Span(m.start(), m.end(), "handle", k, text[m.start():m.end()], extra=m.group(1)))
        for m in RX_EMAIL.finditer(text):
            e = m.group(0).rstrip(".").lower()
            if GENERIC_EMAIL.match(e):
                continue
            spans.append(Span(m.start(), m.start() + len(m.group(0).rstrip(".")), "email",
                              self.reg.by_id.get(e, ""), m.group(0).rstrip("."), extra=e))
        for m in RX_SPELLED.finditer(text):
            e = (m.group(1) + "@" + m.group(2) + "".join("." + x for x in
                 re.findall(r"punto\s+([\w-]+)", m.group(3), re.I))).lower()
            spans.append(Span(m.start(), m.end(), "email_spelled", self.reg.by_id.get(e, ""), m.group(0), extra=e))
        for m in RX_PHONE.finditer(text):
            d = digits(m.group(0))[-10:]
            spans.append(Span(m.start(), m.end(), "phone", self.reg.by_id.get(d, ""), m.group(0), extra=d))
        for rx in (RX_DOTTED, RX_DIGITS):
            for m in rx.finditer(text):
                d = digits(m.group(0))
                if len(d) == 10 and d.startswith("3"):
                    continue  # phone handled above
                key = self.reg.by_id.get(d, "")
                cue = CEDULA_CUE.search(text[max(0, m.start() - 40):m.start()])
                if key or cue or (rx is RX_DIGITS and text.strip() == m.group(0) and len(d) >= 7):
                    spans.append(Span(m.start(), m.end(), "cedula", key, m.group(0), extra=d))
        for m in RX_ADDRESS.finditer(text):
            a = m.group(0).strip()
            spans.append(Span(m.start(), m.start() + len(a), "address", self.address_index.get(fold(a), ""), a, extra=fold(a)))
        for m in RX_PASSPORT.finditer(text):
            if re.search(r"pasaporte|passport", text[max(0, m.start() - 40):m.start()], re.I):
                spans.append(Span(m.start(), m.end(), "passport", "", m.group(0), extra=m.group(0)))
        for m in RX_PLATE.finditer(text):
            if re.search(r"placa|veh[ií]culo|carro|moto|plate", text[max(0, m.start() - 40):m.start()], re.I):
                spans.append(Span(m.start(), m.end(), "plate", "", m.group(0), extra=m.group(0)))
        for m in RX_ISO.finditer(text):
            if m.group(0) in self.birth_index:
                spans.append(Span(m.start(), m.end(), "birth", self.birth_index[m.group(0)], m.group(0), extra=m.group(0)))
        return spans

    def unknown_names(self, text: str, taken: list[Span]) -> list[Span]:
        """Capitalized/CAPS sequences that start with a known first name and aren't kept."""
        out = []
        for rx in (RX_CAP_SEQ, RX_CAPS_SEQ):
            for m in rx.finditer(text):
                s, e = m.start(1), m.end(1)
                if any(sp.start < e and s < sp.end for sp in taken):
                    continue
                cand = m.group(1)
                toks = [t for t in cand.split() if t.lower() not in {"de", "del", "la", "los"}]
                ft = [fold(t) for t in toks]
                if fold(cand) in KEEP_FOLD or any(fold(k) in fold(cand) for k in KEEP if " " in k):
                    continue
                if ft[0] not in self.first_names:
                    if self.jev_cache is not None and jev.enabled() and len(toks) >= 2 and \
                            ft[1] in SURNAMES:
                        res = jev.classify_span(cand, text[max(0, s - 80):e + 80], self.jev_cache)
                        if not (res and res[0] == "person" and res[1] >= jev.MIN_CONFIDENCE):
                            continue
                    else:
                        continue
                if len(toks) < 2 or all(t in self.first_names for t in ft) and len(toks) < 2:
                    continue
                # trim trailing kept words (e.g. "Ana Gómez Bancolombia")
                while len(toks) > 2 and fold(toks[-1]) in KEEP_FOLD:
                    toks = toks[:-1]
                name = " ".join(toks)
                key = "txt:" + fold(name)
                out.append(Span(s, s + len(cand), "name", key, cand, "full"))
        return out

    def context_spans(self, text: str, people: set[str], taken: list[Span]) -> list[Span]:
        """Single-token forms only valid when the person is in the group's context."""
        out = []
        f = folded(text)
        by_first: dict[str, list[str]] = {}
        for k in people:
            p = self.person(k)
            if not p or not p.first:
                continue
            by_first.setdefault(fold(" ".join(p.first)), []).append(k)
            by_first.setdefault(fold(p.first[0]), []).append(k)
            for nick in p.nicknames:
                by_first.setdefault(fold(nick), []).append(k)
            for h in p.handles:
                for m in re.finditer(rf"(?<![\w.@]){re.escape(h.lower())}(?![\w@]|\.\w)", text.lower()):
                    out.append(Span(m.start(), m.end(), "handle", k, text[m.start():m.end()], extra=h.lower()))
            if p.surnames:
                s1 = fold(p.surnames[0])
                for m in re.finditer(rf"(?<![\w]){HONORIFIC}\s+({re.escape(s1)})(?![\w])", f):
                    out.append(Span(m.start(1), m.end(1), "surname", k, text[m.start(1):m.end(1)], "s1"))
        for form, keys in sorted(by_first.items(), key=lambda x: -len(x[0])):
            pat = r"(?<![\w])" + r"\s+".join(map(re.escape, form.split())) + r"(?![\w])"
            for m in re.finditer(pat, f):
                orig = text[m.start():m.end()]
                if not orig[:1].isupper():
                    continue
                k = keys[0]
                p = self.person(k)
                kind = "nickname" if p and form in {fold(n) for n in p.nicknames} else "first"
                out.append(Span(m.start(), m.end(), kind, k, orig, form))
        return out

    def quasi_spans(self, text: str, people: set[str]) -> list[Span]:
        out = []
        f = folded(text)
        for k in people:
            p = self.person(k)
            if not p or p.kind != "client":
                continue
            hit = False
            if p.occupation:
                occ = fold(p.occupation)
                variants = {occ, occ[:-1] + ("a" if occ.endswith("o") else "o")} if occ[-1:] in "ao" else {occ}
                for v in variants:
                    for m in re.finditer(rf"(?<![\w]){re.escape(v)}(?![\w])", f):
                        out.append(Span(m.start(), m.end(), "occupation", k, text[m.start():m.end()]))
                        hit = True
            if p.barrio and fold(p.barrio) not in {fold(p.city)}:
                for m in re.finditer(rf"(?<![\w]){re.escape(fold(p.barrio))}(?![\w])", f):
                    out.append(Span(m.start(), m.end(), "barrio", k, text[m.start():m.end()], extra=p.city))
                    hit = True
            if p.since_year:
                rx = re.compile(r"\b(entr[eé] con ustedes en|entr[eé] en|desde|me vincul[eé] en|"
                                r"abr[ií] (?:la|mi) cuenta en|cliente desde)\s+(" + p.since_year + r")\b", re.I)
                for m in rx.finditer(text):
                    out.append(Span(m.start(2), m.end(2), "year", k, m.group(2)))
                if hit:
                    for m in re.finditer(rf"(?<![\d\-/]){p.since_year}(?![\d\-/])", text):
                        out.append(Span(m.start(), m.end(), "year", k, m.group(0)))
        return out

    # ---------------------------------------------------------------- resolution
    def resolve(self, spans: list[Span], context: set[str], owner: str | None, text_all: str,
                live: bool = False) -> None:
        strong_ids = {s.key for s in spans if s.kind in ("cedula", "email", "phone", "email_spelled") and s.key}
        cl_ids = {self.reg.client_ids.get(c) for c in re.findall(r"CL-\d+", text_all)} - {None}
        unknown_cedulas = [s for s in spans if s.kind == "cedula" and not s.key]
        for s in spans:
            if s.kind not in ("name", "slug") or not s.key or s.key.startswith("txt:"):
                continue
            cands = s.key.split("|")
            if live and unknown_cedulas and not (set(cands) & strong_ids):
                # a known name next to an unknown cedula: a different (new) person with that name
                s.key = f"txt:{fold(s.orig)}#cc{unknown_cedulas[0].extra}"
                for u in unknown_cedulas:
                    u.key = s.key
                continue
            if len(cands) > 1:
                pick = [c for c in cands if c in strong_ids] or [c for c in cands if c in cl_ids] \
                    or [c for c in cands if c in context] or [c for c in cands if c in self.prior_keys]
                s.key = pick[0] if pick else cands[0]
        for s in spans:
            if not s.key:
                if s.kind in ("cedula", "phone", "email", "email_spelled", "address", "passport", "plate"):
                    s.key = owner or f"{s.kind[:2]}:{s.extra}"

    # ---------------------------------------------------------------- rendering
    def render(self, s: Span) -> tuple[str, str]:
        """Return (replacement, pseudonym kind for entities.json)."""
        if s.kind in ("occupation",):
            sector, _ = self.occ.to_sector(s.orig)
            return style_like(s.orig, sector) if s.orig.isupper() else sector, ""
        if s.kind == "barrio":
            return s.extra, ""
        if s.kind == "year":
            return "hace unos años", ""
        ent = self.ent(s.key, s.orig if s.kind == "name" else "")
        p = self.person(s.key)
        pf, ps1, ps2 = ent["first"], ent["s1"], ent["s2"]
        if s.kind in ("name", "slug", "first", "nickname", "surname"):
            role = s.role
            if s.kind in ("name", "slug") and p:
                role = p.name_forms().get(s.role.replace("_", " ").replace("-", " "), "full")
            elif s.kind in ("name",) and not p:
                n_tok = len([t for t in s.orig.split() if t.lower() not in {"de", "del", "la", "los"}])
                role = "full" if n_tok >= 3 else "first_s1"
            if s.kind == "nickname":
                form = pf.split()[0]
            elif s.kind == "surname" or role == "s1":
                form = ps1
            elif role in ("full", "first1_full"):
                n_sur = len(p.surnames) if p else 2
                form = f"{pf if role == 'full' else pf.split()[0]} {ps1}" + (f" {ps2}" if n_sur >= 2 else "")
            elif role == "first_s1":
                form = f"{pf} {ps1}"
            elif role == "first1_s1":
                form = f"{pf.split()[0]} {ps1}"
            else:  # first_all / first1
                form = pf
            if s.kind == "slug":
                sep = "-" if "-" in s.orig else "_"
                return slug(form, sep), "name"
            return style_like(s.orig, form), "name"
        orig_parts = {"first": p.first[0] if p and p.first else "", "s1": p.surnames[0] if p and p.surnames else "",
                      "s2": p.surnames[1] if p and len(p.surnames) > 1 else ""}
        if s.kind in ("email", "email_spelled"):
            e = self.pmap.value(s.key, "email", s.extra, lambda sd: gen_email(s.extra, ent, orig_parts, sd))
            if s.kind == "email_spelled":
                return spell_email(e), "email"
            return (e.upper() if s.orig.isupper() else e), "email"
        if s.kind == "handle":
            if "." in s.extra:
                v = self.pmap.value(s.key, "handle", s.extra,
                                    lambda sd: f"{fold(pf.split()[0])}.{fold(ps1)}".replace(" ", ""))
            else:
                v = self.pmap.value(s.key, "handle", s.extra,
                                    lambda sd: f"{fold(pf)[0]}{fold(ps1)}".replace(" ", ""))
            return v, "handle"
        if s.kind == "cedula":
            d = self.pmap.value(s.key, "cedula", s.extra, lambda sd: gen_cedula(s.extra, sd))
            return _reformat(s.orig, d), "cedula"
        if s.kind == "phone":
            d = self.pmap.value(s.key, "phone", s.extra, lambda sd: gen_phone(s.extra, sd))
            return _reformat(s.orig, digits(s.orig)[:-10] + d) if len(digits(s.orig)) > 10 else _reformat(s.orig, d), "phone"
        if s.kind == "address":
            return self.pmap.value(s.key, "address", s.extra, lambda sd: gen_address(s.orig, sd)), "address"
        if s.kind == "birth":
            return self.pmap.value(s.key, "birth", s.extra, lambda sd: gen_birth(s.extra, sd)), "birth_iso"
        if s.kind == "passport":
            return self.pmap.value(s.key, "passport", s.extra, lambda sd: gen_passport(s.extra, sd)), "passport"
        if s.kind == "plate":
            return self.pmap.value(s.key, "plate", s.extra, lambda sd: gen_plate(s.extra, sd)), "plate"
        return s.orig, ""

    def apply(self, text: str, spans: list[Span], rid: str, log: EntityLog) -> str:
        chosen: list[Span] = []
        for s in sorted(spans, key=lambda x: (-(x.end - x.start), -PRIORITY.get(x.kind, 0))):
            if s.key == "" and s.kind not in ("occupation", "barrio", "year"):
                continue
            if any(c.start < s.end and s.start < c.end for c in chosen):
                continue
            chosen.append(s)
        for s in sorted(chosen, key=lambda x: -x.start):
            rep, pkind = self.render(s)
            text = text[:s.start] + rep + text[s.end:]
            if pkind and s.key:
                log.add(s.key, s.orig, pkind, rep, rid)
        return text

    def keep_hits(self, text: str, rid: str, log: EntityLog):
        for m in RX_KEEP.finditer(text):
            log.keep(m.group(1), rid)

    # ---------------------------------------------------------------- groups
    def anonymize_group(self, items: list[tuple[str, dict, list[tuple]]], base_context: set[str],
                        owner: str | None, log: EntityLog, live: bool = False,
                        extra_names: dict[str, str] | None = None) -> set[str]:
        """items: (record_id, record, [(getter_path, text)]) sharing one context.
        Returns the context people (for thread memory)."""
        all_text = "\n".join(t for _, _, fields in items for _, t in fields)
        context = set(base_context)
        strong = {}
        for rid, rec, fields in items:
            for path, t in fields:
                sp = self.strong_spans(t)
                for nm, k in (extra_names or {}).items():  # e.g. a new author's own name
                    f = folded(t)
                    for m in re.finditer(r"(?<![\w])" + r"\s+".join(map(re.escape, nm.split())) + r"(?![\w])", f):
                        sp.append(Span(m.start(), m.end(), "name", k, t[m.start():m.end()], "full"))
                strong[(rid, path)] = sp
        flat = [s for v in strong.values() for s in v]
        self.resolve(flat, context, owner, all_text, live)
        context |= {s.key for s in flat if s.key and s.kind in ("name", "slug", "cedula", "email", "phone")
                    and s.key in self.reg.people}
        if owner is None:
            clients = [k for k in context if self.person(k) and self.person(k).kind != "employee"]
            owner = clients[0] if len(clients) == 1 else None
            for s in flat:
                if s.key.startswith(("ce:", "ph:", "em:", "ad:", "pa:", "pl:")) and owner:
                    s.key = owner
        literals = {}  # every identifier found anywhere in the group is replaced everywhere in it
        for s_ in flat:
            if s_.kind in ("email", "cedula", "phone", "passport", "plate", "handle") and s_.key and len(s_.orig) >= 5:
                literals.setdefault(s_.orig, s_)
        for rid, rec, fields in items:
            for path, t in fields:
                spans = list(strong[(rid, path)])
                for lit, src in literals.items():
                    for m in re.finditer(re.escape(lit), t, re.I):
                        if not any(x.start < m.end() and m.start() < x.end for x in spans):
                            spans.append(Span(m.start(), m.end(), src.kind, src.key, t[m.start():m.end()],
                                              src.role, src.extra))
                spans += self.unknown_names(t, spans)
                spans += self.context_spans(t, context, spans)
                spans += self.quasi_spans(t, context)
                new = self.apply(t, spans, rid, log)
                set_path(rec, path, new)
                self.keep_hits(t, rid, log)
        return context


def set_path(rec: dict, path: tuple, value: str) -> None:
    obj = rec
    for p in path[:-1]:
        obj = obj[p]
    obj[path[-1]] = value


def text_fields(rec: dict, source: str) -> list[tuple]:
    out = []
    if source == "chats":
        out.append((("text",), rec.get("text", "")))
        for i, a in enumerate(rec.get("attachments", [])):
            out.append((("attachments", i, "filename"), a.get("filename", "")))
    elif source == "docs":
        for k in ("filename", "title", "body"):
            out.append(((k,), rec.get(k, "")))
        for k, v in rec.get("metadata", {}).items():
            if isinstance(v, str):
                out.append((("metadata", k), v))
    return [(p, t) for p, t in out if isinstance(t, str) and t]
