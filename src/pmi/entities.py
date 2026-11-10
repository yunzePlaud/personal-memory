"""Entities (projects, people, ...) resolved by normalised name or alias.

Like the other store primitives, these functions do not open transactions: callers that need
atomicity wrap them in db.write_tx.
"""
from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from typing import Any

from . import clock, ids
from .actors import Actor
from .errors import NotFoundError, ValidationError
from .journal import log_audit

ENTITY_KINDS = ("project", "person", "org", "topic", "system", "other")
_WS = re.compile(r"\s+")
_ASCII_WORDISH = re.compile(r"^[a-z0-9 ._-]+$")


def norm_name(name: str) -> str:
    return _WS.sub(" ", unicodedata.normalize("NFKC", name).casefold()).strip()


def _row(r: sqlite3.Row) -> dict[str, Any]:
    return {"id": r["id"], "kind": r["kind"], "name": r["name"], "aliases": json.loads(r["aliases"]),
            "note": r["note"], "created_at": r["created_at"]}


def _lookup(conn: sqlite3.Connection, norm: str, kind: str) -> sqlite3.Row | None:
    row = conn.execute("select * from entities where kind = ? and norm_name = ?", (kind, norm)).fetchone()
    if row is not None:
        return row
    for r in conn.execute("select * from entities where kind = ? and aliases != '[]'", (kind,)):
        if norm in (norm_name(a) for a in json.loads(r["aliases"])):
            return r
    return None


def resolve_entity(conn: sqlite3.Connection, name: str, kind: str, *, actor: Actor | None = None,
                   create: bool = True) -> dict[str, Any] | None:
    if kind not in ENTITY_KINDS:
        raise ValidationError(f"invalid entity kind: {kind!r}", {"allowed": list(ENTITY_KINDS)})
    norm = norm_name(name)
    if not norm:
        raise ValidationError("entity name is empty")
    row = _lookup(conn, norm, kind)
    if row is not None:
        return _row(row)
    if not create:
        return None
    if actor is None:
        raise ValueError("actor is required to create an entity")
    entity_id = ids.new_id("ent")
    conn.execute(
        "insert into entities(id, kind, name, norm_name, aliases, created_at) values (?,?,?,?,'[]',?)",
        (entity_id, kind, _WS.sub(" ", name).strip(), norm, clock.now()),
    )
    log_audit(conn, actor, "entity.create", "entity", entity_id, {"kind": kind, "name": name})
    return get_entity(conn, entity_id)


def add_alias(conn: sqlite3.Connection, entity_id: str, alias: str, *, actor: Actor) -> dict[str, Any]:
    entity = get_entity(conn, entity_id)
    norm = norm_name(alias)
    if not norm:
        raise ValidationError("alias is empty")
    other = _lookup(conn, norm, entity["kind"])
    if other is not None and other["id"] != entity_id:
        raise ValidationError(f"alias {alias!r} already names entity {other['id']}")
    if other is not None:
        return entity
    aliases = [*entity["aliases"], alias.strip()]
    conn.execute("update entities set aliases = ? where id = ?",
                 (json.dumps(aliases, ensure_ascii=False), entity_id))
    log_audit(conn, actor, "entity.alias", "entity", entity_id, {"alias": alias})
    return get_entity(conn, entity_id)


def _contains(hay: str, needle: str) -> bool:
    if not _ASCII_WORDISH.match(needle):
        return needle in hay
    for m in re.finditer(re.escape(needle), hay):
        before = hay[m.start() - 1] if m.start() > 0 else " "
        after = hay[m.end()] if m.end() < len(hay) else " "
        if not before.isalnum() and not after.isalnum():
            return True
    return False


def find_entities_in_text(conn: sqlite3.Connection, text: str) -> list[dict[str, Any]]:
    hay = norm_name(text)
    candidates: list[tuple[int, str, sqlite3.Row]] = []
    for r in conn.execute("select * from entities"):
        for n in [r["norm_name"], *(norm_name(a) for a in json.loads(r["aliases"]))]:
            if len(n) >= 2:
                candidates.append((len(n), n, r))
    found: dict[str, dict[str, Any]] = {}
    for _, n, r in sorted(candidates, key=lambda c: -c[0]):
        if r["id"] not in found and _contains(hay, n):
            found[r["id"]] = _row(r)
    return list(found.values())


def get_entity(conn: sqlite3.Connection, entity_id: str) -> dict[str, Any]:
    row = conn.execute("select * from entities where id = ?", (entity_id,)).fetchone()
    if row is None:
        raise NotFoundError(f"entity not found: {entity_id}")
    return _row(row)


def list_entities(conn: sqlite3.Connection, kind: str | None = None) -> list[dict[str, Any]]:
    if kind:
        rows = conn.execute("select * from entities where kind = ? order by name", (kind,))
    else:
        rows = conn.execute("select * from entities order by kind, name")
    return [_row(r) for r in rows]
