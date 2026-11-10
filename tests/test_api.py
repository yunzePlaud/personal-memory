import shutil
from pathlib import Path

import pytest

from pmi import Actor, Memory
from pmi.errors import PolicyDenied
from tests.helpers import make_empty_pdf

FIXTURES = Path(__file__).parent / "fixtures"
AGENT = Actor.agent("claude-code")


@pytest.fixture
def mem(home):
    m = Memory.open(home, embedder=None)
    yield m
    m.close()


def test_agent_trust_capped(mem):
    out = mem.add_source(text="agent 的分析结论", trust="owner", actor=AGENT)
    assert mem.get([out["source_id"]])["items"][0]["trust"] == "agent"
    assert "trust capped to agent" in out["notes"]


def test_agent_default_trust_external(mem):
    out = mem.add_source(text="Slack: 明天开会", kind="conversation", actor=AGENT)
    assert mem.get([out["source_id"]])["items"][0]["trust"] == "external"
    owner = mem.add_source(text="我自己的笔记")
    assert mem.get([owner["source_id"]])["items"][0]["trust"] == "owner"


def test_agent_path_outside_roots_denied(home, tmp_path):
    f = tmp_path / "secret.md"
    f.write_text("secret", encoding="utf-8")
    (home / "config").mkdir(exist_ok=True)
    (home / "config" / "settings.toml").write_text(f'[capture]\nimport_roots = ["{tmp_path / "allowed"}"]\n')
    with Memory.open(home, embedder=None) as m:
        with pytest.raises(PolicyDenied):
            m.add_source(path=str(f), actor=AGENT)
        (tmp_path / "allowed").mkdir()
        ok = tmp_path / "allowed" / "ok.md"
        ok.write_text("ok", encoding="utf-8")
        assert m.add_source(path=str(ok), actor=AGENT)["outcome"] == "created"
        link = tmp_path / "allowed" / "link.md"
        link.symlink_to(f)
        with pytest.raises(PolicyDenied):
            m.add_source(path=str(link), actor=AGENT)


def test_owner_only_methods_denied_for_agent(mem, tmp_path):
    src = mem.add_source(text="x")["source_id"]
    calls = [
        lambda: mem.confirm("mem_x", actor=AGENT), lambda: mem.approve("prp_x", actor=AGENT),
        lambda: mem.reject("prp_x", "no", actor=AGENT), lambda: mem.forget_memory("mem_x", "r", actor=AGENT),
        lambda: mem.forget_source(src, "r", actor=AGENT), lambda: mem.import_paths([str(tmp_path)], actor=AGENT),
        lambda: mem.add_alias("ent_x", "y", actor=AGENT), lambda: mem.reindex(actor=AGENT),
    ]
    for call in calls:
        with pytest.raises(PolicyDenied):
            call()


def test_import_dir_skips_and_continues(mem, tmp_path):
    d = tmp_path / "docs"
    (d / ".hidden").mkdir(parents=True)
    (d / ".hidden" / "x.md").write_text("hidden", encoding="utf-8")
    (d / ".DS_Store").write_bytes(b"\x00")
    (d / "a.png").write_bytes(b"\x89PNG")
    (d / "scan.pdf").write_bytes(make_empty_pdf())
    (d / "one.md").write_text("# One\n正文一", encoding="utf-8")
    (d / "sub").mkdir()
    (d / "sub" / "two.md").write_text("# Two\n正文二", encoding="utf-8")
    out = mem.import_paths([str(d)])
    assert sorted(Path(i["path"]).name for i in out["imported"]) == ["one.md", "two.md"]
    assert sorted((Path(s["path"]).name, s["reason"]) for s in out["skipped"]) == [
        (".DS_Store", "hidden"), (".hidden", "hidden"), ("a.png", "unsupported")]
    assert [Path(e["path"]).name for e in out["errors"]] == ["scan.pdf"]
    assert "no extractable text" in out["errors"][0]["error"]


def test_extract_on_import_creates_memories(mem, tmp_path):
    shutil.copytree(FIXTURES, tmp_path / "notes")
    out = mem.import_paths([str(tmp_path / "notes")], extract=True)
    assert len(out["imported"]) == 2
    ctx = mem.get_context("Project A 现在什么时候上线")
    current = [m for m in ctx["memories"] if m["slot"] == "project-a/launch-date"]
    assert len(current) == 1 and "12 月 10 日" in current[0]["content"]


def test_candidates_for_slotless_create(mem):
    s = mem.add_source(text="周会：团队每周五发周报")["source_id"]
    first = mem.propose([{"op": "create", "memory": {"kind": "procedure", "content": "团队每周五发周报",
                          "evidence": [{"source_id": s, "quote": "团队每周五发周报"}]}}])["results"][0]["memory_id"]
    s2 = mem.add_source(text="Slack：周报改成每周五下午发给团队", actor=AGENT)["source_id"]
    r = mem.propose([{"op": "create", "memory": {"kind": "procedure", "content": "周报改成每周五下午发给团队",
                      "evidence": [{"source_id": s2, "quote": "周报改成每周五下午发给团队"}]}}], actor=AGENT)["results"][0]
    assert r["outcome"] == "applied" and first in [c["memory_id"] for c in r["candidates"]]
    assert r["memory_id"] not in [c["memory_id"] for c in r["candidates"]]


def test_read_after_write_sees_new_memory(mem):
    mem.propose([{"op": "create", "memory": {"kind": "preference", "content": "回复用中文，先说结论"}}])
    assert [m["content"] for m in mem.search("先说结论")["memories"]] == ["回复用中文，先说结论"]


def test_get_dispatch_and_list(mem):
    s = mem.add_source(text="会议：负责人是 Alice")["source_id"]
    r = mem.propose([{"op": "create", "memory": {"kind": "person", "content": "负责人是 Alice", "slot": "p/owner",
                      "entities": [{"name": "Alice", "kind": "person"}],
                      "evidence": [{"source_id": s, "quote": "负责人是 Alice"}]}}])["results"][0]
    pending = mem.propose([{"op": "create", "memory": {"kind": "semantic", "content": "猜测"}}], actor=AGENT)
    ent = mem.list("entities")["items"][0]["id"]
    got = mem.get([r["memory_id"], s, ent, pending["results"][0]["proposal_id"], "mem_nope"])
    assert len(got["items"]) == 4 and got["not_found"] == ["mem_nope"]
    assert mem.list("slots")["items"] == [{"slot": "p/owner", "memory_id": r["memory_id"], "title": "负责人是 Alice"}]
    assert len(mem.list("proposals")["items"]) == 1
    assert mem.list("memories", kind="person")["items"][0]["id"] == r["memory_id"]
    assert mem.list("recent", limit=3)["items"]
