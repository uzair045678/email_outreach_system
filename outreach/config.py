"""Configuration loading: config.yaml, .env, profile.yaml.

Everything is exposed through attribute-style namespaces so callers can write
``cfg.sending.max_sends_per_day``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


class NS:
    """Recursive attribute-style wrapper around dict/list/scalar values."""

    def __init__(self, data: Any):
        self._data = data

    def __getattr__(self, item: str) -> Any:
        data = object.__getattribute__(self, "_data")
        if isinstance(data, dict):
            if item not in data:
                raise AttributeError(item)
            return NS(data[item])
        raise AttributeError(item)

    def __getitem__(self, item: Any) -> Any:
        return NS(self._data[item])

    def __contains__(self, item: Any) -> bool:
        return isinstance(self._data, dict) and item in self._data

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def get(self, item: str, default: Any = None) -> Any:
        data = object.__getattribute__(self, "_data")
        if isinstance(data, dict):
            return NS(data[item]) if item in data else default
        return default

    @property
    def raw(self) -> Any:
        return self._data

    def __repr__(self) -> str:  # pragma: no cover - debug only
        return f"NS({self._data!r})"


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


class Config:
    """Top-level configuration holder."""

    def __init__(self, project_dir: Path):
        self.project_dir = project_dir
        self.config_path = project_dir / "config.yaml"
        self.env_path = project_dir / ".env"
        self.profile_path = project_dir / "profile.yaml"

        load_dotenv(self.env_path, override=False)
        self._raw = _read_yaml(self.config_path)
        self._profile_raw = _read_yaml(self.profile_path)

    # -- namespaces ---------------------------------------------------------
    @property
    def raw(self) -> NS:
        return NS(self._raw)

    @property
    def profile(self) -> NS:
        return NS(self._profile_raw)

    def profile_raw(self) -> dict:
        return dict(self._profile_raw)

    # -- helpers ------------------------------------------------------------
    def env(self, key: str, default: str = "") -> str:
        return os.environ.get(key, default) or ""

    def path(self, *keys: str) -> Path:
        """Return an absolute Path from config ``paths`` (keys are dotted)."""
        node: Any = self._raw.get("paths", {})
        for key in keys:
            node = node[key]
        return Path(node)

    @property
    def profiles_required_fields(self) -> list[str]:
        return [
            "full_name",
            "email",
            "years_experience",
            "github_url",
            "linkedin_url",
            "portfolio_url",
            "cv_pdf_path",
        ]

    def profile_todos(self) -> list[str]:
        """Required profile fields still marked TODO/empty."""
        missing = []
        for field in self.profiles_required_fields:
            value = self._profile_raw.get(field)
            if value is None or value == "" or value == "TODO" or (
                isinstance(value, str) and value.strip().upper() == "TODO"
            ):
                missing.append(field)
        if not self._profile_raw.get("projects"):
            missing.append("projects")
        return missing


def _default_project_dir() -> Path:
    # The project root is always the parent of the package: robust to any drive.
    return Path(__file__).resolve().parent.parent


def load_config(project_dir: str | os.PathLike | None = None) -> Config:
    if project_dir is None:
        project_dir = os.environ.get("OUTREACH_HOME") or _default_project_dir()
    return Config(Path(project_dir))
