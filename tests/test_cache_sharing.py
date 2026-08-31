"""Two runs pointed at one cache file must not erase each other.

The write was always atomic, so nothing ever corrupted -- but the whole dict was
written wholesale from memory loaded at startup, so the second run to finish
replaced the first run's entries with its own. On a shared lab machine, or two
terminals in the same directory, that silently threw away work and re-fetched it
from services we are trying to be polite to. Flush now merges what is on disk.
"""

import json

from refaudit.cache import SCHEMA_VERSION, Cache


def _entries(path):
    return json.loads(path.read_text(encoding="utf-8"))["entries"]


def test_two_runs_sharing_a_cache_file_keep_both_sets_of_entries(tmp_path):
    path = tmp_path / "cache.json"
    a = Cache(path)
    b = Cache(path)

    a.put("alpha", {"verdict": "OK"})
    b.put("beta", {"verdict": "OK"})
    a.flush()
    b.flush()          # b started before alpha existed; it must not drop it

    on_disk = _entries(path)
    assert set(on_disk) == {"alpha", "beta"}


def test_the_newer_entry_wins_when_both_runs_cached_the_same_key(tmp_path):
    path = tmp_path / "cache.json"
    a = Cache(path)
    b = Cache(path)

    a.put("shared", {"verdict": "STALE"})
    a.flush()
    b.put("shared", {"verdict": "FRESH"})
    b.flush()

    assert _entries(path)["shared"]["value"]["verdict"] == "FRESH"


def test_an_older_write_does_not_overwrite_a_newer_one(tmp_path):
    """Order of flushing must not decide it; the timestamp must."""
    path = tmp_path / "cache.json"
    a = Cache(path)
    b = Cache(path)

    b.put("shared", {"verdict": "FRESH"})
    b.flush()
    a.put("shared", {"verdict": "STALE"})
    a._data["shared"]["stored_at"] = 0.0        # pretend a cached it long ago
    a.flush()

    assert _entries(path)["shared"]["value"]["verdict"] == "FRESH"


def test_merging_survives_a_corrupt_file_on_disk(tmp_path):
    path = tmp_path / "cache.json"
    c = Cache(path)
    c.put("alpha", {"verdict": "OK"})
    path.write_text("{ not json", encoding="utf-8")

    c.flush()                                    # must not raise
    assert set(_entries(path)) == {"alpha"}


def test_merging_ignores_a_file_written_by_another_schema(tmp_path):
    path = tmp_path / "cache.json"
    path.write_text(json.dumps({"schema": SCHEMA_VERSION + 99,
                                "entries": {"old": {"stored_at": 1, "value": {}}}}),
                    encoding="utf-8")
    c = Cache(path)
    c.put("alpha", {"verdict": "OK"})
    c.flush()

    assert set(_entries(path)) == {"alpha"}


def test_flush_writes_nothing_when_nothing_changed(tmp_path):
    path = tmp_path / "cache.json"
    c = Cache(path)
    c.flush()
    assert not path.exists()


def test_a_reloaded_cache_can_read_what_the_other_run_wrote(tmp_path):
    path = tmp_path / "cache.json"
    a = Cache(path)
    b = Cache(path)
    a.put("alpha", {"verdict": "OK"})
    b.put("beta", {"verdict": "OK"})
    a.flush()
    b.flush()

    fresh = Cache(path)
    assert fresh.get("alpha") == {"verdict": "OK"}
    assert fresh.get("beta") == {"verdict": "OK"}
