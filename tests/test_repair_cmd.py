from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from outreach.commands import repair as rcmd
from outreach.context import Context
from outreach.research.contacts import ContactCandidate
from outreach.store import open_store

BODY = (
    "Hi there,\n\n"
    "I build XR prototypes and would love to help.\n\n"
    "If this isn't relevant, tell me and I won't write again.\n"
)


class DummyCfg:
    def __init__(self, base: Path):
        self.project_dir = base
        self._paths = {
            "workbook": str(base / "outreach.xlsx"),
            "backup_dir": str(base / "backups"),
            "pending_updates": str(base / "pending_updates.jsonl"),
            "cache_dir": str(base / "cache"),
        }
        self.raw = SimpleNamespace(
            followup=SimpleNamespace(max_new_address_attempts=SimpleNamespace(raw=2)),
            toggles=SimpleNamespace(auto_approve_resends=SimpleNamespace(raw=False)),
            search=SimpleNamespace(cache_ttl_days=SimpleNamespace(raw=30)),
            draft=SimpleNamespace(
                min_body_words=SimpleNamespace(raw=5),
                max_body_words=SimpleNamespace(raw=130),
                max_subject_len=SimpleNamespace(raw=70),
            ),
            models=SimpleNamespace(startup_timeout_s=SimpleNamespace(raw=5)),
        )

    def path(self, *keys):
        return Path(self._paths[keys[0]])

    def env(self, key, default=""):
        return default

    def profile_raw(self):
        return {"full_name": "Test User", "projects": []}


class FakeFetcher:
    def __init__(self, *a, **k):
        pass

    def get_text(self, url):
        return "Some company page text."

    def get_html(self, url):
        return "<html></html>"


class FakeVerifier:
    def __init__(self, *a, **k):
        pass


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    cfg = DummyCfg(tmp_path)
    store = open_store(cfg)
    rid = store.queue_add({
        "company": "Acme XR",
        "website": "https://acme.example",
        "to_email": "old@acme.example",
        "contact_name": "Old Name",
        "subject": "Quick question",
        "body": BODY,
        "evidence": "we build immersive experiences",
        "status": "needs_new_address",
        "addresses_tried": "old@acme.example",
    })
    store.bad_add({"email": "old@acme.example", "domain": "acme.example"})

    def fake_discover(domain, website, fetcher, verifier, logger=None):
        return [
            ContactCandidate(email="old@acme.example", source_url="u", kind="generic", rank=1),
            ContactCandidate(email="jane.doe@acme.example", source_url="u", kind="personal", rank=2,
                             name="Jane Doe", role="CTO", confidence="high"),
        ]

    monkeypatch.setattr(rcmd, "Fetcher", FakeFetcher)
    monkeypatch.setattr(rcmd, "EmailVerifier", FakeVerifier)
    monkeypatch.setattr(rcmd, "discover_contacts", fake_discover)
    c = Context(cfg=cfg, logger=logging.getLogger("test"), store=store)
    return c, rid, store


def test_repair_picks_next_candidate(ctx):
    c, rid, store = ctx
    rc = rcmd.run(c)
    assert rc == 0
    row = store.queue_get(rid)
    assert row["to_email"] == "jane.doe@acme.example"
    assert row["status"] == "pending_review"
    assert row["is_resend"] == 1
    assert row["body"].splitlines()[0].startswith("Hi Jane")


def test_repair_gives_up_after_max(ctx):
    c, rid, store = ctx
    store.queue_update(rid, addresses_tried="old@acme.example;jane.doe@acme.example")
    rcmd.run(c)
    assert store.queue_get(rid)["status"] == "gave_up"
