import pytest

from pmi.actors import Actor
from pmi.config import Settings
from pmi.entities import resolve_entity
from pmi.evolution import propose
from pmi.index.indexer import Indexer
from pmi.search import build_fts_query, rrf, search_chunks, search_memories
from tests.helpers import HashEmbedder, seed_source

S = Settings()
OWNER = Actor.owner()
SLOT = "project-a/launch-date"


def create(truth, content, *, kind="decision", slot=None, sid=None, quote=None, entities=None):
    m = {"kind": kind, "content": content}
    if slot:
        m["slot"] = slot
    if sid:
        m["evidence"] = [{"source_id": sid, "quote": quote or content}]
    if entities:
        m["entities"] = entities
    return propose(truth, S, [{"op": "create", "memory": m}], actor=OWNER)["results"][0]["memory_id"]


@pytest.fixture
def world(truth, paths):
    nov = seed_source(truth, "10-01：Project A 计划于 11 月 15 日上线。", source_time="2026-10-01")
    dec = seed_source(truth, "10-20：Project A 上线时间调整至 12 月 10 日。", source_time="2026-10-20")
    pa = [{"name": "Project A", "kind": "project"}]
    ids = {
        "nov": create(truth, "Project A 计划于 11 月 15 日上线", slot=SLOT, sid=nov, entities=pa),
        "dec": create(truth, "Project A 上线时间调整至 12 月 10 日", slot=SLOT, sid=dec, entities=pa),
        "cat": create(truth, "猫咪很可爱", kind="preference"),
        "review": create(truth, "方案评审放在周三", kind="procedure"),
    }
    ix = Indexer(paths, S, None)
    ix.sync(truth)
    return ids, ix.open()


def ids_of(hits):
    return [h["memory_id"] for h in hits]


def test_build_fts_query_and_rrf():
    assert build_fts_query([]) is None
    assert build_fts_query(['a"b', "上线"]) == '"a""b" OR "上线"'
    scores = rrf([["x", "y"], ["y"]])
    assert scores["y"] > scores["x"]


def test_two_char_chinese_word_matches(truth, world):
    ids, idx = world
    hits = search_memories(idx, truth, "上线")
    assert ids["dec"] in ids_of(hits) and "fts" in hits[0]["via"]


def test_mixed_query_ranks_best_match_first(truth, world):
    ids, idx = world
    assert ids_of(search_memories(idx, truth, "Project A 上线时间"))[0] == ids["dec"]


def test_superseded_excluded_by_default(truth, world):
    ids, idx = world
    assert ids["nov"] not in ids_of(search_memories(idx, truth, "11 月 15 日上线"))
    assert ids["nov"] in ids_of(search_memories(idx, truth, "11 月 15 日上线", statuses=("superseded",)))


def test_stopword_only_query_returns_empty(truth, world):
    _, idx = world
    assert search_memories(idx, truth, "为什么") == [] and search_memories(idx, truth, "   ") == []


def test_single_char_like_fallback(truth, world):
    ids, idx = world
    hits = search_memories(idx, truth, "猫")
    assert ids_of(hits) == [ids["cat"]] and hits[0]["via"] == ["like"]


def test_filters_kind_status_entity(truth, world):
    ids, idx = world
    pa = resolve_entity(truth, "Project A", "project", create=False)
    assert ids_of(search_memories(idx, truth, "上线", kinds=["procedure"])) == []
    assert ids_of(search_memories(idx, truth, "上线", entity_ids=[pa["id"]])) == [ids["dec"]]
    assert ids_of(search_memories(idx, truth, "上线", time_range=("2026-10-15", None))) == [ids["dec"]]


def test_as_of_filter(truth, world):
    ids, idx = world
    assert ids_of(search_memories(idx, truth, "Project A 上线", as_of="2026-10-15")) == [ids["nov"]]
    assert ids_of(search_memories(idx, truth, "Project A 上线", as_of="2026-11-01")) == [ids["dec"]]


def test_low_coverage_excluded(truth, world):
    ids, idx = world
    assert ids["review"] not in ids_of(search_memories(idx, truth, "方案 预算 成本 排期"))
    assert ids["review"] in ids_of(search_memories(idx, truth, "方案评审"))


def test_vector_recall_with_hash_embedder(truth, paths):
    mid = create(truth, "launch window confirmed", kind="semantic")
    emb = HashEmbedder(aliases={"launch": "上线", "window": "窗口"})
    ix = Indexer(paths, S, emb)
    ix.sync(truth)
    idx = ix.open()
    assert search_memories(idx, truth, "上线窗口") == []
    hits = search_memories(idx, truth, "上线窗口", embedder=emb)
    assert ids_of(hits) == [mid] and hits[0]["via"] == ["vector"] and hits[0]["similarity"] >= 0.35


def test_search_chunks(truth, world):
    _, idx = world
    hits = search_chunks(idx, truth, "12 月 10 日")
    assert hits and "12 月 10 日" in hits[0]["text"]
    text = truth.execute("select text from sources where id=?", (hits[0]["source_id"],)).fetchone()[0]
    assert text[hits[0]["start"]:hits[0]["end"]] == hits[0]["text"]
    assert search_chunks(idx, truth, "调整至 12 月", time_range=(None, "2026-10-05")) == []
    assert search_chunks(idx, truth, "调整至 12 月", time_range=("2026-10-15", None))


def test_natural_question_matches_on_two_terms(truth, world):
    """Bigrams like 在什/么时 inflate the term count of natural questions; two real hits must suffice."""
    ids, idx = world
    hits = search_memories(idx, truth, "Project A 现在什么时候上线", statuses=("superseded",))
    assert ids_of(hits) == [ids["nov"]]


def test_single_shared_generic_term_still_excluded(truth, world):
    _, idx = world
    assert search_memories(idx, truth, "Project Z 的预算是多少") == []
