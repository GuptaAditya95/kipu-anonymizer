"""Orchestrator for a full rebuild (POST /run): stages 1-7.

Usage from the command line (useful while building with Pi):
  python -m pipeline.run --sources-url https://reto-pii-fuentes-production.up.railway.app/v1 \
                         --token $SOURCES_TOKEN
  python -m pipeline.run --offline     # reuse data/raw/ without downloading
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from pathlib import Path

from pipeline import core, ingest, normalize, output
from pipeline.pseudonyms import PseudonymMap

DATA = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
RAW = DATA / "raw"
OUT = DATA / "output"
MAP = DATA / "state" / "pseudonym_map.json"


class State:
    """In-memory state shared with /anonymize."""

    def __init__(self):
        self.pmap = PseudonymMap(MAP)
        self.registry = None  # TODO: pipeline.registry.Registry


def run_pipeline(sources_url: str | None, token: str | None, state: State,
                 offline: bool = False) -> dict:
    t0 = time.time()
    summary = {}
    if not offline:
        summary["ingest"] = ingest.ingest(sources_url, token, RAW)
    records = normalize.normalize(RAW)

    # TODO stage 3: state.registry = Registry(); state.registry.build(records, users)
    anonymized, entities = {}, []
    for source, recs in records.items():
        anonymized[source], ents = core.anonymize_records(source, recs, state)
        entities.extend(ents)

    tmp = DATA / "output.tmp"
    if tmp.exists():
        shutil.rmtree(tmp)
    output.write_output(tmp, anonymized, entities)
    if OUT.exists():
        shutil.rmtree(OUT)
    tmp.rename(OUT)  # atomic swap: /output never serves a half-written run
    state.pmap.save()

    summary["records"] = {k: len(v) for k, v in anonymized.items()}
    summary["seconds"] = round(time.time() - t0, 1)
    summary["leaks"] = output.leak_sweep(OUT, entities)
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources-url", default=os.environ.get("SOURCES_URL"))
    ap.add_argument("--token", default=os.environ.get("SOURCES_TOKEN"))
    ap.add_argument("--offline", action="store_true")
    a = ap.parse_args()
    print(json.dumps(run_pipeline(a.sources_url, a.token, State(), a.offline), indent=1,
                     ensure_ascii=False))
