"""Contact discovery from a company's own pages.

Preference order: founder/CEO, hiring/recruiter, CTO/technical director,
creative/producer lead, any person, then a generic address.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from .fetch import CONTACT_PATHS, Fetcher, normalize_url
from .verify import email_type

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

ROLE_KEYWORDS = [
    ("founder", 0), ("co-founder", 0), ("cofounder", 0), ("ceo", 0), ("owner", 0), ("president", 0),
    ("hiring", 1), ("recruiter", 1), ("talent", 1), ("people", 1), ("hr", 1),
    ("cto", 2), ("technical director", 2), ("tech lead", 2), ("lead developer", 2), ("head of engineering", 2),
    ("creative", 3), ("producer", 3), ("art director", 3), ("design lead", 3), ("studio manager", 3),
    ("director", 4), ("manager", 4), ("lead", 4), ("developer", 5), ("engineer", 5), ("artist", 5),
]

BAD_LOCAL_PARTS = {"example", "email", "your", "name", "domain", "user", "test"}
ASSET_HINTS = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js")


@dataclass
class ContactCandidate:
    email: str
    source_url: str
    kind: str = "generic"          # personal | role | generic | pattern
    rank: int = 99
    name: str = ""
    role: str = ""
    context: str = ""
    confidence: str = "low"
    detail: str = ""

    def to_row(self, company_domain: str) -> dict:
        return {
            "email": self.email,
            "source_url": self.source_url,
            "kind": self.kind,
            "rank": self.rank,
            "name": self.name,
            "role": self.role,
            "confidence": self.confidence,
            "detail": self.detail,
        }


def _clean_emails(text: str, domain: str) -> list[str]:
    found: list[str] = []
    for raw in EMAIL_RE.findall(text or ""):
        email = raw.strip().strip(".,;:<>()[]").lower()
        if any(email.endswith(h) for h in ASSET_HINTS):
            continue
        local = email.split("@", 1)[0]
        if local in BAD_LOCAL_PARTS:
            continue
        if domain and not email.endswith(domain) and not email.endswith("@" + domain):
            # keep it anyway; some studios use a sibling domain
            pass
        found.append(email)
    return found


def _score_local(local: str) -> tuple[str, int, str]:
    """Return (kind, rank, matched_role)."""
    base = re.split(r"[._\-+0-9]", local)[0].lower()
    for keyword, rank in ROLE_KEYWORDS:
        if base == keyword.replace(" ", "") or base.startswith(keyword.replace(" ", "")):
            return "personal", rank, keyword
    kind = email_type(f"{local}@x.com")
    if kind == "generic":
        return "generic", 6, ""
    return "personal", 5, ""


def _role_rank(role_text: str) -> int | None:
    role_text = (role_text or "").lower()
    best: int | None = None
    for keyword, rank in ROLE_KEYWORDS:
        if keyword in role_text:
            if best is None or rank < best:
                best = rank
    return best


def _find_context(text: str, email: str, window: int = 160) -> tuple[str, str, str]:
    idx = text.lower().find(email.lower())
    if idx < 0:
        return "", "", ""
    chunk = text[max(0, idx - window): idx + window]
    # Try to find "Name, Role" near the email.
    name = ""
    role = ""
    m = re.search(r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,2})\s*[,|\-–]\s*([A-Za-z /&]+)", chunk)
    if m:
        name = m.group(1)
        role = m.group(2).strip()
    else:
        for keyword, _ in ROLE_KEYWORDS:
            if keyword in chunk.lower():
                role = keyword
                break
    return name, role, " ".join(chunk.split())


def find_contact_pages(base_url: str, fetcher: Fetcher) -> list[str]:
    parsed = urlparse(base_url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    pages = [base_url]
    html = fetcher.get_html(base_url)
    if html:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"].lower()
            text = (a.get_text() or "").lower()
            if any(k in href or k in text for k in ("contact", "about", "team", "career", "jobs", "company")):
                url = normalize_url(root, a["href"])
                if url and url not in pages:
                    pages.append(url)
    for path in CONTACT_PATHS:
        url = urljoin(root + "/", path.lstrip("/"))
        if url not in pages:
            pages.append(url)
    return pages[:8]


def discover_contacts(company_domain: str, website: str, fetcher: Fetcher, verifier, logger=None) -> list[ContactCandidate]:
    if not website:
        website = f"https://{company_domain}"
    pages = find_contact_pages(website, fetcher)
    candidates: dict[str, ContactCandidate] = {}

    for page in pages:
        html = fetcher.get_html(page)
        text = fetcher.get_text(page) if html else ""
        if not text and not html:
            continue
        blob = (text or "") + " " + " ".join(
            re.findall(r"mailto:([^\"'<> ]+)", html, flags=re.IGNORECASE)
        )
        for email in _clean_emails(blob, company_domain):
            if email in candidates:
                continue
            kind, rank, role_kw = _score_local(email.split("@", 1)[0])
            name, role, ctx = _find_context(text or "", email)
            context_rank = _role_rank(role)
            if context_rank is not None:
                rank = min(rank, context_rank)
                kind = "personal" if rank <= 5 else kind
            candidates[email] = ContactCandidate(
                email=email, source_url=page, kind=kind, rank=rank,
                name=name, role=role or role_kw, context=ctx,
            )

    # Verify each candidate.
    results: list[ContactCandidate] = []
    for cand in candidates.values():
        conf, detail = verifier.verify(cand.email, found_on_site=True)
        cand.confidence = conf
        cand.detail = detail
        if conf == "low" and detail.startswith("mx:"):
            continue  # dead domain, skip
        results.append(cand)

    results.sort(key=lambda c: (c.rank, {"high": 0, "medium": 1, "low": 2}.get(c.confidence, 3)))
    if logger:
        logger.info("discover_contacts %s -> %d candidate(s)", company_domain, len(results))
    return results
