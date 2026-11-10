"""Hybrid retrieval: FTS5 (CJK bigrams) + optional vectors, fused with reciprocal rank fusion."""
from __future__ import annotations

import sqlite3
from typing import Any, Callable, Iterable

from . import clock
from .index.embed import Embedder
from .textnorm import fts_tokens, is_cjk, query_terms

RECALL_LIMIT = 200
MIN_COVERAGE = 0.34
MIN_MATCHED_TERMS = 2  # natural CJK questions produce junk cross-word bigrams
COMMON_MIN_DOCS = 5    # below this many indexed rows no term is treated as common
COMMON_SHARE = 0.5     # a term in at least this share of rows carries no information


def build_fts_query(terms: list[str]) -> str | None:
    if not terms:
        return None
    return " OR ".join('"' + t.replace('"', '""') + '"' for t in terms)


def rrf(rank_lists: list[list[str]], k: int = 60) -> dict[str, float]:
    scores: dict[str, float] = {}
    for ranked in rank_lists:
        for rank, key in enumerate(ranked):
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
    return scores


def matched_terms(terms: list[str], tokens_text: str) -> int:
    have = set(tokens_text.split())
    return sum(t in have for t in terms)


def coverage(terms: list[str], tokens_text: str) -> float:
    return matched_terms(terms, tokens_text) / len(terms) if terms else 0.0


def relevant(terms: list[str], tokens_text: str, common: set[str] = frozenset(), bonus: int = 0) -> bool:
    """A full-text hit counts when its informative (non-common) matches cover a third of the query,
    or reach two distinct terms; `bonus` adds a match for a link to an entity named in the query."""
    matched = matched_terms([t for t in terms if t not in common], tokens_text)
    return (bool(terms) and matched / len(terms) >= MIN_COVERAGE) or matched + bonus >= MIN_MATCHED_TERMS


def common_terms(index: sqlite3.Connection, terms: list[str], fts_table: str, row_table: str) -> set[str]:
    total = index.execute(f"select count(*) from {row_table}").fetchone()[0]
    if total < COMMON_MIN_DOCS:
        return set()
    out = set()
    for t in terms:
        df = index.execute(f"select count(*) from {fts_table} where {fts_table} match ?",
                           (build_fts_query([t]),)).fetchone()[0]
        if df / total >= COMMON_SHARE:
            out.add(t)
    return out


def _split_terms(query: str) -> tuple[list[str], list[str]]:
    terms = query_terms(query)
    singles = [t for t in terms if len(t) == 1 and is_cjk(t)]
    return [t for t in terms if t not in singles], singles


def _vector_ranking(index: sqlite3.Connection, embedder: Embedder, query: str, target_type: str,
                    min_similarity: float) -> list[tuple[str, float]]:
    import numpy as np

    rows = index.execute("select target_id, vec from vectors where target_type = ? and model = ?",
                         (target_type, embedder.model_id)).fetchall()
    if not rows:
        return []
    qv = np.asarray(embedder.embed([query])[0], dtype=np.float32)
    matrix = np.stack([np.frombuffer(r[1], dtype=np.float32) for r in rows])
    sims = matrix @ qv
    order = np.argsort(-sims)[:RECALL_LIMIT]
    return [(rows[i][0], float(sims[i])) for i in order if sims[i] >= min_similarity]


def _recall(index: sqlite3.Connection, query: str, *, fts_sql: str, like_sql: str, like_column_count: int,
            target_type: str, embedder: Embedder | None, min_similarity: float, common: set[str],
            bonus: Callable[[str, str], int], vector_query: str | None = None
            ) -> tuple[dict[str, float], dict[str, dict[str, Any]]]:
    fts_terms, singles = _split_terms(query)
    info: dict[str, dict[str, Any]] = {}
    lists: list[list[str]] = []
    if fts_terms:
        ranked = []
        for key, tokens in index.execute(fts_sql, (build_fts_query(fts_terms), RECALL_LIMIT)):
            cov = coverage(fts_terms, tokens)
            if relevant(fts_terms, tokens, common, bonus(str(key), tokens)):
                key = str(key)
                ranked.append(key)
                info.setdefault(key, {"via": [], "coverage": 0.0, "similarity": None})
                info[key]["via"].append("fts")
                info[key]["coverage"] = cov
        lists.append(ranked)
    elif singles:
        clause = " and ".join([like_sql] * len(singles))
        params: list[Any] = [f"%{ch}%" for ch in singles for _ in range(like_column_count)]
        ranked = [str(r[0]) for r in index.execute(clause + f" limit {RECALL_LIMIT}", params)]
        for key in ranked:
            info[key] = {"via": ["like"], "coverage": 1.0, "similarity": None}
        lists.append(ranked)
    vq = query if vector_query is None else vector_query
    if embedder is not None and sum(not ch.isspace() for ch in vq) >= 2:
        ranked = []
        try:
            vector_hits = _vector_ranking(index, embedder, vq, target_type, min_similarity)
        except Exception:  # a broken embedder degrades to keyword search
            vector_hits = []
        for key, sim in vector_hits:
            ranked.append(key)
            info.setdefault(key, {"via": [], "coverage": 0.0, "similarity": None})
            info[key]["via"].append("vector")
            info[key]["similarity"] = sim
        lists.append(ranked)
    return rrf(lists), info


def _in(values: Iterable[str]) -> str:
    return ",".join("?" * len(list(values)))


def _norm_range(time_range: tuple[str | None, str | None] | None) -> tuple[str | None, str | None]:
    if not time_range:
        return None, None
    return clock.normalize_time(time_range[0]), clock.normalize_time(time_range[1])


def _in_range(value: str | None, lo: str | None, hi: str | None) -> bool:
    if lo is None and hi is None:
        return True
    if value is None:
        return False
    return (lo is None or value >= lo) and (hi is None or value <= hi)


def search_memories(index: sqlite3.Connection, truth: sqlite3.Connection, query: str, *,
                    kinds: Iterable[str] | None = None, statuses: Iterable[str] = ("active", "disputed"),
                    entity_ids: Iterable[str] | None = None, focus_entity_ids: Iterable[str] | None = None,
                    time_range: tuple[str | None, str | None] | None = None, as_of: str | None = None,
                    limit: int = 20, embedder: Embedder | None = None,
                    min_similarity: float = 0.35, vector_query: str | None = None) -> list[dict[str, Any]]:
    focus = list(focus_entity_ids or [])
    linked_to_focus = {r[0] for r in truth.execute(
        f"select memory_id from memory_entities where entity_id in ({_in(focus)})", focus)} if focus else set()
    scores, info = _recall(
        index, query,
        fts_sql="select m.memory_id, f.tokens from fts_memories f join mem_rows m on m.rowid = f.rowid"
                " where fts_memories match ? order by bm25(fts_memories) limit ?",
        like_sql="select memory_id from mem_rows where text like ?", like_column_count=1,
        target_type="memory", embedder=embedder, min_similarity=min_similarity,
        common=common_terms(index, _split_terms(query)[0], "fts_memories", "mem_rows"),
        bonus=lambda key, _tokens: 1 if key in linked_to_focus else 0, vector_query=vector_query)
    if not scores:
        return []
    ids = list(scores)
    rows = {r["id"]: r for r in truth.execute(
        f"select id, kind, status, valid_from, valid_until from memories where id in ({_in(ids)})", ids)}
    wanted_entities = set(entity_ids or [])
    linked: dict[str, set[str]] = {}
    if wanted_entities:
        for r in truth.execute(f"select memory_id, entity_id from memory_entities where memory_id in ({_in(ids)})", ids):
            linked.setdefault(r[0], set()).add(r[1])
    lo, hi = _norm_range(time_range)
    t = clock.normalize_time(as_of) if as_of else None
    allowed_status = {"active", "superseded", "disputed"} if t else set(statuses)
    kind_set = set(kinds) if kinds else None
    out = []
    for mid in sorted(ids, key=lambda i: -scores[i]):
        row = rows.get(mid)
        if row is None or row["status"] not in allowed_status:
            continue
        if kind_set and row["kind"] not in kind_set:
            continue
        if wanted_entities and not (linked.get(mid, set()) & wanted_entities):
            continue
        if not _in_range(row["valid_from"], lo, hi):
            continue
        if t and not ((row["valid_from"] is None or row["valid_from"] <= t)
                      and (row["valid_until"] is None or row["valid_until"] > t)):
            continue
        if t and row["status"] == "disputed":
            continue
        out.append({"memory_id": mid, "score": scores[mid], **info[mid]})
        if len(out) >= limit:
            break
    return out


def search_chunks(index: sqlite3.Connection, truth: sqlite3.Connection, query: str, *, limit: int = 10,
                  embedder: Embedder | None = None, min_similarity: float = 0.35,
                  time_range: tuple[str | None, str | None] | None = None,
                  focus_names: Iterable[str] | None = None, vector_query: str | None = None) -> list[dict[str, Any]]:
    """Source passages. Terms common among memories or chunks do not count; a passage naming an
    entity from the query earns one match."""
    terms = _split_terms(query)[0]
    name_tokens = [set(fts_tokens(n).split()) for n in focus_names or [] if fts_tokens(n)]
    common = common_terms(index, terms, "fts_chunks", "chunks") | common_terms(index, terms, "fts_memories", "mem_rows")
    scores, info = _recall(
        index, query,
        fts_sql="select f.rowid, f.tokens from fts_chunks f where fts_chunks match ?"
                " order by bm25(fts_chunks) limit ?",
        like_sql="select id from chunks where text like ?", like_column_count=1,
        target_type="chunk", embedder=embedder, min_similarity=min_similarity, common=common,
        bonus=lambda _key, tokens: 1 if any(n <= set(tokens.split()) for n in name_tokens) else 0,
        vector_query=vector_query)
    if not scores:
        return []
    ids = list(scores)
    chunks = {str(r["id"]): r for r in index.execute(
        f"select id, source_id, span_start, span_end, text from chunks where id in ({_in(ids)})", ids)}
    source_ids = list({c["source_id"] for c in chunks.values()})
    sources = {r["id"]: r for r in truth.execute(
        f"select id, status, is_latest, source_time from sources where id in ({_in(source_ids)})", source_ids)}
    lo, hi = _norm_range(time_range)
    out = []
    for cid in sorted(ids, key=lambda i: -scores[i]):
        c = chunks.get(cid)
        src = sources.get(c["source_id"]) if c else None
        if src is None or src["status"] != "active" or not src["is_latest"]:
            continue
        if not _in_range(src["source_time"], lo, hi):
            continue
        out.append({"source_id": c["source_id"], "chunk_id": int(cid), "start": c["span_start"],
                    "end": c["span_end"], "text": c["text"], "score": scores[cid], **info[cid]})
        if len(out) >= limit:
            break
    return out


__all__ = ["build_fts_query", "rrf", "coverage", "search_memories", "search_chunks", "fts_tokens"]
