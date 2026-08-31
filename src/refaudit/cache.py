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
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 2


class Cache:
    def __init__(self, path: str | Path, ttl_days: float = 90.0) -> None:
        self.path = Path(path)
        self.ttl = ttl_days * 86400.0
        self._data: dict[str, dict[str, Any]] = {}
        self._dirty = False
        # Checking runs across threads, and get() both reads and evicts, so the
        # dict needs guarding rather than relying on which operations happen to
        # be atomic today.
        self._lock = threading.RLock()
        self._load()

    def _read_file(self) -> dict[str, dict[str, Any]]:
        """Entries currently on disk, or none if the file is absent or unusable."""
        try:
            blob = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}  # corrupt or unreadable: start clean rather than crash
        if blob.get("schema") != SCHEMA_VERSION:
            return {}
        entries = blob.get("entries")
        return entries if isinstance(entries, dict) else {}

    def _load(self) -> None:
        self._data = self._read_file()

    def get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            item = self._data.get(key)
            if not item:
                return None
            if self.ttl and time.time() - item.get("stored_at", 0) > self.ttl:
                self._data.pop(key, None)
                self._dirty = True
                return None
            return item.get("value")

    def put(self, key: str, value: dict[str, Any]) -> None:
        with self._lock:
            self._data[key] = {"stored_at": time.time(), "value": value}
            self._dirty = True

    def flush(self) -> None:
        # Snapshot under the lock: flush() is called periodically during a run,
        # while worker threads are still writing results. Without this,
        # json.dumps walks a dict another thread is mutating.
        with self._lock:
            if not self._dirty:
                return
            mine = dict(self._data)
            self._dirty = False

        # Merge rather than overwrite. Two people running in the same directory
        # -- or two terminals, or a cluster job array -- share this file, and a
        # wholesale write would replace whatever the other run had finished with
        # only what this one happens to hold. Entries are independent, so the
        # union is always the better answer, and the newer timestamp wins a tie.
        #
        # This narrows the losing window to the moment between reading and
        # replacing rather than closing it: a lost entry costs a repeated lookup
        # on the next run, never a wrong result, which does not justify making
        # every install depend on file locking that differs per platform.
        merged = self._read_file()
        for key, item in mine.items():
            existing = merged.get(key)
            if existing is None or item.get("stored_at", 0) >= existing.get("stored_at", 0):
                merged[key] = item

        payload = json.dumps(
            {"schema": SCHEMA_VERSION, "entries": merged}, ensure_ascii=False
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)      # atomic on POSIX and Windows
        finally:
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    # PYI034 prefers Self, which is 3.11+; this package supports 3.10.
    def __enter__(self) -> Cache:  # noqa: PYI034
        return self

    def __exit__(self, *exc: object) -> None:
        self.flush()
