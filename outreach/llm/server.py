"""Manage a single ``llama-server.exe`` subprocess for the duration of a run.

VRAM is only 4 GB, so exactly one model is loaded at a time. The server is
started, health-polled, used, and always torn down (even on error) so the GPU
is freed for the next phase.
"""

from __future__ import annotations

import os
import socket
import subprocess
import time
from pathlib import Path

import requests

from .. import util


def _nvidia_query(fields: str) -> str:
    try:
        proc = subprocess.run(
            ["nvidia-smi", f"--query-gpu={fields}", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15,
        )
        return (proc.stdout or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def vram_used_mb() -> int:
    out = _nvidia_query("memory.used")
    try:
        return int(out.splitlines()[0])
    except (ValueError, IndexError):
        return -1


def list_llama_processes() -> list[str]:
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-Process llama-server -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id"],
            capture_output=True, text=True, timeout=15,
        )
        return [x for x in (proc.stdout or "").split() if x.strip()]
    except Exception:  # noqa: BLE001
        return []


def free_port(port: int, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return True
            except OSError:
                time.sleep(0.5)
    return False


class LlamaServer:
    def __init__(self, exe: str, model_cfg, log_dir: str | Path, logger=None, dll_dirs=None):
        self.exe = str(exe)
        self.model = model_cfg
        self.name = model_cfg.name.raw if hasattr(model_cfg.name, "raw") else model_cfg.name
        self.path = model_cfg.path.raw if hasattr(model_cfg.path, "raw") else model_cfg.path
        self.port = int(model_cfg.port.raw if hasattr(model_cfg.port, "raw") else model_cfg.port)
        self.flags = list(model_cfg.flags.raw if hasattr(model_cfg.flags, "raw") else model_cfg.flags)
        self.log_dir = Path(log_dir)
        self.logger = logger
        self.dll_dirs = list(dll_dirs or [])
        self.proc: subprocess.Popen | None = None
        self._logfile = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _log(self, level: str, msg: str) -> None:
        if self.logger:
            getattr(self.logger, level, self.logger.info)(msg)

    def start(self, timeout_s: int = 180) -> bool:
        if not Path(self.exe).exists():
            raise FileNotFoundError(f"llama-server not found: {self.exe}")
        if not Path(self.path).exists():
            raise FileNotFoundError(f"model not found: {self.path}")
        if not free_port(self.port, timeout=5):
            raise RuntimeError(f"port {self.port} is not free")

        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._logfile = (self.log_dir / f"llama-{self.name}-{self.port}.log").open("w", encoding="utf-8")
        cmd = [
            self.exe,
            "-m", self.path,
            "--host", "127.0.0.1",
            "--port", str(self.port),
            *self.flags,
        ]
        self._log("info", f"starting llama-server: {self.name} on :{self.port} ({' '.join(cmd[1:])})")
        env = os.environ.copy()
        if self.dll_dirs:
            env["PATH"] = os.pathsep.join([*self.dll_dirs, env.get("PATH", "")])
        self.proc = subprocess.Popen(
            cmd, stdout=self._logfile, stderr=subprocess.STDOUT, env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if self.proc.poll() is not None:
                self._log("error", f"llama-server exited early (code {self.proc.returncode}); see {self._logfile.name}")
                self.stop()
                return False
            try:
                r = requests.get(f"{self.base_url}/health", timeout=2)
                if r.status_code == 200:
                    self._log("info", f"llama-server ready: {self.name}")
                    return True
            except requests.RequestException:
                pass
            time.sleep(1.5)
        self._log("error", f"llama-server did not become ready within {timeout_s}s")
        self.stop()
        return False

    def stop(self) -> None:
        if self.proc is not None:
            try:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait(timeout=10)
            except Exception:  # noqa: BLE001
                pass
            self.proc = None
        if self._logfile is not None:
            try:
                self._logfile.close()
            except Exception:  # noqa: BLE001
                pass
            self._logfile = None
        free_port(self.port, timeout=15)
        self._log("info", f"llama-server stopped: {self.name}")

    def __enter__(self) -> "LlamaServer":
        if not self.start():
            raise RuntimeError(f"Could not start llama-server for {self.name}")
        return self

    def __exit__(self, *exc) -> None:
        self.stop()


def load_one(cfg, which: str, logger=None) -> LlamaServer:
    """Build a LlamaServer for the named model (draft / classify / ab)."""
    model_cfg = cfg.raw.models[which]
    dll_dirs = []
    try:
        dll_dirs = list(cfg.raw.paths.llama_dll_dirs.raw)
    except AttributeError:
        dll_dirs = []
    return LlamaServer(
        cfg.raw.paths.llama_server_exe.raw, model_cfg, cfg.path("log_dir"),
        logger=logger, dll_dirs=dll_dirs,
    )
