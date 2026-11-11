import urllib.parse
from types import SimpleNamespace

import pytest

from pmi import Memory
from pmi.actors import Actor
from pmi.config import Settings
from pmi.evolution import propose
from tests.helpers import seed_source
from tests.ui_support import logdir, ui  # noqa: F401  (fixtures)

S = Settings()
SLOT = "project-a/launch-date"
PLAIN = "转写导出支持 PDF 和 DOCX 格式"


@pytest.fixture
def seeded(home, truth):
    """An owner-confirmed memory, an agent's conflicting claim (disputed + pending proposal), and a
    plain active memory whose source has a URL."""
    dec = seed_source(truth, "10-20 周会：上线 12 月 10 日", source_time="2026-10-20")
    jan = seed_source(truth, "slack: 上线改到 1 月", trust="external", source_time="2026-11-06")
    confirmed = propose(truth, S, [{"op": "create", "memory": {
        "kind": "decision", "content": "上线 12 月 10 日", "slot": SLOT, "verification": "user_confirmed",
        "evidence": [{"source_id": dec, "quote": "上线 12 月 10 日"}]}}], actor=Actor.owner())["results"][0]["memory_id"]
    r = propose(truth, S, [{"op": "create", "memory": {
        "kind": "decision", "content": "上线改到 1 月", "slot": SLOT,
        "evidence": [{"source_id": jan, "quote": "上线改到 1 月"}]}}], actor=Actor.agent("t"))["results"][0]
    with Memory.open(home, embedder=None) as m:
        src = m.add_source(text=PLAIN, url="https://example.com/doc")["source_id"]
        plain = m.propose([{"op": "create", "memory": {
            "kind": "semantic", "content": PLAIN,
            "evidence": [{"source_id": src, "quote": PLAIN}]}}])["results"][0]["memory_id"]
    return SimpleNamespace(confirmed=confirmed, disputed=r["memory_id"], proposal=r["proposal_id"],
                           plain=plain, plain_text=PLAIN, slot=SLOT)


def test_overview(ui, seeded):
    s, o, _ = ui.call("GET", "/api/overview")
    assert o["memories"]["active"] == 2 and o["memories"]["disputed"] == 1 and o["pending_proposals"] == 1
    assert o["latest_log"]["lines"][-1] == "[DM] hi" and o["disk"]["total_bytes"] > 0
    assert len(o["recent_memories"]) == 3 and o["logs"][0]["status"]["state"] == "active"
    assert len(o["disk"]["top"]) == 2


def test_list_filters_and_paging(ui, seeded):
    def get(q):
        return ui.call("GET", "/api/memories" + q)[1]["items"]

    assert [m["id"] for m in get("?status=disputed")] == [seeded.disputed]
    assert len(get("?status=all")) == 3 and len(get("?status=all&limit=2&offset=2")) == 1
    assert {m["id"] for m in get("?slot=" + seeded.slot)} == {seeded.confirmed}
    assert all("updated_at" in m and "created_by" in m for m in get("?status=all"))
    assert seeded.plain in [m["id"] for m in get("?q=" + urllib.parse.quote(seeded.plain_text))]
    assert ui.call("GET", "/api/memories?status=bogus")[0] == 400


def test_detail_actions_and_links(ui, seeded):
    plain = ui.call("GET", f"/api/memories/{seeded.plain}")[1]
    assert plain["actions"] == {"confirm": True, "resolve": False, "archive": True, "restore": False, "forget": True}
    assert plain["evidence"][0]["url"] == "https://example.com/doc"
    disputed = ui.call("GET", f"/api/memories/{seeded.disputed}")[1]
    assert disputed["actions"]["confirm"] is False and disputed["actions"]["resolve"] is True
    assert disputed["pending_proposals"] == [seeded.proposal]


def test_confirm_archive_restore_forget(ui, seeded, home):
    mid = seeded.plain
    assert ui.call("POST", f"/api/memories/{mid}/confirm", {})[0] == 200
    with Memory.open(home, embedder=None) as m:
        assert m.get([mid])["items"][0]["verification"] == "user_confirmed"
        assert m.list("recent")["items"][0]["actor"] == "owner:ui"
    ui.call("POST", f"/api/memories/{mid}/archive", {})
    assert ui.call("GET", f"/api/memories/{mid}")[1]["status"] == "archived"
    ui.call("POST", f"/api/memories/{mid}/restore", {})
    assert ui.call("GET", f"/api/memories/{mid}")[1]["status"] == "active"
    assert ui.call("POST", f"/api/memories/{mid}/forget", {"reason": "test"})[0] == 200
    assert ui.call("GET", f"/api/memories/{mid}")[0] == 404


def test_confirm_disputed_is_conflict(ui, seeded):
    s, body, _ = ui.call("POST", f"/api/memories/{seeded.disputed}/confirm", {})
    assert s == 409 and body["error"]["code"] == "conflict"


def test_approve(ui, seeded):
    assert ui.call("POST", f"/api/proposals/{seeded.proposal}/approve", {})[0] == 200
    assert ui.call("GET", "/api/proposals")[1]["items"] == []
    assert ui.call("GET", f"/api/memories/{seeded.disputed}")[1]["status"] == "active"


def test_reject_requires_reason(ui, seeded):
    assert ui.call("POST", f"/api/proposals/{seeded.proposal}/reject", {"reason": " "})[0] == 400
    assert ui.call("POST", f"/api/proposals/{seeded.proposal}/reject", {"reason": "no"})[0] == 200
    assert ui.call("GET", "/api/proposals")[1]["items"] == []


@pytest.mark.parametrize("method, path", [
    ("GET", "/api/memories/mem_nope"), ("GET", "/api/memories/%2e%2e"), ("GET", "/api/memories/src_1"),
    ("POST", "/api/proposals/prp_nope/approve"), ("POST", "/api/memories/mem_nope/archive")])
def test_bad_ids_are_404(ui, seeded, method, path):
    s, body, _ = ui.call(method, path, {} if method == "POST" else None)
    assert s == 404 and body["error"]["code"] == "not_found"


@pytest.mark.parametrize("kw", [
    {"raw": b"{}", "ctype": "text/plain"}, {"raw": b"not json"}, {"raw": b"[1]"},
    {"raw": b"{}"}, {"raw": b'{"reason": "' + b"x" * 70000 + b'"}'}])
def test_malformed_posts_change_nothing(ui, seeded, kw):
    s, body, _ = ui.call("POST", f"/api/memories/{seeded.plain}/forget", **kw)
    assert s == 400 and body["error"]["code"] == "validation"
    assert ui.call("GET", f"/api/memories/{seeded.plain}")[0] == 200


def test_failed_approval_is_an_error(ui, seeded):
    ui.call("POST", f"/api/memories/{seeded.disputed}/archive", {})  # the proposal can no longer apply
    s, body, _ = ui.call("POST", f"/api/proposals/{seeded.proposal}/approve", {})
    assert s == 409 and body["error"]["code"] == "conflict" and "archived" in body["error"]["message"]
    assert ui.call("GET", "/api/proposals")[1]["items"] == []


def test_refused_restore_shows_the_reason(ui, seeded, home):
    with Memory.open(home, embedder=None) as m:
        src = m.add_source(text="项目 B 的发布窗口是周四")["source_id"]
        make = lambda: m.propose([{"op": "create", "memory": {"kind": "semantic", "slot": "b/window",
            "content": "项目 B 的发布窗口是周四", "evidence": [{"source_id": src, "quote": "发布窗口是周四"}]}}])
        first = make()["results"][0]["memory_id"]
        m.propose([{"op": "archive", "id": first}])
        m.propose([{"op": "create", "memory": {"kind": "semantic", "slot": "b/window", "content": "项目 B 改为周五发布",
                                               "evidence": [{"source_id": src, "quote": "发布窗口是周四"}]}}])
    s, body, _ = ui.call("POST", f"/api/memories/{first}/restore", {})
    assert s == 400 and "held by" in body["error"]["message"]


def test_search_with_status_all_includes_archived(ui, seeded):
    ui.call("POST", f"/api/memories/{seeded.plain}/archive", {})
    q = urllib.parse.quote(seeded.plain_text)
    assert seeded.plain in [m["id"] for m in ui.call("GET", f"/api/memories?q={q}&status=all")[1]["items"]]
    assert seeded.plain not in [m["id"] for m in ui.call("GET", f"/api/memories?q={q}")[1]["items"]]


def test_deeply_nested_body_is_400(ui, seeded):
    s, body, _ = ui.call("POST", f"/api/memories/{seeded.plain}/forget", raw=b"[" * 50000)
    assert s == 400 and body["error"]["code"] == "validation"
