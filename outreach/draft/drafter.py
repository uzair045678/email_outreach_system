"""Draft generation: fit check, project selection, drafting with validation."""

from __future__ import annotations

from . import prompts
from .validate import Draft, collect_allowed_urls, validate_draft
from ..llm.client import add_no_think

OPT_OUT = "If this isn't relevant, tell me and I won't write again."


def pick_projects(profile: dict, tags: list[str], limit: int = 3) -> list[dict]:
    """Return up to ``limit`` projects ranked by tag overlap with the company.

    The first entry is the best match. Falls back to the single highest-tagged
    project when nothing overlaps, so a relevant XR link is still available.
    """
    projects = [p for p in (profile.get("projects") or []) if isinstance(p, dict)]
    if not projects:
        return []
    wanted = {str(t).lower() for t in (tags or [])}
    # XR is a superset of VR/AR in practice; let XR companies match both.
    if "xr" in wanted:
        wanted |= {"vr", "ar"}
    scored = []
    for idx, proj in enumerate(projects):
        ptags = {str(t).lower() for t in (proj.get("tags") or [])}
        scored.append((len(ptags & wanted), -idx, proj))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    matched = [proj for score, _, proj in scored if score > 0]
    if not matched:
        matched = [scored[0][2]]
    return matched[:limit]


class Drafter:
    def __init__(self, cfg, logger=None, fallback=None):
        self.cfg = cfg
        self.logger = logger
        self.fallback = fallback
        self.retries = int(cfg.raw.draft.retries_before_fallback.raw)
        self.profile = cfg.profile_raw()
        self._no_think = bool(getattr(cfg.raw.models.draft, "no_think").raw) if "no_think" in cfg.raw.models.draft else True

    # -- fit classification -------------------------------------------------
    def fit(self, client, company: str, domain: str, page_text: str) -> dict | None:
        messages = prompts.fit_messages(company, domain, page_text)
        if self._no_think:
            messages = add_no_think(messages)
        try:
            return client.chat_json(messages, schema=prompts.FIT_SCHEMA, temperature=0.1, max_tokens=320)
        except Exception as exc:  # noqa: BLE001
            if self.logger:
                self.logger.warning("fit classification failed for %s: %s", company, exc)
            return None

    # -- drafting -----------------------------------------------------------
    def _make_draft(
        self, client, company, page_text, contact, company_type, target_kind,
        include_marketplace, repair_errors: list[str] | None = None,
    ) -> Draft:
        tags = contact.get("tags") or []
        projects = pick_projects(self.profile, tags)
        allowed = collect_allowed_urls(self.profile)
        apply_instructions = (self.profile.get("apply_instructions") or "").strip() or None
        messages = prompts.draft_messages(
            self.profile, company, page_text, contact.get("name", ""), contact.get("role", ""),
            company_type, target_kind, projects, include_marketplace, allowed, OPT_OUT,
            company_tags=tags, apply_instructions=apply_instructions,
        )
        if repair_errors:
            messages.append({
                "role": "user",
                "content": "Correct the next draft so it passes these checks: " + "; ".join(repair_errors[:4]),
            })
        if self._no_think:
            messages = add_no_think(messages)
        data = client.chat_json(messages, schema=prompts.DRAFT_SCHEMA)
        return Draft(
            subject=(data.get("subject") or "").strip(),
            body=(data.get("body") or "").strip(),
            headline=(data.get("headline") or "").strip(),
            evidence_quote=(data.get("evidence_quote") or "").strip(),
            evidence_url=(data.get("evidence_url") or "").strip(),
            template_variant=(data.get("template_variant") or company_type or "").strip(),
            target_kind=target_kind,
        )

    def generate(
        self,
        client,
        company: str,
        page_text: str,
        contact: dict,
        company_type: str,
        target_kind: str,
        include_marketplace: bool,
    ) -> tuple[Draft | None, list[str], bool]:
        """Return (draft, errors, used_fallback)."""
        last_errors: list[str] = []
        repair_errors: list[str] = []
        for attempt in range(self.retries + 1):
            try:
                draft = self._make_draft(
                    client, company, page_text, contact, company_type, target_kind,
                    include_marketplace, repair_errors=repair_errors,
                )
            except Exception as exc:  # noqa: BLE001
                last_errors = [f"model error: {exc}"]
                continue
            errors = validate_draft(draft, self.profile, page_text, self.cfg, contact.get("name", ""), company)
            if not errors:
                return draft, [], False
            last_errors = errors
            repair_errors.extend(error for error in errors if error not in repair_errors)
            if self.logger:
                self.logger.info("draft attempt %d rejected for %s: %s", attempt + 1, company, errors[:3])

        if self.fallback is not None and self.fallback.available:
            try:
                draft = self._make_draft(
                    self.fallback, company, page_text, contact, company_type, target_kind,
                    include_marketplace, repair_errors=repair_errors,
                )
                errors = validate_draft(draft, self.profile, page_text, self.cfg, contact.get("name", ""), company)
                if not errors:
                    return draft, [], True
                last_errors = errors
            except Exception as exc:  # noqa: BLE001
                last_errors = [f"fallback error: {exc}"]

        return None, last_errors, False

    # -- follow-up ----------------------------------------------------------
    def followup(self, client, company: str, contact_name: str, original_subject: str, headline: str = "") -> Draft | None:
        messages = prompts.followup_messages(self.profile, company, contact_name, original_subject, OPT_OUT, headline=headline)
        if self._no_think:
            messages = add_no_think(messages)
        try:
            data = client.chat_json(messages, schema=prompts.FOLLOWUP_SCHEMA)
        except Exception as exc:  # noqa: BLE001
            if self.logger:
                self.logger.warning("followup draft failed for %s: %s", company, exc)
            return None
        return Draft(
            subject=(data.get("subject") or original_subject).strip(),
            body=(data.get("body") or "").strip(),
            template_variant="followup",
        )
