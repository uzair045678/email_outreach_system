"""`research`: discover companies, draft emails, queue them for review."""

from __future__ import annotations

from ..freehire.notify import powershell_notify
from ..research.runner import run as run_pipeline


def run(ctx) -> int:
    try:
        result = run_pipeline(ctx)
    except Exception:
        if not ctx.dry_run:
            powershell_notify(
                "Outreach research failed",
                "Tomorrow's email drafts were not prepared. Check the outreach log.",
            )
        raise
    if not ctx.dry_run:
        pending = len(ctx.store.queue_by_status("pending_review"))
        if pending:
            powershell_notify(
                "Outreach: approve emails",
                f"{pending} email draft(s) are waiting for approval in outreach.xlsx.",
            )
        elif result.get("error"):
            powershell_notify(
                "Outreach research needs attention",
                "No email drafts were prepared. Check the outreach log.",
            )
    return 1 if result.get("error") else 0
