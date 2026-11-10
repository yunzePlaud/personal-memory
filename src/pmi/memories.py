"""Memory store primitives: validated input, evidence resolution, rows, versions and links.

These functions never open transactions; callers wrap them in db.write_tx.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from . import clock, ids
from .actors import Actor
from .entities import ENTITY_KINDS, resolve_entity
from .errors import NotFoundError, ValidationError
from .journal import log_audit, log_change
from .sources import TRUST_RANK
from .textnorm import content_key, find_quote, normalize_slot

MEMORY_KINDS = ("episodic", "semantic", "project", "person", "decision", "procedure", "preference")
MEMORY_STATUSES = ("active", "superseded", "disputed", "archived", "forgotten")
VERIFICATIONS = ("user_confirmed", "source_backed", "inferred")
ENTITY_ROLES = ("subject", "owner", "participant", "mentioned")
LINK_TYPES = ("supersedes", "contradicts", "supports", "relates_to", "part_of")

_INPUT_KEYS = {"kind", "content", "title", "slot", "tags", "entities", "valid_from", "valid_until",
               "confidence", "evidence", "verification"}
_UPDATABLE = {"title", "content", "slot", "tags", "valid_from", "valid_until", "confidence", "status",
              "verification", "superseded_by", "archived_by"}
MAX_CONTENT = 4000
MAX_QUOTE = 2000
MIN_QUOTE_CHARS = 4  # a quote must carry real content
MAX_EVIDENCE = 20


def _title_from(content: str) -> str:
    first = content.strip().splitlines()[0].strip()
    return first[:60]


def validate_entities(raw: Any) -> list[dict[str, str]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValidationError("entities must be a list")
    out = []
    for e in raw:
        if not isinstance(e, dict) or not isinstance(e.get("name"), str) or not e["name"].strip():
            raise ValidationError("each entity needs a non-empty 'name'")
        unknown = set(e) - {"name", "kind", "role"}
        if unknown:
            raise ValidationError(f"unknown entity fields: {sorted(unknown)}")
        kind = e.get("kind", "other")
        role = e.get("role", "subject")
        if kind not in ENTITY_KINDS:
            raise ValidationError(f"invalid entity kind: {kind!r}", {"allowed": list(ENTITY_KINDS)})
        if role not in ENTITY_ROLES:
            raise ValidationError(f"invalid entity role: {role!r}", {"allowed": list(ENTITY_ROLES)})
        out.append({"name": e["name"].strip(), "kind": kind, "role": role})
    return out


def validate_evidence_input(raw: Any) -> list[dict[str, str]]:
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_EVIDENCE:
        raise ValidationError(f"evidence must be a list of at most {MAX_EVIDENCE} items")
    out = []
    for i, ev in enumerate(raw):
        if not isinstance(ev, dict) or set(ev) - {"source_id", "quote"}:
            raise ValidationError("evidence items are {source_id, quote}", {"index": i})
        sid, quote = ev.get("source_id"), ev.get("quote")
        if not isinstance(sid, str) or not isinstance(quote, str) or not quote.strip():
            raise ValidationError("evidence needs source_id and a non-empty quote", {"index": i})
        if sum(not ch.isspace() for ch in quote) < MIN_QUOTE_CHARS:
            raise ValidationError(f"quote must have at least {MIN_QUOTE_CHARS} non-space characters", {"index": i})
        if len(quote) > MAX_QUOTE:
            raise ValidationError(f"quote longer than {MAX_QUOTE} characters", {"index": i})
        out.append({"source_id": sid, "quote": quote})
    return out


def validate_memory_input(d: Any, *, actor: Actor) -> dict[str, Any]:
    if not isinstance(d, dict):
        raise ValidationError("memory must be an object")
    unknown = set(d) - _INPUT_KEYS
    if unknown:
        raise ValidationError(f"unknown memory fields: {sorted(unknown)}")
    kind = d.get("kind")
    if kind not in MEMORY_KINDS:
        raise ValidationError(f"invalid memory kind: {kind!r}", {"allowed": list(MEMORY_KINDS)})
    content = d.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValidationError("content must be a non-empty string")
    content = content.strip()
    if len(content) > MAX_CONTENT:
        raise ValidationError(f"content longer than {MAX_CONTENT} characters")
    title = d.get("title")
    if title is not None and (not isinstance(title, str) or not title.strip()):
        raise ValidationError("title must be a non-empty string")
    slot = d.get("slot")
    if slot is not None and not isinstance(slot, str):
        raise ValidationError("slot must be a string")
    tags = d.get("tags") or []
    if not isinstance(tags, list) or not all(isinstance(t, str) and t.strip() for t in tags):
        raise ValidationError("tags must be a list of non-empty strings")
    confidence = d.get("confidence")
    if confidence is not None and (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
                                   or not 0 <= confidence <= 1):
        raise ValidationError("confidence must be a number between 0 and 1")
    verification = d.get("verification")
    if verification is not None:
        if verification not in VERIFICATIONS:
            raise ValidationError(f"invalid verification: {verification!r}")
        if not actor.is_owner and verification != "inferred":
            raise ValidationError("agents may only set verification='inferred'")
    return {
        "kind": kind, "content": content, "title": (title or _title_from(content)).strip(),
        "slot": normalize_slot(slot) if slot and slot.strip() else None,
        "tags": list(dict.fromkeys(t.strip() for t in tags)),
        "entities": validate_entities(d.get("entities")),
        "valid_from": clock.normalize_time(d.get("valid_from")),
        "valid_until": clock.normalize_time(d.get("valid_until")),
        "confidence": float(confidence) if confidence is not None else None,
        "evidence": validate_evidence_input(d.get("evidence")),
        "verification": verification,
    }


def resolve_evidence(conn: sqlite3.Connection, evidence: list[dict[str, str]]) -> list[dict[str, Any]]:
    out = []
    for i, ev in enumerate(evidence):
        src = conn.execute("select id, text, trust, source_time, status from sources where id = ?",
                           (ev["source_id"],)).fetchone()
        if src is None or src["status"] != "active":
            raise ValidationError(f"evidence source not found: {ev['source_id']}", {"index": i})
        span = find_quote(src["text"], ev["quote"])
        if span is None:
            raise ValidationError(f"quote not found in source {ev['source_id']}", {"index": i})
        out.append({"source_id": src["id"], "quote": ev["quote"].strip(), "span_start": span[0],
                    "span_end": span[1], "trust": src["trust"], "source_time": src["source_time"]})
    return out


def _snapshot(conn: sqlite3.Connection, memory_id: str) -> str:
    row = dict(conn.execute("select * from memories where id = ?", (memory_id,)).fetchone())
    row["entity_ids"] = [r[0] for r in conn.execute(
        "select entity_id from memory_entities where memory_id = ? order by entity_id", (memory_id,))]
    row["evidence"] = [list(r) for r in conn.execute(
        "select source_id, span_start, span_end from evidence where memory_id = ? order by id", (memory_id,))]
    return json.dumps(row, ensure_ascii=False, sort_keys=True)


def _write_version(conn: sqlite3.Connection, memory_id: str, version: int, change_kind: str, actor: Actor,
                   reason: str | None, proposal_id: str | None) -> None:
    conn.execute(
        "insert into memory_versions(memory_id, version, snapshot, change_kind, actor, reason, proposal_id,"
        " changed_at) values (?,?,?,?,?,?,?,?)",
        (memory_id, version, _snapshot(conn, memory_id), change_kind, str(actor), reason, proposal_id,
         clock.now()),
    )


def add_memory_entities(conn: sqlite3.Connection, memory_id: str, entities: list[dict[str, str]], *,
                        actor: Actor) -> None:
    for e in entities:
        ent = resolve_entity(conn, e["name"], e["kind"], actor=actor)
        conn.execute("insert or ignore into memory_entities(memory_id, entity_id, role) values (?,?,?)",
                     (memory_id, ent["id"], e["role"]))


def _insert_evidence(conn: sqlite3.Connection, memory_id: str, resolved: list[dict[str, Any]],
                     actor: Actor) -> int:
    added = 0
    for ev in resolved:
        cur = conn.execute(
            "insert or ignore into evidence(memory_id, source_id, quote, span_start, span_end, added_by,"
            " added_at) values (?,?,?,?,?,?,?)",
            (memory_id, ev["source_id"], ev["quote"], ev["span_start"], ev["span_end"], str(actor),
             clock.now()),
        )
        added += cur.rowcount
    return added


def insert_memory(conn: sqlite3.Connection, data: dict[str, Any], resolved_evidence: list[dict[str, Any]],
                  *, status: str, verification: str, actor: Actor, change_kind: str = "create",
                  reason: str | None = None, proposal_id: str | None = None, memory_id: str | None = None,
                  superseded_by: str | None = None) -> str:
    mid = memory_id or ids.new_id("mem")
    valid_from = data.get("valid_from")
    if valid_from is None:
        times = [ev["source_time"] for ev in resolved_evidence if ev.get("source_time")]
        valid_from = max(times) if times else None
    now = clock.now()
    conn.execute(
        "insert into memories(id, kind, title, content, content_key, slot, tags, status, verification,"
        " confidence, valid_from, valid_until, superseded_by, version, created_by, created_at, updated_at)"
        " values (?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?)",
        (mid, data["kind"], data["title"], data["content"], content_key(data["content"]), data.get("slot"),
         json.dumps(data.get("tags") or [], ensure_ascii=False), status, verification,
         data.get("confidence"), valid_from, data.get("valid_until"), superseded_by, str(actor), now, now),
    )
    add_memory_entities(conn, mid, data.get("entities") or [], actor=actor)
    _insert_evidence(conn, mid, resolved_evidence, actor)
    _write_version(conn, mid, 1, change_kind, actor, reason, proposal_id)
    log_change(conn, "memory", mid, "upsert")
    log_audit(conn, actor, f"memory.{change_kind}", "memory", mid,
              {"status": status, "verification": verification, "slot": data.get("slot"),
               "proposal_id": proposal_id, "reason": reason})
    return mid


def update_memory(conn: sqlite3.Connection, memory_id: str, changes: dict[str, Any], *, actor: Actor,
                  change_kind: str, reason: str | None = None, proposal_id: str | None = None) -> dict[str, Any]:
    unknown = set(changes) - _UPDATABLE
    if unknown:
        raise ValidationError(f"fields cannot be updated: {sorted(unknown)}")
    row = conn.execute("select version from memories where id = ?", (memory_id,)).fetchone()
    if row is None:
        raise NotFoundError(f"memory not found: {memory_id}")
    cols = dict(changes)
    if "content" in cols:
        cols["content_key"] = content_key(cols["content"])
    if "tags" in cols:
        cols["tags"] = json.dumps(cols["tags"], ensure_ascii=False)
    cols["version"] = row["version"] + 1
    cols["updated_at"] = clock.now()
    assignments = ", ".join(f"{k} = ?" for k in cols)
    conn.execute(f"update memories set {assignments} where id = ?", (*cols.values(), memory_id))
    _write_version(conn, memory_id, cols["version"], change_kind, actor, reason, proposal_id)
    log_change(conn, "memory", memory_id, "upsert")
    log_audit(conn, actor, f"memory.{change_kind}", "memory", memory_id,
              {"fields": sorted(changes), "proposal_id": proposal_id, "reason": reason})
    return get_memory(conn, memory_id)


def add_evidence(conn: sqlite3.Connection, memory_id: str, resolved: list[dict[str, Any]], *,
                 actor: Actor) -> int:
    added = _insert_evidence(conn, memory_id, resolved, actor)
    if added:
        log_audit(conn, actor, "memory.evidence_add", "memory", memory_id,
                  {"sources": sorted({ev["source_id"] for ev in resolved})})
    return added


def add_link(conn: sqlite3.Connection, from_id: str, to_id: str, link_type: str, *, actor: Actor) -> None:
    if link_type not in LINK_TYPES:
        raise ValidationError(f"invalid link type: {link_type!r}", {"allowed": list(LINK_TYPES)})
    cur = conn.execute("insert or ignore into links(from_id, to_id, type, actor, created_at) values (?,?,?,?,?)",
                       (from_id, to_id, link_type, str(actor), clock.now()))
    if cur.rowcount:
        log_audit(conn, actor, "link.create", "memory", from_id, {"to": to_id, "type": link_type})


def evidence_trust(conn: sqlite3.Connection, memory_id: str) -> str | None:
    trusts = [r[0] for r in conn.execute(
        "select distinct s.trust from evidence e join sources s on s.id = e.source_id where e.memory_id = ?",
        (memory_id,))]
    return max(trusts, key=TRUST_RANK.__getitem__) if trusts else None


def get_memory(conn: sqlite3.Connection, memory_id: str, *, include_history: bool = False) -> dict[str, Any]:
    row = conn.execute("select * from memories where id = ?", (memory_id,)).fetchone()
    if row is None:
        raise NotFoundError(f"memory not found: {memory_id}")
    out = {k: row[k] for k in ("id", "kind", "title", "content", "slot", "status", "verification",
                               "confidence", "valid_from", "valid_until", "superseded_by", "version",
                               "created_by", "archived_by", "created_at", "updated_at")}
    out["tags"] = json.loads(row["tags"])
    out["entities"] = [dict(r) for r in conn.execute(
        "select e.id, e.name, e.kind, me.role from memory_entities me join entities e on e.id = me.entity_id"
        " where me.memory_id = ? order by e.kind, e.name, me.role", (memory_id,))]
    out["evidence"] = [{
        "source_id": r["source_id"], "title": r["title"], "quote": r["quote"],
        "span": [r["span_start"], r["span_end"]], "trust": r["trust"], "source_time": r["source_time"],
        "version": r["version"], "is_latest": bool(r["is_latest"]),
    } for r in conn.execute(
        "select e.source_id, e.quote, e.span_start, e.span_end, s.title, s.trust, s.source_time, s.version,"
        " s.is_latest from evidence e join sources s on s.id = e.source_id where e.memory_id = ? order by e.id",
        (memory_id,))]
    out["links"] = [dict(r) for r in conn.execute(
        "select type, from_id, to_id from links where from_id = ? or to_id = ? order by id",
        (memory_id, memory_id))]
    out["evidence_trust"] = evidence_trust(conn, memory_id)
    if include_history:
        out["history"] = [{
            "version": r["version"], "change_kind": r["change_kind"], "actor": r["actor"],
            "reason": r["reason"], "proposal_id": r["proposal_id"], "changed_at": r["changed_at"],
            "snapshot": json.loads(r["snapshot"]),
        } for r in conn.execute("select * from memory_versions where memory_id = ? order by version",
                                (memory_id,))]
    return out


def find_active_in_slot(conn: sqlite3.Connection, slot: str) -> dict[str, Any] | None:
    row = conn.execute("select id from memories where slot = ? and status = 'active'", (slot,)).fetchone()
    return get_memory(conn, row["id"]) if row else None


def find_by_content_key(conn: sqlite3.Connection, key: str, statuses: tuple[str, ...]) -> list[dict[str, Any]]:
    marks = ",".join("?" * len(statuses))
    rows = conn.execute(f"select id from memories where content_key = ? and status in ({marks}) order by created_at",
                        (key, *statuses)).fetchall()
    return [get_memory(conn, r["id"]) for r in rows]
