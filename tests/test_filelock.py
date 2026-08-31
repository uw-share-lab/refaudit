"""Cross-process locking, including where the kernel will not help.

``flock`` is the right primitive when it works: the kernel releases it when the
holder dies, so a crashed process cannot leave anything behind. It does not
always work. Some network mounts do not honour it, and on a filesystem without
a lock daemon it can appear to succeed while locking nothing.

The fallback is a lock *directory*, because ``mkdir`` is atomic almost
everywhere ``flock`` is not. That buys correctness at the cost of the one
problem ``flock`` does not have: a process that dies holding the directory
leaves it behind, and every later run blocks forever.

So the fallback is only safe with the two properties tested here. A lock older
than a run could plausibly hold is stolen, and waiting is bounded -- if we
cannot get the lock in time we say so and carry on without it, because a cache
is an optimisation and a paced run that never starts is worse than one that
occasionally repeats a lookup.
"""

from __future__ import annotations

import os
import threading
import time

import pytest

from refaudit import filelock
from refaudit.filelock import exclusive


@pytest.fixture
def no_kernel_lock(monkeypatch):
    """Force the mkdir fallback, which never runs on a developer's laptop."""
    monkeypatch.setattr(filelock, "_lock_fd", None)


# --- the fast path ----------------------------------------------------------

def test_the_lock_is_exclusive(tmp_path):
    order = []
    lock = tmp_path / "x.lock"

    def second():
        with exclusive(lock):
            order.append("second")

    with exclusive(lock):
        t = threading.Thread(target=second)
        t.start()
        time.sleep(0.25)
        order.append("first")
    t.join(timeout=10)

    assert order == ["first", "second"]


def test_it_reports_whether_the_lock_was_really_held(tmp_path):
    with exclusive(tmp_path / "x.lock") as held:
        assert held is True


def test_it_is_released_after_an_exception(tmp_path):
    lock = tmp_path / "x.lock"
    with pytest.raises(RuntimeError), exclusive(lock):
        raise RuntimeError("boom")

    with exclusive(lock) as held:
        assert held is True


# --- the mkdir fallback -----------------------------------------------------

def test_the_fallback_is_exclusive(tmp_path, no_kernel_lock):
    order = []
    lock = tmp_path / "x.lock"

    def second():
        with exclusive(lock):
            order.append("second")

    with exclusive(lock):
        t = threading.Thread(target=second)
        t.start()
        time.sleep(0.25)
        order.append("first")
    t.join(timeout=10)

    assert order == ["first", "second"]


def test_the_fallback_uses_a_directory(tmp_path, no_kernel_lock):
    """mkdir is atomic on filesystems where flock is not honoured, which is the
    entire reason this path exists."""
    lock = tmp_path / "x.lock"
    with exclusive(lock):
        assert (tmp_path / "x.lock.d").is_dir()
    assert not (tmp_path / "x.lock.d").exists(), "not cleaned up on exit"


def test_the_fallback_cleans_up_after_an_exception(tmp_path, no_kernel_lock):
    lock = tmp_path / "x.lock"
    with pytest.raises(RuntimeError), exclusive(lock):
        raise RuntimeError("boom")

    assert not (tmp_path / "x.lock.d").exists()
    with exclusive(lock) as held:
        assert held is True


def test_a_lock_left_by_a_dead_process_is_stolen(tmp_path, no_kernel_lock, monkeypatch):
    """The failure that makes a naive mkdir lock worse than no lock: without
    this, one crashed run blocks every future one forever."""
    monkeypatch.setattr(filelock, "STALE_AFTER", 0.2)
    lock = tmp_path / "x.lock"

    held = tmp_path / "x.lock.d"
    held.mkdir()
    (held / "owner").write_text("999999\n0.0\n", encoding="utf-8")
    time.sleep(0.25)

    with exclusive(lock, timeout=5.0) as got:
        assert got is True, "a stale lock must be stolen, not waited on forever"


def test_a_lock_a_live_process_is_holding_is_not_stolen(tmp_path, no_kernel_lock):
    """Stealing too eagerly would defeat the point of locking at all."""
    lock = tmp_path / "x.lock"
    stolen = []

    with exclusive(lock):
        def impatient():
            with exclusive(lock, timeout=0.4) as got:
                stolen.append(got)
        t = threading.Thread(target=impatient)
        t.start()
        t.join(timeout=10)

    assert stolen == [False], "the fresh lock was taken from its live holder"


def test_waiting_is_bounded_and_we_carry_on_without_the_lock(tmp_path, no_kernel_lock):
    """A cache is an optimisation. A run that never starts is worse than one
    that occasionally repeats a lookup."""
    lock = tmp_path / "x.lock"
    (tmp_path / "x.lock.d").mkdir()
    (tmp_path / "x.lock.d" / "owner").write_text(f"{os.getpid()}\n{time.time()}\n",
                                                 encoding="utf-8")

    started = time.monotonic()
    with exclusive(lock, timeout=0.3) as held:
        assert held is False, "must report that it is running unlocked"
    assert time.monotonic() - started < 3.0, "waited far past the timeout"


def test_an_unwritable_location_still_runs_the_body(tmp_path, no_kernel_lock, monkeypatch):
    """Refusing to work at all because a lock could not be taken would be a
    worse outcome than the race the lock exists to close."""
    def refuse(*a, **k):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(os, "mkdir", refuse)
    ran = []
    with exclusive(tmp_path / "x.lock", timeout=0.3) as held:
        ran.append(held)

    assert ran == [False]


def test_a_corrupt_owner_file_does_not_wedge_the_lock(tmp_path, no_kernel_lock, monkeypatch):
    """An unreadable owner file must be treated as stale rather than as a
    holder that never expires."""
    monkeypatch.setattr(filelock, "STALE_AFTER", 0.2)
    held = tmp_path / "x.lock.d"
    held.mkdir()
    (held / "owner").write_text("not a pid at all", encoding="utf-8")
    time.sleep(0.25)

    with exclusive(tmp_path / "x.lock", timeout=5.0) as got:
        assert got is True


def test_a_missing_owner_file_is_treated_as_stale(tmp_path, no_kernel_lock, monkeypatch):
    monkeypatch.setattr(filelock, "STALE_AFTER", 0.2)
    (tmp_path / "x.lock.d").mkdir()          # directory with no owner written yet
    time.sleep(0.25)

    with exclusive(tmp_path / "x.lock", timeout=5.0) as got:
        assert got is True


def test_many_threads_serialise_through_the_fallback(tmp_path, no_kernel_lock):
    """The property that matters: no two holders inside at once."""
    lock = tmp_path / "x.lock"
    inside = []
    peak = []

    def worker():
        with exclusive(lock, timeout=10.0):
            inside.append(1)
            peak.append(len(inside))
            time.sleep(0.02)
            inside.pop()

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert peak and max(peak) == 1, f"overlapping holders: peak {max(peak)}"


# --- the degradation paths the safety argument rests on --------------------

@pytest.mark.parametrize("errno_value", [
    __import__("errno").ENOLCK,        # no locks available on this filesystem
    __import__("errno").EOPNOTSUPP,    # the mount does not support it
    __import__("errno").EACCES,        # anything else
    None,                              # an OSError with no errno at all
])
def test_any_kernel_lock_failure_falls_back_to_the_directory(tmp_path, monkeypatch,
                                                             errno_value):
    """However flock fails, the answer is the same: use the fallback. There is
    no failure mode where refusing to lock at all is the right response."""
    def refuse(fd):
        err = OSError("nope")
        err.errno = errno_value
        raise err

    monkeypatch.setattr(filelock, "_lock_fd", refuse)
    lock = tmp_path / "x.lock"

    with exclusive(lock, timeout=1.0) as held:
        assert held is True, "did not fall back to the directory lock"
        assert (tmp_path / "x.lock.d").is_dir()


def test_a_lock_file_that_cannot_be_opened_falls_back(tmp_path, monkeypatch):
    monkeypatch.setattr("builtins.open", lambda *a, **k: (_ for _ in ()).throw(
        OSError("permission denied")))

    with exclusive(tmp_path / "x.lock", timeout=1.0) as held:
        assert held is True
        assert (tmp_path / "x.lock.d").is_dir()


def test_the_lock_is_held_even_if_the_owner_file_cannot_be_written(tmp_path,
                                                                   no_kernel_lock,
                                                                   monkeypatch):
    """Holding the lock matters more than being able to say who holds it. The
    stale timeout is what stops that becoming permanent."""
    real = type(tmp_path).write_text

    def selective(self, *a, **k):
        if self.name == "owner":
            raise OSError("disk full")
        return real(self, *a, **k)

    monkeypatch.setattr(type(tmp_path), "write_text", selective)
    with exclusive(tmp_path / "x.lock", timeout=1.0) as held:
        assert held is True


def test_a_failed_cleanup_does_not_raise(tmp_path, no_kernel_lock, monkeypatch):
    """Releasing is best effort too. Anything left behind is picked up by the
    stale timeout on the next run rather than surfacing as an error here."""
    monkeypatch.setattr(filelock.shutil, "rmtree",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("busy")))

    with exclusive(tmp_path / "x.lock", timeout=1.0) as held:
        assert held is True
    # leaving the block must not raise


def test_a_failed_steal_does_not_raise(tmp_path, no_kernel_lock, monkeypatch):
    """Two runs can decide the same lock is stale at once; the loser just
    tries again rather than blowing up."""
    monkeypatch.setattr(filelock, "STALE_AFTER", 0.0)
    (tmp_path / "x.lock.d").mkdir()
    monkeypatch.setattr(filelock.shutil, "rmtree",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("gone")))

    with exclusive(tmp_path / "x.lock", timeout=0.3) as held:
        assert held is False, "could not steal, so must run unlocked"


def test_an_unreadable_lock_directory_is_treated_as_stale(tmp_path, no_kernel_lock,
                                                          monkeypatch):
    """Neither the owner file nor the directory's own mtime available. Something
    is wrong with it, and wedging every future run is the worse failure."""
    lock_dir = tmp_path / "x.lock.d"
    lock_dir.mkdir()

    real_stat = type(lock_dir).stat

    def selective(self, *a, **k):
        if self.name == "x.lock.d":
            raise OSError("cannot stat")
        return real_stat(self, *a, **k)

    monkeypatch.setattr(type(lock_dir), "stat", selective)
    assert filelock._held_since(lock_dir) is None
