import pytest

from pmi.actors import Actor
from pmi.errors import ValidationError
from pmi.memories import (add_link, evidence_trust, find_active_in_slot, get_memory, insert_memory,
                          resolve_evidence, update_memory, validate_memory_input)
from tests.helpers import seed_source

OWNER = Actor.owner()
AGENT = Actor.agent("claude-code")


def _create(truth, data, actor=OWNER, status="active", verification="source_backed"):
    m = validate_memory_input(data, actor=actor)
    ev = resolve_evidence(truth, m["evidence"])
    return insert_memory(truth, m, ev, status=status, verification=verification, actor=actor)


def test_insert_and_get_with_evidence_entities(truth):
    sid = seed_source(truth, "周会：不采用方案 B，原因是成本太高。", source_time="2026-10-01")
    mid = _create(truth, {
        "kind": "decision", "content": "不采用方案 B，因为成本太高", "slot": "Project-A/Architecture",
        "entities": [{"name": "Project A", "kind": "project"}, {"name": "Bob", "kind": "person", "role": "owner"}],
        "evidence": [{"source_id": sid, "quote": "不采用方案 B，原因是成本太高"}],
    })
    m = get_memory(truth, mid)
    assert m["slot"] == "project-a/architecture" and m["status"] == "active" and m["version"] == 1
    assert m["title"] == "不采用方案 B，因为成本太高"
    assert {(e["name"], e["role"]) for e in m["entities"]} == {("Project A", "subject"), ("Bob", "owner")}
    ev = m["evidence"][0]
    src_text = truth.execute("select text from sources where id=?", (sid,)).fetchone()[0]
    assert src_text[ev["span"][0]:ev["span"][1]] == "不采用方案 B，原因是成本太高"
    assert ev["trust"] == "owner" and ev["is_latest"] is True
    assert m["valid_from"] == "2026-10-01T00:00:00Z" and m["evidence_trust"] == "owner"
    assert find_active_in_slot(truth, "project-a/architecture")["id"] == mid


def test_quote_not_in_source_rejected(truth):
    sid = seed_source(truth, "原文内容")
    with pytest.raises(ValidationError) as exc:
        resolve_evidence(truth, [{"source_id": sid, "quote": "原文里没有的话"}])
    assert exc.value.details["index"] == 0


def test_missing_source_rejected(truth):
    with pytest.raises(ValidationError):
        resolve_evidence(truth, [{"source_id": "src_nope", "quote": "x"}])


def test_agent_cannot_claim_user_confirmed():
    with pytest.raises(ValidationError):
        validate_memory_input({"kind": "semantic", "content": "x", "verification": "user_confirmed"},
                              actor=AGENT)
    ok = validate_memory_input({"kind": "semantic", "content": "x", "verification": "user_confirmed"},
                               actor=OWNER)
    assert ok["verification"] == "user_confirmed"


def test_valid_from_defaults_to_latest_evidence_time(truth):
    a = seed_source(truth, "上线定在 12 月", source_time="2026-10-01")
    b = seed_source(truth, "确认上线定在 12 月", source_time="2026-10-05")
    mid = _create(truth, {"kind": "decision", "content": "12 月上线", "evidence": [
        {"source_id": a, "quote": "上线定在 12 月"}, {"source_id": b, "quote": "确认上线定在 12 月"}]})
    assert get_memory(truth, mid)["valid_from"] == "2026-10-05T00:00:00Z"


def test_valid_from_none_when_undated(truth):
    a = seed_source(truth, "没有日期的笔记")
    mid = _create(truth, {"kind": "semantic", "content": "笔记", "evidence": [{"source_id": a, "quote": "没有日期"}]})
    assert get_memory(truth, mid)["valid_from"] is None


def test_update_bumps_version_and_history(truth):
    mid = _create(truth, {"kind": "semantic", "content": "v1"}, verification="inferred")
    update_memory(truth, mid, {"content": "v2"}, actor=OWNER, change_kind="update")
    m = update_memory(truth, mid, {"tags": ["x"]}, actor=OWNER, change_kind="update", reason="tagging")
    assert m["version"] == 3 and m["content"] == "v2" and m["tags"] == ["x"]
    h = get_memory(truth, mid, include_history=True)["history"]
    assert [v["version"] for v in h] == [1, 2, 3]
    assert [v["snapshot"]["content"] for v in h] == ["v1", "v2", "v2"]
    assert h[2]["reason"] == "tagging"


def test_update_rejects_unknown_field(truth):
    mid = _create(truth, {"kind": "semantic", "content": "v1"}, verification="inferred")
    with pytest.raises(ValidationError):
        update_memory(truth, mid, {"id": "other"}, actor=OWNER, change_kind="update")


def test_evidence_trust_highest(truth):
    o = seed_source(truth, "owner 文档：上线 12 月", trust="owner")
    x = seed_source(truth, "slack：上线 12 月", trust="external")
    mid = _create(truth, {"kind": "decision", "content": "上线 12 月", "evidence": [
        {"source_id": x, "quote": "上线 12 月"}, {"source_id": o, "quote": "上线 12 月"}]})
    assert evidence_trust(truth, mid) == "owner"
    inferred = _create(truth, {"kind": "semantic", "content": "猜测"}, verification="inferred")
    assert evidence_trust(truth, inferred) is None


def test_unknown_field_rejected():
    with pytest.raises(ValidationError):
        validate_memory_input({"kind": "semantic", "content": "x", "colour": "red"}, actor=OWNER)
    with pytest.raises(ValidationError):
        validate_memory_input({"kind": "rumour", "content": "x"}, actor=OWNER)
    with pytest.raises(ValidationError):
        validate_memory_input({"kind": "semantic", "content": "  "}, actor=OWNER)


def test_add_link(truth):
    a = _create(truth, {"kind": "semantic", "content": "a"}, verification="inferred")
    b = _create(truth, {"kind": "semantic", "content": "b"}, verification="inferred")
    add_link(truth, a, b, "contradicts", actor=OWNER)
    assert get_memory(truth, a)["links"] == [{"type": "contradicts", "from_id": a, "to_id": b}]
    with pytest.raises(ValidationError):
        add_link(truth, a, b, "loves", actor=OWNER)
