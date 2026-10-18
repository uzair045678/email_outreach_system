"""`send` command: approvals + outbound sends."""

from __future__ import annotations

from ..send import sender


def run(ctx) -> int:
    return sender.run(ctx)
