"""Checking entries in parallel must not change what is reported.

Parallelism here is a latency optimisation, so the contract is that it is
invisible: same results, same order, and no service asked to go faster than its
documented rate.
"""

from __future__ import annotations

import threading

from refaudit.cache import Cache
from refaudit.checker import Checker
from refaudit.models import Entry, Found, Record, Verdict
from refaudit.ratelimit import TokenBucket


class SlowResolver:
    """Sleeps like a network call and records peak concurrency."""

    name = "crossref:doi"

    def __init__(self):
        self.active = 0
        self.peak = 0
        self._lock = threading.Lock()

    def can_handle(self, entry):
        return True

    def resolve(self, entry):
        import time
        with self._lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        time.sleep(0.02)
        with self._lock:
            self.active -= 1
        return Found(Record(source="s", title=entry.title, year=2020,
                            first_author_surname="Smith", doi=entry.doi, url=""))


def entries(n):
    return [Entry(key=f"k{i}", entry_type="article",
                  fields={"title": f"Paper Number {i}", "year": "2020",
                          "author": "Smith, A", "doi": f"10.1145/{1000000 + i}"})
            for i in range(n)]


def test_results_keep_input_order_when_parallel():
    es = entries(20)
    out = list(Checker([SlowResolver()]).check_all(es, workers=8))
    assert [r.key for r in out] == [e.key for e in es]


def test_parallel_and_serial_agree():
    es = entries(12)
    serial = [(r.key, r.verdict) for r in Checker([SlowResolver()]).check_all(es, workers=1)]
    parallel = [(r.key, r.verdict) for r in Checker([SlowResolver()]).check_all(es, workers=6)]
    assert serial == parallel
    assert all(v is Verdict.OK for _, v in parallel)


def test_workers_actually_overlap():
    r = SlowResolver()
    list(Checker([r]).check_all(entries(20), workers=8))
    assert r.peak > 1, "workers>1 should overlap entries"


def test_a_single_entry_never_spawns_a_pool():
    r = SlowResolver()
    list(Checker([r]).check_all(entries(1), workers=8))
    assert r.peak == 1


def test_token_bucket_holds_the_documented_rate_under_threads():
    """Politeness must not depend on the worker count."""
    import time
    bucket = TokenBucket(rate=20.0, capacity=1.0)
    start = time.monotonic()

    def take():
        for _ in range(5):
            bucket.acquire()

    threads = [threading.Thread(target=take) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.monotonic() - start
    # 20 acquisitions at 20/s with a burst of 1 cannot finish faster than ~0.95s.
    assert elapsed >= 0.85, f"rate limit not honoured under threads ({elapsed:.2f}s)"


def test_cache_survives_concurrent_writes_and_flushes(tmp_path):
    """flush() serialises the dict while workers are still writing to it."""
    cache = Cache(tmp_path / "c.json", ttl_days=1)
    errors = []

    def hammer(n):
        try:
            for i in range(200):
                cache.put(f"k{n}-{i}", {"verdict": "OK"})
                if i % 20 == 0:
                    cache.flush()
        except Exception as exc:  # noqa: BLE001 - any exception at all is the bug
            errors.append(exc)

    threads = [threading.Thread(target=hammer, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, f"concurrent cache use raised: {errors[:1]}"
    cache.flush()
    assert Cache(tmp_path / "c.json", ttl_days=1).get("k0-0") == {"verdict": "OK"}
