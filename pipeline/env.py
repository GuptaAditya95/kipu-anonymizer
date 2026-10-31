"""Load KEY=VALUE pairs from a .env file into os.environ (without overriding variables that are
already set). Lets `python -m pipeline.run`, the server and the scripts read the same .env on
macOS, Linux and Windows. No extra dependency."""
from __future__ import annotations

import os
from pathlib import Path


def load_env(path: str | Path | None = None) -> None:
    p = Path(path) if path else Path(__file__).resolve().parent.parent / ".env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and v and k not in os.environ:
            os.environ[k] = v
