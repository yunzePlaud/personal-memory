"""UTC timestamps in the canonical form YYYY-MM-DDTHH:MM:SSZ."""
from __future__ import annotations

import re
from datetime import date, datetime, timezone

from .errors import ValidationError

_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_LOOSE_DATE = re.compile(r"^(\d{4})\s*[年/.]\s*(\d{1,2})\s*[月/.]\s*(\d{1,2})\s*日?")


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_time(value: str | date | datetime | None) -> str | None:
    """Normalise a date or datetime to UTC 'YYYY-MM-DDTHH:MM:SSZ'. Naive datetimes are UTC."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day)
    else:
        text = str(value).strip()
        if not text:
            return None
        loose = _LOOSE_DATE.match(text)
        if loose:  # 2026年10月1日, 2026/10/1, 2026.10.01
            y, m, d = (int(g) for g in loose.groups())
            text = f"{y:04d}-{m:02d}-{d:02d}" + text[loose.end():]
        if _DATE_ONLY.match(text):
            text += "T00:00:00"
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValidationError(f"invalid date/time: {value!r}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
