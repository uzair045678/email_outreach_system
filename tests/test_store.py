import json
from pathlib import Path

import pytest

from outreach.store import open_store, XlsxStore


class DummyCfg:
    def __init__(self, base: Path):
        self.project_dir = base
        self._paths = {
            "workbook": str(base / "outreach.xlsx"),
            "backup_dir": str(base / "backups"),
            "pending_updates": str(base / "pending_updates.jsonl"),
        }

    def path(self, *keys):
        return Path(self._paths[keys[0]])


@pytest.fixture()
def store(tmp_path):
    cfg = DummyCfg(tmp_path)
    s = open_store(cfg)
    return s


def test_create_and_read_back(tmp_path):
    cfg = DummyCfg(tmp_path)
    s = open_store(cfg)
    rid = s.queue_add({"company": "Acme XR", "to_email": "hi@acme.com", "status": "pending_review"})
    s.save()

    s2 = open_store(cfg)
    row = s2.queue_get(rid)
    assert row["company"] == "Acme XR"
    assert row["status"] == "pending_review"
    assert (tmp_path / "outreach.xlsx").exists()


def test_update_row(store):
    rid = store.queue_add({"company": "Beta", "status": "pending_review"})
    assert store.queue_update(rid, status="approved", approve="y")
    assert store.queue_get(rid)["status"] == "approved"


def test_backup_created(store, tmp_path):
    store.save()
    store.queue_add({"company": "Gamma"})
    store.save()
    backups = list((tmp_path / "backups").glob("outreach-*.xlsx"))
    assert backups, "a rolling backup should exist after the second save"


def test_permission_error_journals(tmp_path, monkeypatch):
    cfg = DummyCfg(tmp_path)
    s = open_store(cfg)
    s.queue_add({"company": "Delta", "status": "pending_review"})

    def boom(*a, **k):
        raise PermissionError("file locked by Excel")

    monkeypatch.setattr(s.wb, "save", boom)
    ok = s.save()
    assert ok is False
    journal = tmp_path / "pending_updates.jsonl"
    assert journal.exists()
    lines = [json.loads(x) for x in journal.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert any(op["sheet"] == "Queue" for op in lines)

    # Simulate Excel closing: reload without the monkeypatch, journal replays.
    monkeypatch.undo()
    s2 = open_store(cfg)
    rows = [r for r in s2.queue_all() if r.get("company") == "Delta"]
    assert rows, "journaled row must survive the reload"
    assert not journal.exists() or not journal.read_text().strip()


def test_dedupe_helpers(store):
    store.contacted_add({"domain": "acme.com", "email": "hi@acme.com", "company": "Acme"})
    store.bad_add({"email": "bad@x.com", "domain": "x.com"})
    store.suppressed_add({"email": "stop@y.com", "domain": "y.com"})
    assert "acme.com" in store.contacted_domains()
    assert "bad@x.com" in store.bad_emails()
    assert "y.com" in store.suppressed_domains()
