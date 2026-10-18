"""`freehire draft`: queue an application heads-up email for your review.

For one watched job it finds the company site + a contact with the existing
research stack, builds "I have applied for {title}, here is my information and
my CV" from your profile, and queues the row as ``pending_review``. Nothing is
ever sent without your approval in outreach.xlsx.
"""

from __future__ import annotations

import re

from .. import util
from ..draft.validate import PLACEHOLDER_RE
from ..freehire import db as db_mod
from ..research.contacts import discover_contacts
from ..research.discovery import BLOCKED_DOMAINS, DIRECTORY_DOMAINS
from ..research.fetch import Fetcher
from ..research.providers import SearchManager
from ..research.verify import EmailVerifier

MARKER_PREFIX = "freehire:"
# Generic suffixes that must not decide whether a search hit "is" the company.
_STOP_TOKENS = {"ltd", "limited", "inc", "llc", "gmbh", "co", "corp", "plc",
                "the", "and", "srl", "bv", "pty", "ag", "sa", "kg", "ou"}


def run_draft(ctx, target: str | None) -> int:
    if not target:
        print("usage: python -m outreach freehire draft <job-slug>")
        return 1
    cfg, store, logger = ctx.cfg, ctx.store, ctx.logger
    profile = cfg.profile_raw()

    jobsdb = db_mod.JobsDB(cfg.path("cache_dir") / "freehire" / "jobs.db")
    try:
        job = jobsdb.get(target)
    finally:
        jobsdb.close()
    if job is None:
        print(f"unknown job id: {target} (run `freehire run`, then `freehire list`)")
        return 1

    marker = f"{MARKER_PREFIX}{target}"
    for row in store.queue_all():
        if marker in (row.get("notes") or ""):
            print(f"already drafted: queue row {row.get('id')} (status={row.get('status')})")
            return 0

    website, contact = _find_company_contact(ctx, job["company"] or "")
    to_email = contact.email if contact else ""
    subject = f"Application for {job['title']} at {job['company']}"
    body = _build_body(profile, job, contact)
    cv = ""
    if cfg.raw.toggles.attach_cv.raw and not cfg.raw.toggles.link_cv_instead.raw:
        value = profile.get("cv_pdf_path") or ""
        if value and value != "TODO":
            cv = value

    row = {
        "company": job["company"] or "",
        "website": website,
        "country": job.get("location") or "",
        "contact_name": contact.name if contact else "",
        "contact_role": contact.role if contact else "",
        "to_email": to_email,
        "email_source": contact.source_url if contact else "",
        "email_confidence": contact.confidence if contact else "",
        "subject": subject,
        "headline": job["title"] or "",
        "body": body,
        "attachment": cv,
        "evidence": job.get("url") or "",
        "template_variant": "freehire",
        "status": "pending_review",
        "notes": f"{marker} | {job.get('url') or ''}",
    }

    _warn_existing(store, to_email)
    leftover = PLACEHOLDER_RE.findall(body)
    if leftover:
        print(f"WARNING: placeholder text still in body: {sorted(set(leftover))} - edit before approving")

    if ctx.dry_run:
        print(f"[dry-run] would queue: {subject} -> {to_email or '(no contact found)'}")
        print(f"  website: {website or '(none)'}\n--- body ---\n{body}---")
        return 0
    if not to_email:
        print("WARNING: no contact email found - fill to_email by hand before approving")

    row_id = store.queue_add(row)
    store.summary_bump(ctx.today, "drafted")
    logger.info("freehire draft queued %s for %s (job %s)", row_id, job["company"], target)
    print(f"queued {row_id}: {subject}")
    print("review + approve it in outreach.xlsx (approve=y, status=approved); `send` does the rest.")
    return 0


def _build_body(profile: dict, job: dict, contact) -> str:
    first = util.first_name(contact.name) if contact else ""
    greeting = f"Hi {first}," if first else "Hi Hiring Team,"
    opener = (
        f"I have applied for the {job['title']} position at {job['company']} "
        f"through a job board and wanted to introduce myself directly as well, "
        f"so my application is easy to find on your side."
    )
    story = (profile.get("base_story") or "").strip()
    lines = story.splitlines()
    if lines and lines[0].strip().lower().startswith(("hi ", "hello ", "dear ")):
        story = "\n".join(lines[1:]).strip()
    # base_story references the generated [ROLE] headline; use the real title.
    story = story.replace("[ROLE]", job["title"] or "current").replace("[role]", job["title"] or "current")
    return f"{greeting}\n\n{opener}\n\n{story}\n"


def _find_company_contact(ctx, company: str) -> tuple[str, object | None]:
    cfg, logger = ctx.cfg, ctx.logger
    if not company:
        return "", None
    search = SearchManager(cfg, logger=logger)
    if not search.available:
        logger.info("freehire draft: no search provider; skipping contact discovery")
        return "", None

    fetcher = Fetcher(cfg.path("cache_dir"), ttl_days=int(cfg.raw.search.cache_ttl_days.raw), logger=logger)
    verifier = EmailVerifier(
        api_key=cfg.env("EMAIL_VERIFY_API_KEY", ""),
        base_url=cfg.env("EMAIL_VERIFY_BASE_URL", ""),
        logger=logger,
        smtp_probe=cfg.env("EMAIL_VERIFY_SMTP", "").strip().lower() in ("1", "true", "yes", "on"),
    )

    website = ""
    for res in search.search(f"{company} official website contact"):
        domain = util.registrable_domain(util.domain_of(res.url))
        if not domain or domain in BLOCKED_DOMAINS or domain in DIRECTORY_DOMAINS:
            continue
        if not _company_matches(company, res):
            continue
        website = res.url
        break
    if not website:
        logger.info("freehire draft: no official site found for %s", company)
        return "", None

    domain = util.registrable_domain(util.domain_of(website))
    contacts = discover_contacts(domain, website, fetcher, verifier, logger=logger)
    contact = next((c for c in contacts if c.confidence != "invalid"), None)
    return website, contact


def _company_matches(company: str, res) -> bool:
    """Gate search hits on the domain/URL so we never research the wrong site.

    Snippets are ignored on purpose: aggregator pages repeat the company name.
    """
    tokens = [t for t in re.split(r"[^a-z0-9]+", company.lower())
              if len(t) >= 3 and t not in _STOP_TOKENS]
    if not tokens:
        return True
    haystack = f"{util.domain_of(res.url or '')} {res.url or ''}".lower()
    return any(token in haystack for token in tokens)


def _warn_existing(store, to_email: str) -> None:
    if not to_email:
        return
    domain = util.registrable_domain(util.domain_of(to_email))
    if domain in store.contacted_domains():
        print(f"WARNING: {domain} is already in your Contacted list - send will block it as duplicate")
    if to_email.lower() in store.bad_emails() or to_email.lower() in store.suppressed_emails():
        print(f"WARNING: {to_email} is in BadAddresses/Suppressed - it will not be sent")
