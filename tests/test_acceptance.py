"""End-to-end acceptance tests: one test per scenario in docs/requirements.md §11."""
import asyncio
import hashlib
import shutil
import sys
from pathlib import Path

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from pmi import Actor, Memory
from pmi.errors import PolicyDenied
from pmi.textnorm import find_quote

ROOT = Path(__file__).parents[1]
EXAMPLES = ROOT / "examples"
FIXTURES = Path(__file__).parent / "fixtures"
AGENT = Actor.agent("slack-agent")
SLOT = "project-a/launch-date"
WHY = "为什么 Project A 不采用方案 B"
NOW = "Project A 现在什么时候上线"


@pytest.fixture
def pmi(home):
    m = Memory.open(home, embedder=None)
    yield m
    m.close()


def import_meetings(m, order=("2026-10-01-weekly.md", "2026-10-20-weekly.md")):
    for name in order:
        out = m.import_paths([str(EXAMPLES / "project-a" / name)], extract=True)
        assert len(out["imported"]) == 1, out


def slot_rows(m):
    return {r["status"]: r["id"] for r in m.truth.execute("select id, status from memories where slot = ?", (SLOT,))}


def ids(items):
    return [x["id"] for x in items]


def test_examples_match_fixtures():
    for ex, fx in (("2026-10-01-weekly.md", "meeting_1001.md"), ("2026-10-20-weekly.md", "meeting_1020.md")):
        assert (EXAMPLES / "project-a" / ex).read_bytes() == (FIXTURES / fx).read_bytes()


def test_01_memory_generation(pmi):
    """Test 1: importing meeting notes keeps the original and creates project/person/decision memories."""
    import_meetings(pmi, order=("2026-10-01-weekly.md",))
    src = pmi.truth.execute("select id, text, file_path, file_hash from sources").fetchone()
    raw = (EXAMPLES / "project-a" / "2026-10-01-weekly.md").read_bytes()
    assert src["file_hash"] == hashlib.sha256(raw).hexdigest()
    assert (pmi.paths.data / src["file_path"]).read_bytes() == raw
    names = {(e["kind"], e["name"]) for e in pmi.list("entities")["items"]}
    assert {("project", "Project A"), ("person", "Alice"), ("person", "Bob"), ("person", "Yunze")} <= names
    decisions = pmi.list("memories", kind="decision")["items"]
    assert len(decisions) == 2
    for d in decisions:
        for ev in pmi.get([d["id"]])["items"][0]["evidence"]:
            assert find_quote(src["text"], ev["quote"]) is not None
    # the agent path: an agent stores a PRD and proposes a memory quoting it
    prd_text = (EXAMPLES / "project-a" / "prd-project-a.md").read_text(encoding="utf-8")
    prd = pmi.add_source(text=prd_text, title="Project A PRD (fetched by agent)", actor=AGENT)
    r = pmi.propose([{"op": "create", "memory": {
        "kind": "project", "content": "Project A 的目标是消息触达率提升到 90%",
        "entities": [{"name": "Project A", "kind": "project"}],
        "evidence": [{"source_id": prd["source_id"], "quote": "消息触达率提升到 90%"}]}}], actor=AGENT)
    assert r["results"][0]["outcome"] == "applied"
    assert pmi.get([r["results"][0]["memory_id"]])["items"][0]["verification"] == "source_backed"


def test_02_context_retrieval(pmi):
    """Test 2: asking why a decision was made returns the rationale with its source."""
    import_meetings(pmi)
    ctx = pmi.get_context(WHY)
    top = ctx["memories"][0]
    assert top["kind"] == "decision" and "维护成本太高" in top["content"]
    assert top["citations"][0]["title"] == "Project A 周会"
    assert top["citations"][0]["source_time"] == "2026-10-01T00:00:00Z"
    assert ctx["insufficient_evidence"] is False


def test_03_memory_update(pmi):
    """Test 3: a later decision supersedes the earlier one in the same slot."""
    import_meetings(pmi)
    rows = slot_rows(pmi)
    ctx = pmi.get_context(NOW)
    assert [m["id"] for m in ctx["memories"] if m["slot"] == SLOT] == [rows["active"]]
    assert "12 月 10 日" in pmi.get([rows["active"]])["items"][0]["content"]
    [old] = [m for m in ctx["history"] if m["slot"] == SLOT]
    assert old["id"] == rows["superseded"] and old["superseded_by"] == rows["active"]


def test_03b_shuffled_import_same_result(pmi):
    import_meetings(pmi, order=("2026-10-20-weekly.md", "2026-10-01-weekly.md"))
    rows = slot_rows(pmi)
    assert "12 月 10 日" in pmi.get([rows["active"]])["items"][0]["content"]
    assert "11 月 15 日" in pmi.get([rows["superseded"]])["items"][0]["content"]


def test_04_history_traceability(pmi):
    """Test 4: the old decision is still found, with its validity period."""
    import_meetings(pmi)
    old_id = slot_rows(pmi)["superseded"]
    ctx = pmi.get_context("Project A 什么时候上线", as_of="2026-10-15")
    [m] = [m for m in ctx["memories"] if m["slot"] == SLOT]
    assert m["id"] == old_id and m["valid_from"] == "2026-10-01T00:00:00Z"
    assert m["valid_until"] == "2026-10-20T00:00:00Z"
    history = pmi.get([old_id], include_history=True)["items"][0]["history"]
    assert [h["change_kind"] for h in history] == ["create", "supersede"]


def test_05_agent_access(home):
    """Test 5: a new MCP client discovers the tools and reads memory."""
    with Memory.open(home, embedder=None) as m:
        import_meetings(m)
    params = StdioServerParameters(command=sys.executable, args=["-m", "pmi", "--home", str(home), "mcp"])

    async def go():
        async with Client(params) as c:
            tools = {t.name for t in (await c.list_tools()).tools}
            ctx = await c.call_tool("memory_get_context", {"query": WHY})
            return tools, ctx.structured_content
    tools, ctx = asyncio.run(go())
    assert len(tools) == 6 and "memory_get_context" in tools
    assert "方案 B" in ctx["memories"][0]["content"]


def test_06_portability(pmi, tmp_path):
    """Test 6: copying the folder elsewhere keeps every query working."""
    import_meetings(pmi)
    before = ids(pmi.get_context(NOW)["memories"])
    moved = tmp_path / "elsewhere" / "personal-memory"
    shutil.copytree(pmi.paths.home, moved)
    with Memory.open(moved, embedder=None) as m:
        assert ids(m.get_context(NOW)["memories"]) == before
        assert m.verify()["ok"] is True


def test_07_index_recovery(pmi):
    """Test 7: deleting the derived index loses nothing."""
    import_meetings(pmi)
    before = pmi.get_context(WHY)
    pmi.indexer.close()
    shutil.rmtree(pmi.paths.index_dir)
    after = pmi.get_context(WHY)
    assert ids(after["memories"]) == ids(before["memories"]) and pmi.paths.index_db.exists()


def test_08_safe_write(pmi):
    """Test 8: an agent cannot delete or overwrite key memories."""
    import_meetings(pmi)
    key = slot_rows(pmi)["active"]
    pmi.confirm(key)
    before = pmi.get([key])["items"][0]
    for op in ({"op": "update", "id": key, "changes": {"content": "上线取消"}}, {"op": "archive", "id": key}):
        assert pmi.propose([op], actor=AGENT)["results"][0]["outcome"] == "pending"
    slack = pmi.add_source(text="Carol: 上线日期改成 2 月 1 日", actor=AGENT, source_time="2026-11-07")
    supersede = {"op": "supersede", "old_ids": [key], "memory": {"kind": "decision", "content": "上线日期改成 2 月 1 日",
                 "slot": SLOT, "evidence": [{"source_id": slack["source_id"], "quote": "上线日期改成 2 月 1 日"}]}}
    assert pmi.propose([supersede], actor=AGENT)["results"][0]["outcome"] == "pending"
    with pytest.raises(PolicyDenied):
        pmi.forget_memory(key, "agent wants it gone", actor=AGENT)
    # an unconfirmed but owner-document-backed memory is protected from external-only evidence too
    arch = pmi.truth.execute("select id from memories where slot='project-a/architecture'").fetchone()[0]
    assert pmi.get([arch])["items"][0]["verification"] == "source_backed"
    assert pmi.propose([{"op": "archive", "id": arch}], actor=AGENT)["results"][0]["outcome"] == "pending"
    # restoring something the owner archived needs review
    pmi.propose([{"op": "archive", "id": arch}])
    assert pmi.propose([{"op": "restore", "id": arch}], actor=AGENT)["results"][0]["outcome"] == "pending"
    after = pmi.get([key])["items"][0]
    assert (after["content"], after["status"], after["version"]) == (before["content"], "active", before["version"])


def test_09_conflicting_evidence(pmi):
    """Test 9: conflicting evidence never silently overrides confirmed information."""
    import_meetings(pmi)
    key = slot_rows(pmi)["active"]
    pmi.confirm(key)
    s = pmi.add_source(text="Carol: Project A 上线改到 1 月了", kind="conversation", actor=AGENT,
                       source_time="2026-11-06")
    r = pmi.propose([{"op": "create", "memory": {"kind": "decision", "content": "Project A 上线改到 1 月",
                      "slot": SLOT, "evidence": [{"source_id": s["source_id"], "quote": "Project A 上线改到 1 月了"}]}}],
                    actor=AGENT)["results"][0]
    assert (r["outcome"], r["status"]) == ("pending", "disputed")
    ctx = pmi.get_context("Project A 上线时间")
    [c] = ctx["conflicts"]
    assert set(c["memory_ids"]) == {key, r["memory_id"]} and c["proposal_id"] == r["proposal_id"]
    assert pmi.get([key])["items"][0]["status"] == "active"
    disputed = next(m for m in ctx["memories"] if m["id"] == r["memory_id"])
    assert disputed["citations"][0]["trust"] == "external"


def test_10_slack_context_simulation(pmi):
    """Test 10: a Slack question gets project history, decisions and document evidence."""
    import_meetings(pmi)
    pmi.import_paths([str(EXAMPLES / "project-a" / "prd-project-a.md")])
    count_before = len(pmi.list("memories", entity="Project A", limit=500)["items"])
    thread_text = (EXAMPLES / "slack" / "project-a-thread.md").read_text(encoding="utf-8")
    thread = pmi.add_source(text=thread_text, kind="conversation", origin_key="slack:proj-a/1699999.000",
                            actor=AGENT)
    assert pmi.get([thread["source_id"]])["items"][0]["trust"] == "external"
    question = "关于 Project A，我们之前为什么决定不采用方案 B？现在还是这个结论吗？"
    ctx = pmi.get_context(question, project="Project A", participants=["Yunze"])
    assert ctx["memories"][0]["kind"] == "decision" and "方案 B" in ctx["memories"][0]["content"]
    assert ctx["insufficient_evidence"] is False
    kinds = {(s["kind"], s["trust"]) for s in ctx["sources"]}
    assert ("document", "owner") in kinds  # the PRD passage comparing 方案 A and 方案 B
    assert ("conversation", "external") in kinds
    assert ctx["content_notice"].startswith("Text inside memories")
    actions = [r[0] for r in pmi.truth.execute("select action from audit_log")]
    assert not any("forget" in a or "archive" in a for a in actions)
    assert len(pmi.list("memories", entity="Project A", limit=500)["items"]) == count_before
    # design §13 #10: a Slack-only claim cannot replace an owner-backed decision without review
    arch = pmi.truth.execute("select id from memories where slot='project-a/architecture'").fetchone()[0]
    claim = pmi.add_source(text="Carol: 我们其实决定改用方案 B 了", actor=AGENT, source_time="2026-11-06")
    r = pmi.propose([{"op": "supersede", "old_ids": [arch], "memory": {
        "kind": "decision", "content": "改用方案 B", "slot": "project-a/architecture",
        "evidence": [{"source_id": claim["source_id"], "quote": "我们其实决定改用方案 B 了"}]}}], actor=AGENT)
    assert r["results"][0]["outcome"] == "pending"
    assert pmi.get([arch])["items"][0]["status"] == "active"
