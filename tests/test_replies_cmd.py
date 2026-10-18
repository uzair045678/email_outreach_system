from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from outreach.commands import replies as rcmd
from outreach.context import Context
from outreach.store import open_store

OUR_SENT = "<oursent@mail.gmail.com>"

POSITIVE = (
    "From: Jane Doe <jane@acme.example>\r\n"
    "Subject: Re: Quick question\r\n"
    f"In-Reply-To: {OUR_SENT}\r\n"
    "Message-ID: <reply1@acme.example>\r\n\r\n"
    "Thanks for reaching out - I'm interested, can we schedule a call?\r\n"
)

NEGATIVE = POSITIVE.replace(
    "I'm interested, can we schedule a call?",
    "Please unsubscribe me and do not contact this company again.",
)
NEGATIVE_SUBJECT = NEGATIVE


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
            himalaya=SimpleNamespace(
                account=SimpleNamespace(raw="gmail"),
                scan_page_size=SimpleNamespace(raw=50),
                folders=SimpleNamespace(inbox=SimpleNamespace(raw="Inbox")),
            ),
            followup=SimpleNamespace(ooo_default_days=SimpleNamespace(raw=7)),
            models=SimpleNamespace(
                startup_timeout_s=SimpleNamespace(raw=5),
                request_timeout_s=SimpleNamespace(raw=10),
            ),
        )

    def path(self, *keys):
        return Path(self._paths[keys[0]])


class FakeHimalaya:
    RAW = POSITIVE
    MID = "<reply1@acme.example>"

    def __init__(self, *a, **k):
        pass

    def list_envelopes(self, folder, page_size=50):
        return [{
            "id": "1",
            "message-id": self.MID,
            "subject": "Re: Quick question",
            "from": [{"email": "jane@acme.example", "name": "Jane Doe"}],
        }]

    def raw_message(self, folder, msg_id):
        return self.RAW


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    cfg = DummyCfg(tmp_path)
    store = open_store(cfg)
    rid = store.queue_add({
        "company": "Acme XR", "to_email": "jane@acme.example",
        "status": "sent", "message_id": OUR_SENT, "attempts": 1,
    })
    monkeypatch.setattr(rcmd, "Himalaya", FakeHimalaya)
    c = Context(cfg=cfg, logger=logging.getLogger("test"), store=store)
    return c, rid, store


def test_positive_reply(ctx):
    c, rid, store = ctx
    rcmd.run(c)
    row = store.queue_get(rid)
    assert row["status"] == "replied"
    assert row["reply_sentiment"] == "positive"


def test_negative_reply_suppresses(ctx, monkeypatch):
    c, rid, store = ctx
    monkeypatch.setattr(FakeHimalaya, "RAW", NEGATIVE)
    monkeypatch.setattr(FakeHimalaya, "MID", "<reply2@acme.example>")
    rcmd.run(c)
    row = store.queue_get(rid)
    assert row["status"] == "suppressed"
    assert "jane@acme.example" in store.suppressed_emails()


def test_auto_reply_sets_followup_due(ctx, monkeypatch):
    c, rid, store = ctx
    auto = (
        "From: Jane Doe <jane@acme.example>\r\n"
        "Subject: Automatic reply: Quick question\r\n"
        "Auto-Submitted: auto-replied\r\n"
        "Message-ID: <auto1@acme.example>\r\n\r\n"
        "I am out of office until 2026-04-01.\r\n"
    )
    monkeypatch.setattr(FakeHimalaya, "RAW", auto)
    monkeypatch.setattr(FakeHimalaya, "MID", "<auto1@acme.example>")
    rcmd.run(c)
    row = store.queue_get(rid)
    assert row["status"] == "auto_reply"
    assert row["followup_due"] == "2026-04-01"
