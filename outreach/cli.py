"""Single CLI entry point: ``python -m outreach <command>``."""

from __future__ import annotations

import argparse
import importlib
import sys
import traceback

from . import __version__
from .config import load_config
from .context import Context
from .locking import CommandLock
from .logging_setup import add_store_handler, setup_logging
from .store import open_store

# command -> (module, function, needs_store)
COMMANDS = {
    "research": ("outreach.commands.research", "run", True),
    "send": ("outreach.commands.send", "run", True),
    "bounces": ("outreach.commands.bounces", "run", True),
    "repair": ("outreach.commands.repair", "run", True),
    "replies": ("outreach.commands.replies", "run", True),
    "followups": ("outreach.commands.followups", "run", True),
    "summary": ("outreach.commands.summary", "run", True),
    "import-contacted": ("outreach.commands.import_contacted", "run", False),
    "doctor": ("outreach.commands.doctor", "run", False),
    "tune-gpu": ("outreach.commands.tune_gpu", "run", False),
}

HELP = {
    "research": "Discover companies, draft emails, queue for review.",
    "send": "Send approved, due emails (no LLM).",
    "bounces": "Read inbox/spam, detect bounces, update rows.",
    "repair": "Find + verify replacement contacts for bounced rows.",
    "replies": "Detect and classify replies and auto-replies.",
    "followups": "Draft follow-ups for silent contacts.",
    "summary": "Write today's summary row and log.",
    "import-contacted": "Import your existing contacted list (CSV/XLSX).",
    "doctor": "Check all dependencies and configuration.",
    "tune-gpu": "Probe -ngl values and report VRAM usage.",
}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="outreach", description="Local job-outreach pipeline.")
    p.add_argument("--version", action="version", version=f"outreach {__version__}")
    p.add_argument("--dry-run", action="store_true", help="Do everything except send mail; write nothing destructive.")
    p.add_argument("--limit", type=int, default=None, help="Max items to process this run.")
    p.add_argument("--project-dir", default=None, help="Override project directory (defaults to the project root).")

    sub = p.add_subparsers(dest="command", metavar="<command>")
    for name, (_, _, _) in COMMANDS.items():
        sp = sub.add_parser(name, help=HELP.get(name, ""))
        if name == "import-contacted":
            sp.add_argument("file", help="CSV or XLSX file of already-contacted companies/emails.")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 1

    cfg = load_config(args.project_dir)
    logger = setup_logging(cfg.path("log_dir"), command=args.command)

    module_name, func_name, needs_store = COMMANDS[args.command]
    logger.info("start command=%s dry_run=%s limit=%s", args.command, args.dry_run, args.limit)

    store = None
    lock = CommandLock(cfg.path("lock_dir"), args.command)
    if not lock.acquire():
        logger.error("Another '%s' run is active; exiting.", args.command)
        print(f"Another '{args.command}' run is active; exiting.")
        return 2

    try:
        if needs_store:
            store = open_store(cfg, logger=logger)
            add_store_handler(logger, store, args.command)
        ctx = Context(cfg=cfg, logger=logger, store=store, dry_run=args.dry_run, limit=args.limit, args=args)

        module = importlib.import_module(module_name)
        result = getattr(module, func_name)(ctx)

        if store is not None:
            store.save()
        return int(result or 0)
    except NotImplementedError as exc:
        logger.error("not implemented: %s", exc)
        print(f"That command is not built yet: {exc}")
        return 3
    except Exception as exc:  # noqa: BLE001
        logger.error("command failed: %s", exc)
        logger.debug("%s", traceback.format_exc())
        if store is not None:
            try:
                store.save()
            except Exception:  # noqa: BLE001
                pass
        print(f"ERROR: {exc}")
        return 1
    finally:
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
