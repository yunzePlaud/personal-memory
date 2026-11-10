"""Source store: raw evidence with dedupe, versioning and content-addressed original files."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from . import clock, ids
from .actors import Actor
from .config import Settings
from .db import write_tx
from .errors import NotFoundError, PolicyDenied, ValidationError
from .journal import log_audit, log_change
from .parsers import H1_PATTERN, parse_bytes, parse_markdown_text
from .paths import Paths
from .textnorm import find_quote, normalize_text

SOURCE_KINDS = ("document", "note", "conversation", "agent_output")
TRUSTS = ("owner", "agent", "external")
TRUST_RANK = {"external": 0, "agent": 1, "owner": 2}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _default_origin_key(path: Path) -> str:
    resolved = path.expanduser().resolve()
    try:
        return "file:" + resolved.relative_to(Path.home().resolve()).as_posix()
    except ValueError:
        return "file:" + resolved.as_posix()


def _store_file(paths: Paths, data: bytes, file_hash: str, ext: str) -> str:
    rel = Path("sources") / file_hash[:2] / f"{file_hash}{ext}"
    target = paths.data / rel
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + f".tmp{os.getpid()}")
        tmp.write_bytes(data)
        os.replace(tmp, target)
    return rel.as_posix()


def _text_title(text: str, frontmatter: dict[str, Any]) -> str:
    title = str(frontmatter.get("title") or "").strip()
    if title:
        return title
    m = H1_PATTERN.search(text)
    if m:
        return m.group(1).strip()
    first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "untitled")
    return first[:60]


def _prepare(paths: Paths, settings: Settings, text: str | None, path: Path | None) -> dict[str, Any]:
    limit = settings.max_file_mb * 1024 * 1024
    if path is not None:
        path = Path(path)
        if not path.is_file():
            raise ValidationError(f"not a file: {path.name}")
        if path.stat().st_size > limit:
            raise ValidationError(f"file too large (> {settings.max_file_mb} MB): {path.name}")
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ValidationError(f"cannot read file {path.name}: {exc.strerror or type(exc).__name__}") from exc
        parsed = parse_bytes(data, path.name)
        file_hash = _sha256(data)
        return {
            "text": parsed.text, "title": parsed.title, "mime": parsed.mime,
            "frontmatter": parsed.frontmatter, "file_hash": file_hash,
            "file_path": _store_file(paths, data, file_hash, path.suffix.lower()),
            "origin_key": _default_origin_key(path),
        }
    assert text is not None
    if len(text.encode("utf-8")) > limit:
        raise ValidationError(f"text too large (> {settings.max_file_mb} MB)")
    norm = normalize_text(text)
    if not norm:
        raise ValidationError("empty text")
    frontmatter, _ = parse_markdown_text(norm)
    has_md = bool(frontmatter) or bool(H1_PATTERN.search(norm))
    return {
        "text": norm, "title": _text_title(norm, frontmatter),
        "mime": "text/markdown" if has_md else "text/plain", "frontmatter": frontmatter,
        "file_hash": None, "file_path": None, "origin_key": None,
    }


def _source_time(explicit: str | None, frontmatter: dict[str, Any]) -> str | None:
    if explicit:
        return clock.normalize_time(explicit)
    raw = frontmatter.get("date")
    if raw:
        try:
            return clock.normalize_time(str(raw))
        except ValidationError:
            return None
    return None


def _stale_citations(conn: sqlite3.Connection, prev_id: str, new_text: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "select e.memory_id, e.source_id, e.quote from evidence e join memories m on m.id = e.memory_id"
        " where e.source_id = ? and m.status != 'forgotten' order by e.id",
        (prev_id,),
    ).fetchall()
    return [dict(r) for r in rows if find_quote(new_text, r["quote"]) is None]


def add_source(conn: sqlite3.Connection, paths: Paths, settings: Settings, *, actor: Actor,
               text: str | None = None, path: Path | None = None, title: str | None = None,
               kind: str = "document", origin_key: str | None = None, source_time: str | None = None,
               trust: str = "owner", metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    if (text is None) == (path is None):
        raise ValidationError("give exactly one of text or path")
    if kind not in SOURCE_KINDS:
        raise ValidationError(f"invalid source kind: {kind!r}", {"allowed": list(SOURCE_KINDS)})
    if trust not in TRUSTS:
        raise ValidationError(f"invalid trust: {trust!r}", {"allowed": list(TRUSTS)})
    prep = _prepare(paths, settings, text, path)
    key = origin_key or prep["origin_key"]
    meta = dict(metadata or {})
    if prep["frontmatter"]:
        meta["frontmatter"] = prep["frontmatter"]
    text_hash = _sha256(prep["text"].encode("utf-8"))
    final_title = (title or prep["title"]).strip() or "untitled"
    when = _source_time(source_time, prep["frontmatter"])

    with write_tx(conn):
        prev = None
        if key:
            prev = conn.execute(
                "select * from sources where origin_key = ? and is_latest = 1 and status = 'active'",
                (key,),
            ).fetchone()
            if prev is not None and prev["text_hash"] == text_hash:
                return _result(prev["id"], "duplicate", prev["version"], prev["title"], key, [])
            if prev is not None and not actor.is_owner and TRUST_RANK[prev["trust"]] > TRUST_RANK[trust]:
                raise PolicyDenied(
                    f"document {key} was stored with trust '{prev['trust']}'; a '{trust}' source cannot replace it "
                    "(ask the owner to re-import it, or use a different origin_key)")
        else:
            dup = conn.execute(
                "select * from sources where text_hash = ? and status = 'active' order by imported_at limit 1",
                (text_hash,),
            ).fetchone()
            if dup is not None:
                return _result(dup["id"], "duplicate", dup["version"], dup["title"], dup["origin_key"], [])
        source_id = ids.new_id("src")
        version = 1
        if key:
            max_v = conn.execute("select max(version) from sources where origin_key = ?", (key,)).fetchone()[0]
            version = (max_v or 0) + 1
        conn.execute(
            "insert into sources(id, origin_key, version, prev_version_id, is_latest, kind, title, mime,"
            " text, text_hash, file_path, file_hash, source_time, imported_at, trust, metadata, status,"
            " created_by) values (?,?,?,?,1,?,?,?,?,?,?,?,?,?,?,?,'active',?)",
            (source_id, key, version, prev["id"] if prev else None, kind, final_title, prep["mime"],
             prep["text"], text_hash, prep["file_path"], prep["file_hash"], when, clock.now(), trust,
             json.dumps(meta, ensure_ascii=False, sort_keys=True), str(actor)),
        )
        stale: list[dict[str, Any]] = []
        if prev is not None:
            conn.execute("update sources set is_latest = 0 where id = ?", (prev["id"],))
            log_change(conn, "source", prev["id"], "upsert")
            stale = _stale_citations(conn, prev["id"], prep["text"])
        log_change(conn, "source", source_id, "upsert")
        outcome = "new_version" if prev is not None else "created"
        log_audit(conn, actor, f"source.{'new_version' if prev is not None else 'create'}", "source",
                  source_id, {"origin_key": key, "version": version, "kind": kind, "trust": trust})
    return _result(source_id, outcome, version, final_title, key, stale)


def _result(source_id: str, outcome: str, version: int, title: str, key: str | None,
            stale: list[dict[str, Any]]) -> dict[str, Any]:
    return {"source_id": source_id, "outcome": outcome, "version": version, "title": title,
            "origin_key": key, "stale_citations": stale}


def _versions(conn: sqlite3.Connection, row: sqlite3.Row) -> list[str]:
    if not row["origin_key"]:
        return [row["id"]]
    return [r[0] for r in conn.execute(
        "select id from sources where origin_key = ? order by version", (row["origin_key"],))]


def get_source(conn: sqlite3.Connection, source_id: str, *, text_offset: int = 0,
               text_limit: int = 4000) -> dict[str, Any]:
    row = conn.execute("select * from sources where id = ?", (source_id,)).fetchone()
    if row is None:
        raise NotFoundError(f"source not found: {source_id}")
    offset = max(0, int(text_offset))
    limit = max(0, int(text_limit))
    return {
        "id": row["id"], "origin_key": row["origin_key"], "version": row["version"],
        "is_latest": bool(row["is_latest"]), "kind": row["kind"], "title": row["title"],
        "mime": row["mime"], "source_time": row["source_time"], "imported_at": row["imported_at"],
        "trust": row["trust"], "metadata": json.loads(row["metadata"]), "status": row["status"],
        "file_path": row["file_path"], "text": row["text"][offset: offset + limit],
        "text_offset": offset, "text_length": len(row["text"]), "versions": _versions(conn, row),
    }


def list_versions(conn: sqlite3.Connection, origin_key: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "select id, version, is_latest, status, imported_at, text_hash from sources"
        " where origin_key = ? order by version", (origin_key,),
    ).fetchall()
    return [{**dict(r), "is_latest": bool(r["is_latest"])} for r in rows]
