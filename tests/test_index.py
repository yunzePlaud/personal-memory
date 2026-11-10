import sqlite3

import pytest

from pmi.actors import Actor
from pmi.config import Settings
from pmi.evolution import propose
from pmi.forget import forget_memory
from pmi.index.chunker import chunk_text
from pmi.index.embed import embedder_status, get_embedder
from pmi.index.indexer import Indexer
from tests.helpers import HashEmbedder, seed_source

S = Settings()
OWNER = Actor.owner()


def test_chunk_spans_exact():
    text = "\n\n".join(f"第{i}段：" + "内容" * 120 for i in range(8))
    chunks = chunk_text(text, size=300, overlap=50)
    assert len(chunks) > 3
    for s, e, c in chunks:
        assert text[s:e] == c and len(c) <= 300
    assert chunks[0][0] == 0 and chunks[-1][1] == len(text)
    for (s1, e1, _), (s2, _, _) in zip(chunks, chunks[1:]):
        assert s2 < e1  # overlapping, no gaps


def test_chunk_long_paragraph_prefers_sentence_end():
    text = "这是第一句。" * 100
    for s, e, c in chunk_text(text, size=100, overlap=10)[:-1]:
        assert c.endswith("。")


def test_short_text_single_chunk():
    assert chunk_text("hello", 800, 100) == [(0, 5, "hello")]
    assert chunk_text("", 800, 100) == []


def _memory(truth, content):
    return propose(truth, S, [{"op": "create", "memory": {"kind": "semantic", "content": content,
                   "entities": [{"name": "Project A", "kind": "project"}]}}], actor=OWNER)["results"][0]["memory_id"]


def _rows(paths):
    conn = sqlite3.connect(paths.index_db)
    chunks = sorted(r[0] for r in conn.execute("select text from chunks"))
    mems = sorted(r[0] for r in conn.execute("select memory_id from mem_rows"))
    conn.close()
    return chunks, mems


def test_sync_indexes_and_is_incremental(truth, paths):
    seed_source(truth, "第一份文档")
    seed_source(truth, "第二份文档")
    _memory(truth, "Project A 的目标")
    ix = Indexer(paths, S, None)
    first = ix.sync(truth)
    assert (first["indexed_sources"], first["indexed_memories"]) == (2, 1)
    assert ix.sync(truth) == {"indexed_sources": 0, "indexed_memories": 0, "removed": 0, "vectors_added": 0}
    assert ix.status(truth)["fresh"] is True


def test_old_version_removed_from_index(truth, paths):
    ix = Indexer(paths, S, None)
    seed_source(truth, "旧版本内容", origin_key="doc:x")
    ix.sync(truth)
    seed_source(truth, "新版本内容", origin_key="doc:x")
    ix.sync(truth)
    assert _rows(paths)[0] == ["新版本内容"]


def test_forgotten_removed(truth, paths):
    mid = _memory(truth, "要被遗忘的记忆")
    ix = Indexer(paths, S, None)
    ix.sync(truth)
    forget_memory(truth, mid, actor=OWNER, reason="x")
    ix.sync(truth)
    assert _rows(paths)[1] == []


def test_delete_index_file_rebuilds_same_rows(truth, paths):
    seed_source(truth, "文档一\n\n" + "长内容" * 400)
    _memory(truth, "记忆一")
    Indexer(paths, S, None).sync(truth)
    before = _rows(paths)
    for suffix in ("", "-wal", "-shm"):
        p = paths.index_db.with_name(paths.index_db.name + suffix)
        p.unlink(missing_ok=True)
    Indexer(paths, S, None).sync(truth)
    assert _rows(paths) == before and before[0] and before[1]


def test_vectors_with_hash_embedder(truth, paths):
    seed_source(truth, "向量文档")
    _memory(truth, "向量记忆")
    ix = Indexer(paths, S, HashEmbedder())
    out = ix.sync(truth)
    st = ix.status(truth)
    assert out["vectors_added"] == st["chunks"] + st["memories"] == st["vectors"]
    assert st["missing_vectors"] == 0 and st["embed_model"] == "hash-64"


def test_model_switch_clears_vectors(truth, paths):
    seed_source(truth, "向量文档")
    Indexer(paths, S, HashEmbedder()).sync(truth)
    ix = Indexer(paths, S, HashEmbedder(dim=32))
    ix.sync(truth)
    conn = sqlite3.connect(paths.index_db)
    assert {r[0] for r in conn.execute("select model from vectors")} == {"hash-32"}
    conn.close()


def test_schema_version_mismatch_rebuilds(truth, paths):
    seed_source(truth, "文档")
    Indexer(paths, S, None).sync(truth)
    conn = sqlite3.connect(paths.index_db)
    conn.execute("update meta set value = '0' where key = 'index_schema'")
    conn.commit()
    conn.close()
    ix = Indexer(paths, S, None)
    ix.sync(truth)
    assert _rows(paths)[0] == ["文档"]
    assert ix.status(truth)["fresh"] is True


def test_fastembed_unavailable_without_model(paths):
    assert get_embedder(S, paths) is None
    st = embedder_status(S, paths)
    assert st["available"] is False and st["reason"] and st["provider"] == "fastembed"
    assert embedder_status(Settings(embedding_provider="none"), paths)["reason"] == "disabled in settings"


def test_open_does_not_delete_index_being_created_by_another_process(paths):
    """A search.db without a meta table may be mid-creation by another process: never delete it."""
    paths.index_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(paths.index_db)
    conn.execute("create table other_process_marker(x)")
    conn.commit()
    conn.close()
    Indexer(paths, S, None).open()
    conn = sqlite3.connect(paths.index_db)
    tables = {r[0] for r in conn.execute("select name from sqlite_master")}
    conn.close()
    assert "other_process_marker" in tables and "meta" in tables


def test_corrupted_index_file_is_rebuilt(truth, paths):
    seed_source(truth, "损坏后仍可恢复的文档")
    paths.index_dir.mkdir(parents=True, exist_ok=True)
    paths.index_db.write_bytes(b"this is not a sqlite database" * 100)
    Indexer(paths, S, None).sync(truth)
    assert _rows(paths)[0] == ["损坏后仍可恢复的文档"]
