"""Tiny persistent state: sending pause flag and warm-up start date.

The pause flag is a plain file you can delete by hand to resume sending.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import util


def _state_path(cfg) -> Path:
    return cfg.path("cache_dir") / "state.json"


def _flag_path(cfg) -> Path:
    return cfg.path("cache_dir") / "pause_sending.flag"


def load_state(cfg) -> dict:
    path = _state_path(cfg)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def save_state(cfg, state: dict) -> None:
    path = _state_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError:
        pass


def is_paused(cfg) -> tuple[bool, str]:
    if _flag_path(cfg).exists():
        try:
            return True, _flag_path(cfg).read_text(encoding="utf-8").strip() or "paused"
        except OSError:
            return True, "paused"
    state = load_state(cfg)
    if state.get("pause_sending"):
        return True, state.get("pause_reason", "paused")
    return False, ""


def set_paused(cfg, reason: str) -> None:
    flag = _flag_path(cfg)
    flag.parent.mkdir(parents=True, exist_ok=True)
    try:
        flag.write_text(reason, encoding="utf-8")
    except OSError:
        pass
    state = load_state(cfg)
    state["pause_sending"] = True
    state["pause_reason"] = reason
    save_state(cfg, state)


def clear_paused(cfg) -> None:
    try:
        _flag_path(cfg).unlink()
    except OSError:
        pass
    state = load_state(cfg)
    state["pause_sending"] = False
    state.pop("pause_reason", None)
    save_state(cfg, state)


def get_warmup_start(cfg) -> str:
    return load_state(cfg).get("warmup_start_date", "")


def set_warmup_start(cfg, date_str: str) -> None:
    state = load_state(cfg)
    if not state.get("warmup_start_date"):
        state["warmup_start_date"] = date_str
        save_state(cfg, state)


def set_state(cfg, **kwargs) -> None:
    state = load_state(cfg)
    state.update(kwargs)
    save_state(cfg, state)
