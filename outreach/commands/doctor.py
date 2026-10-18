"""`doctor` command."""

from __future__ import annotations

from ..doctor import format_report, run_checks


def run(ctx) -> int:
    checks = run_checks(ctx.cfg)
    report, ok = format_report(checks)
    print(report)
    if ctx.store is not None:
        for c in checks:
            if not c.ok:
                ctx.store.log("doctor", "ERROR" if c.fatal else "WARN", f"{c.name}: {c.detail}")
    ctx.logger.info("doctor result: %s", "PASS" if ok else "FAIL")
    return 0 if ok else 1
