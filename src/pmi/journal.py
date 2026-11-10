"""Index outbox (changes) and the human-facing audit log. Call inside the write transaction."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from . import clock


def log_change(conn: sqlite3.Connection, target_type: str, target_id: str, op: str) -> None:
    conn.execute(
        "insert into changes(target_type, target_id, op) values (?,?,?)",
        (target_type, target_id, op),
    )


def log_audit(conn: sqlite3.Connection, actor: Any, action: str, target_type: str,
              target_id: str, detail: dict[str, Any] | None = None) -> None:
    conn.execute(
        "insert into audit_log(at, actor, action, target_type, target_id, detail) values (?,?,?,?,?,?)",
        (clock.now(), str(actor), action, target_type, target_id,
         json.dumps(detail or {}, ensure_ascii=False, sort_keys=True)),
    )
