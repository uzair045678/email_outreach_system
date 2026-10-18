"""`send`: process approvals and send due, approved emails. No LLM calls."""

from __future__ import annotations

import random
import time
from datetime import date, timedelta

from .. import state, util
from ..draft.validate import PLACEHOLDER_RE, collect_allowed_urls, extract_urls
from ..mail.himalaya import Himalaya
from ..schedule import slots, tz
from .mime import build_message

SENT_STATUSES = {"sent", "delivered_assumed", "replied", "bounced_soft", "bounced_hard"}


def _append_note(row: dict, note: str) -> str:
    existing = row.get("notes") or ""
    return (existing + " | " + note).strip(" |") if existing else note


def business_days_between(start: date, end: date) -> int:
    if end <= start:
        return 0
    count = 0
    d = start
    while d < end:
        d = d + timedelta(days=1)
        if d.weekday() < 5:
            count += 1
    return count


def daily_cap(cfg) -> int:
    ramp = [int(x) for x in cfg.raw.sending.warmup_ramp.raw]
    maxd = int(cfg.raw.sending.max_sends_per_day.raw)
    start = state.get_warmup_start(cfg)
    if not start:
        return min(ramp[0], maxd)
    try:
        idx = business_days_between(date.fromisoformat(start), util.now_utc().date())
    except ValueError:
        idx = 0
    idx = max(0, min(idx, len(ramp) - 1))
    return min(ramp[idx], maxd)


def _sent_rows(store) -> list[dict]:
    return [r for r in store.queue_all() if (r.get("status") or "") in SENT_STATUSES and r.get("sent_at")]


def sends_today(store) -> int:
    today = util.now_utc().date()
    count = 0
    for r in store.queue_all():
        if (r.get("status") or "") in ("sent", "followup_sent") and r.get("sent_at"):
            dt = util.parse_iso(r["sent_at"])
            if dt and dt.date() == today:
                count += 1
    return count


def sends_last_hour(store) -> int:
    now = util.now_utc()
    count = 0
    for r in store.queue_all():
        if (r.get("status") or "") in ("sent", "followup_sent") and r.get("sent_at"):
            dt = util.parse_iso(r["sent_at"])
            if dt and (now - dt).total_seconds() <= 3600:
                count += 1
    return count


def bounce_rate(store, cfg) -> float:
    window = int(cfg.raw.sending.bounce_window_days.raw)
    now = util.now_utc()
    hard = 0
    delivered = 0
    for r in _sent_rows(store):
        dt = util.parse_iso(r["sent_at"])
        if not dt or (now - dt).total_seconds() > window * 86400:
            continue
        delivered += 1
        if (r.get("status") or "") == "bounced_hard":
            hard += 1
    if delivered == 0:
        return 0.0
    return hard / delivered


# -- approvals ---------------------------------------------------------------
def process_approvals(ctx, dry_run: bool = False) -> list[str]:
    """Move approve=y rows to approved; return the ids that were (or would be) approved."""
    store, cfg = ctx.store, ctx.cfg
    now = util.now_utc()
    auto = bool(cfg.raw.toggles.auto_approve.raw)
    hours = [int(h) for h in cfg.raw.sending.send_local_hours.raw]
    jitter = int(cfg.raw.sending.jitter_minutes.raw)
    approved_ids: list[str] = []

    for row in store.queue_all():
        status = (row.get("status") or "").strip()
        if status != "pending_review":
            continue
        approve = (row.get("approve") or "").strip().lower()
        row_id = row.get("id")

        if approve == "skip":
            if not dry_run:
                store.queue_update(row_id, status="skipped", notes=_append_note(row, "skipped by user"))
            continue
        if approve != "y" and not auto:
            continue
        if not row.get("to_email") or not row.get("subject") or not row.get("body"):
            if not dry_run:
                store.queue_update(row_id, status="error", last_error="missing to/subject/body at approval")
            continue

        tzname = row.get("recipient_tz") or tz.recipient_timezone(row.get("country") or "")[0]
        iso = tz.country_code(row.get("country") or "")
        slot = slots.next_send_slot(now, tzname, hours, jitter, iso)
        draft_hash = util.sha256(
            f"{row.get('subject')}|{row.get('body')}|{row.get('to_email')}|{row.get('contact_name') or ''}"
        )
        approved_ids.append(row_id)
        if not dry_run:
            store.queue_update(
                row_id,
                status="approved",
                send_after_utc=util.to_iso(slot),
                local_send_time=slots.describe_local(slot, tzname),
                recipient_tz=tzname,
                draft_hash=draft_hash,
            )
    return approved_ids


# -- preflight ---------------------------------------------------------------
def preflight(row: dict, cfg, store, profile: dict) -> tuple[bool, str]:
    if not (row.get("to_email") or "").strip():
        return False, "empty to_email"
    to_email = row["to_email"].strip().lower()
    if to_email in store.suppressed_emails():
        return False, "recipient is suppressed"
    if to_email in store.bad_emails():
        return False, "recipient is a known bad address"
    body = row.get("body") or ""
    subject = row.get("subject") or ""
    if PLACEHOLDER_RE.search(body) or PLACEHOLDER_RE.search(subject):
        return False, "placeholder text present"
    allowed = collect_allowed_urls(profile)
    allowed_domains = {
        util.registrable_domain(util.domain_of(u)) for u in allowed if u
    } - {""}
    for url in extract_urls(body):
        if url in allowed:
            continue
        if util.registrable_domain(util.domain_of(url)) in allowed_domains:
            continue
        return False, f"disallowed link: {url}"

    stored_hash = row.get("draft_hash") or ""
    if stored_hash:
        current = util.sha256(
            f"{subject}|{body}|{to_email}|{row.get('contact_name') or ''}"
        )
        if current != stored_hash:
            return False, "draft changed after approval"

    domain = util.registrable_domain(util.domain_of(to_email))
    is_followup = (row.get("template_variant") or "") == "followup" or (row.get("status") or "") == "followup_pending"
    is_resend = bool(row.get("is_resend"))
    if not (is_followup or is_resend) and domain and domain in store.contacted_domains():
        return False, "domain already contacted"
    return True, ""


def run(ctx) -> int:
    store, cfg, logger = ctx.store, ctx.cfg, ctx.logger
    profile = cfg.profile_raw()
    from_email = profile.get("email") or ""
    from_name = profile.get("full_name") or ""

    if not from_email:
        logger.error("profile.email is not set; cannot send")
        print("profile.yaml email is TODO; fill it before sending.")
        return 1

    paused, reason = state.is_paused(cfg)
    if paused:
        logger.warning("sending is paused: %s", reason)
        print(f"Sending paused: {reason}. Delete cache\\pause_sending.flag to resume.")
        return 0

    rate = bounce_rate(store, cfg)
    threshold = float(cfg.raw.sending.hard_bounce_rate_pause.raw)
    if rate > threshold:
        msg = f"hard bounce rate {rate:.1%} > {threshold:.0%}; pausing sending"
        state.set_paused(cfg, msg)
        logger.error(msg)
        store.summary_bump(ctx.today, "errors")
        print(msg + " - clear cache\\pause_sending.flag to resume.")
        return 0

    approved = process_approvals(ctx, dry_run=ctx.dry_run)
    if approved:
        logger.info("processed %d approval(s)", len(approved))

    cap = daily_cap(cfg)
    already = sends_today(store)
    hourly = sends_last_hour(store)
    hourly_cap = int(cfg.raw.sending.max_sends_per_hour.raw)
    budget = max(0, min(cap - already, hourly_cap - hourly))
    base = budget if ctx.limit is None else ctx.limit
    limit = min(base, budget)

    now = util.now_utc()
    due = []
    for row in store.queue_by_status("approved"):
        slot = util.parse_iso(row.get("send_after_utc"))
        if slot is None or slot <= now:
            due.append(row)
    due.sort(key=lambda r: r.get("send_after_utc") or "")

    logger.info(
        "send: cap=%d today=%d hourly=%d budget=%d due=%d limit=%d dry_run=%s",
        cap, already, hourly, budget, len(due), limit, ctx.dry_run,
    )

    sent = 0
    min_delay = int(cfg.raw.sending.min_delay_between_sends_s.raw)
    max_delay = int(cfg.raw.sending.max_delay_between_sends_s.raw)
    sent_folder = cfg.raw.himalaya.folders.sent.raw
    save_copy = bool(cfg.raw.himalaya.save_sent_copy.raw) if "save_sent_copy" in cfg.raw.himalaya else False
    hima = None if ctx.dry_run else Himalaya(cfg.raw.himalaya.account.raw, logger=logger)

    for row in due:
        if sent >= limit:
            break
        ok, err = preflight(row, cfg, store, profile)
        if not ok:
            if not ctx.dry_run:
                new_status = "skipped" if "already contacted" in err else "error"
                store.queue_update(row["id"], status=new_status, last_error=err, notes=_append_note(row, err))
                store.summary_bump(ctx.today, "errors")
            logger.warning("skip %s: %s", row.get("company"), err)
            continue

        tzname = row.get("recipient_tz") or tz.recipient_timezone(row.get("country") or "")[0]
        iso = tz.country_code(row.get("country") or "")
        overdue = int(cfg.raw.sending.overdue_reschedule_hours.raw)
        slot, resched = slots.due_or_reschedule(
            util.parse_iso(row.get("send_after_utc")), now, tzname,
            [int(h) for h in cfg.raw.sending.send_local_hours.raw],
            int(cfg.raw.sending.jitter_minutes.raw), iso, overdue,
        )
        if resched:
            if not ctx.dry_run:
                store.queue_update(
                    row["id"], send_after_utc=util.to_iso(slot),
                    local_send_time=slots.describe_local(slot, tzname),
                    notes=_append_note(row, "rescheduled (overdue)"),
                )
            logger.info("rescheduled overdue row %s -> %s", row.get("company"), slot)
            continue

        attachment = row.get("attachment") or ""
        if not ctx.dry_run:
            if row.get("template_variant") == "followup":
                raw, mid = build_message(
                    from_email, row["to_email"], row.get("subject") or "", row.get("body") or "",
                    from_name=from_name, in_reply_to=row.get("message_id") or "",
                    references=row.get("message_id") or "",
                )
            else:
                raw, mid = build_message(
                    from_email, row["to_email"], row.get("subject") or "", row.get("body") or "",
                    from_name=from_name, attachment_path=attachment or None,
                )
            if sent > 0:
                time.sleep(random.uniform(min_delay, max_delay))
            ok, err = hima.send_raw(raw, save_mailbox=sent_folder if save_copy else "")
        else:
            mid = "(dry-run)"
            ok, err = True, ""

        if not ctx.dry_run and ok:
            followup_days = int(cfg.raw.followup.after_business_days.raw)
            followup_due = slots.add_business_days(now, followup_days, iso)
            is_followup = row.get("template_variant") == "followup"
            new_status = "followup_sent" if is_followup else "sent"
            store.queue_update(
                row["id"], status=new_status, sent_at=util.to_iso(now), message_id=mid,
                thread_key=mid, followup_due=util.to_iso(followup_due),
                attempts=(int(row.get("attempts") or 0) + 1),
            )
            domain = util.registrable_domain(util.domain_of(row["to_email"]))
            store.contacted_add({
                "domain": domain, "email": row["to_email"], "company": row.get("company") or "",
                "first_contacted": util.to_iso(now), "source": "pipeline", "outcome": "sent",
            })
            store.summary_bump(ctx.today, "followups_sent" if is_followup else "sent")
            state.set_warmup_start(cfg, now.date().isoformat())
            sent += 1
            logger.info("sent -> %s (%s)", row["to_email"], mid)
            store.save()
        elif not ctx.dry_run:
            store.queue_update(
                row["id"], status="error", last_error=err,
                attempts=(int(row.get("attempts") or 0) + 1),
            )
            store.summary_bump(ctx.today, "errors")
            logger.error("send failed for %s: %s", row.get("to_email"), err)
            store.save()
        else:
            print(f"[dry-run] would send to {row.get('to_email')} | {row.get('subject')}")
            sent += 1

    print(f"send: {sent} {'planned' if ctx.dry_run else 'sent'} (budget {budget}, cap {cap}).")
    return 0
