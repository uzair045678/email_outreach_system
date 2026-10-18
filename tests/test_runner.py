from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from outreach import util
from outreach.context import Context
from outreach.research import runner
from outreach.research.discovery import CompanyCandidate
from outreach.store import open_store


class DummyCfg:
    def __init__(self, base: Path):
        self.project_dir = base
        self._paths = {
            "workbook": str(base / "outreach.xlsx"),
            "backup_dir": str(base / "backups"),
            "pending_updates": str(base / "pending_updates.jsonl"),
            "cache_dir": str(base / "cache"),
        }
        self.raw = SimpleNamespace(
            quotas=SimpleNamespace(
                companies_per_day=SimpleNamespace(raw=3),
                corporation_max_fraction=SimpleNamespace(raw=0.10),
            ),
            search=SimpleNamespace(
                cache_ttl_days=SimpleNamespace(raw=30),
                providers=[],
            ),
            sending=SimpleNamespace(
                send_local_hours=SimpleNamespace(raw=[9, 10, 14]),
                jitter_minutes=SimpleNamespace(raw=17),
            ),
            models=SimpleNamespace(
                startup_timeout_s=SimpleNamespace(raw=5),
                request_timeout_s=SimpleNamespace(raw=10),
                temperature=SimpleNamespace(raw=0.5),
                max_tokens=SimpleNamespace(raw=700),
            ),
            toggles=SimpleNamespace(
                include_marketplace_links=SimpleNamespace(raw=True),
                attach_cv=SimpleNamespace(raw=False),
                link_cv_instead=SimpleNamespace(raw=False),
            ),
            fallback_enabled=SimpleNamespace(raw=False),
            region_priority=[SimpleNamespace(raw="Japan")],
        )

    def path(self, *keys):
        return Path(self._paths[keys[0]])

    def env(self, key, default=""):
        return default

    def profile_raw(self):
        return {"full_name": "Test", "projects": []}


class FakeContact:
    def __init__(self, email):
        self.email = email
        self.source_url = "https://acme.example/contact"
        self.confidence = "high"
        self.name = "Jane Doe"
        self.role = "CTO"
        self.kind = "personal"


class FakeDrafter:
    def __init__(self, *a, **k):
        pass

    def fit(self, client, company, domain, page_text):
        return {"is_xr_company": True, "company_type": "studio", "tags": ["unity"], "reason": "x"}

    def generate(self, client, company, page_text, contact, company_type, target_kind, include_marketplace):
        from outreach.draft.validate import Draft

        return Draft(subject="Hello", body="Hi Jane,\n\nBody.\n", evidence_quote="we build", template_variant="studio"), [], False


class FakeServer:
    name = "fake"
    base_url = "http://localhost:1"

    def start(self, **k):
        return True

    def stop(self):
        pass


class FakeFetcher:
    def __init__(self, *a, **k):
        pass

    def get_text(self, url):
        return "we build immersive XR experiences"

    def get_html(self, url):
        return "<html></html>"


class FakeVerifier:
    def __init__(self, *a, **k):
        pass


class FakeSearch:
    calls_today = 0

    def __init__(self, *a, **k):
        self.available = True


@pytest.fixture()
def ctx(tmp_path):
    cfg = DummyCfg(tmp_path)
    store = open_store(cfg)
    return Context(cfg=cfg, logger=logging.getLogger("test"), store=store, dry_run=False, limit=None)


def test_runner_queues_candidate(ctx, monkeypatch):
    monkeypatch.setattr(runner, "Drafter", FakeDrafter)
    monkeypatch.setattr(runner, "Fetcher", FakeFetcher)
    monkeypatch.setattr(runner, "EmailVerifier", FakeVerifier)
    monkeypatch.setattr(runner, "SearchManager", FakeSearch)
    monkeypatch.setattr(runner.srv, "load_one", lambda *a, **k: FakeServer())
    monkeypatch.setattr(runner, "LLMClient", lambda *a, **k: object())
    monkeypatch.setattr(runner, "discover_candidates",
                        lambda *a, **k: [CompanyCandidate("acme.example", "https://acme.example", "Acme XR", "Japan", "q")])
    monkeypatch.setattr(runner, "discover_contacts",
                        lambda *a, **k: [FakeContact("jane@acme.example")])

    result = runner.run(ctx)
    assert result["drafted"] == 1
    rows = ctx.store.queue_all()
    assert any(r["to_email"] == "jane@acme.example" and r["status"] == "pending_review" for r in rows)
    assert (ctx.store.summary_get(ctx.today) or {}).get("drafted") == 1
