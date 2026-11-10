"""Keeps search.db in step with the truth database by replaying the `changes` outbox."""
from __future__ import annotations

import json
import sqlite3
from array import array
from typing import Any

from ..config import Settings
from ..db import checkpoint, connect, write_tx
from ..paths import Paths
from ..textnorm import fts_tokens
from .chunker import chunk_text
from .embed import Embedder

INDEX_SCHEMA_VERSION = 1
VECTOR_BATCH = 64
_SCHEMA = [
    "create table if not exists meta(key text primary key, value text)",
    "create table if not exists chunks(id integer primary key, source_id text not null, ord integer not null,"
    " span_start integer not null, span_end integer not null, text text not null)",
    "create index if not exists ix_chunks_source on chunks(source_id)",
    "create table if not exists mem_rows(rowid integer primary key, memory_id text unique not null, text text not null)",
    "create virtual table if not exists fts_chunks using fts5(tokens, tokenize='unicode61 remove_diacritics 2')",
    "create virtual table if not exists fts_memories using fts5(tokens, tokenize='unicode61 remove_diacritics 2')",
    "create table if not exists vectors(target_type text not null, target_id text not null, model text not null,"
    " dim integer not null, vec blob not null, primary key(target_type, target_id))",
]


def pack(vec: list[float]) -> bytes:
    return array("f", vec).tobytes()


class Indexer:
    def __init__(self, paths: Paths, settings: Settings, embedder: Embedder | None):
        self.paths = paths
        self.settings = settings
        self.embedder = embedder
        self.last_error: str | None = None  # non-fatal problem from the last sync (e.g. embedding)
        self._conn: sqlite3.Connection | None = None

    # ------------------------------------------------------------------ connection

    def _meta(self, conn: sqlite3.Connection, key: str) -> str | None:
        row = conn.execute("select value from meta where key = ?", (key,)).fetchone()
        return row[0] if row else None

    def _set_meta(self, conn: sqlite3.Connection, key: str, value: Any) -> None:
        conn.execute("insert or replace into meta(key, value) values (?, ?)", (key, str(value)))

    def _delete_files(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            self.paths.index_db.with_name(self.paths.index_db.name + suffix).unlink(missing_ok=True)

    def _existing_version(self) -> str | None:
        """index_schema of an existing file; None if it has no meta yet (maybe mid-creation by another
        process); 'corrupt' if it is not a usable SQLite database. 'locked' errors propagate."""
        try:
            conn = connect(self.paths.index_db)
        except sqlite3.OperationalError:
            raise
        except sqlite3.DatabaseError:
            return "corrupt"
        try:
            return self._meta(conn, "index_schema")
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc):
                raise
            return None
        except sqlite3.DatabaseError:
            return "corrupt"
        finally:
            conn.close()

    def open(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        self.paths.index_dir.mkdir(parents=True, exist_ok=True)
        if self.paths.index_db.exists() and self._existing_version() not in (None, str(INDEX_SCHEMA_VERSION)):
            self._delete_files()  # other schema or corrupt: it is derived data, rebuild it
        conn = connect(self.paths.index_db)
        with write_tx(conn):
            for stmt in _SCHEMA:
                conn.execute(stmt)
            if self._meta(conn, "index_schema") is None:
                self._set_meta(conn, "index_schema", INDEX_SCHEMA_VERSION)
        self._conn = conn
        return conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------ text sync

    def _remove(self, idx: sqlite3.Connection, target_type: str, target_id: str) -> int:
        if target_type == "source":
            ids = [r[0] for r in idx.execute("select id from chunks where source_id = ?", (target_id,))]
            for cid in ids:
                idx.execute("delete from fts_chunks where rowid = ?", (cid,))
                idx.execute("delete from vectors where target_type = 'chunk' and target_id = ?", (str(cid),))
            idx.execute("delete from chunks where source_id = ?", (target_id,))
            return 1 if ids else 0
        row = idx.execute("select rowid from mem_rows where memory_id = ?", (target_id,)).fetchone()
        if row is None:
            return 0
        idx.execute("delete from fts_memories where rowid = ?", (row[0],))
        idx.execute("delete from mem_rows where rowid = ?", (row[0],))
        idx.execute("delete from vectors where target_type = 'memory' and target_id = ?", (target_id,))
        return 1

    def _index_source(self, idx: sqlite3.Connection, truth: sqlite3.Connection, source_id: str) -> bool:
        src = truth.execute("select id, title, text, status, is_latest from sources where id = ?",
                            (source_id,)).fetchone()
        if src is None or src["status"] != "active" or not src["is_latest"]:
            return False
        title_tokens = fts_tokens(src["title"])
        for ord_, (start, end, text) in enumerate(
                chunk_text(src["text"], self.settings.chunk_chars, self.settings.chunk_overlap)):
            cur = idx.execute("insert into chunks(source_id, ord, span_start, span_end, text) values (?,?,?,?,?)",
                              (source_id, ord_, start, end, text))
            idx.execute("insert into fts_chunks(rowid, tokens) values (?, ?)",
                        (cur.lastrowid, f"{title_tokens} {fts_tokens(text)}"))
        return True

    def _memory_text(self, truth: sqlite3.Connection, m: sqlite3.Row) -> str:
        names = []
        for e in truth.execute("select e.name, e.aliases from memory_entities me join entities e"
                               " on e.id = me.entity_id where me.memory_id = ?", (m["id"],)):
            names.append(e["name"])
            names.extend(json.loads(e["aliases"]))
        parts = [m["title"], m["content"], m["slot"] or "", " ".join(names), " ".join(json.loads(m["tags"]))]
        return "\n".join(p for p in parts if p)

    def _index_memory(self, idx: sqlite3.Connection, truth: sqlite3.Connection, memory_id: str) -> bool:
        m = truth.execute("select * from memories where id = ?", (memory_id,)).fetchone()
        if m is None or m["status"] == "forgotten":
            return False
        text = self._memory_text(truth, m)
        cur = idx.execute("insert into mem_rows(memory_id, text) values (?, ?)", (memory_id, text))
        idx.execute("insert into fts_memories(rowid, tokens) values (?, ?)", (cur.lastrowid, fts_tokens(text)))
        return True

    def _all_targets(self, truth: sqlite3.Connection) -> list[tuple[str, str]]:
        sources = [("source", r[0]) for r in truth.execute(
            "select id from sources where status = 'active' and is_latest = 1")]
        memories = [("memory", r[0]) for r in truth.execute("select id from memories where status != 'forgotten'")]
        return sources + memories

    def sync(self, truth: sqlite3.Connection) -> dict[str, int]:
        idx = self.open()
        self.last_error = None
        stats = {"indexed_sources": 0, "indexed_memories": 0, "removed": 0, "vectors_added": 0}
        truth_seq = truth.execute("select coalesce(max(seq), 0) from changes").fetchone()[0]
        with write_tx(idx):
            last = self._meta(idx, "last_seq")
            if last is None:
                for table in ("chunks", "mem_rows", "fts_chunks", "fts_memories", "vectors"):
                    idx.execute(f"delete from {table}")
                targets = self._all_targets(truth)
            elif int(last) < truth_seq:
                rows = truth.execute("select distinct target_type, target_id from changes where seq > ? and seq <= ?",
                                     (int(last), truth_seq)).fetchall()
                targets = [(r[0], r[1]) for r in rows]
            else:
                targets = []
            for target_type, target_id in targets:
                stats["removed"] += self._remove(idx, target_type, target_id)
                if target_type == "source":
                    stats["indexed_sources"] += self._index_source(idx, truth, target_id)
                else:
                    stats["indexed_memories"] += self._index_memory(idx, truth, target_id)
            if last is None:
                stats["removed"] = 0
            if last is None or targets:
                self._set_meta(idx, "last_seq", truth_seq)
        stats["vectors_added"] = self._sync_vectors(idx)
        return stats

    def rebuild(self, truth: sqlite3.Connection) -> dict[str, int]:
        self.close()
        self._delete_files()
        return self.sync(truth)

    def purge(self) -> None:
        """After forgetting: merge FTS segments (dropping deleted entries) and truncate the WAL."""
        idx = self.open()
        with write_tx(idx):
            idx.execute("insert into fts_chunks(fts_chunks) values ('optimize')")
            idx.execute("insert into fts_memories(fts_memories) values ('optimize')")
        checkpoint(idx)

    # ------------------------------------------------------------------ vectors

    def _missing(self, idx: sqlite3.Connection, limit: int) -> list[tuple[str, str, str]]:
        chunks = idx.execute(
            "select 'chunk', cast(c.id as text), c.text from chunks c left join vectors v"
            " on v.target_type = 'chunk' and v.target_id = cast(c.id as text) where v.target_id is null limit ?",
            (limit,)).fetchall()
        mems = idx.execute(
            "select 'memory', m.memory_id, m.text from mem_rows m left join vectors v"
            " on v.target_type = 'memory' and v.target_id = m.memory_id where v.target_id is null limit ?",
            (limit,)).fetchall()
        return [tuple(r) for r in chunks] + [tuple(r) for r in mems]

    def _sync_vectors(self, idx: sqlite3.Connection) -> int:
        if self.embedder is None:
            return 0
        model = self.embedder.model_id
        if self._meta(idx, "embed_model") != model:
            with write_tx(idx):
                idx.execute("delete from vectors")
                self._set_meta(idx, "embed_model", model)
                self._set_meta(idx, "embed_dim", self.embedder.dim)
        added = 0
        while True:
            batch = self._missing(idx, VECTOR_BATCH)
            if not batch:
                return added
            try:
                vecs = self.embedder.embed([b[2] for b in batch])
            except Exception as exc:  # the text index stays usable; vectors catch up on a later sync
                self.last_error = f"embedding failed ({type(exc).__name__}); semantic results may be incomplete"
                return added
            with write_tx(idx):
                for (target_type, target_id, _), vec in zip(batch, vecs):
                    idx.execute("insert or replace into vectors(target_type, target_id, model, dim, vec)"
                                " values (?,?,?,?,?)", (target_type, target_id, model, len(vec), pack(vec)))
            added += len(batch)

    # ------------------------------------------------------------------ status

    def status(self, truth: sqlite3.Connection) -> dict[str, Any]:
        idx = self.open()
        truth_seq = truth.execute("select coalesce(max(seq), 0) from changes").fetchone()[0]
        last = self._meta(idx, "last_seq")
        count = lambda sql: idx.execute(sql).fetchone()[0]  # noqa: E731
        chunks, mems, vectors = (count("select count(*) from chunks"), count("select count(*) from mem_rows"),
                                 count("select count(*) from vectors"))
        model = self._meta(idx, "embed_model")
        return {"last_seq": int(last) if last is not None else None, "truth_seq": truth_seq,
                "fresh": last is not None and int(last) >= truth_seq, "chunks": chunks, "memories": mems,
                "vectors": vectors, "missing_vectors": (chunks + mems - vectors) if model else None,
                "embed_model": model}
