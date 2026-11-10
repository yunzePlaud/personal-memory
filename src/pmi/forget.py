"""Explicit forgetting (design §7.4): irreversible, owner-only, leaves a content-free tombstone."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Literal

from . import clock
from .actors import Actor
from .db import write_tx
from .errors import ConflictError, NotFoundError, PolicyDenied, ValidationError
from .journal import log_audit, log_change
from .memories import get_memory, update_memory
from .paths import Paths

TOMBSTONE = json.dumps({"forgotten": True})


def _check(actor: Actor, reason: str) -> None:
    if not actor.is_owner:
        raise PolicyDenied("only the owner can forget")
    if not reason or not reason.strip():
        raise ValidationError("a reason is required to forget")


def _scrub_proposals(conn: sqlite3.Connection, ids: list[str], actor: Actor) -> None:
    for pid_row in conn.execute("select id, ops, result, status from proposals").fetchall():
        blob = (pid_row["ops"] or "") + (pid_row["result"] or "")
        if any(i in blob for i in ids):
            status = "rejected" if pid_row["status"] == "pending" else pid_row["status"]
            conn.execute(
                "update proposals set ops = '[]', result = ?, reason = NULL, status = ?,"
                " resolved_at = coalesce(resolved_at, ?), resolved_by = coalesce(resolved_by, ?) where id = ?",
                (json.dumps({"scrubbed": True}), status, clock.now(), str(actor), pid_row["id"]))


def _forget_memory_in_tx(conn: sqlite3.Connection, memory_id: str, actor: Actor, reason: str) -> None:
    row = conn.execute("select version, status from memories where id = ?", (memory_id,)).fetchone()
    if row is None:
        raise NotFoundError(f"memory not found: {memory_id}")
    if row["status"] == "forgotten":
        return
    conn.execute("update memory_versions set snapshot = ?, reason = NULL where memory_id = ?", (TOMBSTONE, memory_id))
    conn.execute(
        "update memories set title = '', content = '', content_key = '', slot = NULL, tags = '[]',"
        " status = 'forgotten', confidence = NULL, valid_from = NULL, valid_until = NULL,"
        " version = version + 1, updated_at = ? where id = ?", (clock.now(), memory_id))
    conn.execute(
        "insert into memory_versions(memory_id, version, snapshot, change_kind, actor, reason, changed_at)"
        " values (?,?,?,'forget',?,NULL,?)", (memory_id, row["version"] + 1, TOMBSTONE, str(actor), clock.now()))
    conn.execute("delete from evidence where memory_id = ?", (memory_id,))
    conn.execute("delete from memory_entities where memory_id = ?", (memory_id,))
    conn.execute("delete from links where from_id = ? or to_id = ?", (memory_id, memory_id))
    _scrub_proposals(conn, [memory_id], actor)
    conn.execute("update audit_log set detail = '{}' where target_id = ?", (memory_id,))
    log_change(conn, "memory", memory_id, "delete")
    log_audit(conn, actor, "memory.forget", "memory", memory_id, {"reason": reason})


def forget_memory(conn: sqlite3.Connection, memory_id: str, *, actor: Actor, reason: str) -> dict[str, Any]:
    _check(actor, reason)
    with write_tx(conn):
        _forget_memory_in_tx(conn, memory_id, actor, reason)
    return {"forgotten_memories": [memory_id], "forgotten_sources": [], "downgraded_memories": [],
            "detached_evidence": 0}


def _target_sources(conn: sqlite3.Connection, source_id: str, all_versions: bool) -> list[sqlite3.Row]:
    src = conn.execute("select * from sources where id = ?", (source_id,)).fetchone()
    if src is None:
        raise NotFoundError(f"source not found: {source_id}")
    if all_versions and src["origin_key"]:
        rows = conn.execute("select * from sources where origin_key = ? and status = 'active' order by version",
                            (src["origin_key"],)).fetchall()
    else:
        rows = [src] if src["status"] == "active" else []
    return rows


def _affected(conn: sqlite3.Connection, target_ids: list[str]) -> list[dict[str, Any]]:
    marks = ",".join("?" * len(target_ids))
    mids = [r[0] for r in conn.execute(
        f"select distinct e.memory_id from evidence e join memories m on m.id = e.memory_id"
        f" where e.source_id in ({marks}) and m.status != 'forgotten' order by e.memory_id", target_ids)]
    out = []
    for mid in mids:
        others = conn.execute(f"select count(*) from evidence where memory_id = ? and source_id not in ({marks})",
                              (mid, *target_ids)).fetchone()[0]
        title = conn.execute("select title from memories where id = ?", (mid,)).fetchone()[0]
        out.append({"id": mid, "title": title, "only_evidence": others == 0})
    return out


def forget_source(conn: sqlite3.Connection, paths: Paths | None, source_id: str, *, actor: Actor, reason: str,
                  mode: Literal["refuse", "cascade", "keep_memories"] = "refuse",
                  all_versions: bool = True) -> dict[str, Any]:
    _check(actor, reason)
    if mode not in ("refuse", "cascade", "keep_memories"):
        raise ValidationError(f"invalid mode: {mode!r}")
    result: dict[str, Any] = {"forgotten_sources": [], "forgotten_memories": [], "downgraded_memories": [],
                              "detached_evidence": 0}
    files: list[tuple[str, str]] = []
    with write_tx(conn):
        targets = _target_sources(conn, source_id, all_versions)
        target_ids = [t["id"] for t in targets]
        if not target_ids:
            return result
        affected = _affected(conn, target_ids)
        if affected and mode == "refuse":
            raise ConflictError("memories depend on this source; choose cascade or keep_memories",
                                {"affected_memories": affected})
        marks = ",".join("?" * len(target_ids))
        for a in affected:
            if a["only_evidence"] and mode == "cascade":
                _forget_memory_in_tx(conn, a["id"], actor, reason)
                result["forgotten_memories"].append(a["id"])
                continue
            cur = conn.execute(f"delete from evidence where memory_id = ? and source_id in ({marks})",
                               (a["id"], *target_ids))
            result["detached_evidence"] += cur.rowcount
            changes: dict[str, Any] = {}
            if a["only_evidence"]:
                tags = get_memory(conn, a["id"])["tags"]
                changes = {"verification": "inferred", "tags": [*tags, "evidence-forgotten"]}
                result["downgraded_memories"].append(a["id"])
            update_memory(conn, a["id"], changes, actor=actor, change_kind="update",
                          reason="evidence source forgotten")
        for t in targets:
            if t["file_path"]:
                files.append((t["file_path"], t["file_hash"]))
            conn.execute(
                "update sources set text = '', title = '', text_hash = '', metadata = '{}', origin_key = NULL,"
                " file_path = NULL, file_hash = NULL, status = 'forgotten', is_latest = 0 where id = ?", (t["id"],))
            conn.execute("update audit_log set detail = '{}' where target_id = ?", (t["id"],))
            log_change(conn, "source", t["id"], "delete")
            log_audit(conn, actor, "source.forget", "source", t["id"], {"reason": reason})
            result["forgotten_sources"].append(t["id"])
        _scrub_proposals(conn, target_ids, actor)
    if paths is not None:
        for rel, digest in files:
            still_used = conn.execute("select 1 from sources where file_hash = ? and status = 'active' limit 1",
                                      (digest,)).fetchone()
            if not still_used:
                (paths.data / Path(rel)).unlink(missing_ok=True)
    return result
