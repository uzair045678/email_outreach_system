"""`summary`: write today's summary row and log it (optionally email to self)."""

from __future__ import annotations

from .. import util
from ..mail.himalaya import Himalaya, HimalayaError
from ..send.mime import build_message

FIELDS = ("researched", "drafted", "approved", "sent", "bounced", "resent",
          "replies", "followups_sent", "gave_up", "errors", "search_calls")


def _derived_today(store, today: str) -> dict:
    sent = 0
    resent = 0
    for row in store.queue_all():
        dt = util.parse_iso(row.get("sent_at"))
        if dt and util.to_iso(dt)[:10] == today:
            sent += 1
            if row.get("is_resend"):
                resent += 1
    return {"sent": sent, "resent": resent}


def run(ctx) -> int:
    store, cfg, logger = ctx.store, ctx.cfg, ctx.logger
    today = ctx.today

    stats = store.summary_get(today) or {}
    derived = _derived_today(store, today)
    for key, value in derived.items():
        if not stats.get(key):
            stats[key] = value

    store.summary_set(today, **{k: (stats.get(k) or 0) for k in FIELDS})
    line = " ".join(f"{k}={stats.get(k) or 0}" for k in FIELDS)
    store.log("summary", "INFO", line)
    logger.info("summary %s %s", today, line)
    print(f"summary {today}: {line}")

    if cfg.raw.toggles.summary_email_to_self.raw and not ctx.dry_run:
        _email_self(ctx, today, stats)

    return 0


def _email_self(ctx, today: str, stats: dict) -> None:
    cfg, logger = ctx.cfg, ctx.logger
    to_email = cfg.profile_raw().get("email") or ""
    if not to_email or to_email == "TODO":
        logger.warning("summary: no profile email; skipping self email")
        return
    body_lines = [f"Outreach summary for {today}", ""]
    body_lines += [f"- {k}: {stats.get(k) or 0}" for k in FIELDS]
    body = "\n".join(body_lines) + "\n"
    raw, mid = build_message(
        from_email=to_email, to_email=to_email,
        subject=f"Outreach summary {today}", body=body,
        from_name=cfg.profile_raw().get("full_name") or "",
    )
    hima = Himalaya(cfg.raw.himalaya.account.raw, logger=logger)
    ok, err = hima.send_raw(raw)
    if ok:
        logger.info("summary email sent to self (%s)", mid)
    else:
        logger.error("summary email failed: %s", err)
