"""Pending proposals: changes that need the owner's review before they take effect."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from . import clock, ids
from .actors import Actor
from .config import Settings
from .db import write_tx
from .errors import ConflictError, NotFoundError, PmiError, PolicyDenied, ValidationError
from .journal import log_audit


def create_proposal(conn: sqlite3.Connection, ops: list[dict[str, Any]], *, actor: Actor,
                    reason: str | None, risk: str) -> str:
    pid = ids.new_id("prp")
    conn.execute(
        "insert into proposals(id, actor, ops, reason, risk, status, created_at) values (?,?,?,?,?,'pending',?)",
        (pid, str(actor), json.dumps(ops, ensure_ascii=False, sort_keys=True), reason, risk, clock.now()),
    )
    log_audit(conn, actor, "proposal.create", "proposal", pid, {"risk": risk})
    return pid


def _row(r: sqlite3.Row) -> dict[str, Any]:
    return {"id": r["id"], "actor": r["actor"], "ops": json.loads(r["ops"]), "reason": r["reason"],
            "risk": r["risk"], "status": r["status"], "result": json.loads(r["result"]) if r["result"] else None,
            "created_at": r["created_at"], "resolved_at": r["resolved_at"], "resolved_by": r["resolved_by"]}


def _memory_preview(conn: sqlite3.Connection, memory_id: str) -> dict[str, Any]:
    from .memories import get_memory

    m = get_memory(conn, memory_id)
    return {"id": m["id"], "content": m["content"], "status": m["status"], "verification": m["verification"],
            "evidence_trust": m["evidence_trust"],
            "citations": [{"title": e["title"], "trust": e["trust"], "quote": e["quote"]} for e in m["evidence"]]}


def _input_preview(conn: sqlite3.Connection, memory: dict[str, Any]) -> dict[str, Any]:
    cites = []
    for ev in memory.get("evidence", []):
        src = conn.execute("select title, trust from sources where id = ?", (ev["source_id"],)).fetchone()
        cites.append({"title": src["title"] if src else None, "trust": src["trust"] if src else None,
                      "quote": ev["quote"]})
    return {"content": memory.get("content"), "kind": memory.get("kind"), "slot": memory.get("slot"),
            "citations": cites}


def proposal_preview(conn: sqlite3.Connection, proposal: dict[str, Any]) -> list[dict[str, Any]]:
    """What approving would do, with full content: the owner must see what they confirm."""
    out = []
    for op in proposal["ops"]:
        item: dict[str, Any] = {"op": op.get("op")}
        if op.get("old_ids"):
            item["replaces"] = [_memory_preview(conn, i) for i in op["old_ids"]]
        if op.get("memory_id"):
            item["activates"] = _memory_preview(conn, op["memory_id"])
        if op.get("memory"):
            item["new_memory"] = _input_preview(conn, op["memory"])
        if op.get("id"):
            item["target"] = _memory_preview(conn, op["id"])
        if op.get("changes"):
            item["changes"] = op["changes"]
        out.append(item)
    return out


def get_proposal(conn: sqlite3.Connection, proposal_id: str) -> dict[str, Any]:
    row = conn.execute("select * from proposals where id = ?", (proposal_id,)).fetchone()
    if row is None:
        raise NotFoundError(f"proposal not found: {proposal_id}")
    return _row(row)


def list_proposals(conn: sqlite3.Connection, status: str | None = "pending") -> list[dict[str, Any]]:
    if status:
        rows = conn.execute("select * from proposals where status = ? order by created_at, id", (status,))
    else:
        rows = conn.execute("select * from proposals order by created_at, id")
    return [_row(r) for r in rows]


def _require_owner(actor: Actor, action: str) -> None:
    if not actor.is_owner:
        raise PolicyDenied(f"only the owner can {action}")


def _pending(conn: sqlite3.Connection, proposal_id: str) -> dict[str, Any]:
    p = get_proposal(conn, proposal_id)
    if p["status"] != "pending":
        raise ConflictError(f"proposal {proposal_id} is already {p['status']}")
    return p


def _resolve(conn: sqlite3.Connection, proposal_id: str, status: str, result: dict[str, Any], actor: Actor) -> None:
    conn.execute("update proposals set status = ?, result = ?, resolved_at = ?, resolved_by = ? where id = ?",
                 (status, json.dumps(result, ensure_ascii=False, sort_keys=True), clock.now(), str(actor),
                  proposal_id))
    log_audit(conn, actor, f"proposal.{status}", "proposal", proposal_id,
              {"error": result.get("error")} if status == "failed" else {})


def approve_proposal(conn: sqlite3.Connection, settings: Settings, proposal_id: str, *,
                     actor: Actor) -> dict[str, Any]:
    """Replay the proposal's ops as the owner. If the state moved on, mark it failed and change nothing."""
    from .evolution import propose_in_tx  # evolution imports this module

    _require_owner(actor, "approve proposals")
    with write_tx(conn):
        p = _pending(conn, proposal_id)
        conn.execute("savepoint replay")
        try:
            result = propose_in_tx(conn, settings, p["ops"], actor=actor, reason=f"approved {proposal_id}",
                                   proposal_id=proposal_id)
            conn.execute("release replay")
            status = "applied"
        except PmiError as exc:
            conn.execute("rollback to replay")
            conn.execute("release replay")
            result, status = exc.to_dict(), "failed"
        _resolve(conn, proposal_id, status, result, actor)
    return get_proposal(conn, proposal_id)


def reject_proposal(conn: sqlite3.Connection, proposal_id: str, *, actor: Actor, reason: str) -> dict[str, Any]:
    """Reject; a disputed claim the proposal would have activated is archived (kept as history)."""
    from .memories import get_memory, update_memory

    _require_owner(actor, "reject proposals")
    if not reason or not reason.strip():
        raise ValidationError("a reason is required to reject a proposal")
    with write_tx(conn):
        p = _pending(conn, proposal_id)
        for op in p["ops"]:
            mid = op.get("memory_id")
            if mid and get_memory(conn, mid)["status"] == "disputed":
                update_memory(conn, mid, {"status": "archived", "archived_by": str(actor)}, actor=actor,
                              change_kind="archive", reason=reason, proposal_id=proposal_id)
        _resolve(conn, proposal_id, "rejected", {"reason": reason}, actor)
    return get_proposal(conn, proposal_id)


def confirm_memory(conn: sqlite3.Connection, memory_id: str, *, actor: Actor) -> dict[str, Any]:
    from .memories import get_memory, update_memory

    _require_owner(actor, "confirm memories")
    with write_tx(conn):
        m = get_memory(conn, memory_id)
        if m["status"] == "disputed":
            raise ConflictError("a disputed memory is resolved by approving or rejecting its proposal")
        if m["status"] not in ("active", "superseded"):
            raise ValidationError(f"cannot confirm a memory that is {m['status']}")
        if m["verification"] != "user_confirmed":
            update_memory(conn, memory_id, {"verification": "user_confirmed"}, actor=actor, change_kind="confirm")
    return get_memory(conn, memory_id)
