"""Thin wrapper around the installed Himalaya CLI (v2 syntax).

Everything goes through the `himalaya` executable on PATH. Credentials live in
Himalaya's own config, never in this project.
"""

from __future__ import annotations

import json
import shutil
import subprocess


class HimalayaError(RuntimeError):
    pass


class Himalaya:
    def __init__(self, account: str = "gmail", exe: str = "himalaya", logger=None):
        self.account = account
        self.exe = shutil.which(exe) or exe
        self.logger = logger

    def _base(self) -> list[str]:
        return [self.exe, "--account", self.account]

    def _run(self, args: list[str], input_text: str | None = None, timeout: int = 60,
             json_out: bool = False) -> subprocess.CompletedProcess:
        cmd = [self.exe, "--account", self.account]
        if json_out:
            cmd.append("--json")
        cmd += args
        if self.logger:
            self.logger.debug("himalaya: %s", " ".join(cmd[1:]))
        return subprocess.run(
            cmd,
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    # -- reading ------------------------------------------------------------
    def list_envelopes(self, mailbox: str, page_size: int = 50, page: int = 1) -> list[dict]:
        proc = self._run(
            ["envelope", "list", "-m", mailbox, "-s", str(page_size), "-p", str(page)],
            json_out=True, timeout=90,
        )
        if proc.returncode != 0:
            raise HimalayaError(f"envelope list failed: {proc.stderr.strip()[:200]}")
        try:
            data = json.loads(proc.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise HimalayaError(f"envelope list returned invalid JSON: {exc}") from exc
        return data.get("envelopes", [])

    def search_envelopes(self, mailbox: str, query: str, page_size: int = 50) -> list[dict]:
        proc = self._run(
            ["envelope", "search", "-m", mailbox, "-s", str(page_size), *query.split()],
            json_out=True, timeout=90,
        )
        if proc.returncode != 0:
            raise HimalayaError(f"envelope search failed: {proc.stderr.strip()[:200]}")
        try:
            data = json.loads(proc.stdout or "{}")
        except json.JSONDecodeError:
            return []
        return data.get("envelopes", [])

    def raw_message(self, mailbox: str, message_id: str) -> str:
        proc = self._run(
            ["message", "read", str(message_id), "-m", mailbox, "--raw"],
            json_out=True, timeout=90,
        )
        if proc.returncode != 0:
            # fall back to non-json raw
            proc = self._run(["message", "read", str(message_id), "-m", mailbox, "--raw"], timeout=90)
            if proc.returncode != 0:
                raise HimalayaError(f"message read failed: {proc.stderr.strip()[:200]}")
            return proc.stdout
        try:
            data = json.loads(proc.stdout or "{}")
            return data.get("message", "")
        except json.JSONDecodeError:
            return proc.stdout

    # -- sending ------------------------------------------------------------
    def send_raw(self, raw_message: str, save_mailbox: str = "") -> tuple[bool, str]:
        args = ["message", "send"]
        if save_mailbox:
            args += ["--save", save_mailbox]
        proc = self._run(args, input_text=raw_message, timeout=180)
        if proc.returncode != 0:
            return False, (proc.stderr or proc.stdout or "unknown error").strip()
        return True, ""

    # -- housekeeping -------------------------------------------------------
    def move(self, message_ids: list[str], from_box: str, to_box: str) -> bool:
        if not message_ids:
            return True
        proc = self._run(
            ["message", "move", "-f", from_box, "-t", to_box, *[str(i) for i in message_ids]],
            timeout=120,
        )
        return proc.returncode == 0
