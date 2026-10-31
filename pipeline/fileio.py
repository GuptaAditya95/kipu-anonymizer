"""Atomic JSON writes that also work on Windows.

Windows refuses to replace a file that another thread/process (or antivirus) has open for a
moment ([WinError 5] Access is denied). We write to a unique temp file and retry the replace."""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path

_LOCKS: dict[str, threading.Lock] = {}
_GUARD = threading.Lock()


def _lock(path: Path) -> threading.Lock:
    with _GUARD:
        return _LOCKS.setdefault(str(path.resolve()), threading.Lock())


def write_json(path: Path, data, indent: int | None = 1, retries: int = 20) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=indent)
    with _lock(path):
        tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.tmp")
        tmp.write_text(text, encoding="utf-8")
        for i in range(retries):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                time.sleep(0.05 * (i + 1))
        try:  # last resort: write in place
            path.write_text(text, encoding="utf-8")
        finally:
            tmp.unlink(missing_ok=True)
