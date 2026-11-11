"""Read configured agent log files: list, status, tail and incremental reads.

Only complete lines are returned (up to the last newline), so a line the agent is still writing,
or a multi-byte character split across two writes, never shows up half-way.
"""
from __future__ import annotations

import glob
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO

from ..errors import NotFoundError
from .config import LogSpec

MAX_INCREMENT_BYTES = 1_000_000  # a client this far behind gets a fresh tail instead


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log_files(spec: LogSpec) -> list[Path]:
    matches = (Path(p) for p in glob.glob(os.path.expanduser(spec.path)))
    return sorted((p for p in matches if p.is_file()), key=lambda p: p.name, reverse=True)


def resolve_log_file(spec: LogSpec, file_name: str) -> Path:
    for p in log_files(spec):
        if p.name == file_name:
            return p
    raise NotFoundError(f"log file not found: {file_name!r}")


def log_status(spec: LogSpec, now: float | None = None) -> dict[str, Any]:
    files = log_files(spec)
    if not files:
        return {"state": "missing", "file": None, "modified_at": None, "age_seconds": None}
    mtime = files[0].stat().st_mtime
    age = max(0, int(round((time.time() if now is None else now) - mtime)))
    state = "active" if age <= spec.stale_minutes * 60 else "stale"
    return {"state": state, "file": files[0].name, "modified_at": _iso(mtime), "age_seconds": age}


def _complete_end(f: BinaryIO, size: int, block_size: int) -> int:
    """Byte position just after the last newline (0 when there is none)."""
    pos = size
    while pos > 0:
        step = min(block_size, pos)
        pos -= step
        f.seek(pos)
        i = f.read(step).rfind(b"\n")
        if i >= 0:
            return pos + i + 1
    return 0


def _result(text: bytes, offset: int, st: os.stat_result, reset: bool) -> dict[str, Any]:
    return {"text": text.decode("utf-8", errors="replace"), "offset": offset, "size": st.st_size,
            "modified_at": _iso(st.st_mtime), "reset": reset}


def read_tail(path: Path, max_lines: int = 1000, block_size: int = 65536) -> dict[str, Any]:
    st = path.stat()
    with path.open("rb") as f:
        end = _complete_end(f, st.st_size, block_size)
        chunks: list[bytes] = []
        newlines, pos = 0, end
        while pos > 0 and newlines <= max_lines:
            step = min(block_size, pos)
            pos -= step
            f.seek(pos)
            block = f.read(step)
            chunks.append(block)
            newlines += block.count(b"\n")
    data = b"".join(reversed(chunks))
    lines = data[:-1].split(b"\n")[-max_lines:] if data else []
    return _result(b"\n".join(lines) + b"\n" if lines else b"", end, st, reset=False)


def read_since(path: Path, offset: int) -> dict[str, Any]:
    st = path.stat()
    if offset < 0 or offset > st.st_size or st.st_size - offset > MAX_INCREMENT_BYTES:
        return {**read_tail(path), "reset": True}
    with path.open("rb") as f:
        f.seek(offset)
        data = f.read(st.st_size - offset)
    cut = data.rfind(b"\n")
    if cut < 0:
        return _result(b"", offset, st, reset=False)
    return _result(data[:cut + 1], offset + cut + 1, st, reset=False)
