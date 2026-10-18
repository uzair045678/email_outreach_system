"""Candidate company discovery via search queries per region/role."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from .. import util
from .fetch import html_to_text

ROLE_TERMS = [
    "Unity developer",
    "Unreal developer",
    "AR developer",
    "VR developer",
    "XR developer",
    "AR/VR studio",
    "mixed reality developer",
]

REGION_QUERIES = {
    "Latin America": [
        "XR studio {role} Brazil",
        "realidad aumentada estudio {role} Mexico",
        "Clutch AR VR developers Argentina",
        "Unity AR agency Colombia",
        "VR company Chile {role}",
    ],
    "Europe (tier 2/3)": [
        "XR studio {role} Poland",
        "VR studio {role} Portugal",
        "AR agency {role} Czech Republic",
        "Unreal VR studio Romania",
        "XR company {role} Greece",
    ],
    "Middle East": [
        "XR studio {role} Dubai",
        "VR company {role} Saudi Arabia",
        "AR agency {role} UAE",
        "Unity VR studio Israel",
        "XR startup {role} Turkey",
    ],
    "China": ["XR studio {role} Shenzhen", "VR company {role} Shanghai", "Unity AR team {role} Beijing"],
    "Japan": ["VR studio {role} Tokyo", "XR company {role} Osaka", "Unity AR studio Japan"],
    "Malaysia": ["Unity AR agency Kuala Lumpur", "XR studio {role} Malaysia"],
    "Singapore": ["XR studio Singapore", "VR company {role} Singapore", "AR agency Singapore"],
    "Indonesia": ["XR studio {role} Jakarta", "VR company Indonesia {role}"],
    "United States": ["AR VR studio {role} USA", "Unity XR company {role} United States"],
    "Europe (tier 1)": ["XR studio {role} Germany", "VR studio {role} United Kingdom", "AR agency {role} France"],
}

DIRECTORY_DOMAINS = {
    "clutch.co", "designrush.com", "goodfirms.co", "sortlist.com", "themanifest.com",
    "behance.net", "dribbble.com", "crunchbase.com", "linkedin.com", "f6s.com",
    "techbehemoths.com", "topdevelopers.co", "businessofapps.com", "startupblink.com",
}

BLOCKED_DOMAINS = {
    "google.com", "youtube.com", "facebook.com", "instagram.com", "twitter.com", "x.com",
    "reddit.com", "wikipedia.org", "medium.com", "quora.com", "pinterest.com",
    "indeed.com", "glassdoor.com", "upwork.com", "fiverr.com", "freelancer.com",
    "guru.com", "toptal.com", "ycombinator.com", "apple.com", "microsoft.com",
    "meta.com", "amazon.com", "g2.com", "capterra.com", "trustpilot.com",
}


@dataclass
class CompanyCandidate:
    domain: str
    url: str
    name: str
    region: str
    query: str
    snippet: str = ""
    source_kind: str = "direct"   # direct | directory

    def key(self) -> str:
        return util.registrable_domain(self.domain)


def _domain(url: str) -> str:
    return util.registrable_domain(util.domain_of(url))


def build_queries(region: str, roles: list[str], per_region: int = 2) -> list[str]:
    templates = REGION_QUERIES.get(region, ["XR studio {role}"])
    queries = []
    for i in range(per_region):
        template = templates[i % len(templates)]
        role = roles[i % len(roles)] if roles else "Unity developer"
        queries.append(template.format(role=role))
    return queries


def _extract_directory_companies(html: str, base_url: str, region: str, query: str, fetcher, logger) -> list[CompanyCandidate]:
    from bs4 import BeautifulSoup

    out: list[CompanyCandidate] = []
    if not html:
        return out
    soup = BeautifulSoup(html, "html.parser")
    root = f"{urlparse(base_url).scheme}://{urlparse(base_url).netloc}"
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href.startswith("http"):
            continue
        d = _domain(href)
        if not d or d in DIRECTORY_DOMAINS or d in BLOCKED_DOMAINS:
            continue
        if d == _domain(base_url):
            continue
        title = " ".join((a.get_text() or "").split())[:80]
        out.append(CompanyCandidate(d, href, title, region, query, source_kind="directory"))
    return out


def discover_candidates(
    search_mgr,
    regions: list[str],
    roles: list[str] | None = None,
    need: int = 35,
    exclude_domains: set[str] | None = None,
    fetcher=None,
    logger=None,
) -> list[CompanyCandidate]:
    roles = roles or ROLE_TERMS
    exclude = {util.registrable_domain(d) for d in (exclude_domains or set())}
    seen: set[str] = set()
    out: list[CompanyCandidate] = []

    for region in regions:
        if len(out) >= need:
            break
        for query in build_queries(region, roles):
            if len(out) >= need:
                break
            for res in search_mgr.search(query):
                d = _domain(res.url)
                if not d or d in seen or d in exclude:
                    continue
                if d in BLOCKED_DOMAINS:
                    continue
                if d in DIRECTORY_DOMAINS:
                    if fetcher is not None:
                        html = fetcher.get_html(res.url)
                        for cand in _extract_directory_companies(html, res.url, region, query, fetcher, logger):
                            if cand.key() in seen or cand.key() in exclude:
                                continue
                            seen.add(cand.key())
                            out.append(cand)
                            if len(out) >= need:
                                break
                    continue
                seen.add(d)
                out.append(CompanyCandidate(d, res.url, res.title, region, query, snippet=res.snippet, source_kind="direct"))
                if len(out) >= need:
                    break
    if logger:
        logger.info("discover_candidates -> %d candidate(s) (need %d)", len(out), need)
    return out[:need]
