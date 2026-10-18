"""Phase 5 real-model check: generate a draft with Qwen3-8B and validate it.

Run explicitly:  pytest tests/test_draft_model.py -m slow -s
"""

from __future__ import annotations

from pathlib import Path

import pytest

from outreach.config import load_config
from outreach.draft.drafter import Drafter, OPT_OUT
from outreach.llm import server as srv
from outreach.llm.client import LLMClient, add_no_think

PAGE = (
    "Studio XR is a Kuala Lumpur studio building immersive VR training experiences for industry. "
    "We use Unity and Unreal to ship multiplayer simulations for Meta Quest headsets, and we are "
    "growing our XR team with Unity developers."
)

PROFILE = None  # filled from a temp profile in the test


@pytest.mark.slow
def test_real_model_produces_valid_draft(tmp_path, monkeypatch):
    cfg = load_config()
    if not Path(cfg.raw.paths.llama_server_exe.raw).exists():
        pytest.skip("llama-server not present")

    profile = {
        "full_name": "Test Developer",
        "headline": "Unity/Unreal XR Developer",
        "based_in": "Lahore, Pakistan",
        "years_experience": 4,
        "github_url": "https://github.com/testdev",
        "linkedin_url": "https://linkedin.com/in/testdev",
        "portfolio_url": "https://testdev.example",
        "fiverr_url": "",
        "upwork_url": "",
        "skills": {"engines": ["Unity", "Unreal"], "xr_devices": ["Quest 3"], "languages": ["C#", "C++"]},
        "projects": [
            {"name": "VR Trainer", "one_line_description": "Multiplayer VR training sim.", "url": "https://testdev.example/vr-trainer", "tags": ["vr", "unity", "multiplayer"]},
        ],
    }
    monkeypatch.setattr(cfg, "profile_raw", lambda: profile)
    monkeypatch.setattr(type(cfg), "profile_raw", lambda self: profile)

    server = srv.load_one(cfg, "draft")
    assert server.start(timeout_s=180)
    try:
        client = LLMClient(server.base_url, timeout_s=240, temperature=0.4, max_tokens=700, model=server.name)
        drafter = Drafter(cfg, fallback=None)
        draft, errors, used_fb = drafter.generate(
            client, "Studio XR", PAGE,
            {"name": "Jane Doe", "role": "Founder", "tags": ["vr", "unity", "multiplayer"]},
            "studio", "fulltime", False,
        )
        print("\n--- DRAFT ---")
        if draft:
            print("SUBJECT:", draft.subject)
            print(draft.body)
        print("ERRORS:", errors)
        assert draft is not None, f"expected a valid draft, got errors: {errors}"
        assert OPT_OUT in draft.body
    finally:
        server.stop()
