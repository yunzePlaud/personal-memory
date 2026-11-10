"""Package layout. Everything is relative to PMI_HOME."""
from __future__ import annotations

import os
from pathlib import Path

from .errors import ValidationError


class Paths:
    def __init__(self, home: Path | str):
        self.home = Path(home).expanduser().resolve()
        self.data = self.home / "data"
        self.sources = self.data / "sources"
        self.memory_dir = self.data / "memory"
        self.db = self.memory_dir / "pmi.db"
        self.backups = self.memory_dir / "backups"
        self.index_dir = self.data / "index"
        self.index_db = self.index_dir / "search.db"
        self.models = self.data / "models"
        self.config_file = self.home / "config" / "settings.toml"

    def ensure(self) -> None:
        for d in (self.sources, self.memory_dir, self.backups, self.index_dir, self.models):
            d.mkdir(parents=True, exist_ok=True)


def _package_root() -> Path | None:
    root = Path(__file__).resolve().parents[2]
    return root if (root / "pyproject.toml").exists() else None


def resolve_home(explicit: str | Path | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get("PMI_HOME")
    if env:
        return Path(env).expanduser().resolve()
    root = _package_root()
    if root is not None:
        return root
    raise ValidationError("PMI home not found: pass --home or set PMI_HOME")
