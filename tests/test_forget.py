import pytest

from pmi.actors import Actor
from pmi.config import Settings
from pmi.errors import ConflictError, PolicyDenied, ValidationError
from pmi.evolution import propose
from pmi.forget import forget_memory, forget_source
from pmi.memories import get_memory
from pmi.sources import add_source, get_source
from tests.helpers import seed_source

OWNER, AGENT = Actor.owner(), Actor.agent("t")
S = Settings()


def create(truth, content, sources, actor=AGENT, **kw):
    ev = [{"source_id": sid, "quote": q} for sid, q in sources]
    return propose(truth, S, [{"op": "create", "memory": {"kind": "decision", "content": content,
                                                          "evidence": ev, **kw}}], actor=actor)["results"][0]


def text_dump(truth, tables):
    out = []
    for t in tables:
        for row in truth.execute(f"select * from {t}"):
            out.extend(str(v) for v in tuple(row))
    return "\n".join(out)


MEM_TABLES = ["memories", "memory_versions", "evidence", "proposals", "audit_log", "memory_entities", "links"]


def test_forget_memory_scrubs_everything(truth):
    s = seed_source(truth, "周会：不采用方案 B，原因是成本太高")
    mid = create(truth, "放弃 B 因为太贵", [(s, "不采用方案 B，原因是成本太高")], actor=OWNER,
                 verification="user_confirmed", entities=[{"name": "Project A", "kind": "project"}])["memory_id"]
    pending = propose(truth, S, [{"op": "update", "id": mid, "changes": {"content": "放弃 B 因为太贵（补充）"}}],
                      actor=AGENT)["results"][0]
    assert pending["outcome"] == "pending"
    out = forget_memory(truth, mid, actor=OWNER, reason="敏感")
    assert out["forgotten_memories"] == [mid]
    dump = text_dump(truth, MEM_TABLES)
    assert "放弃 B 因为太贵" not in dump and "不采用方案 B" not in dump
    m = get_memory(truth, mid, include_history=True)
    assert m["status"] == "forgotten" and m["content"] == "" and m["evidence"] == [] and m["entities"] == []
    assert all(h["snapshot"] == {"forgotten": True} for h in m["history"])
    p = truth.execute("select status, ops from proposals").fetchone()
    assert p["status"] == "rejected" and p["ops"] == "[]"
    assert truth.execute("select count(*) from changes where target_id=? and op='delete'", (mid,)).fetchone()[0] == 1


@pytest.fixture
def two_memories(truth):
    s1 = seed_source(truth, "纪要一：上线 12 月；负责人 Alice", origin_key="doc:minutes")
    s2 = seed_source(truth, "纪要二：负责人 Alice")
    only = create(truth, "上线 12 月", [(s1, "上线 12 月")])["memory_id"]
    shared = create(truth, "负责人 Alice", [(s1, "负责人 Alice"), (s2, "负责人 Alice")])["memory_id"]
    return s1, s2, only, shared


def test_forget_source_refuses_when_memories_depend(truth, two_memories):
    s1, _, only, shared = two_memories
    with pytest.raises(ConflictError) as exc:
        forget_source(truth, None, s1, actor=OWNER, reason="x")
    affected = {a["id"]: a["only_evidence"] for a in exc.value.details["affected_memories"]}
    assert affected == {only: True, shared: False}
    assert get_source(truth, s1)["status"] == "active"


def test_forget_source_cascade(truth, two_memories):
    s1, s2, only, shared = two_memories
    out = forget_source(truth, None, s1, actor=OWNER, reason="x", mode="cascade")
    assert out["forgotten_memories"] == [only] and out["forgotten_sources"] == [s1]
    assert get_memory(truth, only)["status"] == "forgotten"
    sh = get_memory(truth, shared)
    assert sh["status"] == "active" and [e["source_id"] for e in sh["evidence"]] == [s2] and sh["version"] == 2
    src = get_source(truth, s1)
    assert src["status"] == "forgotten" and src["text"] == "" and src["title"] == "" and src["origin_key"] is None
    assert "上线 12 月" not in text_dump(truth, ["sources", "evidence", "audit_log", "proposals"])


def test_forget_source_keep_memories_downgrades(truth, two_memories):
    s1, _, only, shared = two_memories
    out = forget_source(truth, None, s1, actor=OWNER, reason="x", mode="keep_memories")
    assert out["downgraded_memories"] == [only]
    m = get_memory(truth, only)
    assert m["status"] == "active" and m["verification"] == "inferred"
    assert "evidence-forgotten" in m["tags"] and m["evidence"] == []
    assert len(get_memory(truth, shared)["evidence"]) == 1


def test_forget_source_all_versions(truth):
    v1 = seed_source(truth, "第一版", origin_key="doc:prd")
    v2 = seed_source(truth, "第二版", origin_key="doc:prd")
    out = forget_source(truth, None, v2, actor=OWNER, reason="x")
    assert sorted(out["forgotten_sources"]) == sorted([v1, v2])


def test_forget_source_this_version_only(truth):
    v1 = seed_source(truth, "第一版", origin_key="doc:prd")
    v2 = seed_source(truth, "第二版", origin_key="doc:prd")
    out = forget_source(truth, None, v1, actor=OWNER, reason="x", all_versions=False)
    assert out["forgotten_sources"] == [v1] and get_source(truth, v2)["status"] == "active"


def test_shared_file_kept_until_last_reference(truth, paths, tmp_path):
    f = tmp_path / "prd.md"
    f.write_text("# PRD\n内容", encoding="utf-8")
    a = add_source(truth, paths, S, actor=OWNER, path=f)["source_id"]
    b = add_source(truth, paths, S, actor=OWNER, path=f, origin_key="manual:prd")["source_id"]
    stored = paths.data / get_source(truth, a)["file_path"]
    forget_source(truth, paths, a, actor=OWNER, reason="x")
    assert stored.exists()
    forget_source(truth, paths, b, actor=OWNER, reason="x")
    assert not stored.exists()


def test_agent_cannot_forget(truth):
    s = seed_source(truth, "一段需要保护的原文")
    with pytest.raises(PolicyDenied):
        forget_source(truth, None, s, actor=AGENT, reason="x")
    mid = create(truth, "y", [(s, "需要保护的原文")])["memory_id"]
    with pytest.raises(PolicyDenied):
        forget_memory(truth, mid, actor=AGENT, reason="x")


def test_forget_requires_reason(truth):
    s = seed_source(truth, "x")
    with pytest.raises(ValidationError):
        forget_source(truth, None, s, actor=OWNER, reason=" ")
