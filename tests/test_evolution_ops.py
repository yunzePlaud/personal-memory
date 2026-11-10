import pytest

from pmi.actors import Actor
from pmi.config import Settings
from pmi.errors import ValidationError
from pmi.evolution import propose
from pmi.memories import get_memory
from pmi.review import list_proposals
from tests.helpers import seed_source

OWNER, AGENT = Actor.owner(), Actor.agent("t")
S = Settings()


def run(truth, ops, actor=AGENT):
    return propose(truth, S, ops, actor=actor)["results"]


def new(truth, content, actor=OWNER, **kw):
    return run(truth, [{"op": "create", "memory": {"kind": "semantic", "content": content, **kw}}], actor)[0]["memory_id"]


def test_agent_update_unprotected_applies(truth):
    mid = new(truth, "Alice 是 PM")  # owner-created inferred memory, no evidence
    [r] = run(truth, [{"op": "update", "id": mid, "changes": {"content": "Alice 是 Project A 的 PM", "tags": ["people"]}}])
    m = get_memory(truth, mid)
    assert r["outcome"] == "applied" and m["content"] == "Alice 是 Project A 的 PM" and m["version"] == 2


def test_agent_update_with_equal_trust_evidence_applies(truth):
    s = seed_source(truth, "slack: 负责人是 Bob", trust="external")
    s2 = seed_source(truth, "slack: 负责人改成 Carol", trust="external")
    mid = run(truth, [{"op": "create", "memory": {"kind": "semantic", "content": "负责人是 Bob",
                                                  "evidence": [{"source_id": s, "quote": "负责人是 Bob"}]}}])[0]["memory_id"]
    [r] = run(truth, [{"op": "update", "id": mid, "changes": {"content": "负责人是 Carol"},
                       "evidence": [{"source_id": s2, "quote": "负责人改成 Carol"}]}])
    assert r["outcome"] == "applied" and len(get_memory(truth, mid)["evidence"]) == 2


def test_agent_update_entities(truth):
    mid = new(truth, "季度目标")
    run(truth, [{"op": "update", "id": mid, "changes": {"entities": [{"name": "Project A", "kind": "project"}]}}])
    assert [e["name"] for e in get_memory(truth, mid)["entities"]] == ["Project A"]


def test_agent_update_confirmed_goes_pending_data_unchanged(truth):
    mid = new(truth, "上线 12 月", verification="user_confirmed")
    [r] = run(truth, [{"op": "update", "id": mid, "changes": {"content": "上线 1 月"}}])
    assert r["outcome"] == "pending" and r["proposal_id"]
    assert get_memory(truth, mid)["content"] == "上线 12 月"


def test_update_cannot_change_slot(truth):
    mid = new(truth, "x")
    with pytest.raises(ValidationError):
        run(truth, [{"op": "update", "id": mid, "changes": {"slot": "p/x"}}])
    with pytest.raises(ValidationError):
        run(truth, [{"op": "update", "id": mid, "changes": {}}])


def test_agent_archive_confirmed_pending(truth):
    mid = new(truth, "偏好中文回复", verification="user_confirmed")
    [r] = run(truth, [{"op": "archive", "id": mid}])
    assert r["outcome"] == "pending" and get_memory(truth, mid)["status"] == "active"


def test_agent_archive_unprotected_applies(truth):
    mid = new(truth, "临时笔记")
    [r] = run(truth, [{"op": "archive", "id": mid}])
    m = get_memory(truth, mid)
    assert r["outcome"] == "applied" and m["status"] == "archived" and m["archived_by"] == "agent:t"


def test_restore_owner_archived_pending_for_agent(truth):
    mid = new(truth, "旧流程")
    run(truth, [{"op": "archive", "id": mid}], actor=OWNER)
    [r] = run(truth, [{"op": "restore", "id": mid}])
    assert r["outcome"] == "pending" and get_memory(truth, mid)["status"] == "archived"


def test_restore_agent_archived_applies_previous_status(truth):
    mid = new(truth, "旧流程")
    run(truth, [{"op": "archive", "id": mid}])
    [r] = run(truth, [{"op": "restore", "id": mid}])
    m = get_memory(truth, mid)
    assert r["outcome"] == "applied" and m["status"] == "active" and m["archived_by"] is None


def test_restore_slot_occupied_rejected(truth):
    x = new(truth, "版本 A", slot="p/v")
    run(truth, [{"op": "archive", "id": x}], actor=OWNER)
    new(truth, "版本 B", slot="p/v")
    with pytest.raises(ValidationError):
        run(truth, [{"op": "restore", "id": x}], actor=OWNER)


def test_link_contradicts(truth):
    a, b = new(truth, "说法甲"), new(truth, "说法乙")
    [r] = run(truth, [{"op": "link", "from_id": a, "to_id": b, "type": "contradicts"}])
    assert r["outcome"] == "applied"
    assert {"type": "contradicts", "from_id": a, "to_id": b} in get_memory(truth, a)["links"]
    with pytest.raises(ValidationError):
        run(truth, [{"op": "link", "from_id": a, "to_id": b, "type": "supersedes"}])
    with pytest.raises(ValidationError):
        run(truth, [{"op": "link", "from_id": a, "to_id": a, "type": "supports"}])


def test_pending_ops_listed(truth):
    mid = new(truth, "上线 12 月", verification="user_confirmed")
    run(truth, [{"op": "archive", "id": mid}])
    [p] = list_proposals(truth)
    assert p["ops"] == [{"op": "archive", "id": mid}] and p["actor"] == "agent:t"
