"""Full rebuild (POST /run): ingest -> normalize -> registry -> anonymize -> write -> check.

Usage (CLI):
  python -m pipeline.run                      # download with SOURCES_URL / SOURCES_TOKEN from .env
  python -m pipeline.run --offline            # reuse data/raw
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import threading
import time
from pathlib import Path

from pipeline import ingest, jev, knowledge, normalize, output, snapshot
from pipeline.core import Anonymizer
from pipeline.process import Processor
from pipeline.pseudonyms import PseudonymMap
from pipeline.registry import Registry

DATA = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
RAW = DATA / "raw"
OUT = DATA / "output"
STATE_DIR = DATA / "state"
MAP = STATE_DIR / "pseudonym_map.json"
THREADS = STATE_DIR / "thread_people.json"
LINKS = STATE_DIR / "uid_links.json"


class State:
    """In-memory state shared by /run and /anonymize."""

    def __init__(self):
        self.lock = threading.RLock()
        self.pmap = PseudonymMap(MAP)
        self.jev_cache = jev.JevCache(STATE_DIR / "jev_cache.json")
        self.processor: Processor | None = None
        self.warm_start()

    def build(self, records: dict) -> Processor:
        users = normalize.load_users(RAW) if (RAW / "chats" / "users.json").exists() else {}
        by_user: dict[str, list[str]] = {}
        threads_by_user: dict[str, set[str]] = {}
        for r in records.get("chats", []):
            by_user.setdefault(r.get("_uid") or "", []).append(r.get("text", ""))
            threads_by_user.setdefault(r.get("_uid") or "", set()).add(r.get("thread_id", ""))
        thread_people = json.loads(THREADS.read_text("utf-8")) if THREADS.exists() else {}
        prior_people = {u: {k for t in ts for k in thread_people.get(t, [])} for u, ts in threads_by_user.items()}
        prior_links = json.loads(LINKS.read_text("utf-8")) if LINKS.exists() else {}
        reg = Registry()
        reg.build(records, users, by_user, prior_links, prior_people)
        kn = knowledge.load()
        knowledge.apply_to_registry(reg, kn)
        anon = Anonymizer(reg, self.pmap, self.jev_cache, kn)
        if THREADS.exists():
            anon.thread_people = {k: set(v) for k, v in json.loads(THREADS.read_text("utf-8")).items()}
        return Processor(anon)

    def warm_start(self) -> None:
        """After a restart, rebuild the registry from data/raw (no download) so /anonymize
        keeps resolving known people. Without data it still works (cold start)."""
        try:
            if (RAW / "record_index.json").exists():
                self.processor = self.build(normalize.normalize(RAW))
        except Exception:
            self.processor = None
        if self.processor is None:
            self.processor = Processor(Anonymizer(Registry(), self.pmap, self.jev_cache))

    def save(self) -> None:
        self.pmap.save()
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        THREADS.write_text(json.dumps({k: sorted(v) for k, v in self.processor.a.thread_people.items()},
                                      ensure_ascii=False), "utf-8")
        LINKS.write_text(json.dumps(self.processor.reg.uid_links(), ensure_ascii=False), "utf-8")


def run_pipeline(sources_url: str | None, token: str | None, state: State, offline: bool = False) -> dict:
    t0 = time.time()
    summary: dict = {}
    with state.lock:
        prev_snapshot = snapshot.load(STATE_DIR)
        prev_entities = json.loads((OUT / "entities.json").read_text("utf-8")) if (OUT / "entities.json").exists() else None
        if not offline:
            summary["ingest"] = ingest.ingest(sources_url, token, RAW)
        records = normalize.normalize(RAW)
        summary["missing_in_sources"] = records.pop("_missing", {})
        cur_snapshot = snapshot.take(RAW, records)
        summary["changes"] = snapshot.diff(prev_snapshot, cur_snapshot)
        # Self-check: if any real value appears in the output (e.g. a generated value that
        # happens to equal a real one), forbid it and reprocess. Up to 3 passes.
        extra_reserved: set[str] = set()
        passes = 0
        while True:
            passes += 1
            state.processor = state.build(records)
            if extra_reserved:
                state.pmap.reserve(extra_reserved, extra_reserved)
                state.processor.a.pmap.reserve(extra_reserved, extra_reserved)
            out, entities = state.processor.run(records, prev_entities=prev_entities)
            tmp = DATA / "output.tmp"
            if tmp.exists():
                shutil.rmtree(tmp)
            output.write_output(tmp, out, entities)
            leaks = output.leak_sweep(tmp, entities, limit=200)
            if not leaks or passes >= 3:
                break
            extra_reserved |= {l["original"] for l in leaks}
        summary["self_check_passes"] = passes
        if OUT.exists():
            shutil.rmtree(OUT)
        tmp.rename(OUT)  # atomic swap: /output never serves a half-written run
        state.save()
        snapshot.save(STATE_DIR, cur_snapshot)

        summary["records"] = {k: len(v) for k, v in out.items()}
        summary["entities"] = {"people": sum(1 for e in entities if not e.get("kept")),
                               "kept": sum(1 for e in entities if e.get("kept"))}
        summary["stability"] = snapshot.stability(prev_entities, entities)
        summary["leaks"] = leaks[:30]
        summary["unmapped_occupations"] = sorted(set(state.processor.a.occ.misses))
        summary["jev"] = {"calls": state.jev_cache.calls, "tokens_in": state.jev_cache.tokens_in,
                          "cost_usd": round(state.jev_cache.cost, 6)}
        summary["seconds"] = round(time.time() - t0, 1)
        (STATE_DIR / "last_run.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), "utf-8")
    return summary


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv  # optional
        load_dotenv()
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources-url", default=os.environ.get("SOURCES_URL"))
    ap.add_argument("--token", default=os.environ.get("SOURCES_TOKEN"))
    ap.add_argument("--offline", action="store_true")
    a = ap.parse_args()
    print(json.dumps(run_pipeline(a.sources_url, a.token, State(), a.offline), indent=1, ensure_ascii=False))
