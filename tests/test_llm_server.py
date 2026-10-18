"""Phase 3: start the real model, get JSON, stop, confirm VRAM freed.

Run explicitly:  pytest -m slow -s
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from outreach.config import load_config
from outreach.llm import server as srv
from outreach.llm.client import LLMClient, add_no_think

SCHEMA = {
    "type": "object",
    "properties": {
        "is_xr_company": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["is_xr_company", "reason"],
    "additionalProperties": False,
}


@pytest.mark.slow
def test_start_json_stop_frees_vram():
    cfg = load_config()
    if not Path(cfg.raw.paths.llama_server_exe.raw).exists():
        pytest.skip("llama-server not present")

    baseline = srv.vram_used_mb()
    server = srv.load_one(cfg, "draft")
    assert server.start(timeout_s=180), "server should become ready"

    try:
        client = LLMClient(server.base_url, timeout_s=180, temperature=0.2, max_tokens=200, model=server.name)
        messages = add_no_think([
            {"role": "system", "content": "You classify companies. Reply as JSON only."},
            {"role": "user", "content": "Company: 'Metaio AR' builds augmented-reality SDKs and Unity plugins. Is this an XR company?"},
        ])
        data = client.chat_json(messages, schema=SCHEMA)
        assert isinstance(data.get("is_xr_company"), bool)
        assert data["reason"]
    finally:
        server.stop()
        time.sleep(3)

    assert server.proc is None
    assert srv.free_port(server.port, timeout=10)
    after = srv.vram_used_mb()
    if baseline >= 0 and after >= 0:
        # allow a little driver slack
        assert after <= baseline + 400, f"VRAM not freed (baseline={baseline}, after={after})"
