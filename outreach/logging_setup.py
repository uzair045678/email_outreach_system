"""Logging: rotating file + an optional in-memory sink that mirrors to the
workbook ``Log`` sheet.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


class StoreLogHandler(logging.Handler):
    """Forward records to a Store's Log sheet (best effort, never raises)."""

    def __init__(self, store, command: str = ""):
        super().__init__()
        self.store = store
        self.command = command

    def set_command(self, command: str) -> None:
        self.command = command

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.store.log(self.command, record.levelname, self.format(record))
        except Exception:  # noqa: BLE001 - logging must never break the app
            pass


def setup_logging(
    log_dir: str | Path,
    level: int = logging.INFO,
    store=None,
    command: str = "",
) -> logging.Logger:
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("outreach")
    logger.setLevel(level)
    logger.handlers.clear()
    logger.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        log_dir / "outreach.log", maxBytes=2_000_000, backupCount=7, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(levelname)-7s %(message)s"))
    logger.addHandler(console)

    if store is not None:
        store_handler = StoreLogHandler(store, command=command)
        store_handler.setFormatter(logging.Formatter("%(message)s"))
        store_handler.setLevel(logging.INFO)
        logger.addHandler(store_handler)
        logger._outreach_store_handler = store_handler  # type: ignore[attr-defined]

    return logger


def add_store_handler(logger: logging.Logger, store, command: str = "") -> None:
    """Attach a Store-backed handler once the workbook is available."""
    existing = getattr(logger, "_outreach_store_handler", None)
    if existing is not None:
        existing.set_command(command)
        return
    handler = StoreLogHandler(store, command=command)
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger._outreach_store_handler = handler  # type: ignore[attr-defined]


def set_command(logger: logging.Logger, command: str) -> None:
    handler = getattr(logger, "_outreach_store_handler", None)
    if handler is not None:
        handler.set_command(command)
