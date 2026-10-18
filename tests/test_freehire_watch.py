"""Tests for the freehire watch cycle: baseline, alerts, reminders, guards."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from outreach import util
from outreach.commands.freehire import _mark
from outreach.config import load_config
from outreach.context import Context
from outreach.freehire import db as db_mod
from outreach.freehire import watch
from outreach.freehire.watch import derive_keywords, next_due_after, run_cycle
from outreach.store import open_store

UTC = timezone.utc

PROFILE = {
    "full_name": "Test User",
    "email": "me@example.com",
    "timezone": "Asia/Karachi",
    "base_story": "Hi Hiring Team,\n\nI am a test profile paragraph.\n\nKind regards",
    "skills": {"engines": ["Unity", "Unreal"]},
    "projects": [{"name": "P", "url": "https://x.test/p", "tags": ["vr", "games"]}],
    "cv_pdf_path": "",
}

FREEHIRE_CFG = {
    "enabled": True,
    "base_url": "https://freehire.test/api/v1",
    "timeout_s": 5,
    "delay_between_requests_s": 0,
    "searches_per_cycle": 1,
    "results_per_search": 20,
    "q_fields": "title",
    "open_within_days": 14,
    "work_mode": "",
    "is_tech": "tech",
    "keywords": ["unity"],
    "reminder_hours": [3, 8, 24],
    "max_alerts": 10,
    "quiet_hours": [0, 0],  # disabled by default; the quiet-hours test enables it
    "notify_email": True,
    "notify_windows": True,
}


def make_cfg(tmp_path):
    cfg = load_config()
    cfg._raw["paths"] = {
        "workbook": str(tmp_path / "outreach.xlsx"),
        "backup_dir": str(tmp_path / "backups"),
        "pending_updates": str(tmp_path / "pending.jsonl"),
        "cache_dir": str(tmp_path / "cache"),
        "log_dir": str(tmp_path / "logs"),
        "lock_dir": str(tmp_path / "locks"),
    }
    cfg._raw["freehire"] = dict(FREEHIRE_CFG)
    cfg.profile_raw = lambda: dict(PROFILE)
    return cfg


class Logger:
    def debug(self, *a, **k): pass
    info = warning = error = debug


class Clock:
    def __init__(self, start: datetime):
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now += timedelta(**kwargs)


class FakeAPI:
    def __init__(self, results=None, details=None):
        self.results = list(results or [])
        self.details = dict(details or {})
        self.searches = 0

    def search(self, q, q_fields="title", filters=None, limit=20):
        self.searches += 1
        self.last_filters = filters
        if self.results:
            return self.results.pop(0)
        return [], {"total": 0}

    def get_job(self, slug):
        if slug in self.details:
            return self.details[slug]
        return None


class FakeNotifier:
    def __init__(self, ok=True):
        self.ok = ok
        self.calls = []

    def alert(self, job, alert_no, max_alerts):
        self.calls.append((job.get("slug"), alert_no))
        return self.ok


def job(slug, **over):
    base = {
        "public_slug": slug, "title": "Unity Dev", "company": "Acme",
        "url": "https://jobs.test/1", "location": "Remote, United States",
        "work_mode": "remote", "closed_at": None,
    }
    base.update(over)
    return base


@pytest.fixture
def env(tmp_path, monkeypatch):
    cfg = make_cfg(tmp_path)
    clock = Clock(datetime(2026, 10, 18, 10, 0, tzinfo=UTC))  # 15:00 Pakistan: not quiet
    monkeypatch.setattr(util, "now_utc", clock)
    store = open_store(cfg)
    jobsdb = db_mod.JobsDB(cfg.path("cache_dir") / "freehire" / "jobs.db")
    notifier = FakeNotifier()
    ctx = Context(cfg=cfg, logger=Logger(), store=store)
    return type("Env", (), dict(cfg=cfg, clock=clock, store=store, jobsdb=jobsdb,
                                notifier=notifier, ctx=ctx))


def test_first_run_baselines_silently(env):
    api = FakeAPI(results=[([job("a"), job("b")], {})])
    stats = run_cycle(env.ctx, api, env.jobsdb, env.store, env.notifier)
    assert stats["baselined"] == 2
    assert env.notifier.calls == []
    assert {r["slug"] for r in env.jobsdb.all_rows()} == {"a", "b"}
    assert all(r["status"] == db_mod.STATUS_BASELINE for r in env.jobsdb.all_rows())
    # mirrored to the sheet, and never alerted (next_due stays NULL)
    assert {r["slug"] for r in env.store.freehire_all()} == {"a", "b"}
    assert all(r["next_due_at"] is None for r in env.jobsdb.all_rows())
    assert api.last_filters == {
        "work_mode": "", "open_within_days": 14, "is_tech": "tech",
    }


def test_dry_run_first_run_writes_nothing(env):
    api = FakeAPI(results=[([job("a")], {})])
    stats = run_cycle(env.ctx, api, env.jobsdb, env.store, env.notifier, dry_run=True)
    assert stats["baselined"] == 1
    assert stats.get("sample") is not None
    assert env.jobsdb.is_empty()
    assert env.store.freehire_all() == []
    assert env.notifier.calls == []


def test_new_job_alerts_exactly_once(env):
    api = FakeAPI(results=[
        ([job("a")], {}),
        ([job("a"), job("c")], {}),
        ([job("a"), job("c")], {}),
    ])
    run_cycle(env.ctx, api, env.jobsdb, env.store, env.notifier)      # baseline
    s2 = run_cycle(env.ctx, api, env.jobsdb, env.store, env.notifier)  # c appears
    s3 = run_cycle(env.ctx, api, env.jobsdb, env.store, env.notifier)  # same data again
    assert s2["new"] == 1 and s2["alerted"] == 1
    assert env.notifier.calls == [("c", 1)]
    assert s3["new"] == 0 and s3["alerted"] == 0
    row = env.jobsdb.get("c")
    assert row["alerts"] == 1 and row["status"] == db_mod.STATUS_WATCHING


def test_reminder_timing_and_schedule(env):
    api = FakeAPI(results=[
        ([job("a")], {}),          # baseline
        ([job("a"), job("w")], {}),  # alert 1
        ([job("a"), job("w")], {}),  # alert 2
        ([job("a"), job("w")], {}),  # alert 3
        ([job("a"), job("w")], {}),  # alert 4
    ])
    run_cycle(env.ctx, api, env.jobsdb, env.store, env.notifier)  # baseline (a not watched)
    env.jobsdb.insert(job("w"), db_mod.STATUS_WATCHING,
                      util.to_iso(env.clock()), next_due_iso=util.to_iso(env.clock()))

    run_cycle(env.ctx, api, env.jobsdb, env.store, env.notifier)   # alert 1, due +3h
    env.clock.advance(hours=4)
    run_cycle(env.ctx, api, env.jobsdb, env.store, env.notifier)   # alert 2, due +8h
    env.clock.advance(hours=9)
    run_cycle(env.ctx, api, env.jobsdb, env.store, env.notifier)   # alert 3, due +24h
    env.clock.advance(hours=25)
    run_cycle(env.ctx, api, env.jobsdb, env.store, env.notifier)   # alert 4, due +24h

    assert [c[1] for c in env.notifier.calls] == [1, 2, 3, 4]
    row = env.jobsdb.get("w")
    assert row["alerts"] == 4
    assert row["next_due_at"] == util.to_iso(env.clock() + timedelta(hours=24))


def test_next_due_schedule_math():
    t0 = datetime(2026, 10, 18, 10, 0, tzinfo=UTC)
    assert next_due_after(1, t0, [3, 8, 24], 10) == util.to_iso(t0 + timedelta(hours=3))
    assert next_due_after(2, t0, [3, 8, 24], 10) == util.to_iso(t0 + timedelta(hours=8))
    assert next_due_after(3, t0, [3, 8, 24], 10) == util.to_iso(t0 + timedelta(hours=24))
    assert next_due_after(9, t0, [3, 8, 24], 10) == util.to_iso(t0 + timedelta(hours=24))
    assert next_due_after(10, t0, [3, 8, 24], 10) is None


def test_no_reminders_after_applied(env):
    env.jobsdb.insert(job("w"), db_mod.STATUS_WATCHING,
                      util.to_iso(env.clock()), next_due_iso=util.to_iso(env.clock()))
    env.jobsdb.set_status("w", db_mod.STATUS_APPLIED, util.to_iso(env.clock()))
    api = FakeAPI(results=[([job("w")], {})])
    stats = run_cycle(env.ctx, api, env.jobsdb, env.store, env.notifier)
    assert stats["alerted"] == 0 and env.notifier.calls == []
    assert env.jobsdb.get("w")["status"] == db_mod.STATUS_APPLIED


def test_no_reminders_after_skipped(env):
    env.jobsdb.insert(job("w"), db_mod.STATUS_WATCHING,
                      util.to_iso(env.clock()), next_due_iso=util.to_iso(env.clock()))
    env.jobsdb.set_status("w", db_mod.STATUS_SKIPPED, util.to_iso(env.clock()))
    api = FakeAPI(results=[([job("w")], {})])
    stats = run_cycle(env.ctx, api, env.jobsdb, env.store, env.notifier)
    assert stats["alerted"] == 0 and env.notifier.calls == []


def test_applied_column_in_sheet_stops_reminders(env):
    env.jobsdb.insert(job("w"), db_mod.STATUS_WATCHING,
                      util.to_iso(env.clock()), next_due_iso=util.to_iso(env.clock()))
    env.store.freehire_upsert({"slug": "w", "applied": "y"})
    api = FakeAPI(results=[([job("w")], {})])
    stats = run_cycle(env.ctx, api, env.jobsdb, env.store, env.notifier)
    assert stats["applied_synced"] == 1
    assert stats["alerted"] == 0 and env.notifier.calls == []
    assert env.jobsdb.get("w")["status"] == db_mod.STATUS_APPLIED


def test_gone_posting_stops_reminders(env):
    env.jobsdb.insert(job("w"), db_mod.STATUS_WATCHING,
                      util.to_iso(env.clock()), next_due_iso=util.to_iso(env.clock()))
    api = FakeAPI(results=[([], {})], details={})  # 404 on detail
    stats = run_cycle(env.ctx, api, env.jobsdb, env.store, env.notifier)
    assert stats["closed"] == 1 and env.notifier.calls == []
    assert env.jobsdb.get("w")["status"] == db_mod.STATUS_CLOSED


def test_closed_at_stops_reminders(env):
    env.jobsdb.insert(job("w"), db_mod.STATUS_WATCHING,
                      util.to_iso(env.clock()), next_due_iso=util.to_iso(env.clock()))
    api = FakeAPI(results=[([], {})],
                  details={"w": job("w", closed_at="2026-10-18T09:00:00Z")})
    stats = run_cycle(env.ctx, api, env.jobsdb, env.store, env.notifier)
    assert stats["closed"] == 1 and env.notifier.calls == []
    assert env.jobsdb.get("w")["status"] == db_mod.STATUS_CLOSED


def test_failed_delivery_does_not_advance_state(env):
    api = FakeAPI(results=[([job("a")], {}), ([job("a"), job("c")], {}), ([job("a"), job("c")], {})])
    run_cycle(env.ctx, api, env.jobsdb, env.store, env.notifier)   # baseline

    env.notifier.ok = False
    s2 = run_cycle(env.ctx, api, env.jobsdb, env.store, env.notifier)
    assert s2["failed"] == 1 and s2["alerted"] == 0
    row = env.jobsdb.get("c")
    assert row["alerts"] == 0
    assert row["next_due_at"] == util.to_iso(env.clock())  # still due: no advance

    env.clock.advance(minutes=5)
    env.notifier.ok = True
    s3 = run_cycle(env.ctx, api, env.jobsdb, env.store, env.notifier)
    assert s3["alerted"] == 1 and s3["failed"] == 0
    assert env.jobsdb.get("c")["alerts"] == 1


def test_quiet_hours_defer_sending(env):
    env.cfg._raw["freehire"]["quiet_hours"] = [23, 8]
    env.jobsdb.insert(job("w"), db_mod.STATUS_WATCHING,
                      util.to_iso(env.clock()), next_due_iso=util.to_iso(env.clock()))
    due_before = env.jobsdb.get("w")["next_due_at"]
    # 18:30 UTC == 23:30 Pakistan -> quiet
    env.clock.now = datetime(2026, 10, 18, 18, 30, tzinfo=UTC)
    api = FakeAPI(results=[([job("w")], {}), ([job("w")], {})])
    stats = run_cycle(env.ctx, api, env.jobsdb, env.store, env.notifier)
    assert stats["deferred_quiet"] == 1 and env.notifier.calls == []
    assert env.jobsdb.get("w")["alerts"] == 0
    assert env.jobsdb.get("w")["next_due_at"] == due_before  # unchanged: retried next cycle

    env.clock.now = datetime(2026, 10, 19, 5, 0, tzinfo=UTC)  # 10:00 Pakistan
    stats = run_cycle(env.ctx, api, env.jobsdb, env.store, env.notifier)
    assert stats["alerted"] == 1 and env.notifier.calls == [("w", 1)]


def test_ignored_params_are_reported(env):
    api = FakeAPI(results=[([job("a")], {"ignored_params": [{"param": "bogus_param"}]})])
    stats = run_cycle(env.ctx, api, env.jobsdb, env.store, env.notifier)
    assert stats["ignored_params"] == ["bogus_param"]


def test_mark_applied_and_unknown_slug(env):
    env.jobsdb.insert(job("w"), db_mod.STATUS_WATCHING,
                      util.to_iso(env.clock()), next_due_iso=util.to_iso(env.clock()))
    assert _mark(env.ctx, "w", db_mod.STATUS_APPLIED) == 0
    row = env.jobsdb.get("w")
    assert row["status"] == db_mod.STATUS_APPLIED and row["applied_at"]
    sheet = env.store.freehire_get("w")
    assert sheet is None or sheet.get("applied") in (None, "y")

    assert _mark(env.ctx, "nope", db_mod.STATUS_APPLIED) == 1
    assert _mark(env.ctx, None, db_mod.STATUS_APPLIED) == 1


def test_derive_keywords_from_profile():
    words = derive_keywords(PROFILE)
    assert "Unity" in words and "Unreal" in words
    assert "vr" in words and "games" not in words  # broad tags excluded
    assert "XR" in words
