"""ZeroBounce verification wiring: api_key param name + status mapping."""

from __future__ import annotations

import pytest

from outreach.research.verify import EmailVerifier


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_zerobounce_uses_api_key_param(monkeypatch):
    calls = {}

    def fake_get(url, params, timeout):
        calls["url"] = url
        calls["params"] = params
        resp = _FakeResp({"status": "valid"})
        return resp

    monkeypatch.setattr("outreach.research.verify.requests.get", fake_get)
    v = EmailVerifier(api_key="zbkey123", base_url="https://api.zerobounce.net/v2/validate")
    conf, detail = v._api_check("x@y.com")
    assert calls["params"] == {"email": "x@y.com", "api_key": "zbkey123"}
    assert "apikey" not in calls["params"]
    assert conf == "high"


def test_non_zerobounce_keeps_apikey_param(monkeypatch):
    calls = {}

    def fake_get(url, params, timeout):
        calls["params"] = params
        return _FakeResp({"result": "ok"})

    monkeypatch.setattr("outreach.research.verify.requests.get", fake_get)
    v = EmailVerifier(api_key="k", base_url="https://verify.example/validate")
    conf, _ = v._api_check("x@y.com")
    assert calls["params"] == {"email": "x@y.com", "apikey": "k"}
    assert conf == "high"


@pytest.mark.parametrize(
    "status,expected",
    [
        ("valid", "high"),
        ("deliverable", "high"),
        ("invalid", "low"),
        ("spamtrap", "low"),
        ("abuse", "low"),
        ("toxic", "low"),
        ("do_not_mail", "low"),
        ("catch-all", "medium"),
        ("unknown", "medium"),
    ],
)
def test_status_mapping(monkeypatch, status, expected):
    monkeypatch.setattr(
        "outreach.research.verify.requests.get",
        lambda *a, **kw: _FakeResp({"status": status}),
    )
    v = EmailVerifier(api_key="zbkey", base_url="https://api.zerobounce.net/v2/validate")
    conf, detail = v._api_check("x@y.com")
    assert conf == expected, f"status {status!r} -> {conf}"
    assert detail == f"api:{status}"


def test_skipped_when_keys_absent():
    v = EmailVerifier()
    assert v._api_check("x@y.com") == (None, "")
    v2 = EmailVerifier(api_key="k")
    assert v2._api_check("x@y.com") == (None, "")
    v3 = EmailVerifier(base_url="https://api.zerobounce.net/v2/validate")
    assert v3._api_check("x@y.com") == (None, "")


def test_api_failure_falls_back_to_none(monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("network down")

    monkeypatch.setattr("outreach.research.verify.requests.get", boom)
    v = EmailVerifier(api_key="zbkey", base_url="https://api.zerobounce.net/v2/validate")
    assert v._api_check("x@y.com") == (None, "")