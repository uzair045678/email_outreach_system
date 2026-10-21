"""Prompt + JSON-schema builders for every model call."""

from __future__ import annotations

import json

FIT_SCHEMA = {
    "type": "object",
    "properties": {
        "is_xr_company": {"type": "boolean"},
        "company_type": {"type": "string", "enum": ["studio", "agency", "product", "corporation"]},
        "tags": {"type": "array", "maxItems": 6, "items": {"type": "string", "maxLength": 24}},
        "reason": {"type": "string", "maxLength": 200},
    },
    "required": ["is_xr_company", "company_type", "tags", "reason"],
    "additionalProperties": False,
}

DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "subject": {"type": "string"},
        "headline": {"type": "string"},
        "body": {"type": "string"},
        "evidence_quote": {"type": "string"},
        "evidence_url": {"type": "string"},
        "template_variant": {"type": "string", "enum": ["studio", "agency", "product", "corporation"]},
    },
    "required": ["subject", "headline", "body", "evidence_quote", "evidence_url", "template_variant"],
    "additionalProperties": False,
}

FOLLOWUP_SCHEMA = {
    "type": "object",
    "properties": {"subject": {"type": "string"}, "body": {"type": "string"}},
    "required": ["subject", "body"],
    "additionalProperties": False,
}

REPLY_SCHEMA = {
    "type": "object",
    "properties": {
        "category": {
            "type": "string",
            "enum": ["positive", "negative_or_unsubscribe", "auto_reply_out_of_office", "other"],
        },
        "return_date": {"type": "string"},
        "summary": {"type": "string"},
    },
    "required": ["category"],
    "additionalProperties": False,
}

BOUNCE_SCHEMA = {
    "type": "object",
    "properties": {
        "is_bounce": {"type": "boolean"},
        "final_recipient": {"type": "string"},
        "status_code": {"type": "string"},
        "original_message_id": {"type": "string"},
    },
    "required": ["is_bounce", "final_recipient", "status_code"],
    "additionalProperties": False,
}


def profile_summary(profile: dict, selected_projects: list[dict] | None, include_marketplace: bool) -> str:
    skills = profile.get("skills") or {}
    lines = [
        f"Name: {profile.get('full_name')}",
        f"Based in: {profile.get('based_in', '')}",
    ]
    if profile.get("years_experience") not in (None, "", "TODO"):
        lines.append(f"Years of experience: {profile.get('years_experience')}")
    engines = ", ".join(skills.get("engines") or [])
    devices = ", ".join(skills.get("xr_devices") or [])
    languages = ", ".join(skills.get("languages") or [])
    other = ", ".join(skills.get("other") or [])
    if engines:
        lines.append(f"Engines: {engines}")
    if devices:
        lines.append(f"XR devices: {devices}")
    if languages:
        lines.append(f"Languages: {languages}")
    if other:
        lines.append(f"Other skills: {other}")
    projects = [p for p in (selected_projects or []) if isinstance(p, dict)]
    if projects:
        lines.append("Relevant projects (best match first; link the FIRST one, add others only if clearly relevant):")
        for proj in projects:
            desc = (proj.get("one_line_description") or "").strip()
            if desc:
                lines.append(f"- {proj.get('name')} - {desc} ({proj.get('url')})")
            else:
                lines.append(f"- {proj.get('name')} ({proj.get('url')})")
    lines.append(f"GitHub: {profile.get('github_url')} | LinkedIn: {profile.get('linkedin_url')} | Portfolio: {profile.get('portfolio_url')}")
    if include_marketplace:
        if profile.get("fiverr_url"):
            lines.append(f"Fiverr: {profile['fiverr_url']}")
        if profile.get("upwork_url"):
            lines.append(f"Upwork: {profile['upwork_url']}")
    base_story = profile.get("base_story")
    if base_story:
        story = str(base_story).strip()
        lines.append("\nTHE SENDER'S BASE MESSAGE (facts are authoritative; quote and reuse its wording freely):\n" + story)
    return "\n".join(lines)


def fit_messages(company: str, domain: str, page_text: str) -> list[dict]:
    excerpt = (page_text or "")[:2500]
    return [
        {
            "role": "system",
            "content": (
                "You classify companies for an XR/AR/VR/MR/Unity/Unreal job search. "
                "Return JSON only. is_xr_company is true only if the company clearly does or hires for "
                "XR/AR/VR/MR/Unity/Unreal work. tags: list up to 6 lowercase keywords describing their work "
                "(e.g. ar, vr, unity, unreal, quest, hololens, mobile, multiplayer, agency, product). "
                "company_type: studio (team selling XR work/experiences), agency (general digital agency with XR), "
                "product (ships an XR/tech product), corporation (large enterprise)."
            ),
        },
        {
            "role": "user",
            "content": f"Company: {company}\nDomain: {domain}\nPage text:\n{excerpt}",
        },
    ]


DEFAULT_APPLY_INSTRUCTIONS = (
    "Subject line must be strong and catch the recipient's attention (specific to their work, not generic).\n"
    "Body, in order:\n"
    "1. Opener that talks about THEM first: something like \"I came across your company\" + one warm, "
    "genuine compliment about the work they do, grounded in a specific real detail from their site (no flattery you "
    "cannot back).\n"
    "2. Relate the sender's most relevant project to their work and give that project's portfolio link.\n"
    "3. Tell the sender about themselves (from the base message).\n"
    "4. Ask whether any opportunities are available."
)


def draft_messages(
    profile: dict,
    company: str,
    page_text: str,
    contact_name: str,
    contact_role: str,
    company_type: str,
    target_kind: str,
    selected_projects: list[dict] | None,
    include_marketplace: bool,
    allowed_urls: set[str],
    opt_out: str,
    company_tags: list[str] | None = None,
    apply_instructions: str | None = None,
) -> list[dict]:
    excerpt = (page_text or "")[:3500]
    greeting = f"Hi {contact_name.split()[0]}," if contact_name else f"Hi team at {company},"
    ask = {
        "fulltime": "Ask if they are open to a full-time role on their XR team.",
        "parttime": "Ask if they have any part-time XR work available.",
        "freelance": "Ask if they need freelance help on XR projects.",
        "project": "Ask if they have a project where an XR developer could help.",
    }.get(target_kind, "Ask if they are open to full-time, part-time, freelance or project work.")

    style = {
        "studio": "Tone: respectful and craft-focused; lead with their XR work.",
        "agency": "Tone: practical and collaborative; lead with how you plug into client delivery.",
        "product": "Tone: product-minded; lead with their product.",
        "corporation": "Tone: concise and professional; lead with the specific team's stack.",
    }.get(company_type, "")

    instructions = (apply_instructions or "").strip() or DEFAULT_APPLY_INSTRUCTIONS
    tags_note = ", ".join(str(t) for t in (company_tags or [])) or "unknown"
    links_note = "\n".join(sorted(allowed_urls))
    sender_name = (profile.get("full_name") or "Your Name").strip()
    system = (
        "You write short cold outreach emails for a developer/designer who does Unity, Unreal and XR work. "
        "Output JSON only with fields subject, headline, body, evidence_quote, evidence_url, template_variant.\n"
        "THE SENDER'S APPLICATION INSTRUCTIONS (follow exactly):\n"
        f"{instructions}\n\n"
        "Extra rules:\n"
        "- headline: one compact line (15 words MAX) positioning the sender for THIS company and mirroring their "
        f"stack (company tags: {tags_note}). E.g. 'Unity Developer' for Unity shops, 'Unreal Developer' for Unreal "
        "shops, 'XR/VR Developer' or 'AR developer' as the market fits. Base it ONLY on real facts from the sender "
        "profile; never invent skills.\n"
        "- Body 90-150 words MAXIMUM. Plain text. No emojis. No buzzwords. No flattery you cannot back.\n"
        f"- Start the body with exactly: {greeting}\n"
        "- Body structure: (1) greeting; (2) warm genuine compliment about THEIR work grounded in one specific "
        "detail from the page text below, phrased naturally like \"I came across your company ...\" and embedding the "
        "exact evidence_quote; (3) the FIRST (best-matched) project from the relevant-projects list with its link, "
        "plus a short phrase of context about it (add another listed project only if clearly relevant; never invent "
        "project details); (4) who the sender is, "
        "in their own voice from the base message; (5) a low-pressure ask about open opportunities; (6) a signature "
        "with the sender's links; (7) the opt-out line exactly: " 
        f"\"{opt_out}\".\n"
        f"- Use ONLY these URLs, verbatim, never invent links: {links_note}\n"
        "- The final signature block MUST contain all of the sender's links from "
        "the allowed list (GitHub, LinkedIn, portfolio) verbatim; never omit any.\n"
        f"- evidence_quote MUST be copied verbatim (exact substring) from the page text below, and "
        f"evidence_url is where it came from.\n"
        "- Never claim experience or numbers not given.\n"
        "- The base message contains the token [ROLE]: replace EVERY [ROLE] with the exact role you chose for the "
        "headline field (e.g. 'Unity Developer', 'Unreal Developer', 'XR Developer'). The final body must contain no "
        "[ROLE] token and no other bracketed placeholder like [Company] or {name}.\n"
        f"- subject EXACTLY: Application \u2013 <Relevant Role> \u2013 {sender_name} "
        f"(e.g. 'Application \u2013 Unity Developer \u2013 {sender_name}'). <Relevant Role> MUST be the same as the "
        "headline field and mirror their stack (Unity Developer / Unreal Developer / XR Developer / VR Developer / "
        "AR Developer as fits). Max 70 characters, no all-caps words, no extra words, no emojis.\n"
        "- template_variant must be one of studio, agency, product, corporation.\n"
        f"- {style}\n- {ask}"
    )
    user = (
        f"Company: {company} (type: {company_type})\n"
        f"Contact: {contact_name or '(unknown)'} - role: {contact_role or '(unknown)'}\n\n"
        f"SENDER PROFILE:\n{profile_summary(profile, selected_projects, include_marketplace)}\n\n"
        f"THEIR PAGE TEXT:\n{excerpt}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def followup_messages(profile: dict, company: str, contact_name: str, original_subject: str, opt_out: str, headline: str = "") -> list[dict]:
    greeting = f"Hi {contact_name.split()[0]}," if contact_name else f"Hi team at {company},"
    who = profile.get("full_name")
    if headline:
        who = f"{who or ''} - {headline}".strip(" -")
    system = (
        "Write a very short follow-up to a cold email that got no reply. JSON only: subject, body. "
        "Body under 70 words. Polite, no pressure, one sentence of reminder and one question. "
        f"Start with exactly: {greeting} Keep the same subject thread. End with the opt-out line: \"{opt_out}\""
    )
    user = f"Company: {company}\nOriginal subject: {original_subject}\nSender: {who}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def reply_messages(subject: str, body: str) -> list[dict]:
    system = (
        "Classify an email reply. JSON only: category in "
        "[positive, negative_or_unsubscribe, auto_reply_out_of_office, other]. "
        "If it is an out-of-office auto-reply, put the stated return date (ISO yyyy-mm-dd) in return_date if present. "
        "Put a one-line summary in summary."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": f"Subject: {subject}\n\n{body[:3000]}"}]


def bounce_messages(raw: str) -> list[dict]:
    system = (
        "Parse an email that may be a delivery failure notice. JSON only: is_bounce (bool), "
        "final_recipient (email or empty), status_code (SMTP code like 5.1.1 or empty), original_message_id."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": raw[:4000]}]
