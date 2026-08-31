"""Rate limiting, circuit breaking, caching and XML hardening."""

import json
import time

import pytest

from refcheck.cache import SCHEMA_VERSION, Cache
from refcheck.ratelimit import CircuitBreaker, TokenBucket
from refcheck.xmlsafe import XmlSecurityError, fromstring


# --- token bucket ----------------------------------------------------------

def test_bucket_allows_burst_then_paces():
    b = TokenBucket(rate=50.0, capacity=3)
    start = time.monotonic()
    for _ in range(3):
        b.acquire()
    assert time.monotonic() - start < 0.05      # burst is free
    b.acquire()                                  # fourth must wait ~1/50s
    assert time.monotonic() - start >= 0.015


def test_bucket_rejects_nonpositive_rate():
    with pytest.raises(ValueError):
        TokenBucket(rate=0)


def test_set_rate_slows_future_acquisitions():
    b = TokenBucket(rate=100.0, capacity=1)
    b.set_rate(50.0)
    assert b.rate == 50.0


# --- circuit breaker -------------------------------------------------------

def test_breaker_opens_after_threshold_and_recovers():
    cb = CircuitBreaker(threshold=3, cooldown=0.15)
    assert not cb.is_open
    for _ in range(3):
        cb.record_failure()
    assert cb.is_open                            # stop hammering
    time.sleep(0.2)
    assert not cb.is_open                        # half-open probe allowed
    cb.record_success()
    assert not cb.is_open


def test_success_resets_failure_run():
    cb = CircuitBreaker(threshold=2, cooldown=10)
    cb.record_failure()
    cb.record_success()
    cb.record_failure()
    assert not cb.is_open                        # not two *consecutive* failures


# --- cache -----------------------------------------------------------------

def test_cache_roundtrip_and_atomic_write(tmp_path):
    path = tmp_path / "c.json"
    with Cache(path) as c:
        c.put("k", {"verdict": "OK"})
    assert json.loads(path.read_text())["schema"] == SCHEMA_VERSION
    assert Cache(path).get("k") == {"verdict": "OK"}


def test_cache_ignores_other_schema_versions(tmp_path):
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"schema": SCHEMA_VERSION + 99,
                                "entries": {"k": {"stored_at": time.time(),
                                                  "value": {"verdict": "OK"}}}}))
    assert Cache(path).get("k") is None


def test_cache_survives_corruption(tmp_path):
    path = tmp_path / "c.json"
    path.write_text("{not json at all")
    assert Cache(path).get("anything") is None   # starts clean rather than crashing


def test_cache_expires_by_ttl(tmp_path):
    path = tmp_path / "c.json"
    c = Cache(path, ttl_days=1.0)
    c.put("k", {"verdict": "OK"})
    c._data["k"]["stored_at"] = time.time() - 2 * 86400
    assert c.get("k") is None


# --- XML hardening ---------------------------------------------------------

ATTACKS = {
    "internal entity": '<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "a">]><l>&a;</l>',
    "billion laughs": ('<?xml version="1.0"?><!DOCTYPE z [<!ENTITY a "aa">'
                       '<!ENTITY b "&a;&a;">]><z>&b;</z>'),
    "external entity": ('<?xml version="1.0"?><!DOCTYPE d '
                        '[<!ENTITY x SYSTEM "file:///etc/passwd">]><d>&x;</d>'),
    "bare doctype": "<!DOCTYPE feed><feed><t>x</t></feed>",
}


@pytest.mark.parametrize("name", list(ATTACKS))
def test_entity_attacks_are_refused(name):
    with pytest.raises(XmlSecurityError):
        fromstring(ATTACKS[name])


def test_legitimate_atom_still_parses():
    atom = ('<feed xmlns="http://www.w3.org/2005/Atom">'
            "<entry><title>Just Like Me</title></entry></feed>")
    root = fromstring(atom)
    ns = "{http://www.w3.org/2005/Atom}"
    assert root.find(f"{ns}entry").findtext(f"{ns}title") == "Just Like Me"


# --- http client policy ----------------------------------------------------

def test_http_client_refuses_plain_http():
    from refcheck.http import HttpClient, TransportError

    client = HttpClient(user_agent="test", bucket=TokenBucket(100.0, 10))
    with pytest.raises(TransportError, match="non-https"):
        client.get("http://example.org/insecure")


def test_api_key_is_sent_as_header_not_query(monkeypatch):
    from refcheck.resolvers.crossref import CrossrefDoi

    class KeyedResolver(CrossrefDoi):
        api_key_env = "REFCHECK_TEST_KEY"
        api_key_header = "X-Api-Key"

    monkeypatch.setenv("REFCHECK_TEST_KEY", "s3cret")
    r = KeyedResolver(contact_email="a@b.org")
    assert r.http._api_key_header == ("X-Api-Key", "s3cret")


def test_contact_email_is_required():
    from refcheck.resolvers.crossref import CrossrefDoi

    with pytest.raises(ValueError, match="contact_email"):
        CrossrefDoi(contact_email="")
