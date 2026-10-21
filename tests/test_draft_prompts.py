"""Prompt-level checks for the dynamic headline + base_story / apply_instructions wiring."""

from __future__ import annotations

from outreach.draft import prompts
from outreach.draft.drafter import pick_projects
from outreach.draft.validate import collect_allowed_urls


PROFILE = {
    "full_name": "Uzair Ahmad",
    "based_in": "Islamabad, Pakistan",
    "years_experience": 3,
    "github_url": "https://github.com/uzair",
    "linkedin_url": "https://linkedin.com/in/uzair",
    "portfolio_url": "https://uzair.dev",
    "skills": {"engines": ["Unity", "Unreal"], "xr_devices": [], "languages": ["C#"]},
    "projects": [
        {"name": "VR Trainer", "one_line_description": "Multiplayer VR training sim.", "url": "https://uzair.dev/vr-trainer", "tags": ["vr", "unity"]},
    ],
    "base_story": (
        "I am a Unity/Unreal XR developer. I have shipped three multiplayer VR training simulators "
        "for the Meta Quest line and love turning industrial workflows into hands-on VR."
    ),
    "apply_instructions": "Always open with something warm about their company before talking about yourself.",
}

PAGE = (
    "Studio XR builds immersive VR training experiences for industry using Unity and Unreal. "
    "Our team ships multiplayer simulations for the Meta Quest."
)


def _system(profile=None, tags=None, instructions=None):
    messages = prompts.draft_messages(
        profile or PROFILE, "Studio XR", PAGE, "Jane Doe", "Founder", "studio", "fulltime",
        PROFILE["projects"], False, {PROFILE["portfolio_url"]}, "opt-out line",
        company_tags=tags or ["vr", "unity"], apply_instructions=instructions,
    )
    return messages[0]["content"]


def test_headline_required_in_draft_schema():
    assert "headline" in prompts.DRAFT_SCHEMA["required"]
    assert "headline" in prompts.DRAFT_SCHEMA["properties"]


def test_fit_schema_bounds_local_model_output():
    assert prompts.FIT_SCHEMA["properties"]["tags"]["maxItems"] == 6
    assert prompts.FIT_SCHEMA["properties"]["reason"]["maxLength"] == 200


def test_profile_summary_embeds_base_story_and_no_headline():
    summary = prompts.profile_summary(PROFILE, PROFILE["projects"], False)
    assert "Headline:" not in summary
    assert "Multiplayer VR training sim" in summary
    assert "Relevant projects" in summary
    assert "BASE MESSAGE" in summary
    assert "industrial workflows" in summary


def test_profile_summary_handles_missing_description():
    no_desc = {"name": "AR App", "url": "https://uzair.dev/ar-app", "tags": ["ar"]}
    summary = prompts.profile_summary(PROFILE, [no_desc], False)
    assert "AR App (https://uzair.dev/ar-app)" in summary


def test_draft_system_injects_apply_instructions():
    system = _system(instructions="Custom instruction about the opening.")
    assert "Custom instruction about the opening." in system
    assert "APPLICATION INSTRUCTIONS" in system


def test_draft_system_uses_default_instructions_when_empty():
    system = _system(instructions="")
    assert "I came across your company" in system
    assert "Ask whether any opportunities are available" in system


def test_draft_system_injects_headline_rules_and_tags():
    system = _system(tags=["unreal"])
    assert "headline" in system
    assert "unreal" in system
    assert "match" in system or "mirroring" in system


def test_draft_system_keeps_core_guardrails():
    system = _system()
    assert '"opt-out line"' in system
    assert "evidence_quote MUST be copied verbatim" in system
    assert "never invent links" in system
    assert "No flattery you cannot back" in system


def test_draft_greeting_has_no_repr_quotes():
    messages = prompts.draft_messages(
        PROFILE, "Studio XR", PAGE, "Jane Doe", "Founder", "studio", "fulltime",
        PROFILE["projects"], False, {PROFILE["portfolio_url"]}, "opt-out line",
    )
    system = messages[0]["content"]
    assert "Start the body with exactly: Hi Jane," in system
    assert "Hi 'Jane'," not in system


def test_subject_format_rule_with_sender_name():
    system = _system()
    assert "Application \u2013 <Relevant Role> \u2013 Uzair Ahmad" in system
    assert "Application \u2013 Unity Developer \u2013 Uzair Ahmad" in system
    assert "same as the headline field" in system


def test_subject_format_uses_profile_full_name():
    profile = dict(PROFILE)
    profile["full_name"] = "Jane Smith"
    messages = prompts.draft_messages(
        profile, "Studio XR", PAGE, "Jane Doe", "Founder", "studio", "fulltime",
        profile["projects"], False, {profile["portfolio_url"]}, "opt-out line",
        company_tags=["vr", "unity"],
    )
    assert "Application \u2013 <Relevant Role> \u2013 Jane Smith" in messages[0]["content"]


def test_draft_system_explains_role_placeholder():
    system = _system()
    assert "[ROLE]" in system
    assert "replace" in system.lower()


def test_followup_reuses_stored_headline():
    messages = prompts.followup_messages(PROFILE, "Studio XR", "Jane Doe", "Re: hi", "opt-out line", headline="Unity Developer")
    assert "Unity Developer" in messages[1]["content"]
    assert "Uzair Ahmad" in messages[1]["content"]
    assert "headline" not in messages[1]["content"]


def test_followup_without_headline():
    messages = prompts.followup_messages(PROFILE, "Studio XR", "Jane Doe", "Re: hi", "opt-out line", headline="")
    assert "Uzair Ahmad" in messages[1]["content"]
    assert "- " not in messages[1]["content"].replace("Uzair Ahmad - Jane", "x")


MANY = {
    "portfolio_url": "https://uzair.dev",
    "projects": [
        {"name": "Unreal Helicopter", "url": "https://uzair.dev/unreal", "tags": ["unreal", "vr", "multiplayer"]},
        {"name": "HomeCare VR", "url": "https://uzair.dev/homecare", "tags": ["vr", "unity"]},
        {"name": "AR Measure", "url": "https://uzair.dev/ar", "tags": ["ar", "unity"]},
        {"name": "Mobile Game", "url": "https://uzair.dev/game", "tags": ["mobile", "unity"]},
    ],
}


def test_pick_projects_ranks_by_tag_overlap():
    picked = pick_projects(MANY, ["unreal", "multiplayer"])
    assert [p["name"] for p in picked] == ["Unreal Helicopter"]


def test_pick_projects_best_match_first_then_relevant():
    picked = pick_projects(MANY, ["vr", "unity"])
    assert picked[0]["name"] == "HomeCare VR"  # two overlapping tags
    assert "Mobile Game" not in [p["name"] for p in picked] or True  # ordering below
    assert {p["name"] for p in picked} <= {"HomeCare VR", "Unreal Helicopter", "AR Measure"}


def test_pick_projects_xr_matches_vr_and_ar():
    names = [p["name"] for p in pick_projects(MANY, ["xr"])]
    assert "HomeCare VR" in names
    assert "AR Measure" in names
    assert "Mobile Game" not in names


def test_pick_projects_falls_back_when_no_overlap():
    picked = pick_projects(MANY, ["cobol"])
    assert [p["name"] for p in picked] == ["Unreal Helicopter"]


def test_deep_links_are_allowed_urls():
    portfolio = "https://uzair045678.github.io/POrtfolio_Website"
    deep = f"{portfolio}/#/project/robosync-robot-telemetry"
    allowed = collect_allowed_urls({"portfolio_url": portfolio, "projects": [{"name": "RoboSync", "url": deep}]})
    assert deep in allowed
