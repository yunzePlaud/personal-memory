import pytest

from pmi.actors import Actor
from pmi.config import Settings
from pmi.errors import ConflictError, PolicyDenied
from pmi.evolution import propose
from pmi.memories import get_memory
from pmi.review import approve_proposal, confirm_memory, get_proposal, reject_proposal
from tests.helpers import seed_source

OWNER, AGENT = Actor.owner(), Actor.agent("t")
S = Settings()
SLOT = "project-a/launch-date"


@pytest.fixture
def conflict(truth):
    dec = seed_source(truth, "10-20 周会：上线 12 月 10 日", source_time="2026-10-20")
    jan = seed_source(truth, "slack: 上线改到 1 月", trust="external", source_time="2026-11-06")
    confirmed = propose(truth, S, [{"op": "create", "memory": {
        "kind": "decision", "content": "上线 12 月 10 日", "slot": SLOT, "verification": "user_confirmed",
        "evidence": [{"source_id": dec, "quote": "上线 12 月 10 日"}]}}], actor=OWNER)["results"][0]["memory_id"]
    r = propose(truth, S, [{"op": "create", "memory": {
        "kind": "decision", "content": "上线改到 1 月", "slot": SLOT,
        "evidence": [{"source_id": jan, "quote": "上线改到 1 月"}]}}], actor=AGENT)["results"][0]
    return confirmed, r["memory_id"], r["proposal_id"]


def test_approve_conflict_activates_disputed_as_confirmed(truth, conflict):
    confirmed, disputed, pid = conflict
    out = approve_proposal(truth, S, pid, actor=OWNER)
    assert out["status"] == "applied"
    new = get_memory(truth, disputed)
    assert new["status"] == "active" and new["verification"] == "user_confirmed"
    old = get_memory(truth, confirmed)
    assert old["status"] == "superseded" and old["superseded_by"] == disputed
    assert get_proposal(truth, pid)["resolved_by"] == "owner:cli"


def test_approve_stale_proposal_fails(truth, conflict):
    confirmed, disputed, pid = conflict
    propose(truth, S, [{"op": "archive", "id": disputed}], actor=OWNER)
    out = approve_proposal(truth, S, pid, actor=OWNER)
    assert out["status"] == "failed" and out["result"]["error"]
    assert get_memory(truth, confirmed)["status"] == "active"
    assert get_memory(truth, disputed)["status"] == "archived"


def test_reject_archives_disputed(truth, conflict):
    confirmed, disputed, pid = conflict
    out = reject_proposal(truth, pid, actor=OWNER, reason="Slack 消息不准确")
    assert out["status"] == "rejected"
    m = get_memory(truth, disputed, include_history=True)
    assert m["status"] == "archived" and m["archived_by"] == "owner:cli"
    assert m["history"][-1]["reason"] == "Slack 消息不准确"
    assert get_memory(truth, confirmed)["status"] == "active"


def test_resolved_proposal_cannot_be_reused(truth, conflict):
    _, _, pid = conflict
    reject_proposal(truth, pid, actor=OWNER, reason="no")
    with pytest.raises(ConflictError):
        approve_proposal(truth, S, pid, actor=OWNER)


def test_approve_inferred_create_marks_confirmed(truth):
    r = propose(truth, S, [{"op": "create", "memory": {"kind": "person", "content": "Carol 负责增长"}}],
                actor=AGENT)["results"][0]
    out = approve_proposal(truth, S, r["proposal_id"], actor=OWNER)
    mid = out["result"]["results"][0]["memory_id"]
    assert get_memory(truth, mid)["verification"] == "user_confirmed"


def test_approve_undated_supersede(truth):
    s = seed_source(truth, "周会：上线 12 月", source_time="2026-10-20")
    old = propose(truth, S, [{"op": "create", "memory": {"kind": "decision", "content": "上线 12 月", "slot": SLOT,
                  "evidence": [{"source_id": s, "quote": "上线 12 月"}]}}], actor=AGENT)["results"][0]["memory_id"]
    u = seed_source(truth, "随手记：上线改 1 月")
    r = propose(truth, S, [{"op": "create", "memory": {"kind": "decision", "content": "上线改 1 月", "slot": SLOT,
                "evidence": [{"source_id": u, "quote": "上线改 1 月"}]}}], actor=AGENT)["results"][0]
    out = approve_proposal(truth, S, r["proposal_id"], actor=OWNER)
    new_id = out["result"]["results"][0]["memory_id"]
    assert get_memory(truth, old)["superseded_by"] == new_id
    assert get_memory(truth, new_id)["status"] == "active"


def test_confirm_and_confirm_disputed_raises(truth, conflict):
    confirmed, disputed, _ = conflict
    with pytest.raises(ConflictError):
        confirm_memory(truth, disputed, actor=OWNER)
    s = seed_source(truth, "笔记：周报每周五发")
    mid = propose(truth, S, [{"op": "create", "memory": {"kind": "procedure", "content": "周报每周五发",
                  "evidence": [{"source_id": s, "quote": "周报每周五发"}]}}], actor=AGENT)["results"][0]["memory_id"]
    m = confirm_memory(truth, mid, actor=OWNER)
    assert m["verification"] == "user_confirmed" and m["version"] == 2


def test_agent_cannot_approve(truth, conflict):
    _, disputed, pid = conflict
    with pytest.raises(PolicyDenied):
        approve_proposal(truth, S, pid, actor=AGENT)
    with pytest.raises(PolicyDenied):
        reject_proposal(truth, pid, actor=AGENT, reason="x")
    with pytest.raises(PolicyDenied):
        confirm_memory(truth, disputed, actor=AGENT)
