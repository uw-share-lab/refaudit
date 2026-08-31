"""Rate limits belong to the host, not to the resolver.

Two resolvers calling api.crossref.org are still one caller as far as Crossref
is concerned. Giving each its own bucket sent the sum of their rates, which is
how a polite client earns a 429.
"""

from __future__ import annotations

import pytest

from refaudit.doi_registry import DoiExistence
from refaudit.resolvers import default_resolvers
from refaudit.resolvers.base import reset_pacing


@pytest.fixture(autouse=True)
def _clean_pacing():
    reset_pacing()
    yield
    reset_pacing()


def test_resolvers_sharing_a_host_share_one_bucket():
    rs = {r.name: r for r in default_resolvers("x@y.z")}
    doi, title = rs["crossref:doi"], rs["crossref:title"]
    assert doi.host == title.host == "api.crossref.org"
    assert doi.http.bucket is title.http.bucket


def test_the_doi_proxy_is_one_host_even_across_modules():
    """doi:content and the existence check both call doi.org."""
    content = {r.name: r for r in default_resolvers("x@y.z")}["doi:content"]
    existence = DoiExistence(contact_email="x@y.z")
    assert content.host == existence.host == "doi.org"
    assert content.http.bucket is existence.http.bucket


def test_the_most_cautious_rate_wins():
    """The existence check declares 5/s, doi:content 2/s; 2/s must hold."""
    content = {r.name: r for r in default_resolvers("x@y.z")}["doi:content"]
    existence = DoiExistence(contact_email="x@y.z")
    assert existence.http.bucket.rate == pytest.approx(2.0)
    assert content.http.bucket.rate == pytest.approx(2.0)


def test_order_of_construction_does_not_matter():
    existence = DoiExistence(contact_email="x@y.z")   # 5/s, built first
    content = {r.name: r for r in default_resolvers("x@y.z")}["doi:content"]
    assert content.http.bucket.rate == pytest.approx(2.0)
    assert existence.http.bucket.rate == pytest.approx(2.0)


def test_distinct_hosts_are_paced_independently():
    rs = {r.name: r for r in default_resolvers("x@y.z")}
    assert rs["dblp"].http.bucket is not rs["openalex"].http.bucket


def test_a_host_refusing_us_backs_off_every_resolver_on_it():
    """If Crossref is refusing, the title search must not keep knocking."""
    rs = {r.name: r for r in default_resolvers("x@y.z")}
    doi, title = rs["crossref:doi"], rs["crossref:title"]
    for _ in range(4):
        doi.http.breaker.record_failure()
    assert title.http.breaker.is_open


def test_arxiv_keeps_its_documented_one_per_three_seconds():
    rs = {r.name: r for r in default_resolvers("x@y.z")}
    assert rs["arxiv:id"].http.bucket.rate == pytest.approx(1 / 3, abs=1e-3)
