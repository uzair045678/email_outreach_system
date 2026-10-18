"""`followups`: draft a single follow-up for contacts that never replied."""

from __future__ import annotations

from .. import util
from ..draft.drafter import Drafter
from ..draft.validate import OPT_OUT_MARKERS, PLACEHOLDER_RE
from ..llm import server as srv
from ..llm.client import LLMClient
from ..schedule.slots import add_business_days

ELIGIBLE = ("sent", "delivered_assumed", "auto_reply")


def _thread_of(row: dict) -> str:
    return row.get("thread_key") or row.get("message_id") or row["id"]


def select_due_rows(store, cfg, now=None) -> list[dict]:
    after_days = int(cfg.raw.followup.after_business_days.raw)
    now = now or util.now_utc()
    existing_threads = {
        _thread_of(r) for r in store.queue_all()
        if (r.get("template_variant") or "") == "followup"
    }
    due_rows = []
    for row in store.queue_by_status(*ELIGIBLE):
        sent_at = util.parse_iso(row.get("sent_at"))
        if not sent_at:
            continue
        due = add_business_days(sent_at, after_days)
        fdue = util.parse_iso(row.get("followup_due"))
        if fdue and fdue > due:
            due = fdue
        if now < due:
            continue
        if _thread_of(row) in existing_threads:
            continue
        due_rows.append(row)
    return due_rows


def run(ctx) -> int:
    store, cfg, logger = ctx.store, ctx.cfg, ctx.logger
    max_words = int(cfg.raw.followup.max_body_words.raw)
    auto_approve = bool(cfg.raw.toggles.auto_approve_followups.raw)

    existing_threads = {
        _thread_of(r) for r in store.queue_all()
        if (r.get("template_variant") or "") == "followup"
    }
    due_rows = select_due_rows(store, cfg)
    if ctx.limit:
        due_rows = due_rows[: ctx.limit]
    if not due_rows:
        print("followups: 0 to draft.")
        return 0

    server = srv.load_one(cfg, "draft", logger=logger)
    started = server.start(startup_timeout_s=int(cfg.raw.models.startup_timeout_s.raw))
    if not started:
        logger.error("followups: draft model could not start")
        print("followups: draft model unavailable.")
        return 1

    try:
        client = LLMClient(
            server.base_url,
            timeout_s=int(cfg.raw.models.request_timeout_s.raw),
            temperature=float(cfg.raw.models.temperature.raw),
            max_tokens=int(cfg.raw.models.max_tokens.raw),
            model=server.name,
        )
        drafter = Drafter(cfg, logger=logger)
        made = 0
        for row in due_rows:
            draft = drafter.followup(
                client, row.get("company") or "", row.get("contact_name") or "",
                row.get("subject") or "", headline=row.get("headline") or "",
            )
            if draft is None:
                logger.warning("followups: no draft for %s", row.get("company"))
                continue
            errors = _light_check(draft.body, draft.subject, max_words)
            if errors:
                logger.warning("followups: rejecting draft for %s: %s", row.get("company"), errors)
                continue
            new_row = {
                "company": row.get("company"),
                "website": row.get("website"),
                "country": row.get("country"),
                "region_priority": row.get("region_priority"),
                "company_type": row.get("company_type"),
                "contact_name": row.get("contact_name"),
                "contact_role": row.get("contact_role"),
                "to_email": row.get("to_email"),
                "email_source": row.get("email_source"),
                "email_confidence": row.get("email_confidence"),
                "subject": draft.subject or f"Re: {row.get('subject') or ''}".strip(),
                "headline": row.get("headline") or "",
                "body": draft.body,
                "evidence": row.get("evidence"),
                "template_variant": "followup",
                "thread_key": _thread_of(row),
                "status": "approved" if auto_approve else "pending_review",
                "approve": "yes" if auto_approve else "",
            }
            store.queue_add(new_row)
            existing_threads.add(_thread_of(row))
            made += 1
            logger.info("followup drafted for %s", row.get("company"))
        print(f"followups: {made} drafted.")
        return 0
    finally:
        server.stop()


def _light_check(body: str, subject: str, max_words: int) -> list[str]:
    errors = []
    if not body or not body.strip():
        errors.append("empty body")
    wc = util.word_count(body or "")
    if wc > max_words:
        errors.append(f"body too long ({wc} > {max_words})")
    if PLACEHOLDER_RE.search(body or "") or PLACEHOLDER_RE.search(subject or ""):
        errors.append("placeholder text")
    if not any(m in (body or "").lower() for m in OPT_OUT_MARKERS):
        errors.append("missing opt-out line")
    return errors
