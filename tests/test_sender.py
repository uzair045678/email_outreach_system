from datetime import date, timedelta

import pytest

from outreach import state, util
from outreach.config import load_config
from outreach.context import Context
from outreach.draft.drafter import OPT_OUT
from outreach.store import open_store


PROFILE = {
    "full_name": "Test Dev",
    "email": "me@example.com",
    "headline": "XR Dev",
    "years_experience": 4,
    "github_url": "https://github.com/testdev",
    "linkedin_url": "https://linkedin.com/in/testdev",
    "portfolio_url": "https://testdev.example",
    "fiverr_url": "",
    "upwork_url": "",
    "skills": {"engines": ["Unity"], "xr_devices": [], "languages": ["C#"]},
    "projects": [{"name": "P", "one_line_description": "d", "url": "https://testdev.example/p", "tags": ["vr"]}],
}

BODY = (
    "Hi Jane,\n\n"
    "I saw Studio XR builds immersive VR training experiences in Unity. I am an XR developer "
    "building Unity applications.\n\n"
    "One of my projects is here: https://testdev.example/p\n\n"
    "Would you be open to a full-time role?\n\n"
    "Test Dev\nhttps://github.com/testdev\nhttps://linkedin.com/in/testdev\nhttps://testdev.example\n\n"
    f"{OPT_OUT}"
)


class Logger:
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass
    def debug(self, *a, **k): pass


def make_cfg(tmp_path):
    cfg = load_config()
    cfg._raw["paths"] = {
        "workbook": str(tmp_path / "outreach.xlsx"),
        "backup_dir": str(tmp_path / "backups"),
        "pending_updates": str(tmp_path / "pending_updates.jsonl"),
        "cache_dir": str(tmp_path / "cache"),
        "log_dir": str(tmp_path / "logs"),
        "lock_dir": str(tmp_path / "locks"),
    }
    cfg.profile_raw = lambda: PROFILE
    return cfg


def approved_row(to_email="jane@studio-xr.com", **over):
    row = {
        "company": "Studio XR",
        "country": "Singapore",
        "contact_name": "Jane Doe",
        "to_email": to_email,
        "subject": "XR developer for your team",
        "body": BODY,
        "status": "approved",
        "send_after_utc": util.to_iso(util.now_utc() - timedelta(minutes=5)),
        "recipient_tz": "Asia/Singapore",
        "approve": "y",
    }
    row["draft_hash"] = util.sha256(f"{row['subject']}|{row['body']}|{row['to_email']}|{row['contact_name']}")
    row.update(over)
    return row


def test_preflight_hash_mismatch():
    from outreach.send.sender import preflight

    cfg = make_cfg(__import__("pathlib").Path("."))
    store = _mem_store()
    row = approved_row()
    row["body"] = row["body"] + " tampered"
    ok, err = preflight(row, cfg, store, PROFILE)
    assert not ok and "changed" in err


def _mem_store():
    import tempfile
    from pathlib import Path

    cfg = make_cfg(Path(tempfile.mkdtemp()))
    return open_store(cfg)


def test_preflight_placeholder_and_links(tmp_path):
    from outreach.send.sender import preflight

    cfg = make_cfg(tmp_path)
    store = open_store(cfg)

    row = approved_row()
    row["draft_hash"] = ""
    row["body"] = BODY.replace("Hi Jane,", "[Your Name]")
    ok, err = preflight(row, cfg, store, PROFILE)
    assert not ok and "placeholder" in err

    row2 = approved_row()
    row2["draft_hash"] = ""
    row2["body"] = BODY + "\nhttps://evil.example.com"
    ok, err = preflight(row2, cfg, store, PROFILE)
    assert not ok and "disallowed" in err


def test_preflight_suppressed_and_contacted(tmp_path):
    from outreach.send.sender import preflight

    cfg = make_cfg(tmp_path)
    store = open_store(cfg)
    row = approved_row()
    row["draft_hash"] = ""

    store.suppressed_add({"email": "jane@studio-xr.com", "domain": "studio-xr.com"})
    ok, err = preflight(row, cfg, store, PROFILE)
    assert not ok and "suppressed" in err


def test_daily_cap_warmup(tmp_path):
    from outreach.send.sender import daily_cap

    cfg = make_cfg(tmp_path)
    ramp0 = int(cfg.raw.sending.warmup_ramp.raw[0])
    maxd = int(cfg.raw.sending.max_sends_per_day.raw)
    expected = min(ramp0, maxd)
    # no start -> first ramp value
    assert daily_cap(cfg) == expected
    state.set_warmup_start(cfg, date.today().isoformat())
    assert daily_cap(cfg) == expected


def test_mime_builds_multipart_with_attachment(tmp_path):
    from outreach.send.mime import build_message

    pdf = tmp_path / "cv.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    raw, mid = build_message(
        "me@example.com", "them@x.com", "Hi", "Body text",
        from_name="Test Dev", attachment_path=pdf,
    )
    assert mid.startswith("<") and mid.endswith(">")
    assert "Message-ID:" in raw
    assert "Content-Type: multipart/mixed" in raw or "application/pdf" in raw
    assert "cv.pdf" in raw


def test_dry_run_plans_without_sending(tmp_path, monkeypatch):
    from outreach.send import sender

    cfg = make_cfg(tmp_path)
    store = open_store(cfg)
    store.queue_add(approved_row())
    ctx = Context(cfg=cfg, logger=Logger(), store=store, dry_run=True)

    sent_calls = {"n": 0}

    class FakeHima:
        def __init__(self, *a, **k): pass
        def send_raw(self, *a, **k):
            sent_calls["n"] += 1
            return True, ""

    monkeypatch.setattr(sender, "Himalaya", FakeHima)
    rc = sender.run(ctx)
    assert rc == 0
    assert sent_calls["n"] == 0  # dry-run must not send
    assert store.queue_by_status("sent") == []


def test_real_send_marks_sent(tmp_path, monkeypatch):
    from outreach.send import sender

    cfg = make_cfg(tmp_path)
    store = open_store(cfg)
    rid = store.queue_add(approved_row())
    ctx = Context(cfg=cfg, logger=Logger(), store=store, dry_run=False)

    captured = {}

    class FakeHima:
        def __init__(self, *a, **k): pass
        def send_raw(self, raw, save_mailbox=""):
            captured["raw"] = raw
            return True, ""

    monkeypatch.setattr(sender, "Himalaya", FakeHima)
    rc = sender.run(ctx)
    assert rc == 0
    row = store.queue_get(rid)
    assert row["status"] == "sent"
    assert row["message_id"]
    assert row["sent_at"]
    assert row["followup_due"]
    assert "studio-xr.com" in store.contacted_domains()
