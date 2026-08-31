"""Versioned, on-disk cache of resolver results.

A run over a few hundred references takes minutes because it is deliberately
slow, so it must be resumable. Three properties matter:

* **Versioned.** The cache key includes a schema version; changing how records
  are interpreted invalidates old entries instead of silently mixing them.
* **Only successes are cached.** A failure usually means a service was busy, and
  caching that would bake a transient outage into every later run.
* **Written atomically.** A run interrupted mid-write must not leave a truncated
  JSON file that breaks the next run.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

SCHEMA_VERSION = 2


class Cache:
    def __init__(self, path: str | Path, ttl_days: float = 90.0) -> None:
        self.path = Path(path)
        self.ttl = ttl_days * 86400.0
        self._data: dict[str, dict[str, Any]] = {}
        self._dirty = False
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            blob = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return  # corrupt or unreadable: start clean rather than crash
        if blob.get("schema") != SCHEMA_VERSION:
            return
        self._data = blob.get("entries", {})

    def get(self, key: str) -> Optional[dict[str, Any]]:
        item = self._data.get(key)
        if not item:
            return None
        if self.ttl and time.time() - item.get("stored_at", 0) > self.ttl:
            self._data.pop(key, None)
            self._dirty = True
            return None
        return item.get("value")

    def put(self, key: str, value: dict[str, Any]) -> None:
        self._data[key] = {"stored_at": time.time(), "value": value}
        self._dirty = True

    def flush(self) -> None:
        if not self._dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"schema": SCHEMA_VERSION, "entries": self._data}, ensure_ascii=False
        )
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)      # atomic on POSIX and Windows
            self._dirty = False
        finally:
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    def __enter__(self) -> "Cache":
        return self

    def __exit__(self, *exc: object) -> None:
        self.flush()
