"""SQLite connections, write transactions and schema migrations."""
from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from importlib import resources
from pathlib import Path
from typing import Iterator

from . import clock
from .errors import SchemaTooNew
from .paths import Paths

SCHEMA_VERSION = 1
WAL_RETRIES = 60


def _ensure_wal(conn: sqlite3.Connection) -> None:
    """Switch to WAL once (it persists in the file). The switch needs an exclusive lock and ignores
    busy_timeout, so concurrent first openers retry with backoff."""
    if conn.execute("pragma journal_mode").fetchone()[0] == "wal":
        return
    for attempt in range(WAL_RETRIES):
        try:
            conn.execute("pragma journal_mode = wal")
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc) or attempt == WAL_RETRIES - 1:
                raise
            time.sleep(min(0.02 * (attempt + 1), 0.25))


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), isolation_level=None, timeout=5.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("pragma busy_timeout = 5000")
    conn.execute("pragma secure_delete = on")  # deleted content is overwritten, not just unlinked
    _ensure_wal(conn)
    conn.execute("pragma foreign_keys = on")
    conn.execute("pragma synchronous = normal")
    return conn


def checkpoint(conn: sqlite3.Connection) -> None:
    """Fold the WAL into the database and truncate it (best effort while other readers are active)."""
    conn.execute("pragma wal_checkpoint(TRUNCATE)")


@contextmanager
def write_tx(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE ... COMMIT; rolls back on any exception. Not re-entrant."""
    conn.execute("begin immediate")
    try:
        yield conn
    except BaseException:
        conn.execute("rollback")
        raise
    conn.execute("commit")


def _migration_sql(version: int) -> str:
    return resources.files("pmi.schema").joinpath(f"v{version}.sql").read_text(encoding="utf-8")


def _too_new(current: int) -> SchemaTooNew:
    return SchemaTooNew(f"database schema v{current} is newer than this code (v{SCHEMA_VERSION}); upgrade PMI",
                        {"db_version": current, "code_version": SCHEMA_VERSION})


def _backup_before_migration(conn: sqlite3.Connection, paths: Paths, current: int) -> None:
    stamp = clock.now().replace(":", "").replace("-", "")
    dest = sqlite3.connect(str(paths.backups / f"pmi-pre-migrate-v{current}-{stamp}.db"))
    try:
        conn.backup(dest)  # consistent copy including WAL content
    finally:
        dest.close()


def open_truth(paths: Paths) -> sqlite3.Connection:
    """Open the truth database, migrating it under the write lock (safe with concurrent openers)."""
    paths.ensure()
    conn = connect(paths.db)
    current = conn.execute("pragma user_version").fetchone()[0]
    if current > SCHEMA_VERSION:
        conn.close()
        raise _too_new(current)
    if current == SCHEMA_VERSION:
        return conn
    if current > 0:
        _backup_before_migration(conn, paths, current)
    conn.execute("begin immediate")
    try:
        current = conn.execute("pragma user_version").fetchone()[0]  # another process may have migrated
        if current > SCHEMA_VERSION:
            raise _too_new(current)
        for version in range(current + 1, SCHEMA_VERSION + 1):
            for statement in _split_sql(_migration_sql(version)):
                conn.execute(statement)
        conn.execute(f"pragma user_version = {SCHEMA_VERSION}")
    except BaseException:
        conn.execute("rollback")
        conn.close()
        raise
    conn.execute("commit")
    return conn


def _split_sql(script: str) -> list[str]:
    """Split a migration script into statements (no triggers/procedures in our schema)."""
    lines = [ln for ln in script.splitlines() if not ln.strip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]
