from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from outreach import util
from outreach.commands import followups as fcmd
from outreach.commands import summary as scmd
from outreach.context import Context
from outreach.store import open_store


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
            followup=SimpleNamespace(
                after_business_days=SimpleNamespace(raw=6),
                max_body_words=SimpleNamespace(raw=70),
            ),
            toggles=SimpleNamespace(
                auto_approve_followups=SimpleNamespace(raw=False),
                summary_email_to_self=SimpleNamespace(raw=False),
            ),
        )

    def path(self, *keys):
        return Path(self._paths[keys[0]])


def _ctx(tmp_path):
    cfg = DummyCfg(tmp_path)
    store = open_store(cfg)
    return Context(cfg=cfg, logger=logging.getLogger("test"), store=store), store


def test_due_selection_waits_business_days(tmp_path):
    ctx, store = _ctx(tmp_path)
    now = util.now_utc()
    recent = store.queue_add({"company": "A", "status": "sent", "sent_at": util.to_iso(now - timedelta(days=1))})
    old = store.queue_add({"company": "B", "status": "sent", "sent_at": util.to_iso(now - timedelta(days=20))})
    rows = fcmd.select_due_rows(store, ctx.cfg, now=now)
    ids = {r["id"] for r in rows}
    assert old in ids
    assert recent not in ids


def test_no_second_followup(tmp_path):
    ctx, store = _ctx(tmp_path)
    now = util.now_utc()
    rid = store.queue_add({
        "company": "B", "status": "delivered_assumed", "message_id": "<m@x>",
        "sent_at": util.to_iso(now - timedelta(days=20)),
    })
    store.queue_add({"company": "B", "template_variant": "followup", "thread_key": "<m@x>", "status": "pending_review"})
    rows = fcmd.select_due_rows(store, ctx.cfg, now=now)
    assert rid not in {r["id"] for r in rows}


def test_summary_writes_row(tmp_path):
    ctx, store = _ctx(tmp_path)
    now = util.now_utc()
    store.queue_add({"company": "A", "status": "sent", "sent_at": util.to_iso(now)})
    scmd.run(ctx)
    stats = store.summary_get(ctx.today)
    assert stats["sent"] == 1
    assert stats["date"] == ctx.today
