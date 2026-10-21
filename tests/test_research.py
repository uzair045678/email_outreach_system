import json
from pathlib import Path

import pytest

from outreach.config import NS
from outreach.research.providers import SearchManager, SearchProvider, SearchResult


class FakeCfg:
    def __init__(self, base: Path, keys=None, max_calls=200):
        self.base = base
        self._keys = keys or {}
        self._raw = {
            "search": {
                "providers": ["tavily"],
                "calls_per_day_warn": 120,
                "calls_per_day_max": max_calls,
                "results_per_query": 10,
                "cache_ttl_days": 30,
            },
            "paths": {"cache_dir": str(base / "cache")},
        }

    @property
    def raw(self):
        return NS(self._raw)

    def env(self, key, default=""):
        return self._keys.get(key, default)

    def path(self, *keys):
        node = self._raw["paths"]
        for k in keys:
            node = node[k]
        return Path(node)


class CountingProvider(SearchProvider):
    name = "fake"

    def __init__(self):
        super().__init__("key")
        self.calls = 0

    def search(self, query, count):
        self.calls += 1
        return [SearchResult("Acme XR", "https://acme-xr.com", "VR studio", self.name)]


def test_cache_prevents_second_provider_call(tmp_path):
    cfg = FakeCfg(tmp_path)
    mgr = SearchManager(cfg)
    fake = CountingProvider()
    mgr.providers = [fake]

    r1 = mgr.search("XR studio Brazil")
    r2 = mgr.search("XR studio Brazil")
    assert len(r1) == 1 and r1[0].url == "https://acme-xr.com"
    assert fake.calls == 1, "second identical query must be served from cache"
    assert mgr.calls_today == 1


def test_budget_stops_search(tmp_path):
    cfg = FakeCfg(tmp_path, max_calls=1)
    mgr = SearchManager(cfg)
    fake = CountingProvider()
    mgr.providers = [fake]
    mgr.search("q1")  # cached unique -> 1 call
    mgr.search("q2")  # budget exhausted
    assert fake.calls == 1


def test_discovery_dedupe_and_block(tmp_path):
    from outreach.research.discovery import discover_candidates

    class SearchStub:
        def search(self, q, count=None):
            return [
                SearchResult("Acme", "https://acme-xr.com", "", "stub"),
                SearchResult("Dup", "https://www.acme-xr.com/about", "", "stub"),
                SearchResult("Google", "https://google.com", "", "stub"),
                SearchResult("Beta", "https://beta-vr.io", "", "stub"),
            ]

    cands = discover_candidates(SearchStub(), ["Singapore"], need=10, exclude_domains={"beta-vr.io"})
    domains = {c.key() for c in cands}
    assert "acme-xr.com" in domains
    assert "google.com" not in domains
    assert "beta-vr.io" not in domains
    assert len([c for c in cands if c.key() == "acme-xr.com"]) == 1


@pytest.mark.network
def test_mx_verification_real_domains():
    from outreach.research.verify import EmailVerifier

    v = EmailVerifier()
    ok, _ = v.mx_ok("gmail.com")
    assert ok is True
    bad, detail = v.mx_ok("this-domain-should-not-exist-9f8a7b6c.com")
    assert bad is False


@pytest.mark.network
def test_verify_email_syntax_and_confidence():
    from outreach.research.verify import EmailVerifier, is_valid_syntax

    assert is_valid_syntax("a@b.com")
    assert not is_valid_syntax("bad@@b.com")
    v = EmailVerifier()
    conf, detail = v.verify("info@gmail.com", found_on_site=False)
    assert conf in ("medium", "low", "high")


def test_contact_extraction_from_html(tmp_path, monkeypatch):
    from outreach.research import contacts
    from outreach.research.fetch import Fetcher

    pages = {
        "https://studio-xr.com": """
            <html><body>
            <a href="/contact">Contact</a><a href="/team">Team</a>
            <p>Reach us at hello@studio-xr.com</p>
            </body></html>
        """,
        "https://studio-xr.com/team": """
            <html><body>
            <p>Jane Doe, Founder - jane@studio-xr.com</p>
            <p>Bob Smith, CTO - bob@studio-xr.com</p>
            <p>Third-party recruiter - talent@example.org</p>
            </body></html>
        """,
    }

    fetcher = Fetcher(tmp_path / "cache")

    def fake_get_html(url, timeout=20):
        return pages.get(url, "")

    monkeypatch.setattr(fetcher, "get_html", fake_get_html)

    class Verifier:
        def verify(self, email, found_on_site=True):
            return "high", "on-site-mx"

    cands = contacts.discover_contacts("studio-xr.com", "https://studio-xr.com", fetcher, Verifier())
    emails = [c.email for c in cands]
    assert "jane@studio-xr.com" in emails
    assert "bob@studio-xr.com" in emails
    assert "hello@studio-xr.com" in emails
    assert "talent@example.org" not in emails
    # founder and CTO outrank the generic hello@
    assert cands[0].rank <= 2
    assert cands[-1].kind == "generic"
