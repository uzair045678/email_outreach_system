"""Web fetching with an on-disk HTML cache."""

from __future__ import annotations

import json
from pathlib import Path

import requests

from .. import util

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 outreach/0.1"
)

CONTACT_PATHS = [
    "/contact",
    "/contact-us",
    "/about",
    "/about-us",
    "/team",
    "/our-team",
    "/careers",
    "/jobs",
    "/company",
]


class Fetcher:
    def __init__(self, cache_dir: str | Path, ttl_days: int = 30, logger=None):
        self.cache_dir = Path(cache_dir)
        self.ttl_days = ttl_days
        self.logger = logger
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_path(self, url: str) -> Path:
        return self.cache_dir / f"{util.sha256(url)}.json"

    def _read_cache(self, url: str) -> str | None:
        path = self._cache_path(url)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        age_days = (util.now_utc().timestamp() - data.get("ts", 0)) / 86400
        if age_days > self.ttl_days:
            return None
        return data.get("html")

    def _write_cache(self, url: str, html: str) -> None:
        try:
            self._cache_path(url).write_text(
                json.dumps({"ts": util.now_utc().timestamp(), "url": url, "html": html}),
                encoding="utf-8",
            )
        except OSError:
            pass

    def get_html(self, url: str, timeout: int = 20) -> str:
        cached = self._read_cache(url)
        if cached is not None:
            return cached
        try:
            resp = self.session.get(url, timeout=timeout, allow_redirects=True)
            if resp.status_code >= 400:
                return ""
            html = resp.text
        except requests.RequestException as exc:
            if self.logger:
                self.logger.debug("fetch failed %s: %s", url, exc)
            return ""
        self._write_cache(url, html)
        return html

    def get_text(self, url: str) -> str:
        html = self.get_html(url)
        if not html:
            return ""
        return html_to_text(html)


def html_to_text(html: str) -> str:
    if not html:
        return ""
    try:
        import trafilatura

        text = trafilatura.extract(html, include_links=False, include_comments=False)
        if text:
            return text
    except Exception:  # noqa: BLE001
        pass
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        return " ".join(soup.get_text(" ").split())
    except Exception:  # noqa: BLE001
        return ""


def normalize_url(base: str, href: str) -> str:
    from urllib.parse import urljoin, urlparse

    if not href:
        return ""
    if href.startswith(("mailto:", "tel:", "javascript:")):
        return ""
    joined = urljoin(base, href)
    parsed = urlparse(joined)
    if parsed.scheme not in ("http", "https"):
        return ""
    return joined.split("#", 1)[0]
