"""Disk usage of each part of a PMI home (read-only)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..paths import Paths


def _file_size(p: Path) -> int:
    try:
        return p.stat().st_size if p.is_file() else 0
    except OSError:
        return 0


def _dir_size(p: Path) -> int:
    """Each file counted once: model caches link snapshots/<rev>/model.onnx to blobs/<sha>."""
    if not p.is_dir():
        return 0
    seen: set[tuple[int, int]] = set()
    total = 0
    for root, _dirs, files in os.walk(p.resolve()):
        for name in files:
            try:
                st = (Path(root) / name).stat()
            except OSError:
                continue
            if (st.st_dev, st.st_ino) not in seen:
                seen.add((st.st_dev, st.st_ino))
                total += st.st_size
    return total


def storage_usage(paths: Paths) -> dict[str, Any]:
    db_files = [paths.db, Path(f"{paths.db}-wal"), Path(f"{paths.db}-shm")]
    parts = [
        ("memory_db", "Memory database", paths.db, sum(_file_size(p) for p in db_files)),
        ("sources", "Sources", paths.sources, _dir_size(paths.sources)),
        ("index", "Search index", paths.index_dir, _dir_size(paths.index_dir)),
        ("models", "Embedding model", paths.models, _dir_size(paths.models)),
        ("backups", "Backups", paths.backups, _dir_size(paths.backups)),
    ]
    items = [{"key": key, "label": label, "path": path.relative_to(paths.home).as_posix(), "bytes": size}
             for key, label, path, size in parts]
    return {"home": str(paths.home), "items": items, "total_bytes": sum(i["bytes"] for i in items)}
