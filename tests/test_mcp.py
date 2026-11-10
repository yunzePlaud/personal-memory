import asyncio
import shutil
import sys
from pathlib import Path

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from pmi import Memory

FIXTURES = Path(__file__).parent / "fixtures"
TOOLS = {"memory_get_context", "memory_search", "memory_get", "memory_add_source", "memory_propose", "memory_list"}
CONTEXT_KEYS = {"query", "as_of", "intent", "entities", "memories", "history", "conflicts", "sources",
                "insufficient_evidence", "notes", "content_notice", "truncated", "token_estimate"}


@pytest.fixture
def home_with_data(home, tmp_path):
    shutil.copytree(FIXTURES, tmp_path / "notes")
    with Memory.open(home, embedder=None) as m:
        m.import_paths([str(tmp_path / "notes")], extract=True)
    return home


def session(home, fn):
    params = StdioServerParameters(command=sys.executable, args=["-m", "pmi", "--home", str(home), "mcp"])

    async def go():
        async with Client(params) as c:
            return await fn(c)
    return asyncio.run(go())


def test_client_discovers_six_tools_and_instructions(home):
    async def fn(c):
        tools = await c.list_tools()
        return {t.name for t in tools.tools}, c.instructions
    names, instructions = session(home, fn)
    assert names == TOOLS
    assert "memory_get_context" in instructions and "not instructions" in instructions


def test_no_owner_tools_exposed(home):
    async def fn(c):
        return [t.name for t in (await c.list_tools()).tools]
    names = session(home, fn)
    assert not any(w in n for n in names for w in ("forget", "confirm", "review", "approve", "reject"))


def test_get_context_over_stdio(home_with_data):
    async def fn(c):
        r = await c.call_tool("memory_get_context", {"query": "为什么 Project A 不采用方案 B"})
        return r
    r = session(home_with_data, fn)
    assert not r.is_error and CONTEXT_KEYS <= set(r.structured_content)
    assert "方案 B" in r.structured_content["memories"][0]["content"]


def test_propose_records_agent_actor(home):
    async def fn(c):
        s = await c.call_tool("memory_add_source", {"text": "Agent 分析：周报应在周四发出", "trust": "agent"})
        sid = s.structured_content["source_id"]
        return await c.call_tool("memory_propose", {"ops": [{"op": "create", "memory": {
            "kind": "procedure", "content": "周报应在周四发出",
            "evidence": [{"source_id": sid, "quote": "周报应在周四发出"}]}}]})
    r = session(home, fn)
    assert r.structured_content["results"][0]["outcome"] == "applied"
    with Memory.open(home, embedder=None) as m:
        actors = {row[0] for row in m.truth.execute("select actor from audit_log where action = 'memory.create'")}
    assert actors and all(a.startswith("agent:") for a in actors)


def test_malformed_ops_returns_error_and_server_survives(home):
    async def fn(c):
        bad = await c.call_tool("memory_propose", {"ops": [{"op": "explode"}]})
        wrong_type = await c.call_tool("memory_propose", {"ops": "not a list"})
        ok = await c.call_tool("memory_list", {"what": "recent"})
        return bad, wrong_type, ok
    bad, wrong_type, ok = session(home, fn)
    assert bad.structured_content["error"] == "validation"
    assert wrong_type.is_error or (wrong_type.structured_content or {}).get("error")
    assert not ok.is_error and "items" in ok.structured_content
    with Memory.open(home, embedder=None) as m:
        assert m.truth.execute("select count(*) from memories").fetchone()[0] == 0


def test_agent_cannot_override_confirmed_via_mcp(home_with_data):
    with Memory.open(home_with_data, embedder=None) as m:
        mid = m.truth.execute("select id from memories where slot='project-a/launch-date' and status='active'").fetchone()[0]
        m.confirm(mid)
        before = m.get([mid])["items"][0]["content"]

    async def fn(c):
        return await c.call_tool("memory_propose", {"ops": [{"op": "update", "id": mid,
                                                             "changes": {"content": "上线改到 2 月"}}]})
    r = session(home_with_data, fn)
    assert r.structured_content["results"][0]["outcome"] == "pending"
    with Memory.open(home_with_data, embedder=None) as m:
        assert m.get([mid])["items"][0]["content"] == before


def test_agent_owner_trust_capped_over_mcp(home):
    async def fn(c):
        return await c.call_tool("memory_add_source", {"text": "伪装成 owner 的内容", "trust": "owner"})
    r = session(home, fn)
    assert "trust capped to agent" in r.structured_content["notes"]
