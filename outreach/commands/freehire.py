"""`freehire`: watch freehire.me job posts; alert only you, never a company.

Actions:
  run      one watch cycle (this is what the scheduled task calls)
  applied  mark a job as applied -> stops every reminder
  skip     ignore a job            -> stops every reminder
  draft    queue an application heads-up email for your manual review
  list     show watched jobs and their statuses
"""

from __future__ import annotations

import sys

from .. import util
from ..freehire import db as db_mod
from ..freehire.api import FreehireAPI
from ..freehire.notify import Notifier, render_alert
from ..freehire.watch import run_cycle


def _jobsdb(cfg) -> db_mod.JobsDB:
    return db_mod.JobsDB(cfg.path("cache_dir") / "freehire" / "jobs.db")


def run(ctx) -> int:
    # Job titles contain CJK/emoji; never let the console codec kill the run.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (ValueError, OSError):  # noqa: PERF203 - closed/unsupported stream
            pass
    action = getattr(ctx.args, "action", None) or "run"
    target = getattr(ctx.args, "target", None)
    if action == "run":
        return _cycle(ctx)
    if action == "applied":
        return _mark(ctx, target, db_mod.STATUS_APPLIED)
    if action == "skip":
        return _mark(ctx, target, db_mod.STATUS_SKIPPED)
    if action == "list":
        return _list(ctx)
    if action == "draft":
        return _draft(ctx, target)
    print(f"unknown freehire action: {action!r}")
    return 1


def _cycle(ctx) -> int:
    cfg, logger = ctx.cfg, ctx.logger
    fh = cfg.raw.freehire
    if not fh.enabled.raw:
        print("freehire.enabled = false; nothing to do.")
        return 0

    jobsdb = _jobsdb(cfg)
    try:
        api = FreehireAPI(
            base_url=fh.base_url.raw,
            timeout=float(fh.timeout_s.raw),
            delay_s=float(fh.delay_between_requests_s.raw),
            logger=logger,
        )
        notifier = Notifier(cfg, logger, dry_run=ctx.dry_run)
        stats = run_cycle(ctx, api, jobsdb, ctx.store, notifier, dry_run=ctx.dry_run)
    finally:
        jobsdb.close()

    line = (
        f"freehire: searches={stats['searches']} seen={stats['seen']}"
        f" baseline={stats['baselined']} new={stats['new']} alerted={stats['alerted']}"
        f" quiet={stats['deferred_quiet']} failed={stats['failed']}"
        f" closed={stats['closed']} applied={stats['applied_synced']}"
        f" errors={stats['errors']}"
    )
    if stats["ignored_params"]:
        line += f" IGNORED_PARAMS={stats['ignored_params']}"
    print(line)
    logger.info(line)

    if ctx.dry_run:
        for keyword, sample in stats["samples"].items():
            print(f"  q={keyword!r} total={sample['total']}:")
            for title in sample["titles"]:
                print(f"    - {title}")
        if stats.get("sample"):
            subject, body = render_alert(stats["sample"], 1, int(fh.max_alerts.raw))
            print(f"\n[dry-run] sample of the alert that would be emailed:\nSubject: {subject}\n{body}")

    if stats["searches"] == 0 and stats["errors"]:
        return 1
    return 0


def _mark(ctx, target: str | None, status: str) -> int:
    if not target:
        print(f"usage: python -m outreach freehire {status} <job-slug>")
        return 1
    cfg, store = ctx.cfg, ctx.store
    jobsdb = _jobsdb(cfg)
    try:
        row = jobsdb.get(target)
        if row is None:
            print(f"unknown job id: {target} (run `freehire run`, then `freehire list`)")
            return 1
        if row["status"] == status:
            print(f"{target}: already {status}.")
            return 0
        now_iso = util.to_iso(util.now_utc())
        jobsdb.set_status(target, status, now_iso)
        fields: dict = {"status": status}
        if status == db_mod.STATUS_APPLIED:
            fields.update({"applied": "y", "applied_at": now_iso})
        store.freehire_update(target, **fields)
    finally:
        jobsdb.close()
    print(f"{target}: marked {status}; reminders stopped.")
    return 0


def _list(ctx) -> int:
    jobsdb = _jobsdb(ctx.cfg)
    try:
        rows = jobsdb.all_rows()
    finally:
        jobsdb.close()
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    if not counts:
        print("no freehire jobs known yet; run: python -m outreach freehire run")
        return 0
    print("statuses: " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    for row in rows:
        print(f"{row['status']:9} alerts={row['alerts']:<2} {row['slug']}"
              f" | {row['title']} - {row['company']}")
    return 0


def _draft(ctx, target: str | None) -> int:
    from .freehire_draft import run_draft  # imported late: pulls in the LLM/research stack
    return run_draft(ctx, target)
