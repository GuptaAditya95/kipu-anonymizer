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


def normalize_tables(raw: Path) -> list[dict]:
    t = raw / "tables"
    out = []
    for r in csv_rows(t / "clientes.csv", "latin-1", ";"):
        out.append({"id": f"clientes:{r['cliente_id']}", "table": "clientes", "columns": r})
    for r in csv_rows(t / "transacciones.csv", "utf-8", ","):
        out.append({"id": f"transacciones:{r['tx_id']}", "table": "transacciones", "columns": r})
    for r in xml_rows(t / "pqrs.xml", "pqr"):
        out.append({"id": f"pqrs:{r['radicado']}", "table": "pqrs", "columns": r})
    for r in xml_rows(t / "empleados.xml", "empleado"):
        out.append({"id": f"empleados:{r['id']}", "table": "empleados", "columns": r})
    for rec in out:  # values as text
        rec["columns"] = {k: "" if v is None else str(v) for k, v in rec["columns"].items()}
    return out


def normalize(raw: Path) -> dict[str, list[dict]]:
    return {"chats": normalize_chats(raw), "docs": normalize_docs(raw),
            "tables": normalize_tables(raw)}
