"""Tests for the freehire API client (mocked HTTP)."""

from __future__ import annotations

import pytest
import requests

from outreach.freehire.api import FreehireAPI, FreehireAPIError


class FakeResp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, resp=None, exc=None):
        self.resp = resp
        self.exc = exc
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        if self.exc:
            raise self.exc
        return self.resp


def test_search_builds_params_and_returns_meta():
    session = FakeSession(resp=FakeResp(payload={
        "data": [{"public_slug": "s1"}],
        "meta": {"total": 7, "ignored_params": [{"param": "x"}]},
    }))
    api = FreehireAPI(base_url="https://api.test/v1", delay_s=0, session=session)
    jobs, meta = api.search("unity", q_fields="title",
                            filters={"work_mode": "remote", "open_within_days": None, "bad": ""},
                            limit=5)
    call = session.calls[0]
    assert call["url"] == "https://api.test/v1/jobs/search"
    assert call["timeout"] == 20.0
    assert call["params"] == {"q": "unity", "sort": "created_at", "limit": 5,
                              "q_fields": "title", "work_mode": "remote"}
    assert jobs == [{"public_slug": "s1"}]
    assert meta["ignored_params"] == [{"param": "x"}]


def test_get_job_404_returns_none():
    api = FreehireAPI(delay_s=0, session=FakeSession(resp=FakeResp(status_code=404)))
    assert api.get_job("gone") is None


def test_get_job_detail_unwraps_data():
    api = FreehireAPI(delay_s=0, session=FakeSession(
        resp=FakeResp(payload={"data": {"public_slug": "s1", "title": "T"}})))
    assert api.get_job("s1")["title"] == "T"


def test_http_error_raises():
    api = FreehireAPI(delay_s=0, session=FakeSession(resp=FakeResp(status_code=500)))
    with pytest.raises(FreehireAPIError):
        api.search("q")


def test_network_error_raises():
    api = FreehireAPI(delay_s=0, session=FakeSession(exc=requests.Timeout("slow")))
    with pytest.raises(FreehireAPIError):
        api.get_job("s1")
