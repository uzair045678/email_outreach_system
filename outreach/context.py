"""Shared execution context passed to every command."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Context:
    cfg: Any
    logger: Any
    store: Any
    dry_run: bool = False
    limit: int | None = None
    args: Any = None

    @property
    def today(self) -> str:
        return self.store.today_str()
