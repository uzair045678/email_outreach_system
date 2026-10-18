import json

import pytest

from outreach.config import NS
from outreach.draft.drafter import Drafter, OPT_OUT
from outreach.draft.validate import Draft, validate_draft, collect_allowed_urls


PROFILE = {
    "full_name": "Uzair Ahmad",
    "headline": "Unity/Unreal XR Developer",
    "based_in": "Lahore, Pakistan",
    "years_experience": 4,
    "github_url": "https://github.com/uzair",
    "linkedin_url": "https://linkedin.com/in/uzair",
    "portfolio_url": "https://uzair.dev",
    "fiverr_url": "https://fiverr.com/uzair",
    "upwork_url": "https://upwork.com/freelancers/uzair",
    "skills": {"engines": ["Unity", "Unreal"], "xr_devices": ["Quest 3"], "languages": ["C#", "C++"]},
    "projects": [
        {"name": "VR Trainer", "one_line_description": "Multiplayer VR training sim.", "url": "https://uzair.dev/vr-trainer", "tags": ["vr", "unity", "multiplayer"]},
    ],
}

PAGE = (
    "Studio XR builds immersive VR training experiences for industry using Unity and Unreal. "
    "Our team ships multiplayer simulations for the Meta Quest."
)


class FakeCfg:
    def __init__(self):
        self._raw = {
            "draft": {"min_body_words": 70, "max_body_words": 130, "max_subject_len": 70, "retries_before_fallback": 2},
            "models": {"draft": {"no_think": True}},
        }
        self._profile = PROFILE

    @property
    def raw(self):
        return NS(self._raw)

    def profile_raw(self):
        return PROFILE


def good_body():
    return (
        "Hi Jane,\n\n"
        "I saw Studio XR builds immersive VR training experiences in Unity and Unreal, including "
        "multiplayer Quest simulations. I am an XR developer who builds Unity and Unreal applications "
        "for Quest 3, with C# and C++ experience.\n\n"
        "One of my projects is a multiplayer VR training simulator: https://uzair.dev/vr-trainer\n\n"
        "Would you be open to a full-time XR role on your team?\n\n"
        "Thanks,\nUzair Ahmad\nhttps://github.com/uzair\nhttps://linkedin.com/in/uzair\nhttps://uzair.dev\n\n"
        f"{OPT_OUT}"
    )


def make_draft(**over):
    base = dict(
        subject="XR developer for your VR training team",
        body=good_body(),
        evidence_quote="Studio XR builds immersive VR training experiences for industry using Unity and Unreal.",
        evidence_url="https://studio-xr.com/about",
        template_variant="studio",
    )
    base.update(over)
    return Draft(**base)


def cfg():
    return FakeCfg()


def test_good_draft_passes():
    errors = validate_draft(make_draft(), PROFILE, PAGE, cfg(), "Jane Doe", "Studio XR")
    assert errors == []


def test_placeholder_rejected():
    d = make_draft(body=good_body().replace("I am an XR developer", "I am [Your Name], an XR developer"))
    errors = validate_draft(d, PROFILE, PAGE, cfg(), "Jane Doe", "Studio XR")
    assert any("placeholder" in e for e in errors)


def test_fake_link_rejected():
    body = good_body() + "\nhttps://evil.example.com/portfolio"
    errors = validate_draft(make_draft(body=body), PROFILE, PAGE, cfg(), "Jane Doe", "Studio XR")
    assert any("disallowed link" in e for e in errors)


def test_evidence_not_in_page_rejected():
    d = make_draft(evidence_quote="We are a blockchain metaverse company.")
    errors = validate_draft(d, PROFILE, PAGE, cfg(), "Jane Doe", "Studio XR")
    assert any("evidence_quote" in e for e in errors)


def test_too_long_rejected():
    body = good_body() + (" extra words" * 40)
    errors = validate_draft(make_draft(body=body), PROFILE, PAGE, cfg(), "Jane Doe", "Studio XR")
    assert any("too long" in e for e in errors)


def test_allcaps_subject_rejected():
    errors = validate_draft(make_draft(subject="URGENT XR DEVELOPER WANTED NOW"), PROFILE, PAGE, cfg(), "Jane Doe", "Studio XR")
    assert any("all-caps" in e for e in errors)


def test_greeting_mismatch_rejected():
    body = good_body().replace("Hi Jane,", "Hi John,")
    errors = validate_draft(make_draft(body=body), PROFILE, PAGE, cfg(), "Jane Doe", "Studio XR")
    assert any("greeting" in e for e in errors)


def test_missing_optout_rejected():
    body = good_body().replace(OPT_OUT, "")
    errors = validate_draft(make_draft(body=body), PROFILE, PAGE, cfg(), "Jane Doe", "Studio XR")
    assert any("opt-out" in e for e in errors)


def test_placeholder_in_headline_rejected():
    d = make_draft(headline="[Your Name] Developer")
    errors = validate_draft(d, PROFILE, PAGE, cfg(), "Jane Doe", "Studio XR")
    assert any("headline" in e and "placeholder" in e for e in errors)


def test_multiline_headline_rejected():
    d = make_draft(headline="XR Developer\nDark knight")
    errors = validate_draft(d, PROFILE, PAGE, cfg(), "Jane Doe", "Studio XR")
    assert any("single line" in e for e in errors)


def test_unsupported_years_rejected():
    body = good_body().replace("I am an XR developer", "I am an XR developer with 12 years")
    body = body.replace("with C# and C++ experience", "with C# and C++ experience")
    errors = validate_draft(make_draft(body=body), PROFILE, PAGE, cfg(), "Jane Doe", "Studio XR")
    assert any("years" in e for e in errors)


# ---- retry / fallback path with a scripted fake client --------------------

class ScriptedClient:
    """Returns queued JSON payloads in order."""

    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = 0

    def chat_json(self, messages, schema=None, **kw):
        self.calls += 1
        if not self.payloads:
            raise RuntimeError("no more payloads")
        return self.payloads.pop(0)


class FakeFallback:
    def __init__(self, payloads):
        self.available = True
        self.client = ScriptedClient(payloads)

    def chat_json(self, messages, schema=None, **kw):
        return self.client.chat_json(messages, schema=schema)


def test_retry_then_success():
    bad = {"subject": "x", "body": "too short", "evidence_quote": "nope", "evidence_url": "", "template_variant": "studio"}
    good = {
        "subject": "XR developer for your VR training team",
        "body": good_body(),
        "evidence_quote": "Studio XR builds immersive VR training experiences for industry using Unity and Unreal.",
        "evidence_url": "https://studio-xr.com/about",
        "template_variant": "studio",
    }
    d = Drafter(cfg())
    client = ScriptedClient([bad, good])
    draft, errors, used_fb = d.generate(client, "Studio XR", PAGE, {"name": "Jane Doe", "tags": ["vr", "unity"]}, "studio", "fulltime", False)
    assert draft is not None and not errors and used_fb is False
    assert client.calls == 2


def test_fallback_used_after_failures():
    bad = {"subject": "x", "body": "bad", "evidence_quote": "nope", "evidence_url": "", "template_variant": "studio"}
    good = {
        "subject": "XR developer for your VR training team",
        "body": good_body(),
        "evidence_quote": "Studio XR builds immersive VR training experiences for industry using Unity and Unreal.",
        "evidence_url": "https://studio-xr.com/about",
        "template_variant": "studio",
    }
    d = Drafter(cfg(), fallback=FakeFallback([good]))
    client = ScriptedClient([bad, bad, bad])  # 3 local attempts (1 + 2 retries)
    draft, errors, used_fb = d.generate(client, "Studio XR", PAGE, {"name": "Jane Doe", "tags": ["vr"]}, "studio", "fulltime", False)
    assert draft is not None and used_fb is True


def test_all_fail_returns_none():
    bad = {"subject": "x", "body": "bad", "evidence_quote": "nope", "evidence_url": "", "template_variant": "studio"}
    d = Drafter(cfg(), fallback=None)
    client = ScriptedClient([bad, bad, bad])
    draft, errors, used_fb = d.generate(client, "Studio XR", PAGE, {"name": "Jane Doe"}, "studio", "fulltime", False)
    assert draft is None and errors
