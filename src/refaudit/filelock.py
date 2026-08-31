"""Advisory locking between processes, degrading rather than failing.

Two runs of refaudit on one machine share things: the cache file, and the pace
we promise a service we will keep to. Coordinating those needs a lock that
works between processes, and the standard library gives us two options with
opposite weaknesses.

``flock`` is the better primitive where it works, because the kernel releases
it when the holder dies. Nothing can be left behind. But it is not honoured
everywhere: some network mounts ignore it, and without a lock daemon it can
appear to succeed while locking nothing at all.

A lock *directory* works in most of those places, since ``mkdir`` is atomic
almost everywhere, but it reintroduces exactly what ``flock`` avoids -- a
process that dies holding it leaves it behind, and every later run waits on a
lock nobody holds.

So the fallback is only usable with two rules, and both are load-bearing:

* **A lock older than any real run is stolen.** A flush takes milliseconds. A
  directory that has been there for `STALE_AFTER` seconds belongs to something
  that is not coming back.
* **Waiting is bounded.** If the lock cannot be had in time we say so and run
  anyway. Everything guarded here is an optimisation -- a cached lookup, a
  shared pace already enforced per process -- and a run that never starts is a
  worse outcome than one that occasionally repeats a request.

``exclusive`` yields whether the lock was actually held, so a caller that cares
can log the difference instead of assuming it got what it asked for.
"""

from __future__ import annotations

import contextlib
import errno
import os
import shutil
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path

#: How long before a held lock is assumed to belong to a dead process. Every
#: critical section here is a read, a merge and an atomic replace -- milliseconds
#: -- so this is orders of magnitude more than a live holder ever needs.
STALE_AFTER = 30.0

#: Longest we will wait to acquire before giving up and running unlocked.
ACQUIRE_TIMEOUT = 10.0

_POLL = 0.02


#: The kernel's advisory lock, or None where there is not one. Both come from
#: the standard library, so this stays a zero-dependency package.
_lock_fd: Callable[[int], None] | None

if sys.platform == "win32":  # pragma: no cover - exercised only on Windows
    import msvcrt

    def _windows_lock(fd: int) -> None:
        msvcrt.locking(fd, msvcrt.LK_LOCK, 1)

    _lock_fd = _windows_lock
else:
    try:
        import fcntl
    except ImportError:  # pragma: no cover - neither flavour available
        _lock_fd = None
    else:

        def _posix_lock(fd: int) -> None:
            fcntl.flock(fd, fcntl.LOCK_EX)

        _lock_fd = _posix_lock


def _held_since(lock_dir: Path) -> float | None:
    """When the current holder took the lock, or None if that is unknowable.

    The owner file is written immediately after the directory is created, but
    not atomically with it, so there is a window where the lock is genuinely
    held and the owner file is absent or half-written. Falling straight to
    "stale" there lets a second caller delete a lock somebody is actively
    holding -- which is not a theoretical race; it let four threads into the
    critical section at once the first time this was written.

    So the directory's own mtime is the backstop. It is set at creation, so a
    lock taken microseconds ago never looks old. Only when neither can be read
    is the lock treated as abandoned, which is right: something is wrong with
    it, and wedging every future run is the worse failure.
    """
    try:
        _pid, _, stamp = (lock_dir / "owner").read_text(encoding="utf-8").partition("\n")
        return float(stamp.strip())
    except (OSError, ValueError):
        pass
    try:
        return lock_dir.stat().st_mtime
    except OSError:
        return None


def _claim(lock_dir: Path) -> bool:
    """Create the lock directory, or report that somebody else holds it."""
    try:
        os.mkdir(lock_dir)
    except FileExistsError:
        return False
    except OSError:
        # Read-only filesystem, missing parent, permissions. Not lockable here.
        raise
    try:
        (lock_dir / "owner").write_text(f"{os.getpid()}\n{time.time()}\n",
                                        encoding="utf-8")
    except OSError:
        # We hold it even if we could not say so; a later waiter will read no
        # owner file, treat it as stale and take it from us after STALE_AFTER.
        pass
    return True


@contextlib.contextmanager
def _mkdir_lock(path: Path, timeout: float) -> Iterator[bool]:
    lock_dir = path.with_name(path.name + ".d")
    deadline = time.monotonic() + timeout
    mine = False
    try:
        while True:
            try:
                mine = _claim(lock_dir)
            except OSError:
                break                       # cannot lock here at all
            if mine:
                break

            since = _held_since(lock_dir)
            if since is None or (time.time() - since) > STALE_AFTER:
                # Nobody is coming back for this one.
                try:
                    shutil.rmtree(lock_dir)
                except OSError:
                    pass                    # somebody else got there first
                continue

            if time.monotonic() >= deadline:
                break                       # run unlocked rather than not at all
            time.sleep(_POLL)

        yield mine
    finally:
        if mine:
            try:
                shutil.rmtree(lock_dir)
            except OSError:
                pass


@contextlib.contextmanager
def exclusive(path: Path, *, timeout: float = ACQUIRE_TIMEOUT) -> Iterator[bool]:
    """Hold an exclusive lock for the duration of the block.

    Yields True when the lock is genuinely held and False when the body is
    running without it, which callers may log but should not treat as an error:
    everything guarded here is an optimisation.
    """
    if _lock_fd is not None:
        handle = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Not a `with`: the handle must outlive the try, because closing it
            # is what releases the lock.
            handle = open(path, "a+b")  # noqa: SIM115
            _lock_fd(handle.fileno())
        except OSError as e:
            if handle is not None:
                handle.close()
                handle = None
            # ENOLCK/EOPNOTSUPP mean the filesystem will not lock for us, which
            # is exactly what the directory fallback is for.
            if e.errno not in (errno.ENOLCK, errno.EOPNOTSUPP, errno.EINVAL):
                with _mkdir_lock(path, timeout) as held:
                    yield held
                return
        if handle is not None:
            try:
                yield True
            finally:
                # Closing releases both flavours, so there is no unlock call to
                # get wrong on an exception path.
                handle.close()
            return
        with _mkdir_lock(path, timeout) as held:
            yield held
        return

    with _mkdir_lock(path, timeout) as held:
        yield held
