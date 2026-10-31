"""Verification of the anonymized output -> review cases.

Status: IMPLEMENTED. Runs AFTER a full /run (never on the /anonymize hot path) and never changes
the output the judge receives. Flagged records become cases on the review dashboard; human answers
become knowledge (pipeline/knowledge.py) that the next run applies.

  1. mask      our own replacements -> typed placeholders ([NOMBRE_1], [CEDULA], [EMAIL] ...),
               so anything that still looks personal is something we MISSED, not one of our fakes
  2. residual  cheap code check on the masked text (leftover names, ID-like numbers, contacts, addresses)
  3. jev       P(real PII left) per text, parallel workers, cached; skipped when no OPENROUTER_API_KEY
  4. decide    pass / review_low / review_high (code hits can't be cleared by Jev)
  5. context   connected datapoints per suspicious span: thread neighbours, other records with the
               same text, registry matches, people in the record, keep-list, first-name/surname lists
  6. reasoning (pipeline/reasoning.py) for review cases -> verdict, proposed fix, question for a human
"""
from __future__ import annotations

import concurrent.futures as cf
import hashlib
import json
import os
import re
import time
from pathlib import Path

from pipeline import jev
from pipeline.fileio import write_json
from pipeline.core import KEEP, KEEP_FOLD, RX_ADDRESS, RX_EMAIL, SURNAMES, folded
from pipeline.registry import FIRST_NAMES
from pipeline.text import fold, slug

LOW = float(os.environ.get("VERIFY_LOW", "0.2"))
HIGH = float(os.environ.get("VERIFY_HIGH", "0.7"))
WORKERS = int(os.environ.get("VERIFY_WORKERS", "16"))
MAX_CASES = int(os.environ.get("VERIFY_MAX_CASES", "300"))

RX_PH = re.compile(r"\[[A-Z_]+(?:_(?:\d+|P))?\]")
RX_CAPSEQ = re.compile(r"(?<![\w\[])([A-ZÁÉÍÓÚÑ][a-záéíóúñü]+(?:[ \t]+(?:de[ \t]+la[ \t]+|de[ \t]+|del[ \t]+)?[A-ZÁÉÍÓÚÑ][a-záéíóúñü'’]+){1,3})")
RX_ALLCAPS = re.compile(r"(?<![\w\[])([A-ZÁÉÍÓÚÑ]{3,}(?:[ \t]+[A-ZÁÉÍÓÚÑ]{2,}){1,3})(?![\w\]])")
RX_DATEISH = re.compile(r"^\d{4}-\d{2}(-\d{2})?$|^\d{1,2}[/-]\d{1,2}[/-]\d{2,4}$|^\d{4}\s*-\s*\d{4}$")
RX_TABLE_NUMS = re.compile(r"^\d{1,3}(?:[ \t]+\d{1,3})+$")
RX_INITIAL = re.compile(r"(?<![\w.])([A-ZÁÉÍÓÚÑ]\.[ \t]?[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:[ \t]+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)?)")
RX_IP = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_B = json.loads((Path(__file__).resolve().parent.parent / "resources" / "barrios_by_city.json").read_text("utf-8"))
BARRIO_FOLD = {fold(b) for c, zs in _B.items() if not c.startswith("_") for bs in zs.values() for b in bs}
RX_PH_SURNAME = re.compile(r"(\[NOMBRE_(?:\d+|P)\])[ \t]+([A-ZÁÉÍÓÚÑ][a-záéíóúñ'’]{2,}(?:[ \t]+[A-ZÁÉÍÓÚÑ][a-záéíóúñ'’]{2,})?)")
RX_ARTICLE_NICK = re.compile(r"(?<![\w])(?:la|el)[ \t]+([A-ZÁÉÍÓÚÑ][a-záéíóúñ]{2,})(?![\w])")
RX_UNKNOWN_PAIR = re.compile(r"(?<=[a-záéíóúñ,;:]\s)([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+[ \t]+[A-ZÁÉÍÓÚÑ][a-záéíóúñA-Z'’]+)")
STOP_CAP = {"hola", "gracias", "buenas", "buenos", "señor", "señora", "estimado", "estimada", "caso", "cuenta",
            "tarjeta", "app", "plan", "pago", "banco", "comité", "acta", "equipo", "soporte", "cliente", "nota",
            "asunto", "fecha", "total", "mora", "estado", "anexo", "tabla", "sección", "resumen", "política",
            "reclamo", "rechazo", "doble", "queja", "peticion", "petición", "solicitud", "compra", "retiro",
            "cobro", "transferencia", "consulta", "error", "bloqueo", "cambio", "cerrado", "abierto",
            "pendiente", "aprobada", "rechazada", "cajero", "cuota", "corresponsal", "tarjeta", "credito",
            "crédito", "cupo", "saldo", "debito", "débito", "nomina", "nómina", "seguro", "poliza", "póliza", "certificate", "annex", "annexes", "schedule", "section"}
MERCHANT_CUE = re.compile(r"(?:compra|pago|retiro|parqueadero|recarga)\s+\[NOMBRE_(?:\d+|P)\]", re.I)
CITY_FOLD = {"bogota", "medellin", "cali", "barranquilla", "bucaramanga", "cartagena", "pereira", "colombia"}
RX_SINGLE = re.compile(r"(?<=[a-záéíóúñ,;:]\s)([A-ZÁÉÍÓÚÑ][a-záéíóúñ]{2,})(?![\w])")
RX_DIGITRUN = re.compile(r"(?<![\w-])\d[\d.\s-]{5,16}\d(?![\w-])")
RX_OBFUSC = re.compile(r"\b[\w.]+\s*(?:arroba|\[at\]|\(at\)|\sat\s)\s*[\w-]+\s*(?:punto|\[dot\]|\(dot\)|\sdot\s)\s*\w+", re.I)
INTERNAL_ID = re.compile(r"\b(?:CL|TX|KP|PQR|PG|INC|DOC)-[\w-]+|\b\d{4}-\d{4}\b")
MONEY_CUE = re.compile(r"(\$|cop|pesos|cuota|monto|saldo|cupo|valor|millones|mil\b|usd)", re.I)
PH_KIND = {"name": "NOMBRE", "email": "EMAIL", "phone": "TELEFONO", "cedula": "CEDULA", "address": "DIRECCION",
           "birth": "FECHA_NAC", "plate": "PLACA", "passport": "PASAPORTE", "handle": "USUARIO",
           "identifier": "ID", "account": "ID"}
CASES = "cases.json"


# ------------------------------------------------------------------ masking
class Masker:
    """Builds, from the pseudonym map, every form we may have written, and masks them."""

    def __init__(self, pmap_data: dict, entity_records: dict[str, set[str]] | None = None):
        self.forms: dict[str, tuple[str, str]] = {}  # folded form -> (entity_id, kind)
        self.single: dict[str, set[str]] = {}         # folded single first name -> entity_ids
        self.entity_records = entity_records or {}
        for ent in pmap_data.get("entities", {}).values():
            eid, f, s1, s2 = ent["entity_id"], ent["first"], ent["s1"], ent["s2"]
            f1 = f.split()[0]
            for form in {f"{f} {s1} {s2}", f"{f} {s1}", f"{f1} {s1}", f"{f1} {s1} {s2}", f, f1}:
                if " " not in form:
                    self.single.setdefault(fold(form), set()).add(eid)
                    continue
                self.forms.setdefault(fold(form), (eid, "name"))
                if " " in form:
                    self.forms.setdefault(slug(form), (eid, "name"))
                    self.forms.setdefault(slug(form, "-"), (eid, "name"))
            for vk, val in ent.get("values", {}).items():
                kind = vk.split("|", 1)[0]
                self.forms.setdefault(fold(val), (eid, kind))
                if kind in ("cedula", "phone"):
                    self.forms.setdefault(re.sub(r"\D", "", val), (eid, kind))
                if kind == "email":
                    loc, _, dom = val.partition("@")
                    self.forms.setdefault(fold(f"{loc} arroba " + " punto ".join(dom.split("."))), (eid, kind))
        keys = sorted(self.forms, key=len, reverse=True)
        self.rx = re.compile(r"(?<![\w])(" + "|".join(r"[\s_-]*".join(map(re.escape, re.split(r"\s+", k)))
                                                      for k in keys) + r")(?![\w])") if keys else None

    def mask(self, text: str, rid: str = "") -> tuple[str, list[str]]:
        """Return (masked_text, entity_ids found). Placeholders are numbered per text per entity.
        Single first names are masked only for people that appear in this record."""
        if not self.rx or not text:
            return self._mask_single(text, rid), []
        f = folded(text)
        out, last, num, ents = [], 0, {}, []
        for m in self.rx.finditer(f):
            key = re.sub(r"[\s_-]+", " ", m.group(1)).strip()
            hit = self.forms.get(key) or self.forms.get(m.group(1)) or self.forms.get(slug(key))
            if not hit:
                continue
            eid, kind = hit
            ents.append(eid)
            tag = PH_KIND.get(kind, "DATO")
            if tag == "NOMBRE":
                num.setdefault(eid, len(num) + 1)
                tag = f"NOMBRE_{num[eid]}"
            out.append(text[last:m.start()] + f"[{tag}]")
            last = m.end()
        out.append(text[last:])
        # digits-only forms (cedulas/phones written with separators)
        masked = "".join(out)
        for m in list(RX_DIGITRUN.finditer(masked))[::-1]:
            d = re.sub(r"\D", "", m.group(0))
            hit = self.forms.get(d) or self.forms.get(d[-10:])
            if hit:
                masked = masked[:m.start()] + f"[{PH_KIND.get(hit[1], 'DATO')}]" + masked[m.end():]
                ents.append(hit[0])
        return self._mask_single(masked, rid), sorted(set(ents))


    def _mask_single(self, text: str, rid: str) -> str:
        if not rid:
            return text
        allowed = {w: e for w, ids in self.single.items() for e in ids if rid in self.entity_records.get(e, ())}
        if not allowed:
            return text
        rx = re.compile(r"(?<![\w\[])(" + "|".join(map(re.escape, sorted(allowed, key=len, reverse=True))) + r")(?![\w\]])")
        f = folded(text)
        out, last = [], 0
        for m in rx.finditer(f):
            out.append(text[last:m.start()] + "[NOMBRE_P]")
            last = m.end()
        out.append(text[last:])
        return "".join(out)


# ------------------------------------------------------------------ residual check
def residual(masked: str, keep_extra: set[str] = frozenset()) -> list[dict]:
    """Suspicious spans left in masked text. strength: strong | weak."""
    hits = []
    keep = KEEP_FOLD | {fold(k) for k in keep_extra}

    def kept(s):
        fs = fold(s)
        return fs in keep or any(k in fs for k in keep if " " in k) or any(fs == fold(k) for k in KEEP)

    for m in RX_CAPSEQ.finditer(masked):
        cand = m.group(1)
        toks = [fold(t) for t in cand.split() if t.lower() not in ("de", "del", "la")]
        if kept(cand) or len(toks) < 2:
            continue
        if any(t in CITY_FOLD for t in toks):
            continue
        if toks[0] in FIRST_NAMES or (toks[0] in SURNAMES and toks[1] in SURNAMES):
            hits.append({"span": cand, "start": m.start(1), "reason": "possible_name", "strength": "strong"})
    for m in RX_ALLCAPS.finditer(masked):
        cand = m.group(1)
        toks = [fold(t) for t in cand.split()]
        if not kept(cand) and not any(t in CITY_FOLD for t in toks) and toks[0] in FIRST_NAMES:
            hits.append({"span": cand, "start": m.start(1), "reason": "possible_name_caps", "strength": "strong"})
    for m in RX_INITIAL.finditer(masked):
        toks = [fold(t) for t in re.split(r"[ .\t]+", m.group(1)) if len(t) > 1]
        if toks and len(toks[0]) >= 4 and toks[0] not in STOP_CAP and not kept(m.group(1)) \
                and toks[0] not in CITY_FOLD:
            hits.append({"span": m.group(1), "start": m.start(1), "reason": "possible_name",
                         "strength": "strong" if toks[0] in SURNAMES else "weak"})
    for m in RX_PH_SURNAME.finditer(masked):
        cand = m.group(2)
        if MERCHANT_CUE.search(masked[max(0, m.start() - 12):m.end(1)]):
            continue  # "Compra Juan Valdez ...": a merchant, the first name was a coincidence
        if not kept(cand) and fold(cand.split()[0]) not in STOP_CAP and fold(cand.split()[0]) not in CITY_FOLD:
            hits.append({"span": cand, "start": m.start(2), "reason": "surname_next_to_placeholder", "strength": "strong"})
    for m in RX_ARTICLE_NICK.finditer(masked):
        w = m.group(1)
        if not kept(w) and fold(w) not in STOP_CAP and fold(w) not in CITY_FOLD and fold(w) not in BARRIO_FOLD:
            hits.append({"span": w, "start": m.start(1), "reason": "possible_nickname", "strength": "weak"})
    for m in RX_UNKNOWN_PAIR.finditer(masked):
        cand = m.group(1)
        toks = [fold(t) for t in cand.split()]
        if kept(cand) or toks[0] in STOP_CAP or any(t in CITY_FOLD or t in BARRIO_FOLD for t in toks) \
                or toks[0] in FIRST_NAMES or any(h["start"] <= m.start(1) < h["start"] + len(h["span"]) for h in hits):
            continue
        hits.append({"span": cand, "start": m.start(1), "reason": "possible_name_unknown", "strength": "weak"})
    for m in RX_IP.finditer(masked):
        hits.append({"span": m.group(0), "start": m.start(), "reason": "possible_id_number", "strength": "strong"})
    for m in RX_SINGLE.finditer(masked):
        w = m.group(1)
        prev = masked[max(0, m.start(1) - 25):m.start(1)].split()
        if prev and prev[-1][:1].isupper():
            continue  # part of a multi-word proper noun (Santa Mónica, Plaza Botero)
        if any(fold(b).endswith(fold(w)) for b in BARRIO_FOLD if fold(w) in fold(b)):
            continue
        if fold(w) in FIRST_NAMES and not kept(w) and not any(h["start"] <= m.start(1) < h["start"] + len(h["span"]) for h in hits):
            hits.append({"span": w, "start": m.start(1), "reason": "possible_first_name", "strength": "weak"})
    for m in RX_DIGITRUN.finditer(masked):
        d = re.sub(r"\D", "", m.group(0))
        ctx = masked[max(0, m.start() - 25):m.start()]
        raw = m.group(0).strip()
        if RX_DATEISH.match(raw) or RX_TABLE_NUMS.match(raw) or re.search(r"\bNIT\b", ctx, re.I) \
                or "\n" in raw:
            continue
        if 6 <= len(d) <= 12 and not INTERNAL_ID.search(masked[max(0, m.start() - 4):m.end() + 1]) \
                and not MONEY_CUE.search(ctx) and not re.fullmatch(r"\d{1,3}(\.\d{3})+", raw):
            hits.append({"span": m.group(0).strip(), "start": m.start(), "reason": "possible_id_number", "strength": "strong"})
    for rx, reason in ((RX_EMAIL, "possible_contact"), (RX_OBFUSC, "possible_contact"), (RX_ADDRESS, "possible_address")):
        for m in rx.finditer(masked):
            if not RX_PH.search(m.group(0)):
                hits.append({"span": m.group(0), "start": m.start(), "reason": reason, "strength": "strong"})
    hits.sort(key=lambda h: (h["strength"] != "strong", h["start"]))
    return hits


# ------------------------------------------------------------------ jev
QUESTION = ("Ignore every bracketed placeholder such as [NOMBRE_1], [CEDULA] or [EMAIL]: they are already "
            "anonymized. Does the remaining text still contain a real person's name, nickname, ID number, "
            "phone, email or street address?")


def jev_scores(texts: dict[str, str], cache, workers: int = WORKERS) -> dict[str, float | None]:
    """text_id -> P(real PII left). Parallel, cached, deduplicated by content."""
    if not jev.enabled():
        return {k: None for k in texts}
    by_hash: dict[str, list[str]] = {}
    for tid, t in texts.items():
        by_hash.setdefault(hashlib.sha1(t.encode()).hexdigest(), []).append(tid)
    uniq = {h: texts[ids[0]] for h, ids in by_hash.items()}

    def ask(h):
        return h, jev.yes_probability(QUESTION, uniq[h][:24000], cache)

    out: dict[str, float | None] = {}
    with cf.ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for h, p in ex.map(ask, uniq):
            for tid in by_hash[h]:
                out[tid] = p
    return out


def decide(hits: list[dict], p: float | None) -> str:
    if any(h["strength"] == "strong" for h in hits) or (p is not None and p > HIGH):
        return "review_high"
    if hits or (p is not None and p >= LOW):
        return "review_low"
    return "pass"


# ------------------------------------------------------------------ main
def _texts(rec: dict, source: str) -> list[tuple[str, str]]:
    if source == "chats":
        return [("text", rec.get("text", ""))] + [(f"attachments.{i}", a.get("filename", ""))
                                                  for i, a in enumerate(rec.get("attachments", []))]
    if source == "docs":
        return [("title", rec.get("title", "")), ("body", rec.get("body", ""))] + \
            [(f"metadata.{k}", v) for k, v in rec.get("metadata", {}).items() if isinstance(v, str)]
    skip = {"cliente_id", "tx_id", "radicado", "id", "fecha", "fecha_vinculacion", "monto", "tipo", "canal",
            "estado", "ciudad", "cargo", "ingreso", "tipo_doc", "documento_tipo"}
    return [(f"columns.{k}", v) for k, v in rec.get("columns", {}).items() if isinstance(v, str) and k not in skip]


def verify(out_dir: Path, state_dir: Path, registry=None, knowledge_data: dict | None = None,
           use_jev: bool = True, use_reasoning: bool = True, progress=None) -> dict:
    t0 = time.time()
    pmap_data = json.loads((state_dir / "pseudonym_map.json").read_text("utf-8"))
    kn = knowledge_data or {}
    ents_file = out_dir / "entities.json"
    entity_records = {e["entity_id"]: set(e.get("records", [])) for e in
                      json.loads(ents_file.read_text("utf-8")).get("entities", [])} if ents_file.exists() else {}
    masker = Masker(pmap_data, entity_records)
    eid_to_key = {e["entity_id"]: k for k, e in pmap_data["entities"].items()}
    records = {}
    for src in ("chats", "docs", "tables"):
        p = out_dir / f"{src}.jsonl"
        if p.exists():
            for line in p.read_text("utf-8").splitlines():
                if line.strip():
                    r = json.loads(line)
                    records[r["id"]] = (src, r)
    threads: dict[str, list[str]] = {}
    for rid, (src, r) in records.items():
        if src == "chats":
            threads.setdefault(r.get("thread_id", ""), []).append(rid)
    for t in threads.values():
        t.sort(key=lambda i: records[i][1].get("ts", ""))

    # 1-2: mask + residual (fast, all records)
    items, masked_by_rid = [], {}
    seen_text: dict[str, list] = {}
    for rid, (src, r) in records.items():
        parts = []
        for field, text in _texts(r, src):
            if not text:
                continue
            m, ents = masker.mask(text, rid)
            parts.append((field, m, ents))
        masked_by_rid[rid] = parts
        for field, m, ents in parts:
            hits = residual(m, set(kn.get("keep", [])))
            items.append({"rid": rid, "source": src, "field": field, "masked": m, "entities": ents, "hits": hits})
            for h in hits:
                seen_text.setdefault(fold(h["span"]), []).append(rid)

    # weak hits whose text repeats in many records are titles/products/phrases, not people
    for i in items:
        i["hits"] = [h for h in i["hits"] if h["strength"] == "strong" or len(set(seen_text.get(fold(h["span"]), []))) <= 2]

    # 3: jev on free text only, deduplicated, parallel
    free = {f"{i['rid']}::{i['field']}": i["masked"] for i in items
            if i["source"] != "tables" or i["field"] in ("columns.notas", "columns.descripcion", "columns.respuesta")}
    jcache = jev.JevCache(state_dir / "jev_cache.json")
    scores = jev_scores(free, jcache) if use_jev else {}
    jcache.flush()

    # 4: decide
    counts = {"pass": 0, "review_low": 0, "review_high": 0}
    flagged = []
    for i in items:
        p = scores.get(f"{i['rid']}::{i['field']}")
        i["jev_p"] = p
        i["decision"] = decide(i["hits"], p)
        counts[i["decision"]] += 1
        if i["decision"] != "pass":
            flagged.append(i)
    flagged.sort(key=lambda x: (x["decision"] != "review_high", -(x["jev_p"] or 0)))

    # 5: cases with connected datapoints
    cases = []
    for i in flagged[:MAX_CASES]:
        main = i["hits"][0] if i["hits"] else {"span": "", "reason": "jev_only", "strength": "weak", "start": 0}
        span = main["span"]
        ctx = {"window": i["masked"][max(0, main["start"] - 220): main["start"] + len(span) + 220]}
        src, rec = records[i["rid"]]
        if src == "chats":
            ids = threads.get(rec.get("thread_id", ""), [])
            pos = ids.index(i["rid"]) if i["rid"] in ids else 0
            ctx["thread"] = [{"id": x, "author": records[x][1].get("author"),
                              "text": next((m for f, m, _ in masked_by_rid.get(x, []) if f == "text"), "")[:300],
                              "this": x == i["rid"]} for x in ids[max(0, pos - 3): pos + 4]]
        same = [x for x in seen_text.get(fold(span), []) if x != i["rid"]] if span else []
        ctx["same_text_elsewhere"] = {"count": len(set(same)), "examples": sorted(set(same))[:5]}
        reg_info = {}
        if registry is not None and span:
            fs = fold(span)
            reg_info["known_person_form"] = sorted(registry.by_form.get(fs, set()))[:5]
            first = fs.split()[0] if fs else ""
            reg_info["first_token_is_first_name"] = first in FIRST_NAMES
            reg_info["has_surname_token"] = any(t in SURNAMES for t in fs.split()[1:])
            reg_info["people_with_this_first_name"] = sorted(
                k for k, pp in registry.people.items() if pp.first and fold(pp.first[0]) == first)[:8]
        reg_info["in_keep_list"] = fold(span) in KEEP_FOLD if span else False
        ctx["registry"] = reg_info
        ctx["people_in_record"] = [{"entity_id": e, "key": eid_to_key.get(e, "")} for e in i["entities"]][:10]
        cid = "c_" + hashlib.sha1(f"{i['rid']}|{i['field']}|{span}".encode()).hexdigest()[:10]
        cases.append({"case_id": cid, "record_id": i["rid"], "source": i["source"], "field": i["field"],
                      "span": span, "reason": main["reason"], "all_hits": i["hits"][:8],
                      "priority": "high" if i["decision"] == "review_high" else "low",
                      "jev_p": i["jev_p"], "masked": i["masked"][:4000], "context": ctx,
                      "status": "open", "reasoning": None})

    # 6: reasoning on cases (parallel)
    if use_reasoning and cases:
        from pipeline import reasoning
        reasoning.reason_all(cases, state_dir, progress=progress)

    prev = load_cases(state_dir)
    answered = {c["case_id"]: c for c in prev.get("cases", []) if c.get("status") == "answered"}
    for c in cases:
        if c["case_id"] in answered:
            c.update({k: answered[c["case_id"]][k] for k in ("status", "answer") if k in answered[c["case_id"]]})
    result = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "seconds": round(time.time() - t0, 1),
              "records": len(records), "texts_checked": len(items), "jev_enabled": jev.enabled() and use_jev,
              "decisions": counts, "cases": cases,
              "thresholds": {"low": LOW, "high": HIGH}}
    write_json(state_dir / CASES, result)
    return result


def load_cases(state_dir: Path) -> dict:
    p = state_dir / CASES
    return json.loads(p.read_text("utf-8")) if p.exists() else {"cases": []}
