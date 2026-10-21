"""Tests for the freehire watch cycle: baseline, digest slots, categories, guards."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from outreach import util
from outreach.commands.freehire import _mark
from outreach.config import load_config
from outreach.context import Context
from outreach.freehire import db as db_mod
from outreach.freehire.watch import classify, derive_keywords, run_cycle
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
    "digest_times": ["08:00", "16:00", "20:00", "22:00"],
    "categories": {
        "priority": ["Unity", "Unreal", "Game Developer"],
        "medium": ["Virtual Reality", "Augmented Reality"],
        "low": ["Extended Reality", "Mixed reality"],
    },
    "quiet_hours": [0, 0],  # disabled by default; the quiet-hours test enables it
    "notify_email": True,
    "notify_windows": True,
}

# Pakistan is UTC+5, no DST: 11:05 UTC == 16:05 local (inside the 16:00 slot).
IN_SLOT = datetime(2026, 10, 18, 11, 5, tzinfo=UTC)
OUT_OF_SLOT = datetime(2026, 10, 18, 10, 0, tzinfo=UTC)  # 15:00 local


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

    def digest(self, groups):
        self.calls.append(groups)
        return self.ok


def job(slug, **over):
    base = {
        "public_slug": slug, "title": "Unity Dev", "company": "Acme",
        "url": "https://jobs.test/1", "location": "Remote, United States",
        "work_mode": "remote", "closed_at": None,
    }
    base.update(over)
    return base


def digested_slugs(groups) -> list[str]:
    return [j.get("slug") or j.get("public_slug") for _, jobs in groups for j in jobs]


def tier_names(groups) -> list[str]:
    return [name for name, jobs in groups if jobs]


@pytest.fixture
def env(tmp_path, monkeypatch):
    cfg = make_cfg(tmp_path)
    clock = Clock(OUT_OF_SLOT)  # 15:00 Pakistan: outside every slot
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
    # mirrored to the sheet, category computed, never alerted
    assert {r["slug"] for r in env.store.freehire_all()} == {"a", "b"}
    assert all(r["category"] == "priority" for r in env.store.freehire_all())


def test_dry_run_first_run_writes_nothing(env):
    api = FakeAPI(results=[([job("a")], {})])
    stats = run_cycle(env.ctx, api, env.jobsdb, env.store, env.notifier, dry_run=True)
    assert stats["baselined"] == 1
    assert stats.get("sample") is not None
    assert env.jobsdb.is_empty()
    assert env.store.freehire_all() == []
    assert env.notifier.calls == []


def test_new_jobs_wait_for_digest_window(env):
    api = FakeAPI(results=[([job("a")], {}), ([job("a"), job("c")], {})])
    run_cycle(env.ctx, api, env.jobsdb, env.store, env.notifier)  # baseline
    stats = run_cycle(env.ctx, api, env.jobsdb, env.store, env.notifier)  # 15:0x: outside
    assert stats["new"] == 1
    assert stats["digest"] == 0 and stats["alerted"] == 0
    assert env.notifier.calls == []
    assert stats["queued"] == 1
    assert env.jobsdb.get("c")["alerts"] == 0


def test_digest_sends_one_grouped_email_in_window(env):
    api = FakeAPI(results=[
        ([job("a")], {}),
        ([job("a"),
          job("p", title="Unreal Engineer"),
          job("m", title="Virtual Reality Tester"),
          job("l", title="Warehouse Extended Goods")], {}),
    ])
    run_cycle(env.ctx, api, env.jobsdb, env.store, env.notifier)  # baseline
    env.clock.now = IN_SLOT  # 16:05 Pakistan
    stats = run_cycle(env.ctx, api, env.jobsdb, env.store, env.notifier)

    assert stats["digest"] == 1 and stats["alerted"] == 3
    assert len(env.notifier.calls) == 1
    groups = env.notifier.calls[0]
    assert tier_names(groups) == ["priority", "medium", "low"]
    assert digested_slugs(groups) == ["p", "m", "l"]
    for slug in ("p", "m", "l"):
        row = env.jobsdb.get(slug)
        assert row["alerts"] == 1 and row["status"] == db_mod.STATUS_WATCHING
    state = json.loads(env.jobsdb.meta_get("digest_state"))
    assert state == {"date": "2026-10-18"}


def test_empty_morning_window_falls_back_to_later_slot(env):
    api = FakeAPI(results=[([job("a")], {}), ([job("a")], {}), ([job("a"), job("c")], {})])
    run_cycle(env.ctx, api, env.jobsdb, env.store, env.notifier)  # baseline

    env.clock.now = datetime(2026, 10, 18, 3, 30, tzinfo=UTC)  # 08:30: nothing queued
    s2 = run_cycle(env.ctx, api, env.jobsdb, env.store, env.notifier)
    assert s2["digest"] == 0 and env.notifier.calls == []
    assert env.jobsdb.meta_get("digest_state") is None  # day not consumed

    env.clock.now = IN_SLOT  # 16:05: fallback window, job c now queued
    s3 = run_cycle(env.ctx, api, env.jobsdb, env.store, env.notifier)
    assert s3["digest"] == 1 and digested_slugs(env.notifier.calls[0]) == ["c"]

    # a later window the same day must NOT send a second email
    env.clock.now = datetime(2026, 10, 18, 15, 5, tzinfo=UTC)  # 20:05
    api.results.append(([job("a"), job("c"), job("z")], {}))
    s4 = run_cycle(env.ctx, api, env.jobsdb, env.store, env.notifier)
    assert s4["new"] == 1 and s4["digest"] == 0
    assert len(env.notifier.calls) == 1
    assert env.jobsdb.get("z")["alerts"] == 0


def test_slot_fires_only_once_per_day(env):
    api = FakeAPI(results=[
        ([job("a")], {}),
        ([job("a"), job("c")], {}),
        ([job("a"), job("c"), job("z")], {}),
    ])
    run_cycle(env.ctx, api, env.jobsdb, env.store, env.notifier)
    env.clock.now = IN_SLOT
    s2 = run_cycle(env.ctx, api, env.jobsdb, env.store, env.notifier)
    assert s2["digest"] == 1

    env.clock.now = datetime(2026, 10, 18, 11, 50, tzinfo=UTC)  # 16:50, same window
    s3 = run_cycle(env.ctx, api, env.jobsdb, env.store, env.notifier)
    assert s3["new"] == 1 and s3["digest"] == 0 and s3["queued"] == 1
    assert len(env.notifier.calls) == 1
    assert env.jobsdb.get("z")["alerts"] == 0


def test_missed_slot_never_catches_up(env):
    api = FakeAPI(results=[([job("a")], {}), ([job("a"), job("c")], {}), ([job("a"), job("c")], {})])
    run_cycle(env.ctx, api, env.jobsdb, env.store, env.notifier)
    env.clock.now = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)  # 14:00: 08:00 slot missed
    s2 = run_cycle(env.ctx, api, env.jobsdb, env.store, env.notifier)
    assert s2["digest"] == 0 and env.notifier.calls == []
    env.clock.now = IN_SLOT  # 16:05: next slot fires normally
    s3 = run_cycle(env.ctx, api, env.jobsdb, env.store, env.notifier)
    assert s3["digest"] == 1 and digested_slugs(env.notifier.calls[0]) == ["c"]


def test_failed_digest_keeps_state_and_retries_in_window(env):
    api = FakeAPI(results=[([job("a")], {}), ([job("a"), job("c")], {}), ([job("a"), job("c")], {})])
    run_cycle(env.ctx, api, env.jobsdb, env.store, env.notifier)
    env.clock.now = IN_SLOT

    env.notifier.ok = False
    s2 = run_cycle(env.ctx, api, env.jobsdb, env.store, env.notifier)
    assert s2["failed"] == 1 and s2["digest"] == 0 and s2["alerted"] == 0
    assert env.jobsdb.get("c")["alerts"] == 0
    assert env.jobsdb.meta_get("digest_state") is None  # slot not burned

    env.clock.now = datetime(2026, 10, 18, 11, 20, tzinfo=UTC)  # still in window
    env.notifier.ok = True
    s3 = run_cycle(env.ctx, api, env.jobsdb, env.store, env.notifier)
    assert s3["digest"] == 1 and env.jobsdb.get("c")["alerts"] == 1


def test_dry_run_digest_previews_without_writing(env):
    api = FakeAPI(results=[([job("a")], {}), ([job("a"), job("c")], {})])
    run_cycle(env.ctx, api, env.jobsdb, env.store, env.notifier)
    env.clock.now = IN_SLOT
    stats = run_cycle(env.ctx, api, env.jobsdb, env.store, env.notifier, dry_run=True)
    assert stats["digest"] == 1 and stats["alerted"] == 1
    assert env.jobsdb.get("c") is None  # dry run wrote nothing
    assert env.jobsdb.meta_get("digest_state") is None


def test_applied_column_in_sheet_excludes_from_digest(env):
    api = FakeAPI(results=[([job("a")], {}), ([job("a"), job("w")], {})])
    run_cycle(env.ctx, api, env.jobsdb, env.store, env.notifier)
    env.jobsdb.insert(job("w"), db_mod.STATUS_WATCHING, util.to_iso(env.clock()))
    env.store.freehire_upsert({"slug": "w", "applied": "y"})
    env.clock.now = IN_SLOT
    stats = run_cycle(env.ctx, api, env.jobsdb, env.store, env.notifier)
    assert stats["applied_synced"] == 1
    assert stats["digest"] == 0 and env.notifier.calls == []
    assert env.jobsdb.get("w")["status"] == db_mod.STATUS_APPLIED


def test_gone_posting_excluded_from_digest(env):
    api = FakeAPI(results=[([job("a")], {}), ([job("a")], {})], details={})
    run_cycle(env.ctx, api, env.jobsdb, env.store, env.notifier)
    env.jobsdb.insert(job("w"), db_mod.STATUS_WATCHING, util.to_iso(env.clock()))
    env.clock.now = IN_SLOT
    stats = run_cycle(env.ctx, api, env.jobsdb, env.store, env.notifier)
    assert stats["closed"] == 1 and env.notifier.calls == []
    assert env.jobsdb.get("w")["status"] == db_mod.STATUS_CLOSED
    assert env.jobsdb.meta_get("digest_state") is None  # nothing sent, day not burned


def test_closed_at_excluded_from_digest(env):
    api = FakeAPI(
        results=[([job("a")], {}), ([job("a")], {})],
        details={"w": job("w", closed_at="2026-10-18T09:00:00Z")},
    )
    run_cycle(env.ctx, api, env.jobsdb, env.store, env.notifier)
    env.jobsdb.insert(job("w"), db_mod.STATUS_WATCHING, util.to_iso(env.clock()))
    env.clock.now = IN_SLOT
    stats = run_cycle(env.ctx, api, env.jobsdb, env.store, env.notifier)
    assert stats["closed"] == 1 and env.notifier.calls == []
    assert env.jobsdb.get("w")["closed_at"] == "2026-10-18T09:00:00Z"


def test_quiet_hours_defer_digest_and_slot_is_missed(env):
    env.cfg._raw["freehire"]["quiet_hours"] = [23, 8]
    env.cfg._raw["freehire"]["digest_times"] = ["23:00"]
    api = FakeAPI(results=[([job("a")], {}), ([job("w")], {}), ([job("w")], {})])
    run_cycle(env.ctx, api, env.jobsdb, env.store, env.notifier)
    env.jobsdb.insert(job("w"), db_mod.STATUS_WATCHING, util.to_iso(env.clock()))

    env.clock.now = datetime(2026, 10, 18, 18, 35, tzinfo=UTC)  # 23:35: quiet + in window
    stats = run_cycle(env.ctx, api, env.jobsdb, env.store, env.notifier)
    assert stats["deferred_quiet"] == 1 and env.notifier.calls == []
    assert env.jobsdb.get("w")["alerts"] == 0

    env.clock.now = datetime(2026, 10, 19, 4, 0, tzinfo=UTC)  # 09:00 next day
    stats = run_cycle(env.ctx, api, env.jobsdb, env.store, env.notifier)
    assert stats["digest"] == 0 and env.notifier.calls == []  # no catch-up for 23:00


def test_ignored_params_are_reported(env):
    api = FakeAPI(results=[([job("a")], {"ignored_params": [{"param": "bogus_param"}]})])
    stats = run_cycle(env.ctx, api, env.jobsdb, env.store, env.notifier)
    assert stats["ignored_params"] == ["bogus_param"]


def test_mark_applied_and_unknown_slug(env):
    env.jobsdb.insert(job("w"), db_mod.STATUS_WATCHING, util.to_iso(env.clock()))
    assert _mark(env.ctx, "w", db_mod.STATUS_APPLIED) == 0
    row = env.jobsdb.get("w")
    assert row["status"] == db_mod.STATUS_APPLIED and row["applied_at"]
    sheet = env.store.freehire_get("w")
    assert sheet is None or sheet.get("applied") in (None, "y")

    assert _mark(env.ctx, "nope", db_mod.STATUS_APPLIED) == 1
    assert _mark(env.ctx, None, db_mod.STATUS_APPLIED) == 1


def test_classify_tiers_and_priority_wins():
    cats = FREEHIRE_CFG["categories"]
    assert classify("Unreal Engineer", cats) == "priority"
    assert classify("Unity Developer", cats) == "priority"
    assert classify("Virtual Reality Tester", cats) == "medium"
    assert classify("Unity + Virtual Reality Engineer", cats) == "priority"
    assert classify("Warehouse Extended Goods", cats) == "low"
    assert classify("Some Random Title", cats) == "low"


def test_derive_keywords_from_profile():
    words = derive_keywords(PROFILE)
    assert "Unity" in words and "Unreal" in words
    assert "vr" in words and "games" not in words  # broad tags excluded
    assert "XR" in words
