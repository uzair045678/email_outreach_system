"""`doctor`: check that every external dependency is in place."""

from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import util

REQUIRED_DEPS = [
    "openpyxl",
    "requests",
    "yaml",
    "dotenv",
    "dns",
    "tzdata",
    "holidays",
    "bs4",
    "trafilatura",
    "tenacity",
    "filelock",
]

SEARCH_KEY_ENVS = ["TAVILY_API_KEY", "BRAVE_API_KEY", "SERPER_API_KEY"]


class Check:
    def __init__(self, name: str, ok: bool, detail: str = "", fatal: bool = True):
        self.name = name
        self.ok = ok
        self.detail = detail
        self.fatal = fatal

    def __repr__(self) -> str:
        mark = "PASS" if self.ok else ("FAIL" if self.fatal else "WARN")
        return f"[{mark}] {self.name}" + (f" - {self.detail}" if self.detail else "")


def _run(cmd: list[str], timeout: int = 20) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace"
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except FileNotFoundError:
        return 127, "not found"
    except subprocess.TimeoutExpired:
        return 124, "timeout"


def run_checks(cfg) -> list[Check]:
    checks: list[Check] = []

    # Python deps
    missing = []
    for dep in REQUIRED_DEPS:
        try:
            importlib.import_module(dep)
        except Exception:  # noqa: BLE001
            missing.append(dep)
    checks.append(
        Check("python dependencies", not missing, ", ".join(missing) or f"{len(REQUIRED_DEPS)} ok")
    )
    checks.append(Check("python version", sys.version_info >= (3, 10), sys.version.split()[0]))

    # Config files
    checks.append(Check("config.yaml", cfg.config_path.exists(), str(cfg.config_path)))
    checks.append(Check(".env", cfg.env_path.exists(), "present" if cfg.env_path.exists() else "missing (copy .env.example)"))

    # Profile
    todos = cfg.profile_todos()
    checks.append(
        Check(
            "profile.yaml required fields",
            not todos,
            "TODO: " + ", ".join(todos) if todos else "complete",
        )
    )

    # Search keys
    present = [k for k in SEARCH_KEY_ENVS if os.environ.get(k)]
    checks.append(
        Check(
            "search provider keys",
            bool(present),
            "found: " + ", ".join(present) if present else "none of " + ", ".join(SEARCH_KEY_ENVS),
        )
    )

    # Fallback keys (non-fatal)
    fb = [k for k in ["GOOGLE_AI_STUDIO_API_KEY", "OPENCODE_ZEN_API_KEY", "OPENROUTER_API_KEY"] if os.environ.get(k)]
    checks.append(Check("fallback LLM keys", bool(fb), ", ".join(fb) or "none set (local-only)", fatal=False))

    # Himalaya
    hima = shutil.which("himalaya")
    checks.append(Check("himalaya on PATH", bool(hima), hima or "not found"))
    if hima:
        rc, out = _run(["himalaya", "--version"])
        checks.append(Check("himalaya runs", rc == 0, out.strip().splitlines()[0] if out else ""))
        rc, out = _run(["himalaya", "account", "list"])
        checks.append(Check("himalaya account configured", rc == 0 and "gmail" in out, "gmail" if rc == 0 else out.strip()[:80]))
        rc, out = _run(["himalaya", "mailbox", "list"])
        boxes_ok = rc == 0 and "Inbox" in out
        checks.append(Check("himalaya mailboxes reachable", boxes_ok, "ok" if boxes_ok else out.strip()[:80]))

    # llama-server
    exe = Path(cfg.raw.paths.llama_server_exe.raw)
    checks.append(Check("llama-server.exe", exe.exists(), str(exe)))

    # Models
    for key in ["draft", "classify"]:
        model = cfg.raw.models[key]
        p = Path(model.path.raw)
        size = f"{p.stat().st_size / 1e9:.2f} GB" if p.exists() else "missing"
        checks.append(Check(f"model:{model.name.raw}", p.exists(), size))

    # GPU
    smi = shutil.which("nvidia-smi")
    if smi:
        rc, out = _run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"])
        checks.append(Check("GPU visible", rc == 0, out.strip().replace("\n", "; ")))
    else:
        checks.append(Check("nvidia-smi", False, "not found", fatal=False))

    # Workbook integrity
    try:
        from openpyxl import load_workbook

        if cfg.path("workbook").exists():
            load_workbook(cfg.path("workbook"))
            checks.append(Check("workbook readable", True, str(cfg.path("workbook"))))
        else:
            checks.append(Check("workbook exists", False, "will be created on first run", fatal=False))
    except Exception as exc:  # noqa: BLE001
        checks.append(Check("workbook readable", False, f"corrupt? {exc}"))

    # Writable project dir
    checks.append(Check("project dir writable", os.access(cfg.project_dir, os.W_OK), str(cfg.project_dir)))

    return checks


def format_report(checks: list[Check]) -> tuple[str, bool]:
    lines = ["", "outreach doctor", "=" * 40]
    failed = False
    for c in checks:
        lines.append(repr(c))
        if not c.ok and c.fatal:
            failed = True
    lines.append("=" * 40)
    lines.append("RESULT: " + ("FAIL - fix the items above" if failed else "PASS"))
    return "\n".join(lines), not failed
