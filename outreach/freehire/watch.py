"""One watch cycle: search, baseline, new-job alerts, reminders, closures.

Rules enforced here:
  * first ever run stores everything silently (baseline, no alerts)
  * an alert is sent only when delivery succeeds; failures never advance
    ``alerts`` or ``next_due_at``, so the next cycle simply retries
  * reminders follow config ``reminder_hours`` up to ``max_alerts``
  * quiet hours defer sending without touching state
  * a job whose posting closed (404 or ``closed_at``) stops reminders
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .. import util
from . import db as db_mod
from .api import FreehireAPI, FreehireAPIError

# Core XR terms; the profile's own tags alone never spell out plain "XR".
CORE_KEYWORDS = ["XR", "VR", "AR", "WebXR"]
# Project tags worth searching (games/mobile/multiplayer/ai are far too broad).
TAG_ALLOW = {"xr", "vr", "mr", "ar", "webar", "hololens", "quest", "metaverse"}


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


def next_due_after(alerts_done: int, now: datetime, reminder_hours: list[int],
                   max_alerts: int) -> str | None:
    """ISO due time for the next reminder, or None once the schedule is spent."""
    if alerts_done >= max_alerts or not reminder_hours:
        return None
    index = min(alerts_done - 1, len(reminder_hours) - 1)
    return util.to_iso(now + timedelta(hours=float(reminder_hours[index])))


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
    }
    stats: dict[str, Any] = {
        "searches": 0, "ignored_params": [], "seen": 0, "baselined": 0,
        "new": 0, "alerted": 0, "deferred_quiet": 0, "failed": 0,
        "closed": 0, "applied_synced": 0, "errors": 0,
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
        logger.info("freehire baseline: %d existing job(s) stored silently", len(seen))
        return stats

    # -- applied column in the sheet is an input ------------------------------
    if not dry_run and store is not None:
        for slug in store.freehire_applied_slugs():
            row = jobsdb.get(slug)
            if row and row["status"] not in (db_mod.STATUS_APPLIED, db_mod.STATUS_SKIPPED):
                jobsdb.set_status(slug, db_mod.STATUS_APPLIED, now_iso)
                stats["applied_synced"] += 1

    # -- new jobs --------------------------------------------------------------
    known = jobsdb.slugs()
    new_slugs = [slug for slug in seen if slug not in known]
    stats["new"] = len(new_slugs)
    if not dry_run:
        for slug in new_slugs:
            jobsdb.insert(seen[slug], db_mod.STATUS_WATCHING, now_iso, next_due_iso=now_iso)

    # -- delivery over due rows ------------------------------------------------
    if dry_run:
        rows = jobsdb.due(now_iso)
        for slug in new_slugs:
            job = seen[slug]
            rows.append({
                "slug": slug, "title": job.get("title") or "",
                "company": job.get("company") or "", "url": job.get("url") or "",
                "location": job.get("location") or "",
                "work_mode": job.get("work_mode") or "",
                "status": db_mod.STATUS_WATCHING, "alerts": 0,
            })
    else:
        rows = jobsdb.due(now_iso)

    quiet = list(fh.quiet_hours.raw)
    max_alerts = int(fh.max_alerts.raw)
    reminder_hours = list(fh.reminder_hours.raw)
    tz_name = str(profile.get("timezone") or "")

    for row in rows:
        slug = row["slug"]
        if int(row["alerts"] or 0) >= max_alerts:
            if not dry_run:
                jobsdb.update(slug, next_due_at=None)
            continue

        job = seen.get(slug)
        if job is None:
            try:
                job = api.get_job(slug)
            except FreehireAPIError as exc:
                logger.warning("freehire detail %s failed: %s", slug, exc)
                stats["errors"] += 1
                continue  # no state change: retried next cycle
            if job is None:
                stats["closed"] += 1
                if not dry_run:
                    jobsdb.set_status(slug, db_mod.STATUS_CLOSED, now_iso)
                    _sync_sheet(store, jobsdb.get(slug))
                continue
        if job.get("closed_at"):
            stats["closed"] += 1
            if not dry_run:
                jobsdb.update(slug, status=db_mod.STATUS_CLOSED, next_due_at=None,
                              closed_at=str(job.get("closed_at")))
                _sync_sheet(store, jobsdb.get(slug))
            continue

        if in_quiet_hours(now, quiet, tz_name):
            stats["deferred_quiet"] += 1
            logger.debug("freehire: %s deferred (quiet hours)", slug)
            continue

        alert_no = int(row["alerts"] or 0) + 1
        payload = {**row, **{k: job.get(k) for k in
                             ("title", "company", "url", "location", "work_mode")
                             if job.get(k)}}
        delivered = notifier.alert(payload, alert_no=alert_no, max_alerts=max_alerts)
        if not delivered:
            stats["failed"] += 1
            logger.warning("freehire: delivery failed for %s; state unchanged, will retry", slug)
            continue

        stats["alerted"] += 1
        if not dry_run:
            next_due = next_due_after(alert_no, now, reminder_hours, max_alerts)
            jobsdb.record_alert(slug, now_iso, next_due)
            _sync_sheet(store, jobsdb.get(slug))

    if not dry_run and store is not None:
        for row in jobsdb.all_rows():
            _sync_sheet(store, row)
    return stats


def _sync_sheet(store, row: dict | None) -> None:
    """Mirror a job to the Freehire sheet without touching the applied column."""
    if store is None or not row:
        return
    store.freehire_upsert({
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
    })
