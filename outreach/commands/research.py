"""`research`: discover companies, draft emails, queue them for review."""

from __future__ import annotations

from ..research.runner import run as run_pipeline


def run(ctx) -> int:
    run_pipeline(ctx)
    return 0
