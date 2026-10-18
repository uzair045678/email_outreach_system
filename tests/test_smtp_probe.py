"""Free SMTP fallback probe: mocked socket behaviour + verifier wiring."""

from __future__ import annotations

import pytest

from outreach.research import smtp_probe
from outreach.research.verify import EmailVerifier


class _FakeSMTP:
    """Scripted smtplib.SMTP stand-in driven by per-host reply queues."""

    script: dict[str, list] = {}
    log: list[tuple] = []

    def __init__(self, timeout=None):
        self._host = None

    def connect(self, host, port):
        self._host = host
        _FakeSMTP.log.append(("connect", host, port))
        return 220, b"hi"

    def ehlo(self, name):
        _FakeSMTP.log.append(("ehlo", name))
        return 250, b"ok"

    def helo(self, name):
        _FakeSMTP.log.append(("helo", name))
        return 250, b"ok"

    def mail(self, from_addr):
        _FakeSMTP.log.append(("mail", from_addr))
        assert "@" in from_addr
        return 250, b"ok"

    def rcpt(self, to_addr):
        _FakeSMTP.log.append(("rcpt", to_addr))
        queue = _FakeSMTP.script.get(self._host, [])
        code = queue.pop(0) if queue else 250
        return code, b""

    def quit(self):
        _FakeSMTP.log.append(("quit", self._host))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeResolver:
    def __init__(self, mx_map):
        self._mx_map = mx_map

    def resolve(self, domain, rdtype):
        hosts = self._mx_map.get(domain, [])
        if not hosts:
            raise RuntimeError("nxdomain")

        class _Ans(list):
            pass

        class _Rec:
            def __init__(self, host, pref):
                self.preference = pref
                self.exchange = host

        return _Ans(_Rec(h, i) for i, h in enumerate(hosts))


@pytest.fixture(autouse=True)
def _patch_smtp(monkeypatch):
    _FakeSMTP.script = {}
    _FakeSMTP.log = []
    monkeypatch.setattr(smtp_probe.smtplib, "SMTP", _FakeSMTP)


def test_accepted():
    _FakeSMTP.script = {"mx.example.com": [250]}
    ok, detail = smtp_probe.probe_email(
        "a@example.com", _FakeResolver({"example.com": ["mx.example.com"]})
    )
    assert ok is True
    assert "mx.example.com" in detail


def test_rejected_550():
    _FakeSMTP.script = {"mx.example.com": [550]}
    ok, detail = smtp_probe.probe_email(
        "a@example.com", _FakeResolver({"example.com": ["mx.example.com"]})
    )
    assert ok is False


def test_greylist_451_uncertain_then_next_host():
    _FakeSMTP.script = {"mx1.example.com": [451], "mx2.example.com": [250]}
    ok, detail = smtp_probe.probe_email(
        "a@example.com",
        _FakeResolver({"example.com": ["mx1.example.com", "mx2.example.com"]}),
    )
    assert ok is True
    assert "mx2.example.com" in detail


def test_bad_syntax_rejected():
    ok, _ = smtp_probe.probe_email("not-an-email", _FakeResolver({}))
    assert ok is False


def test_verifier_smtp_high(monkeypatch):
    monkeypatch.setattr(smtp_probe, "probe_email", lambda addr, res: (True, "smtp:mx:250"))
    v = EmailVerifier(smtp_probe=True)
    conf, detail = v.verify("careers@kodek.in", found_on_site=True)
    assert conf == "high"
    assert detail == "smtp:mx:250"


def test_verifier_smtp_low(monkeypatch):
    monkeypatch.setattr(smtp_probe, "probe_email", lambda addr, res: (False, "smtp:mx:550"))
    v = EmailVerifier(smtp_probe=True)
    conf, detail = v.verify("gone@example.com", found_on_site=True)
    assert conf == "low"


def test_verifier_smtp_off_by_default(monkeypatch):
    def boom(*a, **kw):
        raise AssertionError("probe should not run when disabled")

    monkeypatch.setattr(smtp_probe, "probe_email", boom)
    v = EmailVerifier()
    conf, detail = v.verify("info@example.com", found_on_site=True)
    assert conf == "medium"
    assert detail == "on-site-generic"


def test_one_probe_per_domain(monkeypatch):
    calls = []

    def fake(addr, res):
        calls.append(addr)
        return True, "smtp:mx:250"

    monkeypatch.setattr(smtp_probe, "probe_email", fake)
    v = EmailVerifier(smtp_probe=True)
    v.verify("a@same.com", found_on_site=True)
    v.verify("b@same.com", found_on_site=True)
    assert len(calls) == 1
