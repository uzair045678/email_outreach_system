"""Hard validation rules for a generated draft.

A draft only survives if every rule passes; otherwise the caller retries with
the local model, then the fallback chain, then marks the row as error.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .. import util

PLACEHOLDER_RE = re.compile(
    r"(\[[A-Za-z][^\]]{0,40}\]|\{[A-Za-z_][^}]{0,40}\}|<[A-Za-z][^>]{0,40}>|\bTODO\b|\bXXX\b)",
    re.IGNORECASE,
)
URL_RE = re.compile(r"https?://[^\s<>()\"']+")
YEARS_RE = re.compile(r"\b(\d{1,2})\+?\s*(?:years|yrs)\b", re.IGNORECASE)
EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF\u2190-\u21FF\u2B00-\u2BFF]"
)
ALLCAPS_RE = re.compile(r"\b[A-Z]{3,}\b")

OPT_OUT_MARKERS = ("won't write again", "will not write again", "won't email again", "won't contact again")


@dataclass
class Draft:
    subject: str
    body: str
    evidence_quote: str = ""
    evidence_url: str = ""
    template_variant: str = ""
    variant: str = ""
    target_kind: str = "fulltime"
    headline: str = ""

    @property
    def as_row(self) -> dict:
        return {
            "subject": self.subject,
            "headline": self.headline,
            "body": self.body,
            "evidence": self.evidence_quote,
            "template_variant": self.template_variant or self.variant,
        }


def normalize_url(url: str) -> str:
    url = (url or "").strip()
    return url[:-1] if url.endswith("/") else url


def extract_urls(text: str) -> list[str]:
    return [normalize_url(u.rstrip(".,;)")) for u in URL_RE.findall(text or "")]


def collect_allowed_urls(profile: dict) -> set[str]:
    urls = set()
    for key in ("github_url", "linkedin_url", "portfolio_url", "fiverr_url", "upwork_url", "cv_link_url"):
        value = profile.get(key)
        if value and value != "TODO":
            urls.add(normalize_url(value))
    for proj in profile.get("projects") or []:
        if isinstance(proj, dict) and proj.get("url"):
            urls.add(normalize_url(proj["url"]))
    return urls


def collect_profile_terms(profile: dict) -> set[str]:
    terms = set()
    skills = profile.get("skills") or {}
    for group in ("engines", "xr_devices", "languages", "other"):
        for term in skills.get(group) or []:
            terms.add(str(term).strip().lower())
    return terms


def project_urls(profile: dict) -> set[str]:
    return {normalize_url(p["url"]) for p in (profile.get("projects") or []) if isinstance(p, dict) and p.get("url")}


def _greeting_ok(body: str, contact_name: str, company: str) -> bool:
    stripped = (body or "").lstrip()
    if not stripped:
        return False
    first_line = stripped.splitlines()[0].strip().lower()
    if contact_name:
        expected = f"hi {util.first_name(contact_name).lower()}"
        return first_line.startswith(expected)
    return first_line.startswith(f"hi team at {company.lower()}")


def validate_draft(
    draft: Draft,
    profile: dict,
    page_text: str,
    cfg,
    contact_name: str,
    company: str,
    require_signature_links: bool = True,
) -> list[str]:
    errors: list[str] = []
    body = draft.body or ""
    subject = draft.subject or ""

    min_words = int(cfg.raw.draft.min_body_words.raw)
    max_words = int(cfg.raw.draft.max_body_words.raw)
    max_subj = int(cfg.raw.draft.max_subject_len.raw)

    # 1. No placeholders.
    for m in PLACEHOLDER_RE.findall(body):
        if m.lower() in ("[and]", "{n}"):  # allow literal-ish
            continue
        errors.append(f"placeholder text in body: {m!r}")
    if PLACEHOLDER_RE.search(subject):
        errors.append("placeholder text in subject")

    # 2. Lengths.
    wc = util.word_count(body)
    if wc < min_words:
        errors.append(f"body too short ({wc} < {min_words} words)")
    if wc > max_words:
        errors.append(f"body too long ({wc} > {max_words} words)")
    if not subject:
        errors.append("subject empty")
    if len(subject) > max_subj:
        errors.append(f"subject too long ({len(subject)} > {max_subj})")
    for caps in ALLCAPS_RE.findall(subject):
        if caps not in ("AR", "VR", "XR", "MR", "CV", "AI"):
            errors.append(f"all-caps word in subject: {caps}")

    # 2b. Headline light checks.
    headline = (draft.headline or "").strip()
    if headline:
        if PLACEHOLDER_RE.search(headline):
            errors.append("placeholder text in headline")
        if "\n" in headline:
            errors.append("headline must be a single line")
        if len(headline) > 120:
            errors.append(f"headline too long ({len(headline)} > 120 chars)")
        if EMOJI_RE.search(headline):
            errors.append("headline contains emoji")
        for caps in ALLCAPS_RE.findall(headline):
            if caps not in ("AR", "VR", "XR", "MR", "CV", "AI", "UNITY", "UNREAL", "3D"):
                errors.append(f"all-caps word in headline: {caps}")

    # 3. Emojis.
    if EMOJI_RE.search(body):
        errors.append("body contains emoji")

    # 4. URLs must be my own.
    allowed = collect_allowed_urls(profile)
    used_urls = extract_urls(body)
    for url in used_urls:
        if url not in allowed:
            errors.append(f"disallowed link in body: {url}")
    for url in project_urls(profile):
        pass  # any project link used is fine; it's in allowed

    # 5. Signature links present.
    if require_signature_links:
        for key in ("github_url", "linkedin_url", "portfolio_url"):
            value = profile.get(key)
            if not value or value == "TODO":
                continue
            if normalize_url(value) not in used_urls:
                errors.append(f"missing signature link: {key}")

    # 6. Evidence must be an exact substring of the page.
    if not draft.evidence_quote or draft.evidence_quote.strip() not in (page_text or ""):
        errors.append("evidence_quote is not an exact substring of the fetched page")

    # 7. Greeting.
    if not _greeting_ok(body, contact_name, company):
        if contact_name:
            errors.append(f"greeting does not address the contact ({contact_name})")
        else:
            errors.append(f"greeting does not address the team ({company})")

    # 8. No unsupported claims about me.
    terms = collect_profile_terms(profile)
    for match in YEARS_RE.findall(body):
        try:
            n = int(match)
        except ValueError:
            continue
        allowed_years = profile.get("years_experience")
        if isinstance(allowed_years, int) and n > allowed_years:
            errors.append(f"claims {n} years experience (profile says {allowed_years})")
        elif not isinstance(allowed_years, int) and allowed_years not in (None, ""):
            # years_experience is TODO; any explicit claim is unbacked
            errors.append(f"claims {n} years experience not present in profile")

    # 9. Opt-out line.
    if not any(marker in body.lower() for marker in OPT_OUT_MARKERS):
        errors.append("missing opt-out line")

    return errors
