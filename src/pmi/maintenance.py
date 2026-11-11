"""Integrity verification, backup/restore, vendor-neutral export and environment checks."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import sqlite3
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from . import clock
from .config import Settings, load_settings
from .db import SCHEMA_VERSION, connect, open_truth
from .errors import ConflictError, ValidationError
from .index.embed import embedder_status
from .index.indexer import Indexer
from .paths import Paths
from .textnorm import collapse_ws

REQUIRED = ["data/memory/pmi.db", "data/sources/"]
REBUILDABLE = ["data/index/search.db", "data/models/", ".venv/"]


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _same_ws(a: str, b: str) -> bool:
    return collapse_ws(a)[0].strip() == collapse_ws(b)[0].strip()


# ---------------------------------------------------------------------------- verify

def _check_sources(conn, paths, err) -> set[str]:
    referenced: set[str] = set()
    for s in conn.execute("select id, text, text_hash, file_path, file_hash from sources where status = 'active'"):
        if hashlib.sha256(s["text"].encode("utf-8")).hexdigest() != s["text_hash"]:
            err("text_hash", "stored text does not match its hash", s["id"])
        if s["file_path"]:
            if s["file_path"].startswith("/") or ":" in s["file_path"]:
                err("relative_path", "file_path must be relative to data/", s["id"])
                continue
            f = paths.data / s["file_path"]
            referenced.add(str(PurePosixPath(s["file_path"])))
            if not f.exists():
                err("file_missing", f"original file missing: {s['file_path']}", s["id"])
            elif _sha256_file(f) != s["file_hash"]:
                err("file_hash", f"original file changed: {s['file_path']}", s["id"])
    return referenced


def _check_memories(conn, err) -> None:
    for m in conn.execute("select id, version, status, title, content from memories"):
        if m["status"] == "forgotten":
            if m["title"] or m["content"]:
                err("forgotten_content", "forgotten memory still holds content", m["id"])
            if conn.execute("select count(*) from memory_versions where memory_id = ? and snapshot != ?",
                            (m["id"], json.dumps({"forgotten": True}))).fetchone()[0]:
                err("forgotten_content", "forgotten memory still has version snapshots", m["id"])
            continue
        n = conn.execute("select count(*) from memory_versions where memory_id = ?", (m["id"],)).fetchone()[0]
        if n != m["version"]:
            err("versions", f"memory has {n} version rows but version={m['version']}", m["id"])
    for e in conn.execute("select e.memory_id, e.source_id, e.quote, e.span_start, e.span_end, s.text, s.status"
                          " from evidence e join sources s on s.id = e.source_id"):
        if e["status"] != "active":
            err("evidence", f"evidence points at a {e['status']} source {e['source_id']}", e["memory_id"])
        elif not _same_ws(e["text"][e["span_start"]:e["span_end"]], e["quote"]):
            err("evidence", f"quote no longer matches source {e['source_id']}", e["memory_id"])
    for r in conn.execute("select slot from memories where status = 'active' and slot is not null"
                          " group by slot having count(*) > 1"):
        err("slot", "more than one active memory in slot", r["slot"])
    for s in conn.execute("select id from sources where status = 'forgotten' and (text != '' or title != '')"):
        err("forgotten_content", "forgotten source still holds content", s["id"])


def verify(conn: sqlite3.Connection, paths: Paths, indexer: Indexer, *, fix: bool = False) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    def err(check: str, message: str, target: str | None = None) -> None:
        errors.append({"check": check, "message": message, "target": target})

    integrity = conn.execute("pragma integrity_check").fetchone()[0]
    if integrity != "ok":
        err("integrity", integrity)
    for row in conn.execute("pragma foreign_key_check"):
        err("foreign_key", f"{row[0]} row {row[1]} references missing {row[2]}")
    version = conn.execute("pragma user_version").fetchone()[0]
    if version != SCHEMA_VERSION:
        err("schema", f"schema v{version}, code expects v{SCHEMA_VERSION}")
    referenced = _check_sources(conn, paths, err)
    _check_memories(conn, err)
    fixed: list[str] = []
    if paths.sources.exists():
        for f in sorted(paths.sources.rglob("*")):
            rel = f.relative_to(paths.data).as_posix()
            if f.is_file() and rel not in referenced:
                if fix:
                    f.unlink()
                    fixed.append(rel)
                else:
                    warnings.append({"check": "orphan_file", "message": "file not referenced by any source "
                                     "(run verify --fix to delete)", "target": rel})
    status = indexer.status(conn)
    if not status["fresh"]:
        warnings.append({"check": "index", "message": "index is behind the truth database (it catches up on use)",
                         "target": None})
    count = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    stats = {"sources": count("select count(*) from sources where status = 'active'"),
             "memories": count("select count(*) from memories where status != 'forgotten'"),
             "entities": count("select count(*) from entities"),
             "proposals_pending": count("select count(*) from proposals where status = 'pending'")}
    return {"ok": not errors, "errors": errors, "warnings": warnings, "fixed": fixed, "required": REQUIRED,
            "rebuildable": REBUILDABLE, "stats": stats}


# ---------------------------------------------------------------------------- backup / restore

def backup(conn: sqlite3.Connection, paths: Paths, out: Path | None = None) -> Path:
    stamp = clock.now().replace("-", "").replace(":", "")
    out = Path(out) if out else paths.backups / f"pmi-backup-{stamp}.tar.gz"
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=paths.backups) as tmp:
        snap = Path(tmp) / "pmi.db"
        dest = sqlite3.connect(str(snap))
        conn.backup(dest)
        dest.close()
        members: dict[str, Path] = {"pmi.db": snap}
        if paths.sources.exists():
            for f in sorted(paths.sources.rglob("*")):
                if f.is_file():
                    members["sources/" + f.relative_to(paths.sources).as_posix()] = f
        if paths.config_file.exists():
            members["config/settings.toml"] = paths.config_file
        manifest = {"created_at": clock.now(), "schema_version": SCHEMA_VERSION,
                    "files": {name: _sha256_file(p) for name, p in members.items()}}
        mpath = Path(tmp) / "MANIFEST.json"
        mpath.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        partial = out.with_name(out.name + ".partial")
        with tarfile.open(partial, "w:gz") as tar:
            for name, p in members.items():
                tar.add(p, arcname=name, recursive=False)
            tar.add(mpath, arcname="MANIFEST.json")
        os.replace(partial, out)
    return out


def _safe_members(tar: tarfile.TarFile) -> list[tarfile.TarInfo]:
    members = tar.getmembers()
    for m in members:
        parts = PurePosixPath(m.name).parts
        if m.name.startswith("/") or ".." in parts or not (m.isfile() or m.isdir()):
            raise ValidationError(f"unsafe archive member: {m.name}")
    return members


def restore(archive: Path, target_home: Path, *, force: bool = False) -> dict[str, Any]:
    target = Paths(target_home)
    if target.db.exists() and not force:
        raise ConflictError(f"{target.home} already has data; pass force to replace it (it is kept as data.bak-*)")
    target.home.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.home, prefix=".restore-") as tmp:
        with tarfile.open(archive, "r:gz") as tar:
            members = _safe_members(tar)
            tar.extractall(tmp, members=members, filter="data")
        root = Path(tmp)
        manifest_path = root / "MANIFEST.json"
        if not manifest_path.exists():
            raise ValidationError("archive has no MANIFEST.json")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for name, digest in manifest["files"].items():
            f = root / name
            if not f.is_file() or _sha256_file(f) != digest:
                raise ValidationError(f"archive file failed its checksum: {name}")
        if force and target.data.exists():
            target.data.rename(target.home / f"data.bak-{clock.now().replace(':', '').replace('-', '')}")
        target.ensure()
        shutil.move(str(root / "pmi.db"), target.db)
        if (root / "sources").exists():
            shutil.rmtree(target.sources)
            shutil.move(str(root / "sources"), target.sources)
        if (root / "config" / "settings.toml").exists() and (force or not target.config_file.exists()):
            target.config_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(root / "config" / "settings.toml"), target.config_file)
    conn = open_truth(target)
    try:
        indexer = Indexer(target, load_settings(target), None)
        report = verify(conn, target, indexer)
        indexer.close()
    finally:
        conn.close()
    return {"restored_to": str(target.home), "verify": report}


# ---------------------------------------------------------------------------- export / doctor

_EXPORT = [("source", "select * from sources order by id"), ("memory", "select * from memories order by id"),
           ("memory_version", "select * from memory_versions order by memory_id, version"),
           ("evidence", "select * from evidence order by id"), ("entity", "select * from entities order by id"),
           ("memory_entity", "select * from memory_entities order by memory_id, entity_id"),
           ("link", "select * from links order by id"), ("proposal", "select * from proposals order by id")]


def export_jsonl(conn: sqlite3.Connection, out: Path) -> dict[str, Any]:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    with out.open("w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "header", "format": "pmi-export", "schema_version": SCHEMA_VERSION,
                            "exported_at": clock.now()}) + "\n")
        for kind, sql in _EXPORT:
            for row in conn.execute(sql):
                f.write(json.dumps({"type": kind, "row": dict(row)}, ensure_ascii=False) + "\n")
                counts[kind] = counts.get(kind, 0) + 1
    return {"path": str(out), "counts": counts}


def doctor(paths: Paths, settings: Settings) -> dict[str, Any]:
    probe = sqlite3.connect(":memory:")
    try:
        probe.execute("create virtual table t using fts5(x)")
        fts5 = True
    except sqlite3.OperationalError:
        fts5 = False
    journal = connect(paths.db).execute("pragma journal_mode").fetchone()[0] if paths.db.exists() else None
    checks = {
        "home": str(paths.home), "python": sys.version.split()[0], "platform": platform.platform(),
        "sqlite_version": sqlite3.sqlite_version, "fts5": fts5, "journal_mode": journal,
        "data_writable": os.access(paths.data, os.W_OK) if paths.data.exists() else False,
        "embedder": embedder_status(settings, paths),
        "model_files": any(paths.models.rglob("*.onnx")) if paths.models.exists() else False,
    }
    ok = fts5 and checks["data_writable"] and (journal in (None, "wal"))
    checks["ui_config"] = _ui_config_status(paths)  # informational: only `pmi ui` depends on it
    return {"ok": ok, "checks": checks}


def _ui_config_status(paths: Paths) -> dict[str, Any]:
    from .ui.config import load_ui_config

    try:
        return {"logs": len(load_ui_config(paths))}
    except ValidationError as exc:
        return {"error": exc.message}
