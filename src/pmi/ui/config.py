"""The [ui] section of settings.toml. Only `pmi ui` (and `pmi doctor`) read it."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from typing import Any

from ..errors import ValidationError
from ..paths import Paths

_LOG_KEYS = {"name", "path", "stale_minutes", "hide_pattern"}


@dataclass(frozen=True)
class LogSpec:
    name: str
    path: str
    stale_minutes: int = 10
    hide_pattern: str | None = None


def _fail(message: str) -> ValidationError:
    return ValidationError(f"settings.toml: {message}")


def _text(entry: dict[str, Any], key: str, where: str, *, required: bool) -> str | None:
    value = entry.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise _fail(f"{where}.{key} must be a non-empty string")
    return value


def _log_spec(entry: Any, where: str) -> LogSpec:
    if not isinstance(entry, dict):
        raise _fail(f"{where} must be a table")
    unknown = sorted(set(entry) - _LOG_KEYS)
    if unknown:
        raise _fail(f"unknown key {where}.{unknown[0]}")
    stale = entry.get("stale_minutes", 10)
    if isinstance(stale, bool) or not isinstance(stale, int) or stale <= 0:
        raise _fail(f"{where}.stale_minutes must be a positive integer")
    return LogSpec(name=_text(entry, "name", where, required=True),
                   path=_text(entry, "path", where, required=True),
                   stale_minutes=stale,
                   hide_pattern=_text(entry, "hide_pattern", where, required=False))


def load_ui_config(paths: Paths) -> tuple[LogSpec, ...]:
    if not paths.config_file.exists():
        return ()
    try:
        raw = tomllib.loads(paths.config_file.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ValidationError(f"invalid settings.toml: {exc}") from exc
    ui = raw.get("ui")
    if ui is None:
        return ()
    if not isinstance(ui, dict):
        raise _fail("ui must be a table")
    unknown = sorted(set(ui) - {"logs"})
    if unknown:
        raise _fail(f"unknown key ui.{unknown[0]}")
    entries = ui.get("logs", [])
    if not isinstance(entries, list):
        raise _fail("ui.logs must be an array of tables ([[ui.logs]])")
    specs = tuple(_log_spec(e, f"ui.logs[{i}]") for i, e in enumerate(entries))
    names = [s.name for s in specs]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise _fail(f"ui.logs names must be unique ({duplicates[0]!r} repeats)")
    return specs
