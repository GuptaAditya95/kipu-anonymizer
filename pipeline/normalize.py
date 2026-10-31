"""Stage 2 - Normalize: raw sources -> records in the output schema.

Status: IMPLEMENTED for structure (ids, fields, metadata keys, columns).
Produces REAL (not yet anonymized) records. Detection/replacement happen later.

Output schema (see ENUNCIADO "Que entregas"):
  chats : id, thread_id, author, ts, text, attachments[{filename}]
  docs  : id, format, filename, title, body, metadata{...}
  tables: id, table, columns{...all original columns as strings}

TODO / verify:
  - PDF metadata keys: we emit the non-empty standard keys (title, author, subject,
    keywords, creator, producer). Check the run detail for "perdio/anadio claves" and adjust.
  - HTML -> markdown conversion keeps comments; check similarity vs sample later.
"""
from __future__ import annotations

import csv
import io
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path

MENTION = re.compile(r"<@([A-Z0-9]+)>")


# ---------------------------------------------------------------- chats
def load_users(raw: Path) -> dict:
    users = json.loads((raw / "chats" / "users.json").read_text(encoding="utf-8"))
    return {u["id"]: u for u in users}


def user_name(u: dict | None, msg: dict) -> str:
    if u is None:
        return msg.get("username") or msg.get("user") or ""
    return (u.get("real_name") or u.get("profile", {}).get("real_name")
            or u.get("name") or "")


def normalize_chats(raw: Path) -> list[dict]:
    users = load_users(raw)
    out = []
    for ch_dir in sorted(p for p in (raw / "chats").iterdir() if p.is_dir()):
        canal = ch_dir.name
        for day in sorted(ch_dir.glob("*.json")):
            for m in json.loads(day.read_text(encoding="utf-8")):
                if "client_msg_id" not in m:
                    continue
                root_ts = m.get("thread_ts") or m["ts"]
                text = MENTION.sub(
                    lambda g: "@" + user_name(users.get(g.group(1)), {}), m.get("text", "")
                )
                out.append({
                    "_uid": m.get("user"),
                    "id": m["client_msg_id"],
                    "thread_id": f"{canal}:{root_ts}",
                    "author": user_name(users.get(m.get("user")), m),
                    "ts": m["ts"],
                    "text": text,
                    "attachments": [{"filename": f.get("name") or f.get("title") or ""}
                                    for f in m.get("files", [])],
                })
    return out


# ---------------------------------------------------------------- docs
FRONT = re.compile(r"^---\n(.*?)\n---\n", re.S)


def md_doc(text: str) -> tuple[str, dict]:
    meta = {}
    m = FRONT.match(text)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
    return text, meta  # body keeps the front matter (required)


def html_doc(text: str) -> tuple[str, dict]:
    from markdownify import markdownify

    meta = {}
    for m in re.finditer(r'<meta\s+[^>]*name="([^"]+)"[^>]*content="([^"]*)"', text, re.I):
        meta[m.group(1)] = m.group(2)
    t = re.search(r"<title>(.*?)</title>", text, re.I | re.S)
    if t:
        meta.setdefault("title", t.group(1).strip())
    comments = re.findall(r"<!--.*?-->", text, re.S)
    body = markdownify(text, heading_style="ATX").strip()
    if comments:  # keep comments (they may hide PII and must be anonymized, not dropped)
        body = "\n".join(comments) + "\n\n" + body
    return body, meta


def pdf_doc(path: Path) -> tuple[str, dict]:
    from pypdf import PdfReader

    r = PdfReader(str(path))
    body = "\n\n".join((p.extract_text() or "") for p in r.pages)
    meta = {}
    for k, v in (r.metadata or {}).items():
        key = k.lstrip("/").lower()
        if key in {"title", "author", "subject", "keywords", "creator", "producer"} and v:
            meta[key] = str(v)
    return body, meta


def normalize_docs(raw: Path) -> list[dict]:
    index = json.loads((raw / "docs" / "index.json").read_text(encoding="utf-8"))
    out = []
    for d in index:
        p = raw / "docs" / f"{d['id']}.{d['format']}"
        if d["format"] == "pdf":
            body, meta = pdf_doc(p)
        elif d["format"] == "html":
            body, meta = html_doc(p.read_text(encoding="utf-8"))
        else:
            body, meta = md_doc(p.read_text(encoding="utf-8"))
        title = meta.get("title") or d["filename"].rsplit(".", 1)[0].replace("_", " ")
        out.append({"id": d["id"], "format": d["format"], "filename": d["filename"],
                    "title": title, "body": body, "metadata": meta})
    return out


# ---------------------------------------------------------------- tables
TABLE_KEYS = {"clientes": "cliente_id", "transacciones": "tx_id"}


def csv_rows(path: Path, encoding: str, sep: str) -> list[dict]:
    text = path.read_bytes().decode(encoding)
    return list(csv.DictReader(io.StringIO(text), delimiter=sep))


def xml_rows(path: Path, tag: str) -> list[dict]:
    root = ET.fromstring(path.read_bytes())
    rows = []
    for el in root.iter(tag):
        cols = dict(el.attrib)
        for child in el:
            cols[child.tag] = (child.text or "").strip()
            for ak, av in child.attrib.items():  # e.g. <documento tipo="CC">
                cols[f"{child.tag}_{ak}"] = av
        rows.append(cols)
    return rows


KNOWN = {"clientes": ("cliente_id", None), "transacciones": ("tx_id", None),
         "pqrs": ("radicado", "pqr"), "empleados": ("id", "empleado")}


def _xml_row_tag(path: Path) -> str:
    root = ET.fromstring(path.read_bytes())
    tags = [c.tag for c in root]
    return max(set(tags), key=tags.count) if tags else ""


def _key_col(rows: list[dict], table: str, index_ids: set[str], default: str | None) -> str | None:
    if default and rows and default in rows[0]:
        return default
    keys = {i.split(":", 1)[1] for i in index_ids if i.startswith(table + ":")}
    for col in (rows[0] if rows else {}):
        if keys and sum(1 for r in rows[:50] if r.get(col) in keys) >= min(5, len(rows[:50])):
            return col
    return next(iter(rows[0]), None) if rows else None


def normalize_tables(raw: Path) -> list[dict]:
    """Tables are discovered from tables.json (name, format, charset, separator), so a new table,
    a different charset or separator after the incident doesn't break the run."""
    t = raw / "tables"
    meta = json.loads((t / "tables.json").read_text(encoding="utf-8")) if (t / "tables.json").exists() else [
        {"name": "clientes.csv", "format": "csv", "charset": "latin-1", "separator": ";"},
        {"name": "transacciones.csv", "format": "csv", "charset": "utf-8", "separator": ","},
        {"name": "pqrs.xml", "format": "xml"}, {"name": "empleados.xml", "format": "xml"}]
    idx_path = raw / "record_index.json"
    index_ids = set(json.loads(idx_path.read_text("utf-8")).get("tables", [])) if idx_path.exists() else set()
    out = []
    for m in meta:
        name = m["name"]
        table = name.rsplit(".", 1)[0]
        path = t / name
        if not path.exists():
            continue
        default_key, tag = KNOWN.get(table, (None, None))
        if m.get("format") == "xml" or name.endswith(".xml"):
            rows = xml_rows(path, tag or _xml_row_tag(path))
        else:
            cs = (m.get("charset") or "utf-8").replace("latin-1", "latin-1")
            rows = csv_rows(path, cs, m.get("separator") or ",")
        key = _key_col(rows, table, index_ids, default_key)
        for r in rows:
            out.append({"id": f"{table}:{r.get(key, '')}", "table": table, "columns": r})
    for rec in out:  # values as text
        rec["columns"] = {k: "" if v is None else str(v) for k, v in rec["columns"].items()}
    return out


def _nfc(obj):
    if isinstance(obj, str):
        return unicodedata.normalize("NFC", obj)
    if isinstance(obj, list):
        return [_nfc(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _nfc(v) for k, v in obj.items()}
    return obj


def normalize(raw: Path) -> dict[str, list[dict]]:
    """All sources in the output schema. Any id in record_index that couldn't be built is added
    as a pass-through placeholder (logged), so the output is always 1:1."""
    recs = {"chats": normalize_chats(raw), "docs": normalize_docs(raw), "tables": normalize_tables(raw)}
    recs = _nfc(recs)
    idx_path = raw / "record_index.json"
    missing = {}
    if idx_path.exists():
        index = json.loads(idx_path.read_text("utf-8"))
        for src, ids in index.items():
            have = {r["id"] for r in recs.get(src, [])}
            miss = [i for i in ids if i not in have]
            missing[src] = len(miss)
            for i in miss:
                if src == "chats":
                    recs[src].append({"id": i, "thread_id": f"unknown:{i}", "author": "", "ts": "",
                                      "text": "", "attachments": []})
                elif src == "docs":
                    recs[src].append({"id": i, "format": "md", "filename": "", "title": "", "body": "",
                                      "metadata": {}})
                else:
                    table, _, k = i.partition(":")
                    recs.setdefault(src, []).append({"id": i, "table": table, "columns": {}})
            # drop extras and duplicates not in the index
            seen, keep = set(), []
            for r in recs.get(src, []):
                if r["id"] in set(ids) and r["id"] not in seen:
                    seen.add(r["id"])
                    keep.append(r)
            recs[src] = keep
    recs["_missing"] = missing
    return recs
