"""End-to-end research pipeline: discover -> fit -> contacts -> draft -> queue."""

from __future__ import annotations

from .. import util
from ..draft.drafter import Drafter
from ..llm import server as srv
from ..llm.client import LLMClient
from ..llm.fallback import FallbackChain
from ..research.contacts import discover_contacts
from ..research.discovery import ROLE_TERMS, discover_candidates
from ..research.fetch import Fetcher
from ..research.providers import SearchManager
from ..research.verify import EmailVerifier
from ..schedule.slots import describe_local, next_send_slot
from ..schedule.tz import recipient_timezone

REGION_TZ = {
    "Latin America": "America/Sao_Paulo",
    "Europe (tier 2/3)": "Europe/Warsaw",
    "Europe (tier 1)": "Europe/Berlin",
    "Middle East": "Asia/Dubai",
    "China": "Asia/Shanghai",
    "Japan": "Asia/Tokyo",
    "Malaysia": "Asia/Kuala_Lumpur",
    "Singapore": "Asia/Singapore",
    "Indonesia": "Asia/Jakarta",
    "United States": "America/New_York",
}

CORP_NAMES = ("accenture", "deloitte", "infosys", "wipro", "tcs", "cognizant", "ibm",
              "microsoft", "meta", "google", "amazon", "nvidia", "sony", "samsung")


def _pick_contact(contacts: list) -> object | None:
    for cand in contacts:
        if cand.confidence == "invalid":
            continue
        return cand
    return None


def _target_today(store, cfg, today: str) -> int:
    quota = int(cfg.raw.quotas.companies_per_day.raw)
    done = int((store.summary_get(today) or {}).get("researched") or 0)
    return max(0, quota - done)


def run(ctx) -> dict:
    store, cfg, logger = ctx.store, ctx.cfg, ctx.logger
    today = ctx.today

    target = _target_today(store, cfg, today)
    if ctx.limit:
        target = min(target, ctx.limit)
    if target <= 0:
        print("research: daily quota already met.")
        return {"researched": 0, "drafted": 0, "skipped": 0}

    search_mgr = SearchManager(cfg, logger=logger)
    if not search_mgr.available:
        logger.error("research: no usable search provider (add API keys to .env)")
        print("research: no search provider configured.")
        return {"researched": 0, "drafted": 0, "skipped": 0, "error": "no_search_provider"}

    fetcher = Fetcher(cfg.path("cache_dir"), ttl_days=int(cfg.raw.search.cache_ttl_days.raw), logger=logger)
    verifier = EmailVerifier(
        api_key=cfg.env("EMAIL_VERIFY_API_KEY", ""),
        base_url=cfg.env("EMAIL_VERIFY_BASE_URL", ""),
        logger=logger,
        smtp_probe=cfg.env("EMAIL_VERIFY_SMTP", "").strip().lower() in ("1", "true", "yes", "on"),
    )

    exclude = store.contacted_domains() | store.bad_domains() | store.suppressed_domains()
    regions = [r.raw if hasattr(r, "raw") else r for r in cfg.raw.region_priority]
    candidates = discover_candidates(
        search_mgr, regions, roles=ROLE_TERMS, need=max(target * 3, target + 10),
        exclude_domains=exclude, fetcher=fetcher, logger=logger,
    )
    if not candidates:
        logger.warning("research: discovery returned no candidates")
        print("research: no candidates found.")
        return {"researched": 0, "drafted": 0, "skipped": 0}

    corp_fraction = float(cfg.raw.quotas.corporation_max_fraction.raw)
    corp_cap = max(1, int(target * corp_fraction))
    corp_count = 0

    server = srv.load_one(cfg, "draft", logger=logger)
    started = server.start(startup_timeout_s=int(cfg.raw.models.startup_timeout_s.raw))
    if not started:
        logger.error("research: draft model could not start")
        print("research: draft model unavailable.")
        return {"researched": 0, "drafted": 0, "skipped": 0, "error": "no_model"}

    fallback = FallbackChain(cfg, logger=logger) if cfg.raw.fallback_enabled.raw else None
    counts = {"researched": 0, "drafted": 0, "skipped": 0}
    try:
        client = LLMClient(
            server.base_url,
            timeout_s=int(cfg.raw.models.request_timeout_s.raw),
            temperature=float(cfg.raw.models.temperature.raw),
            max_tokens=int(cfg.raw.models.max_tokens.raw),
            model=server.name,
        )
        drafter = Drafter(cfg, logger=logger, fallback=fallback)
        profile = cfg.profile_raw()

        for cand in candidates:
            if counts["drafted"] >= target:
                break
            domain = util.registrable_domain(cand.domain)
            if domain in exclude:
                continue
            try:
                if _process_candidate(cand, domain, ctx, drafter, client, fetcher, verifier, profile,
                                      corp_cap, corp_count, counts, server):
                    if _is_corp(cand, profile):
                        corp_count += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("research: candidate %s failed: %s", domain, exc)
                counts["skipped"] += 1
                continue

        store.summary_bump(today, "researched", counts["researched"])
        store.summary_bump(today, "drafted", counts["drafted"])
        store.summary_bump(today, "search_calls", search_mgr.calls_today)
    finally:
        server.stop()

    logger.info("research done: %s", counts)
    print(f"research: drafted={counts['drafted']} skipped={counts['skipped']}")
    return counts


def _is_corp(cand, profile) -> bool:
    name = (cand.name or "").lower()
    return any(c in name for c in CORP_NAMES)


def _process_candidate(cand, domain, ctx, drafter, client, fetcher, verifier, profile,
                       corp_cap, corp_count, counts, server) -> bool:
    store, cfg, logger = ctx.store, ctx.cfg, ctx.logger
    if _is_corp(cand, profile) and corp_count >= corp_cap:
        logger.info("research: skipping corporation %s (cap reached)", domain)
        counts["skipped"] += 1
        return False

    website = cand.url or f"https://{domain}"
    page_text = fetcher.get_text(website) or ""
    if not page_text.strip():
        counts["skipped"] += 1
        return False

    fit = drafter.fit(client, cand.name or domain, domain, page_text)
    if not fit or not fit.get("is_xr_company"):
        logger.info("research: %s not an XR company", domain)
        counts["skipped"] += 1
        return False

    contacts = discover_contacts(domain, website, fetcher, verifier, logger=logger)
    contact = _pick_contact(contacts)
    if contact is None:
        logger.info("research: no contact found for %s", domain)
        counts["skipped"] += 1
        return False

    company_type = fit.get("company_type") or "studio"
    tags = fit.get("tags") or []
    contact_dict = {"name": contact.name, "role": contact.role, "tags": tags}

    draft, errors, used_fallback = drafter.generate(
        client, cand.name or domain, page_text, contact_dict, company_type,
        "fulltime", bool(cfg.raw.toggles.include_marketplace_links.raw),
    )
    if draft is None:
        logger.warning("research: draft failed for %s: %s", domain, errors[:3])
        if not ctx.dry_run:
            store.queue_add({
                "company": cand.name or domain, "website": website,
                "region_priority": cand.region, "status": "error",
                "to_email": contact.email, "last_error": "; ".join(errors[:3]),
            })
        counts["skipped"] += 1
        return False

    tz_name, ok = recipient_timezone(cand.region, "")
    if not ok:
        tz_name = REGION_TZ.get(cand.region, "UTC")
    slot = next_send_slot(
        util.now_utc(), tz_name,
        [int(h) for h in cfg.raw.sending.send_local_hours.raw],
        jitter_minutes=int(cfg.raw.sending.jitter_minutes.raw),
    )
    attachment = ""
    if cfg.raw.toggles.attach_cv.raw and not cfg.raw.toggles.link_cv_instead.raw:
        cv = profile.get("cv_pdf_path") or ""
        if cv and cv != "TODO":
            attachment = cv

    row = {
        "company": cand.name or domain,
        "website": website,
        "country": cand.region,
        "region_priority": cand.region,
        "company_type": company_type,
        "contact_name": contact.name,
        "contact_role": contact.role,
        "to_email": contact.email,
        "email_source": contact.source_url,
        "email_confidence": contact.confidence,
        "subject": draft.subject,
        "headline": draft.headline,
        "body": draft.body,
        "attachment": attachment,
        "evidence": draft.evidence_quote,
        "template_variant": draft.template_variant,
        "status": "pending_review",
        "send_after_utc": util.to_iso(slot),
        "local_send_time": describe_local(slot, tz_name),
        "recipient_tz": tz_name,
        "notes": "fallback draft" if used_fallback else "",
    }
    if ctx.dry_run:
        logger.info("research[dry-run]: would queue %s <%s>", cand.name, contact.email)
    else:
        store.queue_add(row)
    counts["researched"] += 1
    counts["drafted"] += 1
    logger.info("research: queued %s -> %s", domain, contact.email)
    return True
