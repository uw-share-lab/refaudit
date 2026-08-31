"""Pacing shared between processes on one machine.

Per-process pacing is right across *users*: everyone runs under their own
contact address and is a separate identified caller with their own allowance.
It is wrong for one person running several processes at once -- two terminals,
a job array, a shell loop -- because Crossref sees one caller going at twice
the rate we promised it.

So the token bucket can keep its state in a file shared by every refaudit on
the machine, under the user's own cache directory. Not a world-writable
location: pacing state another local user could edit would let them slow your
run down, or speed it up into a ban.

Everything here degrades. If the file cannot be created, read, written or
locked, the bucket behaves exactly as it did before -- correct within the
process, uncoordinated outside it. That is the pre-existing behaviour, so the
worst case of the whole feature is the status quo.
"""

from __future__ import annotations

import json
import threading
import time

import pytest

from refaudit.ratelimit import SharedTokenBucket, TokenBucket


def _bucket(tmp_path, rate=1000.0, capacity=1000.0, name="host"):
    return SharedTokenBucket(rate, capacity, state_dir=tmp_path, host=name)


# --- it is still a token bucket --------------------------------------------

def test_it_behaves_like_the_plain_bucket_for_one_holder(tmp_path):
    b = _bucket(tmp_path, rate=50.0, capacity=3)
    started = time.monotonic()
    for _ in range(3):
        b.acquire()
    assert time.monotonic() - started < 0.2       # the burst is free
    b.acquire()
    assert time.monotonic() - started >= 0.015    # the fourth waits


def test_it_still_refuses_a_nonsense_rate(tmp_path):
    with pytest.raises(ValueError):
        SharedTokenBucket(0, 1, state_dir=tmp_path, host="h")


# --- the point of it --------------------------------------------------------

def test_two_processes_draw_from_one_allowance(tmp_path):
    """The whole reason this exists: two runs must not each get the full rate."""
    a = _bucket(tmp_path, rate=4.0, capacity=2, name="api.crossref.org")
    b = _bucket(tmp_path, rate=4.0, capacity=2, name="api.crossref.org")

    a.acquire()
    a.acquire()                       # burst spent
    started = time.monotonic()
    b.acquire()                       # must wait for a refill, not get its own burst
    assert time.monotonic() - started >= 0.15


def test_separate_hosts_do_not_share_an_allowance(tmp_path):
    a = _bucket(tmp_path, rate=4.0, capacity=2, name="api.crossref.org")
    b = _bucket(tmp_path, rate=4.0, capacity=2, name="export.arxiv.org")

    a.acquire()
    a.acquire()
    started = time.monotonic()
    b.acquire()                       # a different service, untouched
    assert time.monotonic() - started < 0.1


def test_a_penalty_in_one_process_slows_the_others(tmp_path):
    """A 429 is a fact about the whole machine's traffic, not about the process
    that happened to receive it."""
    a = _bucket(tmp_path, rate=8.0, capacity=8, name="h")
    b = _bucket(tmp_path, rate=8.0, capacity=8, name="h")

    a.penalise()
    assert b.rate == pytest.approx(4.0)


def test_recovery_in_one_process_is_seen_by_the_others(tmp_path):
    a = _bucket(tmp_path, rate=8.0, capacity=8, name="h")
    b = _bucket(tmp_path, rate=8.0, capacity=8, name="h")

    a.penalise()
    for _ in range(50):
        a.recover()
    assert b.rate == pytest.approx(8.0)


def test_a_ceiling_a_service_published_is_shared(tmp_path):
    a = _bucket(tmp_path, rate=8.0, capacity=8, name="h")
    b = _bucket(tmp_path, rate=8.0, capacity=8, name="h")

    a.set_rate(2.0)
    assert b.rate == pytest.approx(2.0)
    for _ in range(50):
        b.recover()
    assert b.rate == pytest.approx(2.0), "recovery must not climb past it"


def test_the_floor_still_applies_across_processes(tmp_path):
    a = _bucket(tmp_path, rate=8.0, capacity=8, name="h")
    b = _bucket(tmp_path, rate=8.0, capacity=8, name="h")

    for _ in range(50):
        a.penalise()
    assert b.rate >= 8.0 / 16


def test_concurrent_holders_never_exceed_the_allowance(tmp_path):
    """Six threads through one shared bucket must take no less time than the
    rate allows, or the sharing is not actually happening."""
    buckets = [_bucket(tmp_path, rate=20.0, capacity=1, name="h") for _ in range(6)]
    started = time.monotonic()
    threads = [threading.Thread(target=b.acquire) for b in buckets]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    elapsed = time.monotonic() - started

    # One free from the burst, five more at 20/s = 0.25s at an absolute minimum.
    assert elapsed >= 0.2, f"six requests in {elapsed:.3f}s exceeds the shared rate"


# --- where the state lives --------------------------------------------------

def test_the_state_file_is_not_world_writable(tmp_path):
    """Pacing another local user can edit would let them slow a run down, or
    speed it up into a ban."""
    b = _bucket(tmp_path, name="api.crossref.org")
    b.acquire()

    state = next(tmp_path.glob("*.json"))
    mode = state.stat().st_mode
    assert not mode & 0o022, f"group/other writable: {oct(mode)}"


def test_the_host_name_cannot_escape_the_state_directory(tmp_path):
    """The host comes from a resolver's api_base, so it is not attacker
    controlled -- but a path separator in it must still not write elsewhere."""
    b = SharedTokenBucket(10.0, 1, state_dir=tmp_path, host="../../etc/passwd")
    b.acquire()

    written = list(tmp_path.glob("**/*"))
    assert written, "nothing was written at all"
    for p in written:
        assert tmp_path in p.resolve().parents or p.resolve() == tmp_path


def test_state_survives_a_bucket_being_recreated(tmp_path):
    """A second run starting a moment later inherits the pace, rather than
    beginning again with a full burst."""
    a = _bucket(tmp_path, rate=4.0, capacity=2, name="h")
    a.acquire()
    a.acquire()
    del a

    b = _bucket(tmp_path, rate=4.0, capacity=2, name="h")
    started = time.monotonic()
    b.acquire()
    assert time.monotonic() - started >= 0.15


# --- degrading rather than failing -----------------------------------------

def test_an_unwritable_state_directory_falls_back_to_local_pacing(tmp_path, monkeypatch):
    """The worst case of this whole feature must be the behaviour we had
    before it: correct in-process, uncoordinated outside."""
    b = _bucket(tmp_path, rate=50.0, capacity=3, name="h")
    monkeypatch.setattr("os.replace", lambda *a, **k: (_ for _ in ()).throw(
        OSError("read-only filesystem")))

    started = time.monotonic()
    for _ in range(3):
        b.acquire()
    assert time.monotonic() - started < 0.5, "still paced locally"


def test_corrupt_state_is_discarded_rather_than_crashing(tmp_path):
    b = _bucket(tmp_path, rate=50.0, capacity=3, name="h")
    b.acquire()
    state = next(tmp_path.glob("*.json"))
    state.write_text("{ not json", encoding="utf-8")

    b.acquire()          # must not raise
    assert b.rate == pytest.approx(50.0)


def test_state_from_a_future_schema_is_ignored(tmp_path):
    state = tmp_path / "h.json"
    state.write_text(json.dumps({"schema": 999, "tokens": 0.0, "last": time.time(),
                                 "rate": 0.001, "ceiling": 0.001}), encoding="utf-8")
    b = _bucket(tmp_path, rate=50.0, capacity=3, name="h")

    started = time.monotonic()
    b.acquire()
    assert time.monotonic() - started < 0.5
    assert b.rate == pytest.approx(50.0)


def test_it_is_a_token_bucket_so_callers_need_no_special_case(tmp_path):
    assert isinstance(_bucket(tmp_path), TokenBucket)


def test_a_clock_jump_backwards_does_not_grant_free_tokens(tmp_path):
    """The shared state has to use wall-clock time to be comparable between
    processes, which means it can go backwards. That must not refill."""
    b = _bucket(tmp_path, rate=4.0, capacity=2, name="h")
    b.acquire()
    b.acquire()

    state = tmp_path / "h.json"
    blob = json.loads(state.read_text())
    blob["last"] = time.time() + 3600      # as if written by a clock an hour ahead
    state.write_text(json.dumps(blob), encoding="utf-8")

    started = time.monotonic()
    b.acquire()
    assert time.monotonic() - started >= 0.15, "a future timestamp granted a refill"
    assert time.monotonic() - started < 5.0, "or wedged the bucket entirely"


# --- wired into the resolvers ----------------------------------------------

def test_resolvers_get_a_shared_bucket_by_default(monkeypatch, tmp_path):
    from refaudit.resolvers import base
    from refaudit.resolvers.crossref import CrossrefDoi

    monkeypatch.delenv("REFAUDIT_NO_SHARED_PACING", raising=False)
    monkeypatch.setattr("refaudit.ratelimit.default_state_dir", lambda: tmp_path)
    base.reset_pacing()
    try:
        r = CrossrefDoi(contact_email="a@b.org")
        assert isinstance(r.http.bucket, SharedTokenBucket)
    finally:
        base.reset_pacing()


def test_shared_pacing_can_be_turned_off(monkeypatch, tmp_path):
    """An escape hatch for a filesystem where this is a bad idea, and for
    anyone who would rather not have refaudit write outside its output dir."""
    from refaudit.resolvers import base
    from refaudit.resolvers.crossref import CrossrefDoi

    monkeypatch.setenv("REFAUDIT_NO_SHARED_PACING", "1")
    monkeypatch.setattr("refaudit.ratelimit.default_state_dir", lambda: tmp_path)
    base.reset_pacing()
    try:
        r = CrossrefDoi(contact_email="a@b.org")
        assert isinstance(r.http.bucket, TokenBucket)
        assert not isinstance(r.http.bucket, SharedTokenBucket)
    finally:
        base.reset_pacing()


def test_two_resolvers_on_one_host_still_share_within_the_process(monkeypatch, tmp_path):
    """The per-host rule from 0.3.1 must survive: sharing between processes is
    an addition to it, not a replacement."""
    from refaudit.resolvers import base
    from refaudit.resolvers.crossref import CrossrefDoi, CrossrefTitle

    monkeypatch.setattr("refaudit.ratelimit.default_state_dir", lambda: tmp_path)
    base.reset_pacing()
    try:
        a, b = CrossrefDoi(contact_email="x@y.org"), CrossrefTitle(contact_email="x@y.org")
        assert a.http.bucket is b.http.bucket
    finally:
        base.reset_pacing()


def test_the_state_directory_is_private_to_the_user(tmp_path):
    """The files are 0600, but a directory anyone can write also lets another
    local user sit on our lock files and cost us the coordination."""
    d = tmp_path / "pacing"
    SharedTokenBucket(10.0, 1, state_dir=d, host="h").acquire()

    assert not d.stat().st_mode & 0o077, f"reachable by others: {oct(d.stat().st_mode)}"
