"""Memory evolution engine (design §7.2–7.3): one entry point, `propose`, for every memory change.

All ops in a call are validated first (nothing is written if any is invalid), then applied in one
transaction. Each op either takes effect, becomes a pending proposal, merges as a duplicate, or is
rejected by policy.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Callable

from . import clock, ids
from .actors import Actor
from .config import Settings
from .db import write_tx
from .entities import norm_name
from .errors import PmiError, ValidationError
from .memories import (TRUST_RANK, add_evidence, add_link, add_memory_entities, find_active_in_slot,
                       find_by_content_key, get_memory, insert_memory, resolve_evidence, update_memory,
                       validate_evidence_input, validate_memory_input)
from .policy import decide, is_protected
from .review import create_proposal
from .textnorm import content_key

MAX_OPS = 50


@dataclass
class Prepared:
    op: str
    stored: dict[str, Any]                       # normalised op, as kept in a proposal
    memory: dict[str, Any] | None = None         # validated memory input
    resolved: list[dict[str, Any]] = field(default_factory=list)
    targets: list[dict[str, Any]] = field(default_factory=list)
    existing: dict[str, Any] | None = None       # supersede by memory_id
    extra: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- helpers

def storable_memory(m: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in m.items() if v not in (None, [], {})}


def authorising_trust(resolved: list[dict[str, Any]], targets: list[dict[str, Any]]) -> str | None:
    """Trust a change brings against protected targets: the LOWEST trust among its evidence sources
    that do not already back a target. Re-citing the target's own sources adds nothing, and one
    trusted quote cannot lift an untrusted claim."""
    backing = {ev["source_id"] for t in targets for ev in t.get("evidence", [])}
    fresh = [ev["trust"] for ev in resolved if ev["source_id"] not in backing]
    return min(fresh, key=TRUST_RANK.__getitem__) if fresh else None


def effective_valid_from(m: dict[str, Any], resolved: list[dict[str, Any]]) -> str | None:
    if m.get("valid_from"):
        return m["valid_from"]
    times = [ev["source_time"] for ev in resolved if ev.get("source_time")]
    return max(times) if times else None


def _verification(m: dict[str, Any], resolved: list[dict[str, Any]], actor: Actor,
                  proposal_id: str | None) -> str:
    if proposal_id and actor.is_owner:
        return "user_confirmed"
    if m.get("verification"):
        return m["verification"]
    return "source_backed" if resolved else "inferred"


def _summary(m: dict[str, Any], score: float | None = None) -> dict[str, Any]:
    return {"memory_id": m["id"], "title": m["title"], "status": m["status"], "score": score}


def _result(op: str, outcome: str, reason: str, memory_id: str | None = None, status: str | None = None,
            proposal_id: str | None = None, candidates: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"op": op, "outcome": outcome, "memory_id": memory_id, "proposal_id": proposal_id,
            "status": status, "reason": reason, "candidates": candidates or []}


def _require_keys(op: dict[str, Any], allowed: set[str]) -> None:
    unknown = set(op) - allowed - {"op"}
    if unknown:
        raise ValidationError(f"unknown fields for op '{op['op']}': {sorted(unknown)}")


def _live_memory(conn: sqlite3.Connection, memory_id: Any, statuses: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(memory_id, str):
        raise ValidationError("memory ids must be strings")
    m = get_memory(conn, memory_id)
    if m["status"] not in statuses:
        raise ValidationError(f"memory {memory_id} is {m['status']}; expected one of {list(statuses)}")
    return m


# --------------------------------------------------------------------------- create

def _validate_create(conn: sqlite3.Connection, op: dict[str, Any], actor: Actor) -> Prepared:
    _require_keys(op, {"memory"})
    m = validate_memory_input(op.get("memory"), actor=actor)
    resolved = resolve_evidence(conn, m["evidence"])
    return Prepared("create", {"op": "create", "memory": storable_memory(m)}, memory=m, resolved=resolved)


def _insert(conn, p: Prepared, *, status: str, actor: Actor, reason, proposal_id, change_kind="create",
            memory_id=None, superseded_by=None, valid_until=None) -> str:
    data = dict(p.memory)
    if valid_until is not None:
        data["valid_until"] = valid_until
    return insert_memory(conn, data, p.resolved, status=status,
                         verification=_verification(p.memory, p.resolved, actor, proposal_id), actor=actor,
                         change_kind=change_kind, reason=reason, proposal_id=proposal_id, memory_id=memory_id,
                         superseded_by=superseded_by)


def _do_supersede(conn, olds: list[dict[str, Any]], p: Prepared, *, actor: Actor, reason, proposal_id) -> str:
    """Mark olds superseded by the new memory (inserted) or by p.existing (activated)."""
    if p.existing is not None:
        new_id = p.existing["id"]
        vf = p.existing["valid_from"] or clock.now()
    else:
        new_id = ids.new_id("mem")
        vf = effective_valid_from(p.memory, p.resolved) or clock.now()
    for old in olds:
        update_memory(conn, old["id"], {"status": "superseded", "superseded_by": new_id, "valid_until": vf},
                      actor=actor, change_kind="supersede", reason=reason, proposal_id=proposal_id)
    if p.existing is None:
        _insert(conn, p, status="active", actor=actor, reason=reason, proposal_id=proposal_id, memory_id=new_id)
    else:
        changes: dict[str, Any] = {"status": "active"}
        if proposal_id and actor.is_owner:
            changes["verification"] = "user_confirmed"
        update_memory(conn, new_id, changes, actor=actor, change_kind="supersede", reason=reason,
                      proposal_id=proposal_id)
    for old in olds:
        add_link(conn, new_id, old["id"], "supersedes", actor=actor)
    return new_id


def _entity_key(entities: list[dict[str, Any]]) -> frozenset[tuple[str, str]]:
    return frozenset((e["kind"], norm_name(e["name"])) for e in entities)


def _same_claim(existing: dict[str, Any], m: dict[str, Any]) -> bool:
    """A duplicate must be the same claim about the same thing: same kind, slot and entities."""
    return (existing["kind"] == m["kind"] and existing["slot"] == m["slot"]
            and _entity_key(existing["entities"]) == _entity_key(m["entities"]))


def _backfill(conn, p: Prepared, slot: str, vf: str, *, actor: Actor, reason, proposal_id) -> str:
    """Insert an older claim into the slot's history between its true neighbours by valid_from."""
    chain = [get_memory(conn, r[0]) for r in conn.execute(
        "select id from memories where slot = ? and status in ('active', 'superseded') and valid_from is not null",
        (slot,))]
    successor = min((m for m in chain if m["valid_from"] > vf), key=lambda m: m["valid_from"])
    predecessor = max((m for m in chain if m["valid_from"] < vf), key=lambda m: m["valid_from"], default=None)
    mid = _insert(conn, p, status="superseded", actor=actor, reason=reason, proposal_id=proposal_id,
                  superseded_by=successor["id"], valid_until=successor["valid_from"])
    add_link(conn, successor["id"], mid, "supersedes", actor=actor)
    if predecessor is not None and (predecessor["valid_until"] is None or predecessor["valid_until"] > vf):
        update_memory(conn, predecessor["id"], {"superseded_by": mid, "valid_until": vf}, actor=actor,
                      change_kind="supersede", reason="earlier claim backfilled", proposal_id=proposal_id)
        add_link(conn, mid, predecessor["id"], "supersedes", actor=actor)
    return mid


def _apply_create(conn, settings: Settings, p: Prepared, actor: Actor, reason, proposal_id) -> dict[str, Any]:
    m = p.memory
    key = content_key(m["content"])
    dups = [d for d in find_by_content_key(conn, key, ("active", "disputed")) if _same_claim(d, m)]
    if dups:                                                                    # row 1
        add_evidence(conn, dups[0]["id"], p.resolved, actor=actor)
        return _result("create", "duplicate", "same content as an existing memory; evidence merged",
                       dups[0]["id"], dups[0]["status"])
    candidates = [_summary(a) for a in find_by_content_key(conn, key, ("archived",))]   # row 3
    if not p.resolved:                                                          # row 4
        decision, why = decide("create", actor, targets=[], new_evidence_trust=None, has_evidence=False,
                               settings=settings)
        if decision == "review":
            pid = create_proposal(conn, [p.stored], actor=actor, reason=reason, risk=why)
            return _result("create", "pending", why, proposal_id=pid, candidates=candidates)
        if decision == "deny":
            return _result("create", "rejected", why, candidates=candidates)
    slot = m["slot"]
    current = find_active_in_slot(conn, slot) if slot else None
    if current is None:                                                         # rows 5 and 11
        mid = _insert(conn, p, status="active", actor=actor, reason=reason, proposal_id=proposal_id)
        return _result("create", "applied", "new memory", mid, "active", candidates=candidates)
    vf, cur_vf = effective_valid_from(m, p.resolved), current["valid_from"]
    if vf and cur_vf and vf < cur_vf:                                           # row 6: backfill
        mid = _backfill(conn, p, slot, vf, actor=actor, reason=reason, proposal_id=proposal_id)
        return _result("create", "applied", "older claim recorded as history", mid, "superseded",
                       candidates=candidates)
    if not actor.is_owner:
        if vf is None or cur_vf is None:                                        # row 7: undated
            why = "cannot order an undated claim against the current memory in this slot"
            stored = {"op": "supersede", "old_ids": [current["id"]], "memory": p.stored["memory"]}
            pid = create_proposal(conn, [stored], actor=actor, reason=reason, risk=why)
            return _result("create", "pending", why, proposal_id=pid, candidates=candidates)
        if is_protected(current, authorising_trust(p.resolved, [current])):     # rows 8 and 9
            why = "contradicts a confirmed memory or one backed by more trusted evidence"
            mid = _insert(conn, p, status="disputed", actor=actor, reason=reason, proposal_id=proposal_id,
                          change_kind="dispute")
            add_link(conn, mid, current["id"], "contradicts", actor=actor)
            stored = {"op": "supersede", "old_ids": [current["id"]], "memory_id": mid}
            pid = create_proposal(conn, [stored], actor=actor, reason=reason, risk=why)
            return _result("create", "pending", why, mid, "disputed", pid, candidates)
    mid = _do_supersede(conn, [current], p, actor=actor, reason=reason, proposal_id=proposal_id)  # row 10
    return _result("create", "applied", f"supersedes {current['id']} in slot {slot}", mid, "active",
                   candidates=candidates)


# --------------------------------------------------------------------------- supersede

def _validate_supersede(conn: sqlite3.Connection, op: dict[str, Any], actor: Actor) -> Prepared:
    _require_keys(op, {"old_ids", "memory", "memory_id"})
    old_ids = op.get("old_ids")
    if not isinstance(old_ids, list) or not old_ids or len(set(map(str, old_ids))) != len(old_ids):
        raise ValidationError("old_ids must be a non-empty list of distinct memory ids")
    olds = [_live_memory(conn, i, ("active", "disputed")) for i in old_ids]
    if ("memory" in op) == ("memory_id" in op):
        raise ValidationError("supersede needs exactly one of 'memory' or 'memory_id'")
    if "memory" in op:
        m = validate_memory_input(op["memory"], actor=actor)
        p = Prepared("supersede", {"op": "supersede", "old_ids": list(old_ids), "memory": storable_memory(m)},
                     memory=m, resolved=resolve_evidence(conn, m["evidence"]), targets=olds)
        slot = m["slot"]
    else:
        existing = _live_memory(conn, op["memory_id"], ("active", "disputed"))
        if existing["id"] in old_ids:
            raise ValidationError("a memory cannot supersede itself")
        p = Prepared("supersede", {"op": "supersede", "old_ids": list(old_ids), "memory_id": existing["id"]},
                     targets=olds, existing=existing)
        slot = existing["slot"]
    if slot:
        holder = find_active_in_slot(conn, slot)
        if holder and holder["id"] not in old_ids and holder["id"] != op.get("memory_id"):
            raise ValidationError(f"slot {slot} is held by {holder['id']}; include it in old_ids")
    return p


def _apply_supersede(conn, settings: Settings, p: Prepared, actor: Actor, reason, proposal_id) -> dict[str, Any]:
    if p.existing is not None:
        evidence = [{"source_id": ev["source_id"], "trust": ev["trust"]} for ev in p.existing["evidence"]]
    else:
        evidence = p.resolved
    new_trust, has_ev = authorising_trust(evidence, p.targets), bool(evidence)
    decision, why = decide("supersede", actor, targets=p.targets, new_evidence_trust=new_trust,
                           has_evidence=has_ev, settings=settings)
    if decision == "review":
        pid = create_proposal(conn, [p.stored], actor=actor, reason=reason, risk=why)
        return _result("supersede", "pending", why, p.existing["id"] if p.existing else None,
                       p.existing["status"] if p.existing else None, pid)
    if decision == "deny":
        return _result("supersede", "rejected", why)
    mid = _do_supersede(conn, p.targets, p, actor=actor, reason=reason, proposal_id=proposal_id)
    return _result("supersede", "applied", f"supersedes {', '.join(t['id'] for t in p.targets)}", mid, "active")


# --------------------------------------------------------------------------- update / link / archive / restore

_UPDATE_FIELDS = {"title", "content", "tags", "valid_from", "valid_until", "confidence", "entities"}
_LINKABLE = ("contradicts", "supports", "relates_to", "part_of")


def _validate_update(conn: sqlite3.Connection, op: dict[str, Any], actor: Actor) -> Prepared:
    _require_keys(op, {"id", "changes", "evidence"})
    target = _live_memory(conn, op.get("id"), ("active", "disputed", "superseded"))
    changes = op.get("changes") or {}
    if not isinstance(changes, dict):
        raise ValidationError("changes must be an object")
    unknown = set(changes) - _UPDATE_FIELDS
    if unknown:
        raise ValidationError(f"fields cannot be updated: {sorted(unknown)} (use supersede to change a slot)")
    evidence = validate_evidence_input(op.get("evidence"))
    if not changes and not evidence:
        raise ValidationError("update needs changes or evidence")
    merged = {"kind": target["kind"], "content": changes.get("content", target["content"]),
              "title": changes.get("title", target["title"]), "tags": changes.get("tags", target["tags"]),
              "valid_from": changes.get("valid_from", target["valid_from"]),
              "valid_until": changes.get("valid_until", target["valid_until"]),
              "confidence": changes.get("confidence", target["confidence"]),
              "entities": changes.get("entities")}
    norm = validate_memory_input(merged, actor=Actor.owner())  # field validation only
    fields = {k: norm[k] for k in changes if k != "entities"}
    stored = {"op": "update", "id": target["id"], "changes": changes}
    if evidence:
        stored["evidence"] = evidence
    return Prepared("update", stored, resolved=resolve_evidence(conn, evidence), targets=[target],
                    extra={"fields": fields, "entities": norm["entities"]})


def _apply_update(conn, settings: Settings, p: Prepared, actor: Actor, reason, proposal_id) -> dict[str, Any]:
    target = p.targets[0]
    decision, why = decide("update", actor, targets=p.targets,
                           new_evidence_trust=authorising_trust(p.resolved, p.targets),
                           has_evidence=bool(p.resolved), settings=settings)
    if decision != "apply":
        return _gate(conn, "update", decision, why, p, actor, reason, target)
    if p.extra["fields"] or p.extra["entities"]:
        add_memory_entities(conn, target["id"], p.extra["entities"], actor=actor)
        update_memory(conn, target["id"], p.extra["fields"], actor=actor, change_kind="update",
                      reason=reason, proposal_id=proposal_id)
    if p.resolved:
        add_evidence(conn, target["id"], p.resolved, actor=actor)
    status = get_memory(conn, target["id"])["status"]
    return _result("update", "applied", "updated", target["id"], status)


def _validate_link(conn: sqlite3.Connection, op: dict[str, Any], actor: Actor) -> Prepared:
    _require_keys(op, {"from_id", "to_id", "type"})
    live = ("active", "disputed", "superseded", "archived")
    a = _live_memory(conn, op.get("from_id"), live)
    b = _live_memory(conn, op.get("to_id"), live)
    if a["id"] == b["id"]:
        raise ValidationError("a memory cannot link to itself")
    if op.get("type") not in _LINKABLE:
        raise ValidationError(f"link type must be one of {list(_LINKABLE)} (use supersede for replacement)")
    return Prepared("link", {"op": "link", "from_id": a["id"], "to_id": b["id"], "type": op["type"]},
                    targets=[a, b])


def _apply_link(conn, settings: Settings, p: Prepared, actor: Actor, reason, proposal_id) -> dict[str, Any]:
    decision, why = decide("link", actor, targets=p.targets, new_evidence_trust=None, has_evidence=False,
                           settings=settings)
    if decision != "apply":
        return _gate(conn, "link", decision, why, p, actor, reason, p.targets[0])
    add_link(conn, p.stored["from_id"], p.stored["to_id"], p.stored["type"], actor=actor)
    return _result("link", "applied", "linked", p.stored["from_id"], p.targets[0]["status"])


def _validate_archive(conn: sqlite3.Connection, op: dict[str, Any], actor: Actor) -> Prepared:
    _require_keys(op, {"id"})
    target = _live_memory(conn, op.get("id"), ("active", "disputed", "superseded"))
    return Prepared("archive", {"op": "archive", "id": target["id"]}, targets=[target])


def _apply_archive(conn, settings: Settings, p: Prepared, actor: Actor, reason, proposal_id) -> dict[str, Any]:
    target = p.targets[0]
    decision, why = decide("archive", actor, targets=p.targets, new_evidence_trust=None, has_evidence=False,
                           settings=settings)
    if decision != "apply":
        return _gate(conn, "archive", decision, why, p, actor, reason, target)
    update_memory(conn, target["id"], {"status": "archived", "archived_by": str(actor)}, actor=actor,
                  change_kind="archive", reason=reason, proposal_id=proposal_id)
    return _result("archive", "applied", "archived", target["id"], "archived")


def _status_before_archive(conn: sqlite3.Connection, memory_id: str) -> str:
    rows = conn.execute("select version, change_kind, snapshot from memory_versions where memory_id = ?"
                        " order by version desc", (memory_id,)).fetchall()
    for i, r in enumerate(rows):
        if r["change_kind"] == "archive" and i + 1 < len(rows):
            return json.loads(rows[i + 1]["snapshot"])["status"]
    return "active"


def _validate_restore(conn: sqlite3.Connection, op: dict[str, Any], actor: Actor) -> Prepared:
    _require_keys(op, {"id"})
    target = _live_memory(conn, op.get("id"), ("archived",))
    previous = _status_before_archive(conn, target["id"])
    if previous == "active" and target["slot"]:
        holder = find_active_in_slot(conn, target["slot"])
        if holder is not None:
            raise ValidationError(f"slot {target['slot']} is now held by {holder['id']}; use supersede")
    return Prepared("restore", {"op": "restore", "id": target["id"]}, targets=[target],
                    extra={"previous": previous})


def _apply_restore(conn, settings: Settings, p: Prepared, actor: Actor, reason, proposal_id) -> dict[str, Any]:
    target = p.targets[0]
    decision, why = decide("restore", actor, targets=p.targets, new_evidence_trust=None, has_evidence=False,
                           settings=settings)
    if decision != "apply":
        return _gate(conn, "restore", decision, why, p, actor, reason, target)
    update_memory(conn, target["id"], {"status": p.extra["previous"], "archived_by": None}, actor=actor,
                  change_kind="restore", reason=reason, proposal_id=proposal_id)
    return _result("restore", "applied", "restored", target["id"], p.extra["previous"])


def _gate(conn: sqlite3.Connection, op: str, decision: str, why: str, p: Prepared, actor: Actor, reason, target) -> dict[str, Any]:
    if decision == "deny":
        return _result(op, "rejected", why, target["id"], target["status"])
    pid = create_proposal(conn, [p.stored], actor=actor, reason=reason, risk=why)
    return _result(op, "pending", why, target["id"], target["status"], pid)


# --------------------------------------------------------------------------- dispatcher

Validator = Callable[[sqlite3.Connection, dict[str, Any], Actor], Prepared]
Applier = Callable[..., dict[str, Any]]
OPS: dict[str, tuple[Validator, Applier]] = {
    "create": (_validate_create, _apply_create),
    "supersede": (_validate_supersede, _apply_supersede),
    "update": (_validate_update, _apply_update),
    "link": (_validate_link, _apply_link),
    "archive": (_validate_archive, _apply_archive),
    "restore": (_validate_restore, _apply_restore),
}


def _validate(conn: sqlite3.Connection, op: Any, actor: Actor) -> Prepared:
    if not isinstance(op, dict) or not isinstance(op.get("op"), str):
        raise ValidationError("each op must be an object with an 'op' field")
    if op["op"] not in OPS:
        raise ValidationError(f"unknown op: {op['op']!r}", {"allowed": sorted(OPS)})
    return OPS[op["op"]][0](conn, op, actor)


def propose_in_tx(conn: sqlite3.Connection, settings: Settings, ops: Any, *, actor: Actor,
                  reason: str | None = None, proposal_id: str | None = None) -> dict[str, Any]:
    """Validate then apply ops inside the caller's write transaction."""
    if not isinstance(ops, list) or not ops or len(ops) > MAX_OPS:
        raise ValidationError(f"ops must be a list of 1..{MAX_OPS} operations")
    prepared, errors = [], []
    for i, op in enumerate(ops):
        try:
            prepared.append(_validate(conn, op, actor))
        except PmiError as exc:
            errors.append({"index": i, "message": exc.message})
    if errors:
        raise ValidationError("invalid ops; nothing was written", {"errors": errors})
    results = []
    for i, p in enumerate(prepared):
        try:
            res = OPS[p.op][1](conn, settings, p, actor, reason, proposal_id)
        except sqlite3.IntegrityError as exc:
            raise ValidationError(f"op {i} conflicts with the current state; nothing was written",
                                  {"errors": [{"index": i, "message": str(exc)}]}) from exc
        results.append({"index": i, **res})
    return {"results": results,
            "applied": sum(r["outcome"] == "applied" for r in results),
            "pending": sum(r["outcome"] == "pending" for r in results)}


def propose(conn: sqlite3.Connection, settings: Settings, ops: Any, *, actor: Actor, reason: str | None = None,
            proposal_id: str | None = None) -> dict[str, Any]:
    with write_tx(conn):
        return propose_in_tx(conn, settings, ops, actor=actor, reason=reason, proposal_id=proposal_id)
