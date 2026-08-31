"""What a run tells you about itself while it is happening.

A run is slow by design, and until now the only thing it emitted was a progress
line per entry. When forty entries came back UNVERIFIED there was no way to see
whether a service had refused us, timed out, or was never asked -- the reason
existed inside an ``Unavailable`` and went nowhere. Importing the package must
still stay silent: a library that configures logging for its host application is
a library that gets vendored badly.
"""

import logging

import pytest

from refaudit.http import HttpClient, HttpError, Response, TransportError
from refaudit.ratelimit import CircuitBreaker, TokenBucket


class _Scripted(HttpClient):
    def __init__(self, outcomes, **kw):
        super().__init__(**kw)
        self._outcomes = list(outcomes)

    def _open_once(self, url, headers):
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _client(outcomes, **kw):
    kw.setdefault("user_agent", "test")
    kw.setdefault("bucket", TokenBucket(1000.0, 1000))
    return _Scripted(outcomes, **kw)


# --- library etiquette -----------------------------------------------------

def test_importing_the_package_installs_a_null_handler():
    """A library must not print unless its host asks it to."""
    import refaudit  # noqa: F401

    handlers = logging.getLogger("refaudit").handlers
    assert any(isinstance(h, logging.NullHandler) for h in handlers)


def test_the_package_logger_does_not_set_a_level_of_its_own():
    """Level is the application's decision, not ours."""
    import refaudit  # noqa: F401

    assert logging.getLogger("refaudit").level == logging.NOTSET


# --- what actually gets logged ---------------------------------------------

def test_a_successful_request_is_logged_at_debug(caplog):
    client = _client([Response(200, b"{}", {})])
    with caplog.at_level(logging.DEBUG, logger="refaudit"):
        client.get("https://example.org/thing")
    assert any("example.org/thing" in r.message for r in caplog.records)
    assert all(r.levelno <= logging.DEBUG for r in caplog.records)


def test_being_throttled_is_logged_as_a_warning_with_the_new_rate(caplog):
    bucket = TokenBucket(1000.0, 1000)
    client = _client(
        [HttpError(429, "slow down", retry_after=0.0), Response(200, b"{}", {})],
        bucket=bucket, max_attempts=2,
    )
    with caplog.at_level(logging.DEBUG, logger="refaudit"):
        client.get("https://example.org/a")

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "a 429 is the thing a user most needs to be told about"
    assert any("429" in r.message for r in warnings)
    assert any("500" in r.message for r in warnings), "say what the rate became"


def test_a_transport_failure_is_logged_before_it_is_retried(caplog):
    client = _client(
        [TransportError("connection reset"), Response(200, b"{}", {})],
        max_attempts=2,
    )
    with caplog.at_level(logging.DEBUG, logger="refaudit"):
        client.get("https://example.org/a")
    assert any("connection reset" in r.message for r in caplog.records)


def test_an_open_circuit_says_which_host_it_gave_up_on(caplog):
    breaker = CircuitBreaker(threshold=1, cooldown=60.0)
    breaker.record_failure()
    client = _client([], breaker=breaker)
    with caplog.at_level(logging.DEBUG, logger="refaudit"), pytest.raises(TransportError):
        client.get("https://example.org/a")
    assert any("example.org" in r.message for r in caplog.records)


def test_a_4xx_is_logged_but_not_as_a_warning(caplog):
    """A 404 is a normal answer about the entry, not a problem with the run."""
    client = _client([HttpError(404, "not found")])
    with caplog.at_level(logging.DEBUG, logger="refaudit"), pytest.raises(HttpError):
        client.get("https://example.org/missing")
    assert caplog.records
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


# --- the CLI switch --------------------------------------------------------

def test_verbose_sends_debug_records_to_stderr():
    from refaudit.cli import configure_logging

    log = logging.getLogger("refaudit")
    before = list(log.handlers)
    try:
        configure_logging(verbose=True)
        assert log.level == logging.DEBUG
        streams = [h for h in log.handlers if isinstance(h, logging.StreamHandler)]
        assert streams, "verbose must attach a real handler"
    finally:
        log.handlers[:] = before
        log.setLevel(logging.NOTSET)


def test_without_verbose_only_warnings_get_through():
    from refaudit.cli import configure_logging

    log = logging.getLogger("refaudit")
    before = list(log.handlers)
    try:
        configure_logging(verbose=False)
        assert log.level == logging.WARNING
    finally:
        log.handlers[:] = before
        log.setLevel(logging.NOTSET)


def test_configure_logging_is_idempotent():
    """Calling it twice must not double every line."""
    from refaudit.cli import configure_logging

    log = logging.getLogger("refaudit")
    before = list(log.handlers)
    try:
        configure_logging(verbose=True)
        first = len(log.handlers)
        configure_logging(verbose=True)
        assert len(log.handlers) == first
    finally:
        log.handlers[:] = before
        log.setLevel(logging.NOTSET)
