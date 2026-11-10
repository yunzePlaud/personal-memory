import pytest

from pmi.actors import Actor
from pmi.config import Settings
from pmi.errors import ValidationError
from pmi.evolution import propose
from pmi.memories import get_memory, update_memory
from pmi.review import list_proposals
from tests.helpers import seed_source

OWNER, AGENT = Actor.owner(), Actor.agent("t")
S = Settings()
SLOT = "project-a/launch-date"


def mem(content, slot=None, sid=None, quote=None, kind="decision", **kw):
    d = {"kind": kind, "content": content, **kw}
    if slot:
        d["slot"] = slot
    if sid:
        d["evidence"] = [{"source_id": sid, "quote": quote or content}]
    return d


def create(truth, m, actor=AGENT):
    return propose(truth, S, [{"op": "create", "memory": m}], actor=actor)["results"][0]


def count(truth, table, where="1=1"):
    return truth.execute(f"select count(*) from {table} where {where}").fetchone()[0]


@pytest.fixture
def nov_dec(truth):
    nov = seed_source(truth, "10-01 周会：Project A 计划于 11 月 15 日上线。", source_time="2026-10-01")
    dec = seed_source(truth, "10-20 周会：Project A 上线时间调整至 12 月 10 日。", source_time="2026-10-20")
    return (mem("Project A 计划于 11 月 15 日上线", SLOT, nov),
            mem("Project A 上线时间调整至 12 月 10 日", SLOT, dec))


def test_slot_auto_supersede_newer(truth, nov_dec):
    r1, r2 = create(truth, nov_dec[0]), create(truth, nov_dec[1])
    assert (r1["outcome"], r1["status"]) == ("applied", "active")
    assert (r2["outcome"], r2["status"]) == ("applied", "active")
    old = get_memory(truth, r1["memory_id"])
    assert old["status"] == "superseded" and old["superseded_by"] == r2["memory_id"]
    assert old["valid_until"] == "2026-10-20T00:00:00Z"
    assert {"type": "supersedes", "from_id": r2["memory_id"], "to_id": r1["memory_id"]} in old["links"]


def test_backfill_older_goes_superseded(truth, nov_dec):
    dec, nov = create(truth, nov_dec[1]), create(truth, nov_dec[0])
    assert (nov["outcome"], nov["status"]) == ("applied", "superseded")
    m = get_memory(truth, nov["memory_id"])
    assert m["superseded_by"] == dec["memory_id"] and m["valid_until"] == "2026-10-20T00:00:00Z"
    assert get_memory(truth, dec["memory_id"])["status"] == "active"


def test_backfill_into_confirmed_slot_no_conflict(truth, nov_dec):
    dec = create(truth, {**nov_dec[1], "verification": "user_confirmed"}, actor=OWNER)
    nov = create(truth, nov_dec[0])
    assert nov["status"] == "superseded" and count(truth, "proposals") == 0
    assert get_memory(truth, dec["memory_id"])["status"] == "active"


def test_conflict_with_confirmed_creates_disputed_and_proposal(truth, nov_dec):
    dec = create(truth, {**nov_dec[1], "verification": "user_confirmed"}, actor=OWNER)
    jan = seed_source(truth, "Carol: Project A 上线改到 1 月了", trust="external", source_time="2026-11-06")
    r = create(truth, mem("Project A 上线改到 1 月", SLOT, jan, quote="Project A 上线改到 1 月了"))
    assert (r["outcome"], r["status"]) == ("pending", "disputed") and r["proposal_id"]
    disputed = get_memory(truth, r["memory_id"])
    assert {"type": "contradicts", "from_id": r["memory_id"], "to_id": dec["memory_id"]} in disputed["links"]
    [p] = list_proposals(truth)
    assert p["id"] == r["proposal_id"]
    assert p["ops"] == [{"op": "supersede", "old_ids": [dec["memory_id"]], "memory_id": r["memory_id"]}]
    assert get_memory(truth, dec["memory_id"])["status"] == "active"


def test_lower_trust_evidence_cannot_replace_owner_backed(truth, nov_dec):
    dec = create(truth, nov_dec[1])
    jan = seed_source(truth, "Carol: 上线改到 1 月", trust="external", source_time="2026-11-06")
    r = create(truth, mem("上线改到 1 月", SLOT, jan))
    assert (r["outcome"], r["status"]) == ("pending", "disputed")
    assert get_memory(truth, dec["memory_id"])["status"] == "active"


def test_external_can_replace_external(truth):
    a = seed_source(truth, "slack: 周五发布", trust="external", source_time="2026-11-01")
    b = seed_source(truth, "slack: 改到下周一发布", trust="external", source_time="2026-11-02")
    r1 = create(truth, mem("周五发布", "p/release", a))
    r2 = create(truth, mem("改到下周一发布", "p/release", b))
    assert r2["outcome"] == "applied" and get_memory(truth, r1["memory_id"])["status"] == "superseded"


def test_undated_in_occupied_slot_goes_pending(truth, nov_dec):
    create(truth, nov_dec[1])
    undated = seed_source(truth, "随手记：Project A 好像要 1 月上线")
    r = create(truth, mem("Project A 好像要 1 月上线", SLOT, undated))
    assert r["outcome"] == "pending" and r["memory_id"] is None
    assert count(truth, "memories") == 1
    [p] = list_proposals(truth)
    assert p["ops"][0]["op"] == "supersede" and p["ops"][0]["memory"]["content"] == "Project A 好像要 1 月上线"


def test_duplicate_merges_evidence(truth, nov_dec):
    r1 = create(truth, nov_dec[0])
    again = seed_source(truth, "复述：Project A 计划于 11 月 15 日上线。", source_time="2026-10-02")
    r2 = create(truth, mem("project a 计划于 11 月 15 日上线。", SLOT, again,
                           quote="Project A 计划于 11 月 15 日上线"))
    assert r2["outcome"] == "duplicate" and r2["memory_id"] == r1["memory_id"]
    assert len(get_memory(truth, r1["memory_id"])["evidence"]) == 2


def test_revert_a_b_a(truth):
    s1 = seed_source(truth, "10-01：上线 11 月 15 日", source_time="2026-10-01")
    s2 = seed_source(truth, "10-10：上线 12 月 1 日", source_time="2026-10-10")
    s3 = seed_source(truth, "10-20：还是上线 11 月 15 日", source_time="2026-10-20")
    a1 = create(truth, mem("上线 11 月 15 日", SLOT, s1))
    create(truth, mem("上线 12 月 1 日", SLOT, s2))
    a2 = create(truth, mem("上线 11 月 15 日", SLOT, s3))
    assert a2["outcome"] == "applied" and a2["memory_id"] != a1["memory_id"]
    active = truth.execute("select id, content from memories where status='active'").fetchall()
    assert [tuple(r) for r in active] == [(a2["memory_id"], "上线 11 月 15 日")]


def test_identical_to_archived_creates_new_with_candidate(truth):
    r1 = create(truth, mem("团队偏好异步沟通", kind="preference"), actor=OWNER)
    update_memory(truth, r1["memory_id"], {"status": "archived", "archived_by": "owner:cli"},
                  actor=OWNER, change_kind="archive")
    r2 = create(truth, mem("团队偏好异步沟通", kind="preference"), actor=OWNER)
    assert r2["outcome"] == "applied" and r2["memory_id"] != r1["memory_id"]
    assert [c["memory_id"] for c in r2["candidates"]] == [r1["memory_id"]]


def test_inferred_create_pending_for_agent(truth):
    r = create(truth, mem("Alice 可能会接手 Project A"))
    assert r["outcome"] == "pending" and count(truth, "memories") == 0
    owner = create(truth, mem("Alice 会接手 Project A"), actor=OWNER)
    assert owner["outcome"] == "applied"
    assert get_memory(truth, owner["memory_id"])["verification"] == "inferred"


def test_inferred_create_denied_by_setting(truth):
    r = propose(truth, Settings(policy_inferred_create="deny"),
                [{"op": "create", "memory": mem("猜测")}], actor=AGENT)["results"][0]
    assert r["outcome"] == "rejected" and count(truth, "proposals") == 0


def test_explicit_supersede_without_slot(truth):
    s = seed_source(truth, "方案 A 改为方案 C", source_time="2026-10-05")
    x = create(truth, mem("采用方案 A", kind="decision"), actor=OWNER)
    r = propose(truth, S, [{"op": "supersede", "old_ids": [x["memory_id"]],
                             "memory": mem("改为方案 C", sid=s, quote="方案 A 改为方案 C")}], actor=AGENT)
    new_id = r["results"][0]["memory_id"]
    assert r["results"][0]["outcome"] == "applied"
    old = get_memory(truth, x["memory_id"])
    assert old["status"] == "superseded" and old["superseded_by"] == new_id
    assert old["valid_until"] == "2026-10-05T00:00:00Z"


def test_supersede_into_slot_held_by_other_rejected(truth, nov_dec):
    held = create(truth, nov_dec[1])
    x = create(truth, mem("别的记忆"), actor=OWNER)
    with pytest.raises(ValidationError):
        propose(truth, S, [{"op": "supersede", "old_ids": [x["memory_id"]], "memory": nov_dec[0]}], actor=OWNER)
    assert get_memory(truth, held["memory_id"])["status"] == "active"


def test_validation_error_writes_nothing(truth, nov_dec):
    with pytest.raises(ValidationError) as exc:
        propose(truth, S, [{"op": "create", "memory": nov_dec[0]},
                           {"op": "create", "memory": {"kind": "rumour", "content": "x"}}], actor=AGENT)
    assert exc.value.details["errors"][0]["index"] == 1
    assert count(truth, "memories") == 0 and count(truth, "proposals") == 0 and count(truth, "entities") == 0


def test_unknown_op_rejected(truth):
    with pytest.raises(ValidationError):
        propose(truth, S, [{"op": "explode"}], actor=AGENT)
    with pytest.raises(ValidationError):
        propose(truth, S, [], actor=AGENT)
