from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import logging

import pytest

from outreach.commands import bounces as bcmd
from outreach.context import Context
from outreach.store import open_store
from tests.test_bounces import HARD, SOFT

PAGE = int


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
                folders=SimpleNamespace(
                    inbox=SimpleNamespace(raw="Inbox"),
                    spam=SimpleNamespace(raw="[Gmail]/Spam"),
                    sent=SimpleNamespace(raw="[Gmail]/Sent Mail"),
                ),
            ),
            sending=SimpleNamespace(delivered_assumed_after_hours=SimpleNamespace(raw=24)),
            models=SimpleNamespace(startup_timeout_s=SimpleNamespace(raw=5)),
        )

    def path(self, *keys):
        return Path(self._paths[keys[0]])


class FakeHimalaya:
    RAW = HARD

    def __init__(self, *a, **k):
        pass

    def list_envelopes(self, folder, page_size=50):
        if folder == "Inbox":
            return [{
                "id": "1",
                "message-id": "<bounce1@mail.gmail.com>",
                "subject": "Delivery Status Notification (Failure)",
                "from": [{"email": "mailer-daemon@googlemail.com", "name": "Mail Delivery Subsystem"}],
            }]
        return []

    def raw_message(self, folder, msg_id):
        return self.RAW


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    cfg = DummyCfg(tmp_path)
    store = open_store(cfg)
    rid = store.queue_add({
        "company": "Acme XR", "to_email": "someone@example.com",
        "status": "sent", "attempts": 1, "message_id": "<oursent@mail.gmail.com>",
    })
    monkeypatch.setattr(bcmd, "Himalaya", FakeHimalaya)
    c = Context(cfg=cfg, logger=logging.getLogger("test"), store=store)
    return c, rid, store


def test_hard_bounce_updates_row(ctx):
    c, rid, store = ctx
    rc = bcmd.run(c)
    assert rc == 0
    row = store.queue_get(rid)
    assert row["status"] == "needs_new_address"
    assert "someone@example.com" in store.bad_emails()
    assert store.summary_get(c.today).get("bounced", 0) == 1


def test_soft_bounce_requeues(ctx, monkeypatch):
    c, rid, store = ctx
    monkeypatch.setattr(FakeHimalaya, "RAW", SOFT, raising=False)
    bcmd.run(c)
    row = store.queue_get(rid)
    assert row["status"] == "approved"
    assert row["is_resend"] == 1
    assert int(row["attempts"]) == 2
