from pathlib import Path

from pmi.actors import Actor
from pmi.config import Settings
from pmi.evolution import propose
from pmi.extractor import extract_ops
from pmi.memories import get_memory
from pmi.sources import add_source, get_source
from pmi.textnorm import find_quote

FIXTURES = Path(__file__).parent / "fixtures"
S = Settings()


def load(truth, paths, name):
    sid = add_source(truth, paths, S, actor=Actor.owner(), path=FIXTURES / name)["source_id"]
    return get_source(truth, sid, text_limit=10**9)


def test_extracts_meeting_decisions_and_actions(truth, paths):
    src = load(truth, paths, "meeting_1001.md")
    ops = extract_ops(src)
    mems = [o["memory"] for o in ops]
    assert [m["kind"] for m in mems] == ["episodic", "decision", "decision", "episodic"]
    assert mems[0]["content"] == "Meeting: Project A 周会 on 2026-10-01. Attendees: Alice, Bob, Yunze."
    assert [m.get("slot") for m in mems[1:3]] == ["project-a/launch-date", "project-a/architecture"]
    assert mems[1]["content"] == "Project A 计划于 11 月 15 日上线。"
    assert mems[3]["tags"] == ["action-item"]
    assert {"name": "Bob", "kind": "person", "role": "owner"} in mems[3]["entities"]
    assert {"name": "Alice", "kind": "person", "role": "participant"} in mems[0]["entities"]
    for m in mems:
        assert m["valid_from"] == "2026-10-01T00:00:00Z"
        assert {"name": "Project A", "kind": "project", "role": "subject"} in m["entities"]
        for ev in m["evidence"]:
            assert ev["source_id"] == src["id"] and find_quote(src["text"], ev["quote"]) is not None


def test_extracted_ops_apply_as_owner_trust(truth, paths):
    src = load(truth, paths, "meeting_1001.md")
    res = propose(truth, S, extract_ops(src), actor=Actor.extractor())
    assert res["applied"] == 4
    m = get_memory(truth, res["results"][1]["memory_id"])
    assert m["evidence_trust"] == "owner" and m["verification"] == "source_backed"
    assert m["created_by"] == "extractor" and m["valid_from"] == "2026-10-01T00:00:00Z"


def test_prd_without_sections_returns_empty(truth, paths):
    sid = add_source(truth, paths, S, actor=Actor.owner(), text="# PRD\n## 背景\n推送平台需要升级。")["source_id"]
    # 背景 is a fact section but has no bullets → nothing to extract
    assert extract_ops(get_source(truth, sid, text_limit=10**9)) == []


def test_no_frontmatter_no_meeting_memory(truth, paths):
    sid = add_source(truth, paths, S, actor=Actor.owner(), text="## 决策\n- [slot: Pricing] 用方案 A")["source_id"]
    [op] = extract_ops(get_source(truth, sid, text_limit=10**9))
    assert op["memory"]["slot"] == "pricing" and op["memory"]["entities"] == []
    assert "valid_from" not in op["memory"]


def test_numbered_bullets_english_headings(truth, paths):
    text = "---\nproject: Apollo\n---\n### Decisions\n1. Use plan A\n### Facts\n* Budget is fixed\n### TODO\n2) @Carol ship it"
    sid = add_source(truth, paths, S, actor=Actor.owner(), text=text)["source_id"]
    kinds = [(o["memory"]["kind"], o["memory"]["content"]) for o in extract_ops(get_source(truth, sid, text_limit=10**9))]
    assert kinds == [("decision", "Use plan A"), ("semantic", "Budget is fixed"), ("episodic", "@Carol ship it")]


def test_invalid_slot_tag_is_ignored_not_fatal(truth, paths):
    sid = add_source(truth, paths, S, actor=Actor.owner(), text="## 决策\n- [slot: ??*] 用方案 A")["source_id"]
    [op] = extract_ops(get_source(truth, sid, text_limit=10**9))
    assert "slot" not in op["memory"] and op["memory"]["content"] == "用方案 A"
