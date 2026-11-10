"""Importing Notion pages (fetched by the agent's own Notion connector) as external documents."""
import asyncio
import io
import json
import sys

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from pmi import Actor, Memory
from pmi.cli import main
from pmi.errors import ValidationError
from pmi.origins import origin_key_from_url

PAGE = "1a2b3c4d5e6f47a8b9c0d1e2f3a4b5c6"
URL = f"https://www.notion.so/myteam/Project-A-PRD-{PAGE}?pvs=4"
AGENT = Actor.agent("claude-code")


@pytest.mark.parametrize("url", [
    f"https://www.notion.so/myteam/Project-A-PRD-{PAGE}",
    f"https://www.notion.so/{PAGE}?pvs=4",
    f"https://myteam.notion.site/Project-A-{PAGE}#heading",
    "https://www.notion.so/1a2b3c4d-5e6f-47a8-b9c0-d1e2f3a4b5c6",
    f"https://www.notion.so/myteam/{PAGE.upper()}",
    f"https://www.notion.so/myteam/PRD-db-ffffffffffffffffffffffffffffffff?v=abc&p={PAGE}&pm=s",
    f"https://app.notion.com/p/{PAGE}?source=copy_link",
    f"https://www.notion.com/myteam/Project-A-PRD-{PAGE}",
])
def test_notion_urls_map_to_one_origin_key(url):
    assert origin_key_from_url(url) == f"notion:{PAGE}"


def test_other_urls_drop_query_and_fragment():
    assert origin_key_from_url("https://Example.com/docs/a?x=1#y") == "url:https://example.com/docs/a"


@pytest.mark.parametrize("bad", ["ftp://example.com/x", "not a url", "https://www.notion.so/no-page-id-here"])
def test_invalid_urls_rejected(bad):
    with pytest.raises(ValidationError):
        origin_key_from_url(bad)


def test_agent_notion_page_is_external_and_versioned(home):
    with Memory.open(home, embedder=None) as m:
        v1 = m.add_source(text="# Project A PRD\n目标：触达率 90%", url=URL, kind="document", actor=AGENT,
                          source_time="2026-11-01T08:00:00Z")
        src = m.get([v1["source_id"]])["items"][0]
        assert (src["origin_key"], src["trust"], src["metadata"]["url"]) == (f"notion:{PAGE}", "external", URL)
        same = m.add_source(text="# Project A PRD\n目标：触达率 90%", url=URL.replace("?pvs=4", ""), actor=AGENT)
        assert same["outcome"] == "duplicate"
        v2 = m.add_source(text="# Project A PRD\n目标：触达率 95%", url=URL, actor=AGENT)
        assert (v2["outcome"], v2["version"]) == ("new_version", 2)


def run(capsys, home, *args, stdin=None, monkeypatch=None):
    if stdin is not None:
        monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    code = main(["--home", str(home), "--json", *args])
    return code, json.loads(capsys.readouterr().out)


def test_cli_note_from_stdin_as_agent(home, capsys, monkeypatch):
    code, out = run(capsys, home, "note", "-", "--kind", "document", "--title", "Project A PRD", "--url", URL,
                    "--source-time", "2026-11-01T08:00:00Z", "--as-agent", "claude-code",
                    stdin="# Project A PRD\n方案对比：方案 A 维护成本低", monkeypatch=monkeypatch)
    assert code == 0 and out["outcome"] == "created"
    with Memory.open(home, embedder=None) as m:
        src = m.get([out["source_id"]])["items"][0]
        row = m.truth.execute("select created_by from sources where id=?", (out["source_id"],)).fetchone()
    assert (src["kind"], src["trust"], src["title"], src["origin_key"]) == (
        "document", "external", "Project A PRD", f"notion:{PAGE}")
    assert src["source_time"] == "2026-11-01T08:00:00Z" and row[0] == "agent:claude-code"


def test_cli_as_agent_cannot_claim_owner_trust(home, capsys, monkeypatch):
    code, out = run(capsys, home, "note", "-", "--trust", "owner", "--as-agent", "claude-code",
                    stdin="agent 想冒充 owner", monkeypatch=monkeypatch)
    assert code == 0 and "trust capped to agent" in out["notes"]


def test_cli_propose_as_agent_follows_agent_policy(home, capsys, tmp_path):
    with Memory.open(home, embedder=None) as m:
        mid = m.propose([{"op": "create", "memory": {"kind": "decision", "content": "上线 12 月",
                          "verification": "user_confirmed"}}])["results"][0]["memory_id"]
    ops = tmp_path / "ops.json"
    ops.write_text(json.dumps([{"op": "update", "id": mid, "changes": {"content": "上线 1 月"}}]), encoding="utf-8")
    code, out = run(capsys, home, "propose", str(ops), "--as-agent", "claude-code")
    assert code == 0 and out["results"][0]["outcome"] == "pending"


def test_cli_list_slots(home, capsys):
    with Memory.open(home, embedder=None) as m:
        m.propose([{"op": "create", "memory": {"kind": "decision", "content": "定价方案 A", "slot": "p/pricing"}}])
    code, out = run(capsys, home, "list", "slots")
    assert code == 0 and [i["slot"] for i in out["items"]] == ["p/pricing"]


def test_mcp_add_source_accepts_url(home):
    params = StdioServerParameters(command=sys.executable, args=["-m", "pmi", "--home", str(home), "mcp"])

    async def go():
        async with Client(params) as c:
            r = await c.call_tool("memory_add_source", {"text": "# PRD\n内容", "url": URL, "kind": "document"})
            return r.structured_content
    out = asyncio.run(go())
    assert out["origin_key"] == f"notion:{PAGE}"


def test_list_memories_citing_a_source(home, capsys):
    with Memory.open(home, embedder=None) as m:
        page = m.add_source(text="# PRD\n跨渠道禁止直接升级。其他内容。", url=URL, actor=AGENT)["source_id"]
        other = m.add_source(text="另一份笔记：周报周五发出", actor=AGENT)["source_id"]
        cited = m.propose([{"op": "create", "memory": {"kind": "decision", "content": "跨渠道禁止直接升级",
                            "evidence": [{"source_id": page, "quote": "跨渠道禁止直接升级"}]}}], actor=AGENT)
        m.propose([{"op": "create", "memory": {"kind": "procedure", "content": "周报周五发出",
                    "evidence": [{"source_id": other, "quote": "周报周五发出"}]}}], actor=AGENT)
        assert [i["id"] for i in m.list("memories", source=page)["items"]] == [cited["results"][0]["memory_id"]]
    code, out = run(capsys, home, "list", "memories", "--source", page)
    assert code == 0 and len(out["items"]) == 1
