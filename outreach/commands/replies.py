"""`replies`: classify inbound replies and update the workbook."""

from __future__ import annotations

import re
from datetime import timedelta

from .. import util
from ..mail import processed, replies as rl
from ..mail.himalaya import Himalaya, HimalayaError
from ..llm import server as srv
from ..llm.client import LLMClient

RELEVANT_STATUSES = ("sent", "delivered_assumed", "followup_pending", "auto_reply")


def _from_email(env: dict) -> str:
    frm = env.get("from") or []
    if frm and isinstance(frm, list):
        return (frm[0].get("email") or "").lower()
    return ""


def _index(store) -> tuple[dict, dict, set]:
    by_mid: dict[str, dict] = {}
    by_email: dict[str, dict] = {}
    sent_mids: set[str] = set()
    for row in store.queue_all():
        mid = row.get("message_id")
        if mid:
            by_mid[mid] = row
            sent_mids.add(mid)
        if row.get("to_email"):
            by_email[row["to_email"].lower()] = row
    return by_mid, by_email, sent_mids


def _match(info: rl.ReplyInfo, by_mid: dict, by_email: dict) -> dict | None:
    blob = f"{info.in_reply_to} {info.references}"
    for mid in re.findall(r"<[^<>@\s]+@[^<>\s]+>", blob):
        if mid in by_mid:
            return by_mid[mid]
    return by_email.get(info.from_addr)


def run(ctx) -> int:
    store, cfg, logger = ctx.store, ctx.cfg, ctx.logger
    hima = Himalaya(cfg.raw.himalaya.account.raw, logger=logger)
    page_size = int(cfg.raw.himalaya.scan_page_size.raw)
    folder = cfg.raw.himalaya.folders.inbox.raw

    by_mid, by_email, sent_mids = _index(store)
    processed_data = processed.load(cfg)
    processed_ids = set(processed_data.get("replies", []))

    candidates: list[tuple[dict, str, rl.ReplyInfo]] = []
    try:
        envelopes = hima.list_envelopes(folder, page_size=page_size)
    except HimalayaError as exc:
        logger.error("replies: cannot list inbox: %s", exc)
        envelopes = []

    for env in envelopes:
        mid = env.get("message-id") or ""
        if mid and mid in processed_ids:
            continue
        from_addr = _from_email(env)
        if not from_addr:
            continue
        subject = env.get("subject") or ""
        if any(h in (subject or "").lower() for h in ("undelivered", "delivery status", "mail delivery")):
            continue
        try:
            raw = hima.raw_message(folder, env.get("id"))
        except HimalayaError as exc:
            logger.warning("replies: cannot read %s: %s", env.get("id"), exc)
            continue
        info = rl.parse_message(raw)
        if not info.from_addr:
            info.from_addr = from_addr
        row = _match(info, by_mid, by_email)
        if row is None and not rl.references_us(info, sent_mids):
            continue
        if (row and (row.get("status") in ("bounced_hard", "suppressed", "needs_new_address"))):
            continue
        candidates.append((env, mid, info))

    if not candidates:
        print("replies: 0 new.")
        return 0

    needs_model = [
        c for c in candidates
        if not rl.is_auto_reply(c[2]) and rl.classify_heuristic(c[2]) == "other"
    ]
    client = None
    server = None
    if needs_model:
        server = srv.load_one(cfg, "classify", logger=logger)
        if server.start(timeout_s=int(cfg.raw.models.startup_timeout_s.raw)):
            client = LLMClient(
                server.base_url,
                timeout_s=int(cfg.raw.models.request_timeout_s.raw),
                temperature=0.1, max_tokens=200, model=server.name,
            )
        else:
            logger.warning("replies: classify model unavailable; using heuristics")
            server = None

    handled = 0
    try:
        for env, mid, info in candidates:
            _apply(ctx, info, client, by_mid, by_email)
            if mid:
                processed.mark_processed(cfg, "replies", mid)
                processed_ids.add(mid)
            handled += 1
    finally:
        if server is not None:
            server.stop()

    logger.info("replies: handled %d", handled)
    print(f"replies: {handled} classified.")
    return 0


def _apply(ctx, info: rl.ReplyInfo, client, by_mid, by_email) -> None:
    store, cfg, logger = ctx.store, ctx.cfg, ctx.logger
    row = _match(info, by_mid, by_email)
    if row is None:
        return

    if rl.is_auto_reply(info):
        category = "auto_reply_out_of_office"
    else:
        category = rl.classify_heuristic(info)
        if category == "other" and client is not None:
            data = rl.classify_with_model(client, info)
            category = data.get("category") or category

    note = f"reply {category}"
    if category == "auto_reply_out_of_office":
        return_date = rl.extract_return_date(info.body) or rl.extract_return_date(info.subject)
        if return_date:
            due = return_date
        else:
            days = int(cfg.raw.followup.ooo_default_days.raw)
            due = util.to_iso(util.now_utc() + timedelta(days=days))[:10]
        store.queue_update(
            row["id"], status="auto_reply", followup_due=due,
            reply_sentiment="auto_reply", notes=_append(row, note),
        )
    elif category == "negative_or_unsubscribe":
        email = (row.get("to_email") or info.from_addr or "").lower()
        domain = util.registrable_domain(util.domain_of(email))
        store.suppressed_add({
            "email": email, "domain": domain,
            "reason": "unsubscribe/negative reply", "added_at": util.to_iso(util.now_utc()),
        })
        store.queue_update(
            row["id"], status="suppressed", reply_sentiment="negative",
            notes=_append(row, note),
        )
    else:
        sentiment = "positive" if category == "positive" else "other"
        store.queue_update(
            row["id"], status="replied", reply_sentiment=sentiment,
            notes=_append(row, note),
        )

    store.summary_bump(ctx.today, "replies")
    logger.info("reply from %s -> %s", info.from_addr, category)


def _append(row: dict, note: str) -> str:
    existing = row.get("notes") or ""
    return (existing + " | " + note).strip(" |") if existing else note
