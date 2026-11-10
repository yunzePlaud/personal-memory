from pathlib import Path

import pytest

from pmi.actors import Actor
from pmi.config import Settings
from pmi.context import CONTENT_NOTICE, detect_intent, get_context
from pmi.evolution import propose
from pmi.extractor import extract_ops
from pmi.index.indexer import Indexer
from pmi.review import confirm_memory
from pmi.sources import add_source, get_source

FIXTURES = Path(__file__).parent / "fixtures"
S = Settings()
OWNER, AGENT = Actor.owner(), Actor.agent("slack-bot")
SLOT = "project-a/launch-date"
SLACK = ("Carol: 关于 Project A，我们之前为什么决定不采用方案 B？现在还是这个结论吗？\n"
         "Carol: 另外 Project A 上线改到 1 月了，听说推送平台又延期。")


@pytest.fixture
def world(truth, paths):
    for name in ("meeting_1001.md", "meeting_1020.md"):
        sid = add_source(truth, paths, S, actor=OWNER, path=FIXTURES / name)["source_id"]
        propose(truth, S, extract_ops(get_source(truth, sid, text_limit=10**9)), actor=Actor.extractor())
    ix = Indexer(paths, S, None)

    def ctx(query, **kw):
        ix.sync(truth)
        return get_context(truth, ix.open(), S, query=query, **kw)
    return ctx


def slot_ids(items):
    return [m["id"] for m in items if m["slot"] == SLOT]


def active_in_slot(truth):
    return truth.execute("select id from memories where slot=? and status='active'", (SLOT,)).fetchone()[0]


def test_detect_intent():
    assert detect_intent("Project A 现在什么时候上线") == {"current": True, "history": False, "why": False, "who": False}
    assert detect_intent("Why did we reject B before?") == {"current": False, "history": True, "why": True, "who": False}


def test_why_question_returns_decision_with_citation(world):
    out = world("为什么 Project A 不采用方案 B")
    top = out["memories"][0]
    assert top["kind"] == "decision" and "方案 B" in top["content"] and top["status"] == "active"
    cite = top["citations"][0]
    assert cite["source_time"] == "2026-10-01T00:00:00Z" and "不采用方案 B" in cite["quote"]
    assert cite["trust"] == "owner" and cite["is_latest"] is True
    assert out["insufficient_evidence"] is False and out["intent"]["why"] is True
    assert {"kind": "project", "name": "Project A"}.items() <= out["entities"][0].items()


def test_latest_status_prefers_current(world, truth):
    out = world("Project A 现在什么时候上线")
    current = active_in_slot(truth)
    assert slot_ids(out["memories"]) == [current]
    [old] = [m for m in out["history"] if m["slot"] == SLOT]
    assert old["status"] == "superseded" and old["valid_until"] == "2026-10-20T00:00:00Z"
    assert old["superseded_by"] == current


def test_as_of_returns_old_decision(world):
    out = world("Project A 什么时候上线", as_of="2026-10-15")
    [m] = [m for m in out["memories"] if m["slot"] == SLOT]
    assert "11 月 15 日" in m["content"] and out["as_of"] == "2026-10-15T00:00:00Z"


def test_include_history_lists_predecessors(world):
    out = world("Project A 上线", include_history=True)
    assert any("11 月 15 日" in m["content"] for m in out["history"])


def test_conflict_reported(world, truth, paths):
    current = active_in_slot(truth)
    confirm_memory(truth, current, actor=OWNER)
    sid = add_source(truth, paths, S, actor=AGENT, text=SLACK, kind="conversation", trust="external",
                     source_time="2026-11-05")["source_id"]
    r = propose(truth, S, [{"op": "create", "memory": {
        "kind": "decision", "content": "Project A 上线改到 1 月", "slot": SLOT,
        "evidence": [{"source_id": sid, "quote": "Project A 上线改到 1 月了"}]}}], actor=AGENT)["results"][0]
    out = world("Project A 上线时间")
    [c] = out["conflicts"]
    assert set(c["memory_ids"]) == {current, r["memory_id"]} and c["proposal_id"] == r["proposal_id"]
    ids = {m["id"]: m for m in out["memories"]}
    assert ids[current]["verification"] == "user_confirmed" and ids[r["memory_id"]]["status"] == "disputed"
    assert ids[r["memory_id"]]["citations"][0]["trust"] == "external"


def test_external_source_marked(world, truth, paths):
    add_source(truth, paths, S, actor=AGENT, text=SLACK, kind="conversation", trust="external")
    out = world("为什么 Project A 不采用方案 B")
    assert any(s["trust"] == "external" and s["kind"] == "conversation" for s in out["sources"])
    assert out["content_notice"] == CONTENT_NOTICE


def test_unknown_topic_insufficient(world):
    out = world("Project Z 的预算是多少")
    assert out["insufficient_evidence"] is True and out["memories"] == [] and out["notes"]


def test_budget_truncation(world):
    out = world("为什么 Project A 不采用方案 B", max_tokens=50)
    assert out["truncated"] is True and out["token_estimate"] <= 50


def test_empty_query_ok(world):
    out = world("")
    assert out["insufficient_evidence"] is True and out["memories"] == []


def test_semantic_unavailable_noted(world):
    assert "semantic search unavailable" in world("Project A")["notes"]


def test_explicit_project_param_boosts(world):
    out = world("上线", project="Project A")
    assert out["entities"] and out["entities"][0]["name"] == "Project A"
    unknown = world("上线", project="Project Q")
    assert any("Project Q" in n for n in unknown["notes"])


def test_who_question_prefers_person_memories(truth, paths):
    from pmi.evolution import propose as _propose

    s = add_source(truth, paths, S, actor=OWNER, text="周会：Tag 改版由 Alice review，Tag 改版的方案是移到输入框上方")["source_id"]
    _propose(truth, S, [
        {"op": "create", "memory": {"kind": "decision", "content": "Tag 改版的方案是移到输入框上方",
                                    "evidence": [{"source_id": s, "quote": "Tag 改版的方案是移到输入框上方"}]}},
        {"op": "create", "memory": {"kind": "person", "content": "Tag 改版由 Alice review",
                                    "evidence": [{"source_id": s, "quote": "Tag 改版由 Alice review"}]}}], actor=OWNER)
    ix = Indexer(paths, S, None)
    ix.sync(truth)
    out = get_context(truth, ix.open(), S, query="Tag 改版是谁 review 的")
    assert out["intent"]["who"] is True and out["memories"][0]["kind"] == "person"
