"""Keyless public freehire.me API client.

Sequential GETs only, a timeout on every call, and a pause between requests.
Unknown query params come back in ``meta.ignored_params`` so the caller can
verify that no configured filter was silently dropped.
"""

from __future__ import annotations

import time
from typing import Any

import requests


class FreehireAPIError(RuntimeError):
    pass


class FreehireAPI:
    def __init__(
        self,
        base_url: str = "https://freehire.me/api/v1",
        timeout: float = 20.0,
        delay_s: float = 1.0,
        logger=None,
        session: requests.Session | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.delay_s = delay_s
        self.logger = logger
        self.session = session or requests.Session()

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict:
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            resp = self.session.get(url, params=params, timeout=self.timeout)
        except requests.RequestException as exc:
            raise FreehireAPIError(f"GET {url} failed: {exc}") from exc
        if resp.status_code == 404:
            return {}
        if resp.status_code >= 400:
            raise FreehireAPIError(f"GET {url} -> HTTP {resp.status_code}")
        if self.delay_s:
            time.sleep(self.delay_s)
        try:
            return resp.json()
        except ValueError as exc:
            raise FreehireAPIError(f"GET {url} returned invalid JSON") from exc

    def search(
        self,
        q: str,
        q_fields: str = "title",
        filters: dict[str, Any] | None = None,
        limit: int = 20,
    ) -> tuple[list[dict], dict]:
        """Run one search. Returns (jobs, meta); meta may list ignored_params."""
        params: dict[str, Any] = {"q": q, "sort": "created_at", "limit": limit}
        if q_fields:
            params["q_fields"] = q_fields
        for key, value in (filters or {}).items():
            if value not in (None, ""):
                params[key] = value
        payload = self._get("jobs/search", params)
        jobs = payload.get("data") or []
        meta = payload.get("meta") or {}
        return jobs, meta

    def get_job(self, slug: str) -> dict | None:
        """Job detail, or None when the posting is gone (404)."""
        payload = self._get(f"jobs/{slug}")
        if not payload:
            return None
        job = payload.get("data") or payload
        return job if isinstance(job, dict) else None
