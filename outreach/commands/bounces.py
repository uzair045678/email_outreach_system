"""`bounces`: detect hard/soft bounces, update rows, add to BadAddresses."""

from __future__ import annotations

from datetime import timedelta

from .. import util
from ..mail import dsn, processed
from ..mail.himalaya import Himalaya, HimalayaError
from ..llm import server as srv
from ..llm.client import LLMClient, add_no_think
from ..draft import prompts

SENT_STATUSES = ("sent", "delivered_assumed", "replied", "bounced_soft")


def _from_email(env: dict) -> str:
    frm = env.get("from") or []
    if frm and isinstance(frm, list):
        return (frm[0].get("email") or "").lower()
    return ""


def _index_rows(store) -> tuple[dict, dict]:
    by_mid: dict[str, dict] = {}
    by_email: dict[str, dict] = {}
    for row in store.queue_all():
        if row.get("message_id"):
            by_mid[row["message_id"]] = row
        if row.get("to_email"):
            by_email[row["to_email"].lower()] = row
    return by_mid, by_email


def _match_row(result: dsn.DsnResult, by_mid: dict, by_email: dict) -> dict | None:
    if result.original_message_id and result.original_message_id in by_mid:
        return by_mid[result.original_message_id]
    if result.final_recipient and result.final_recipient in by_email:
        return by_email[result.final_recipient]
    return None


def sweep_delivered(store, cfg, logger) -> int:
    """Mark sent rows as delivered_assumed after the delivery window with no bounce."""
    hours = int(cfg.raw.sending.delivered_assumed_after_hours.raw)
    now = util.now_utc()
    n = 0
    for row in store.queue_by_status("sent"):
        dt = util.parse_iso(row.get("sent_at"))
        if dt and (now - dt) >= timedelta(hours=hours):
            store.queue_update(row["id"], status="delivered_assumed")
            n += 1
    if n:
        logger.info("marked %d row(s) delivered_assumed", n)
    return n


def run(ctx) -> int:
    store, cfg, logger = ctx.store, ctx.cfg, ctx.logger
    hima = Himalaya(cfg.raw.himalaya.account.raw, logger=logger)
    page_size = int(cfg.raw.himalaya.scan_page_size.raw)
    folders = [cfg.raw.himalaya.folders.inbox.raw, cfg.raw.himalaya.folders.spam.raw]

    sweep_delivered(store, cfg, logger)

    by_mid, by_email = _index_rows(store)
    processed_data = processed.load(cfg)
    processed_ids = set(processed_data.get("bounces", []))

    pending_raw: list[tuple[str, str]] = []  # (envelope message-id, raw)
    handled = 0

    for folder in folders:
        try:
            envelopes = hima.list_envelopes(folder, page_size=page_size)
        except HimalayaError as exc:
            logger.error("bounces: cannot list %s: %s", folder, exc)
            continue
        for env in envelopes:
            mid = env.get("message-id") or ""
            if mid and mid in processed_ids:
                continue
            from_addr = _from_email(env)
            subject = env.get("subject") or ""
            if not dsn.looks_like_bounce(subject, from_addr):
                continue
            try:
                raw = hima.raw_message(folder, env.get("id"))
            except HimalayaError as exc:
                logger.warning("bounces: cannot read message %s: %s", env.get("id"), exc)
                continue
            result = dsn.parse_dsn(raw)
            if not result.is_bounce:
                if mid:
                    processed.mark_processed(cfg, "bounces", mid)
                    processed_ids.add(mid)
                continue
            if not result.parseable:
                pending_raw.append((mid, raw))
                continue
            _apply_bounce(ctx, result, by_mid, by_email)
            if mid:
                processed.mark_processed(cfg, "bounces", mid)
                processed_ids.add(mid)
            handled += 1

    # Model fallback for DSNs that weren't machine-parseable.
    if pending_raw:
        _handle_unparseable(ctx, pending_raw, by_mid, by_email)
        handled += len(pending_raw)

    store.summary_bump(ctx.today, "bounced", handled) if handled else None
    logger.info("bounces: handled %d notice(s)", handled)
    print(f"bounces: {handled} processed.")
    return 0


def _apply_bounce(ctx, result: dsn.DsnResult, by_mid: dict, by_email: dict) -> None:
    store, cfg, logger = ctx.store, ctx.cfg, ctx.logger
    row = _match_row(result, by_mid, by_email)
    if row is None:
        logger.warning("bounce for unknown recipient %s (status %s)", result.final_recipient, result.status_code)
        return
    recipient = (result.final_recipient or row.get("to_email") or "").lower()
    domain = util.registrable_domain(util.domain_of(recipient))

    if result.kind == "hard":
        _record_bad(store, recipient, domain, row.get("company") or "", "hard bounce", result.status_code)
        store.queue_update(
            row["id"], status="needs_new_address", last_error=f"hard bounce {result.status_code}",
            notes=_append(row, f"hard bounce {result.status_code}"),
        )
        logger.info("hard bounce: %s (%s)", recipient, result.status_code)
        return

    if result.kind == "soft":
        attempts = int(row.get("attempts") or 1)
        if attempts < 3:
            slot = util.now_utc() + timedelta(hours=3)
            store.queue_update(
                row["id"], status="approved", send_after_utc=util.to_iso(slot),
                is_resend=1, attempts=attempts + 1,
                last_error=f"soft bounce {result.status_code} (retry {attempts})",
                notes=_append(row, f"soft bounce retry {attempts}"),
            )
            logger.info("soft bounce: %s -> retry %d", recipient, attempts)
        else:
            _record_bad(store, recipient, domain, row.get("company") or "", "soft bounce x2", result.status_code)
            store.queue_update(
                row["id"], status="needs_new_address",
                last_error=f"soft bounce exhausted {result.status_code}",
                notes=_append(row, "soft bounce exhausted"),
            )
        return

    # unknown status
    store.queue_update(
        row["id"], status="needs_new_address", last_error="unparsed bounce",
        notes=_append(row, "unparsed bounce"),
    )


def _record_bad(store, email: str, domain: str, company: str, reason: str, code: str) -> None:
    store.bad_add({
        "email": email, "domain": domain, "company": company,
        "reason": reason, "status_code": code, "added_at": util.to_iso(util.now_utc()),
    })


def _append(row: dict, note: str) -> str:
    existing = row.get("notes") or ""
    return (existing + " | " + note).strip(" |") if existing else note


def _handle_unparseable(ctx, pending_raw, by_mid, by_email) -> None:
    store, cfg, logger = ctx.store, ctx.cfg, ctx.logger
    server = srv.load_one(cfg, "classify", logger=logger)
    started = False
    try:
        started = server.start(timeout_s=int(cfg.raw.models.startup_timeout_s.raw))
        if not started:
            logger.warning("classify model unavailable; parsing bounce with heuristics")
        client = LLMClient(server.base_url, timeout_s=120, temperature=0.1, max_tokens=200, model=server.name) if started else None
        for mid, raw in pending_raw:
            result = None
            if client is not None:
                messages = add_no_think(prompts.bounce_messages(raw))
                try:
                    data = client.chat_json(messages, schema=prompts.BOUNCE_SCHEMA)
                    if data.get("is_bounce"):
                        result = dsn.DsnResult(
                            is_bounce=True,
                            kind=dsn._status_kind(data.get("status_code", "")),
                            final_recipient=(data.get("final_recipient") or "").lower(),
                            status_code=data.get("status_code", ""),
                            original_message_id=data.get("original_message_id", ""),
                            parseable=True,
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("bounce model parse failed: %s", exc)
            if result is not None:
                _apply_bounce(ctx, result, by_mid, by_email)
            if mid:
                processed.mark_processed(cfg, "bounces", mid)
    finally:
        if started:
            server.stop()
