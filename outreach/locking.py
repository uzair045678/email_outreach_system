"""Per-command lock files so two runs of the same command never overlap."""

from __future__ import annotations

from pathlib import Path

from filelock import FileLock, Timeout


class CommandLock:
    """Context manager wrapping a FileLock for one command."""

    def __init__(self, lock_dir: str | Path, command: str):
        self.path = Path(lock_dir) / f"{command}.lock"
        self.command = command
        self._lock: FileLock | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = FileLock(str(self.path))
        try:
            self._lock.acquire(timeout=0)
            return True
        except Timeout:
            return False

    def release(self) -> None:
        if self._lock is not None:
            try:
                self._lock.release()
            except Exception:  # noqa: BLE001
                pass
            self._lock = None

    def __enter__(self) -> "CommandLock":
        if not self.acquire():
            raise RuntimeError(f"Another '{self.command}' run is already active.")
        return self

    def __exit__(self, *exc) -> None:
        self.release()
