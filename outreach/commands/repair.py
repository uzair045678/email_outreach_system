"""`repair`: after a hard bounce, try the next best contact at the same company."""

from __future__ import annotations

from .. import util
from ..draft.validate import Draft, validate_draft
from ..research.contacts import discover_contacts
from ..research.fetch import Fetcher
from ..research.verify import EmailVerifier

GOOD_STATUSES = ("needs_new_address", "gave_up")


def _split_addresses(value: str) -> list[str]:
    return [a.strip().lower() for a in (value or "").split(";") if a.strip()]


def _rebuild_greeting(body: str, contact_name: str, company: str) -> str:
    lines = (body or "").splitlines()
    for i, line in enumerate(lines):
        if line.strip():
            if contact_name:
                lines[i] = f"Hi {util.first_name(contact_name)},"
            else:
                lines[i] = f"Hi team at {company},"
            break
    return "\n".join(lines)


def run(ctx) -> int:
    store, cfg, logger = ctx.store, ctx.cfg, ctx.logger
    max_attempts = int(cfg.raw.followup.max_new_address_attempts.raw)
    auto_approve = bool(cfg.raw.toggles.auto_approve_resends.raw)
    profile = cfg.profile_raw()

    fetcher = Fetcher(cfg.path("cache_dir"), ttl_days=int(cfg.raw.search.cache_ttl_days.raw), logger=logger)
    verifier = EmailVerifier(
        api_key=cfg.env("EMAIL_VERIFY_API_KEY", ""),
        base_url=cfg.env("EMAIL_VERIFY_BASE_URL", ""),
        logger=logger,
        smtp_probe=cfg.env("EMAIL_VERIFY_SMTP", "").strip().lower() in ("1", "true", "yes", "on"),
    )

    tried_globally = store.bad_emails() | store.suppressed_emails() | store.contacted_emails()
    rows = store.queue_by_status(*GOOD_STATUSES)
    if ctx.limit:
        rows = rows[: ctx.limit]

    repaired = 0
    for row in rows:
        company = row.get("company") or ""
        website = row.get("website") or ""
        old_email = (row.get("to_email") or "").lower()
        tried = util.dedupe_keep_order([a for a in _split_addresses(row.get("addresses_tried")) + [old_email] if a])
        if len(tried) >= max_attempts:
            if row.get("status") != "gave_up":
                store.queue_update(row["id"], status="gave_up", notes=_append(row, "no more candidate addresses"))
            continue

        domain = util.registrable_domain(util.domain_of(old_email or website))
        if not domain:
            store.queue_update(row["id"], status="gave_up", notes=_append(row, "no domain to search"))
            continue

        candidates = discover_contacts(domain, website or f"https://{domain}", fetcher, verifier, logger=logger)
        exclude = set(tried) | tried_globally
        pick = None
        for cand in candidates:
            if cand.email in exclude:
                continue
            if cand.rank > 8:
                continue
            pick = cand
            break

        if pick is None:
            logger.info("repair: no new candidate for %s", domain)
            continue

        new_body = _rebuild_greeting(row.get("body") or "", pick.name or "", company)
        draft = Draft(
            subject=row.get("subject") or "",
            body=new_body,
            evidence_quote=row.get("evidence") or "",
            evidence_url="",
            template_variant=row.get("template_variant") or "",
        )
        page_text = fetcher.get_text(website or f"https://{domain}") or ""
        errors = validate_draft(
            draft, profile, (page_text + "\n" + (row.get("evidence") or "")),
            cfg, pick.name or "", company,
        )
        if errors:
            logger.warning("repair: revalidation failed for %s: %s", pick.email, errors)
            store.queue_update(row["id"], notes=_append(row, "repair revalidation failed"))
            continue

        tried_value = ";".join([a for a in tried if a] + [pick.email])
        store.queue_update(
            row["id"],
            to_email=pick.email,
            contact_name=pick.name or "",
            contact_role=pick.role or row.get("contact_role") or "",
            email_source=pick.source_url or "",
            email_confidence=pick.confidence or row.get("email_confidence") or "",
            body=new_body,
            addresses_tried=tried_value,
            is_resend=1,
            status="approved" if auto_approve else "pending_review",
            approve="yes" if auto_approve else "",
            draft_hash="",
            last_error="",
            notes=_append(row, f"repaired -> {pick.email}"),
        )
        tried_globally.add(pick.email)
        repaired += 1
        logger.info("repair: %s -> %s", old_email, pick.email)

    print(f"repair: {repaired} row(s) updated with a new candidate.")
    return 0


def _append(row: dict, note: str) -> str:
    existing = row.get("notes") or ""
    return (existing + " | " + note).strip(" |") if existing else note
