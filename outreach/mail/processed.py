"""Track which mailbox messages have already been processed (bounces/replies)."""

from __future__ import annotations

import json
from pathlib import Path


def _path(cfg) -> Path:
    return cfg.path("cache_dir") / "processed_mail.json"


def load(cfg) -> dict:
    path = _path(cfg)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return {"bounces": list(data.get("bounces", [])), "replies": list(data.get("replies", []))}
        except (OSError, json.JSONDecodeError):
            pass
    return {"bounces": [], "replies": []}


def save(cfg, data: dict) -> None:
    path = _path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


def is_processed(cfg, kind: str, message_id: str) -> bool:
    if not message_id:
        return False
    return message_id in set(load(cfg).get(kind, []))


def mark_processed(cfg, kind: str, message_id: str) -> None:
    if not message_id:
        return
    data = load(cfg)
    bucket = data.setdefault(kind, [])
    if message_id not in bucket:
        bucket.append(message_id)
        # keep the list bounded
        data[kind] = bucket[-5000:]
        save(cfg, data)
