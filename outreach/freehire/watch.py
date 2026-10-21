"""One watch cycle: search, baseline, daily digest, closures.

Rules enforced here:
  * first ever run stores everything silently (baseline, no digest)
  * new jobs wait quietly until a digest slot fires (config ``digest_times``):
    four local-time slots per day; a slot only fires if this cycle runs inside
    ``[slot, slot+1h)`` - missed windows are skipped, never catch-up
  * the digest groups jobs priority/medium/low by title keywords (config
    ``categories``) and is one email; each job is listed in exactly one digest
  * a failed digest email never advances ``alerts`` or the slot record, so the
    next in-window cycle simply retries
  * quiet hours defer the digest without touching state
  * a job whose posting closed (404 or ``closed_at``) is dropped from the digest
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .. import util
from . import db as db_mod
from .api import FreehireAPI, FreehireAPIError

# Core XR terms; the profile's own tags alone never spell out plain "XR".
CORE_KEYWORDS = ["XR", "VR", "AR", "WebXR"]
# Project tags worth searching (games/mobile/multiplayer/ai are far too broad).
TAG_ALLOW = {"xr", "vr", "mr", "ar", "webar", "hololens", "quest", "metaverse"}
TIERS = ("priority", "medium", "low")


def derive_keywords(profile_raw: dict) -> list[str]:
    """Keywords from profile.yaml: engines + allowed project tags + XR core."""
    out: list[str] = []
    skills = profile_raw.get("skills") or {}
    for engine in skills.get("engines") or []:
        out.append(str(engine).strip())
    for project in profile_raw.get("projects") or []:
        for tag in project.get("tags") or []:
            if str(tag).lower() in TAG_ALLOW:
                out.append(str(tag).strip())
    out.extend(CORE_KEYWORDS)
    seen: set[str] = set()
    unique: list[str] = []
    for word in out:
        if word and word.lower() not in seen:
            seen.add(word.lower())
            unique.append(word)
    return unique


def in_quiet_hours(now: datetime, quiet: list[int], tz_name: str) -> bool:
    try:
        local = now.astimezone(ZoneInfo(tz_name or "UTC"))
    except Exception:  # noqa: BLE001 - unknown tz name: fall back to machine local
        local = now.astimezone()
    start, end = int(quiet[0]), int(quiet[1])
    if start == end:
        return False
    return local.hour >= start or local.hour < end


def categories_of(fh) -> dict[str, list[str]]:
    cats = fh.categories.raw or {}
    return {tier: [str(k) for k in (cats.get(tier) or [])] for tier in TIERS}


def classify(title: str | None, categories: dict[str, list[str]]) -> str:
    """Tier a job by title keywords; highest tier wins, no match = low."""
    text = (title or "").lower()
    for tier in TIERS:
        for kw in categories.get(tier) or []:
            if kw.lower() in text:
                return tier
    return "low"


def _slot_minutes(hhmm: str) -> int | None:
    try:
        hours, minutes = hhmm.split(":")
        return int(hours) * 60 + int(minutes)
    except ValueError:
        return None


def run_cycle(ctx, api: FreehireAPI, jobsdb: db_mod.JobsDB, store, notifier,
              dry_run: bool = False) -> dict[str, Any]:
    cfg, logger = ctx.cfg, ctx.logger
    fh = cfg.raw.freehire
    now = util.now_utc()
    now_iso = util.to_iso(now)
    profile = cfg.profile_raw()

    keywords = fh.keywords.raw or derive_keywords(profile)
    keywords = list(keywords)[: int(fh.searches_per_cycle.raw)]
    filters = {
        "work_mode": fh.work_mode.raw,
        "open_within_days": fh.open_within_days.raw,
        "is_tech": fh.is_tech.raw,
    }
    categories = categories_of(fh)
    stats: dict[str, Any] = {
        "searches": 0, "ignored_params": [], "seen": 0, "baselined": 0,
        "new": 0, "queued": 0, "alerted": 0, "digest": 0, "deferred_quiet": 0,
        "failed": 0, "closed": 0, "applied_synced": 0, "errors": 0, "samples": {},
    }

    # -- search (sequential, capped) ----------------------------------------
    seen: dict[str, dict] = {}
    for keyword in keywords:
        try:
            jobs, meta = api.search(
                keyword, q_fields=fh.q_fields.raw, filters=filters,
                limit=int(fh.results_per_search.raw),
            )
        except FreehireAPIError as exc:
            logger.error("freehire search %r failed: %s", keyword, exc)
            stats["errors"] += 1
            continue
        stats["searches"] += 1
        stats["samples"][keyword] = {
            "total": meta.get("total"),
            "titles": [str(j.get("title") or "") for j in jobs[:5]],
        }
        for entry in meta.get("ignored_params") or []:
            name = str(entry.get("param") or "")
            if name and name not in stats["ignored_params"]:
                stats["ignored_params"].append(name)
                logger.warning("freehire dropped filter param: %s", name)
        for job in jobs:
            slug = job.get("public_slug")
            if slug:
                seen.setdefault(slug, job)
    stats["seen"] = len(seen)
    if stats["searches"] == 0:
        return stats

    # -- first ever run: baseline silently -----------------------------------
    if jobsdb.is_empty():
        stats["baselined"] = len(seen)
        if not dry_run:
            for job in seen.values():
                jobsdb.insert(job, db_mod.STATUS_BASELINE, now_iso)
            for row in jobsdb.all_rows():
                _sync_sheet(store, row, classify(row.get("title"), categories))
        elif seen:
            stats["sample"] = next(iter(seen.values()))
        logger.info("freehire baseline: %d existing job(s) %s", len(seen),
                    "stored silently" if not dry_run else "seen (dry-run, not written)")
        return stats

    # -- applied column in the sheet is an input ------------------------------
    if not dry_run and store is not None:
        for slug in store.freehire_applied_slugs():
            row = jobsdb.get(slug)
            if row and row["status"] not in (db_mod.STATUS_APPLIED, db_mod.STATUS_SKIPPED):
                jobsdb.set_status(slug, db_mod.STATUS_APPLIED, now_iso)
                stats["applied_synced"] += 1

    # -- new jobs: stored quietly, digested later -----------------------------
    known = jobsdb.slugs()
    new_slugs = [slug for slug in seen if slug not in known]
    stats["new"] = len(new_slugs)
    if not dry_run:
        for slug in new_slugs:
            jobsdb.insert(seen[slug], db_mod.STATUS_WATCHING, now_iso)

    # Dry runs never insert, so new jobs must be carried into the digest as
    # virtual candidates to preview what a real run would send.
    virtual_new = [seen[slug] for slug in new_slugs] if dry_run else []
    stats["queued"] = len(jobsdb.undigested()) + len(virtual_new)
    _maybe_send_digest(ctx, api, jobsdb, store, notifier, stats, seen, now,
                       dry_run, virtual_new)

    if not dry_run and store is not None:
        for row in jobsdb.all_rows():
            _sync_sheet(store, row, classify(row.get("title"), categories))
    if dry_run and stats["alerted"] == 0 and not stats.get("sample") and seen:
        stats["sample"] = next(iter(seen.values()))
    return stats


def _maybe_send_digest(ctx, api: FreehireAPI, jobsdb: db_mod.JobsDB, store,
                       notifier, stats: dict[str, Any], seen: dict[str, dict],
                       now: datetime, dry_run: bool,
                       virtual_new: list[dict] | None = None) -> None:
    """Fire one digest when the local clock sits inside an unsent slot window."""
    cfg, logger = ctx.cfg, ctx.logger
    fh = cfg.raw.freehire
    tz_name = str(ctx.cfg.profile_raw().get("timezone") or "")
    try:
        local = now.astimezone(ZoneInfo(tz_name or "UTC"))
    except Exception:  # noqa: BLE001 - unknown tz: machine local
        local = now.astimezone()
    today = local.strftime("%Y-%m-%d")
    now_min = local.hour * 60 + local.minute

    state: dict[str, Any] = {}
    raw = jobsdb.meta_get("digest_state")
    if raw:
        try:
            state = json.loads(raw)
        except ValueError:
            state = {}
    sent_today = list(state.get("slots") or []) if state.get("date") == today else []

    slot: str | None = None
    for candidate in fh.digest_times.raw or []:
        name = str(candidate)
        start = _slot_minutes(name)
        if start is None:
            logger.warning("freehire: bad digest_times entry %r ignored", candidate)
            continue
        if name in sent_today:
            continue
        if start <= now_min < start + 60:
            slot = name
            break
    if slot is None:
        return  # outside every window: missed slots are skipped, no catch-up

    def _record_slot() -> None:
        if not dry_run:
            jobsdb.meta_set("digest_state", json.dumps(
                {"date": today, "slots": sent_today + [slot]}))

    # Quiet hours defer real sending; a dry run is a preview.
    if not dry_run and in_quiet_hours(now, list(fh.quiet_hours.raw), tz_name):
        stats["deferred_quiet"] += 1
        logger.debug("freehire: digest slot %s deferred (quiet hours)", slot)
        return

    candidates = jobsdb.undigested()
    if dry_run and virtual_new:
        known = {row["slug"] for row in candidates}
        candidates = candidates + [
            {**job, "slug": job.get("public_slug") or ""}
            for job in virtual_new
            if (job.get("public_slug") or "") not in known
        ]
    if not candidates:
        _record_slot()  # nothing new; never email an empty digest
        logger.info("freehire: digest slot %s: 0 new jobs, nothing sent", slot)
        return

    categories = categories_of(fh)
    now_ref = util.to_iso(now)
    fresh: list[dict] = []
    for row in candidates:
        slug = row["slug"]
        job = seen.get(slug)
        if job is None:
            try:
                job = api.get_job(slug)
            except FreehireAPIError as exc:
                logger.warning("freehire detail %s failed: %s; listing stored copy", slug, exc)
                stats["errors"] += 1
                job = row  # keep it; a listing is better than a lost job
            if job is None:
                stats["closed"] += 1
                if not dry_run:
                    jobsdb.set_status(slug, db_mod.STATUS_CLOSED, now_ref)
                    stored = jobsdb.get(slug)
                    _sync_sheet(store, stored,
                                classify(stored.get("title") if stored else "", categories))
                continue
        if job.get("closed_at"):
            stats["closed"] += 1
            if not dry_run:
                jobsdb.update(slug, status=db_mod.STATUS_CLOSED, next_due_at=None,
                              closed_at=str(job.get("closed_at")))
                _sync_sheet(store, jobsdb.get(slug),
                            classify(str(job.get("title") or row.get("title") or ""),
                                     categories))
            continue
        fresh.append({**row, **{k: job.get(k) for k in
                                ("title", "company", "url", "location", "work_mode")
                                if job.get(k)}})

    if not fresh:
        _record_slot()  # every candidate closed; don't spin on this slot forever
        return

    groups = [(tier, [j for j in fresh
                      if classify(str(j.get("title") or ""), categories) == tier])
              for tier in TIERS]
    delivered = notifier.digest(groups)
    if not delivered:
        stats["failed"] += 1
        logger.warning("freehire: digest delivery failed; state unchanged, will retry in-window")
        return
    stats["digest"] = 1
    stats["alerted"] = sum(len(jobs) for _, jobs in groups)
    if not dry_run:
        for _, jobs in groups:
            for job in jobs:
                jobsdb.record_alert(job["slug"], now_ref, None)
        _record_slot()
        logger.info("freehire: digest slot %s sent (%s)", slot,
                    _counts_label(groups))


def _counts_label(groups: list[tuple[str, list[dict]]]) -> str:
    total = sum(len(j) for _, j in groups)
    parts = [f"{tier}:{len(jobs)}" for tier, jobs in groups if jobs]
    return f"{total} jobs ({', '.join(parts)})"


def _sync_sheet(store, row: dict | None, category: str = "") -> None:
    """Mirror a job to the Freehire sheet without touching the applied column."""
    if store is None or not row:
        return
    data = {
        "slug": row.get("slug"),
        "title": row.get("title"),
        "company": row.get("company"),
        "location": row.get("location"),
        "work_mode": row.get("work_mode"),
        "url": row.get("url"),
        "status": row.get("status"),
        "first_seen": row.get("first_seen"),
        "last_alert_at": row.get("last_alert_at"),
        "alerts": row.get("alerts"),
        "applied_at": row.get("applied_at"),
        "closed_at": row.get("closed_at"),
    }
    if category:
        data["category"] = category
    store.freehire_upsert(data)
