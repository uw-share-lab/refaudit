"""Closing the window that merge-on-flush only narrowed.

0.4.0 made ``flush()`` merge what is on disk instead of overwriting it, so two
runs sharing a cache file stopped erasing each other wholesale. That left a real
but small race: between reading the file and replacing it, another run can
complete a whole flush, and its entries are then overwritten by ours.

An advisory lock held across read-merge-write closes it. The lock is best
effort by design -- on a filesystem that cannot honour it we still flush, since
losing a cached lookup is a slower run, and refusing to write at all would be a
worse outcome than the race we are fixing.
"""

import json
import threading
import time

from refaudit.cache import Cache
from refaudit.filelock import exclusive as _exclusive


def _entries(path):
    return json.loads(path.read_text(encoding="utf-8"))["entries"]


# --- the lock itself --------------------------------------------------------

def test_a_second_holder_waits_for_the_first(tmp_path):
    lock = tmp_path / "c.json.lock"
    order = []

    def second():
        with _exclusive(lock):
            order.append("second")

    with _exclusive(lock):
        t = threading.Thread(target=second)
        t.start()
        time.sleep(0.25)          # ample time to barge in if the lock is not held
        order.append("first")
    t.join(timeout=5)

    assert order == ["first", "second"], "the second holder entered while the first held it"


def test_the_lock_is_released_even_if_the_body_raises(tmp_path):
    lock = tmp_path / "c.json.lock"
    try:
        with _exclusive(lock):
            raise RuntimeError("boom")
    except RuntimeError:
        pass

    done = threading.Event()
    threading.Thread(target=lambda: (_exclusive(lock).__enter__(), done.set())).start()
    assert done.wait(timeout=5), "lock was not released after an exception"


# --- what it protects -------------------------------------------------------

class _SlowMerge(Cache):
    """A cache whose merge window is wide enough to race against on purpose."""

    def _read_file(self):
        data = super()._read_file()
        time.sleep(0.4)
        return data


def test_a_flush_landing_inside_another_flushs_merge_window_is_not_lost(tmp_path):
    """The exact race merge-on-flush left open.

    Without the lock, `other` completes its whole flush while `slow` sits
    between reading and replacing, and `slow` then writes a merge that never
    saw it. With the lock, `other` waits its turn.
    """
    path = tmp_path / "cache.json"
    slow = _SlowMerge(path)
    other = Cache(path)

    slow.put("from-slow", {"verdict": "OK"})
    other.put("from-other", {"verdict": "OK"})

    t = threading.Thread(target=slow.flush)
    t.start()
    time.sleep(0.1)               # let `slow` get inside its merge window
    other.flush()
    t.join(timeout=10)

    assert set(_entries(path)) == {"from-slow", "from-other"}


def test_many_concurrent_flushes_keep_every_entry(tmp_path):
    path = tmp_path / "cache.json"
    caches = [Cache(path) for _ in range(8)]
    for i, c in enumerate(caches):
        c.put(f"key-{i}", {"verdict": "OK"})

    threads = [threading.Thread(target=c.flush) for c in caches]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert set(_entries(path)) == {f"key-{i}" for i in range(8)}


def test_flushing_still_works_when_the_lock_cannot_be_taken(tmp_path, monkeypatch):
    """Best effort: an unlockable filesystem must not stop us writing."""
    import refaudit.filelock as filelock_mod

    # No kernel lock and no lockable directory either: the last-resort path.
    monkeypatch.setattr(filelock_mod, "_lock_fd", None)
    monkeypatch.setattr("os.mkdir", lambda *a, **k: (_ for _ in ()).throw(
        OSError("read-only filesystem")))

    path = tmp_path / "cache.json"
    c = Cache(path)
    c.put("alpha", {"verdict": "OK"})
    c.flush()

    assert _entries(path)["alpha"]["value"] == {"verdict": "OK"}


def test_the_lock_file_sits_beside_the_cache_and_is_not_the_cache(tmp_path):
    """Locking the cache itself would be undone by the atomic replace."""
    path = tmp_path / "cache.json"
    c = Cache(path)
    c.put("alpha", {"verdict": "OK"})
    c.flush()

    assert path.exists()
    assert (tmp_path / "cache.json.lock").exists()
    assert _entries(path).keys() == {"alpha"}
