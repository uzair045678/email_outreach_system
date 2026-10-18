"""Search providers (Tavily / Brave / Serper) with rotation, cache and budget.

30-40 companies/day with contact discovery can need 150+ calls, so every query
is cached and the daily call budget is tracked and enforced.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path

import requests

from .. import util

PROVIDER_KEY_ENV = {
    "tavily": "TAVILY_API_KEY",
    "brave": "BRAVE_API_KEY",
    "serper": "SERPER_API_KEY",
}


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str = ""
    provider: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class SearchProvider:
    name = "base"

    def __init__(self, api_key: str, logger=None):
        self.api_key = api_key
        self.logger = logger
        self.session = requests.Session()

    def search(self, query: str, count: int) -> list[SearchResult]:  # pragma: no cover
        raise NotImplementedError


class TavilyProvider(SearchProvider):
    name = "tavily"
    url = "https://api.tavily.com/search"

    def search(self, query: str, count: int) -> list[SearchResult]:
        resp = self.session.post(
            self.url,
            json={
                "api_key": self.api_key,
                "query": query,
                "max_results": count,
                "search_depth": "basic",
                "include_answer": False,
            },
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return [
            SearchResult(r.get("title", ""), r.get("url", ""), r.get("content", ""), self.name)
            for r in data.get("results", [])
            if r.get("url")
        ]


class BraveProvider(SearchProvider):
    name = "brave"
    url = "https://api.search.brave.com/res/v1/web/search"

    def search(self, query: str, count: int) -> list[SearchResult]:
        resp = self.session.get(
            self.url,
            params={"q": query, "count": count},
            headers={"Accept": "application/json", "X-Subscription-Token": self.api_key},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        items = data.get("web", {}).get("results", [])
        return [
            SearchResult(r.get("title", ""), r.get("url", ""), r.get("description", ""), self.name)
            for r in items
            if r.get("url")
        ]


class SerperProvider(SearchProvider):
    name = "serper"
    url = "https://google.serper.dev/search"

    def search(self, query: str, count: int) -> list[SearchResult]:
        resp = self.session.post(
            self.url,
            json={"q": query, "num": count},
            headers={"X-API-KEY": self.api_key, "Content-Type": "application/json"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        out = []
        for r in data.get("organic", []):
            if r.get("link"):
                out.append(SearchResult(r.get("title", ""), r.get("link", ""), r.get("snippet", ""), self.name))
        return out


PROVIDERS = {
    "tavily": TavilyProvider,
    "brave": BraveProvider,
    "serper": SerperProvider,
}


@dataclass
class SearchBudget:
    calls: int = 0
    warned: bool = False


class SearchManager:
    def __init__(self, cfg, logger=None):
        self.cfg = cfg
        self.logger = logger
        self.cache_dir = cfg.path("cache_dir") / "search"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl_days = int(cfg.raw.search.cache_ttl_days.raw)
        self.warn_at = int(cfg.raw.search.calls_per_day_warn.raw)
        self.max_calls = int(cfg.raw.search.calls_per_day_max.raw)
        self.results_per_query = int(cfg.raw.search.results_per_query.raw)

        self.providers: list[SearchProvider] = []
        for name in cfg.raw.search.providers:
            n = name.raw if hasattr(name, "raw") else name
            env = PROVIDER_KEY_ENV.get(n)
            key = cfg.env(env) if env else ""
            if key:
                self.providers.append(PROVIDERS[n](key, logger=logger))
            elif logger:
                logger.info("search provider %s disabled (no %s)", n, env)
        self._rotation = 0
        self.budget = self._load_budget()

    # -- budget ------------------------------------------------------------
    @property
    def _budget_path(self) -> Path:
        return self.cache_dir / f"calls-{util.now_utc().strftime('%Y-%m-%d')}.json"

    def _load_budget(self) -> SearchBudget:
        path = self._budget_path
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return SearchBudget(calls=int(data.get("calls", 0)), warned=bool(data.get("warned")))
            except (OSError, json.JSONDecodeError):
                pass
        return SearchBudget()

    def _save_budget(self) -> None:
        try:
            self._budget_path.write_text(
                json.dumps({"calls": self.budget.calls, "warned": self.budget.warned}),
                encoding="utf-8",
            )
        except OSError:
            pass

    def _record_call(self) -> None:
        self.budget.calls += 1
        if not self.budget.warned and self.budget.calls >= self.warn_at and self.logger:
            self.logger.warning(
                "search call budget: %d calls today (warn threshold %d)",
                self.budget.calls, self.warn_at,
            )
            self.budget.warned = True
        self._save_budget()

    @property
    def calls_today(self) -> int:
        return self.budget.calls

    @property
    def available(self) -> bool:
        return bool(self.providers) and self.budget.calls < self.max_calls

    # -- cache -------------------------------------------------------------
    def _cache_path(self, query: str) -> Path:
        return self.cache_dir / f"{util.sha256(query.lower())}.json"

    def _read_cache(self, query: str) -> list[SearchResult] | None:
        path = self._cache_path(query)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        age = (util.now_utc().timestamp() - data.get("ts", 0)) / 86400
        if age > self.ttl_days:
            return None
        return [SearchResult(**r) for r in data.get("results", [])]

    def _write_cache(self, query: str, results: list[SearchResult]) -> None:
        try:
            self._cache_path(query).write_text(
                json.dumps({"ts": util.now_utc().timestamp(), "results": [r.to_dict() for r in results]}),
                encoding="utf-8",
            )
        except OSError:
            pass

    # -- search ------------------------------------------------------------
    def search(self, query: str, count: int | None = None) -> list[SearchResult]:
        count = count or self.results_per_query
        cached = self._read_cache(query)
        if cached is not None:
            return cached
        if not self.providers:
            if self.logger:
                self.logger.warning("no search provider configured; cannot search: %s", query)
            return []
        if self.budget.calls >= self.max_calls:
            if self.logger:
                self.logger.error("search daily max reached (%d); skipping '%s'", self.max_calls, query)
            return []

        n = len(self.providers)
        for attempt in range(n):
            provider = self.providers[(self._rotation + attempt) % n]
            try:
                results = provider.search(query, count) or []
                self._record_call()
                self._write_cache(query, results)
                self._rotation = (self._rotation + attempt + 1) % n
                if self.logger:
                    self.logger.info("search[%s] '%s' -> %d results", provider.name, query, len(results))
                return results
            except Exception as exc:  # noqa: BLE001
                if self.logger:
                    self.logger.warning("search[%s] failed for '%s': %s", provider.name, query, exc)
                continue
        return []
