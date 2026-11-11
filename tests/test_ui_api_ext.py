import pytest

from pmi import Memory


@pytest.fixture
def mem(home):
    m = Memory.open(home, embedder=None)
    yield m
    m.close()


def make(mem, text, slot=None):
    src = mem.add_source(text=text)["source_id"]
    op = {"op": "create", "memory": {"kind": "semantic", "content": text,
                                     "evidence": [{"source_id": src, "quote": text}]}}
    if slot:
        op["memory"]["slot"] = slot
    return mem.propose([op])["results"][0]["memory_id"]


def test_list_offset_and_slot(mem):
    ids = {make(mem, "第一条记忆内容", "s/1"), make(mem, "第二条记忆内容", "s/2"), make(mem, "第三条记忆内容")}
    p1 = [i["id"] for i in mem.list("memories", limit=2)["items"]]
    p2 = [i["id"] for i in mem.list("memories", limit=2, offset=2)["items"]]
    assert len(p1) == 2 and len(p2) == 1 and set(p1) | set(p2) == ids
    assert [i["slot"] for i in mem.list("memories", slot="s/1")["items"]] == ["s/1"]
    assert len(mem.list("memories", offset=-5)["items"]) == 3


def test_counts(mem):
    a = make(mem, "第一条记忆内容")
    make(mem, "第二条记忆内容")
    mem.propose([{"op": "archive", "id": a}])
    c = mem.counts()
    assert c["memories"] == {"active": 1, "superseded": 0, "disputed": 0, "archived": 1}
    assert c["pending_proposals"] == 0
