import json
import shutil
from pathlib import Path

import pytest

from pmi import Actor, Memory
from pmi.cli import main

FIXTURES = Path(__file__).parent / "fixtures"
SLOT = "project-a/launch-date"


def run(capsys, home, *args):
    code = main(["--home", str(home), "--json", *args])
    out = capsys.readouterr().out
    return code, (json.loads(out) if out.strip() else None)


@pytest.fixture
def imported(home, tmp_path, capsys):
    shutil.copytree(FIXTURES, tmp_path / "notes")
    code, out = run(capsys, home, "import", str(tmp_path / "notes"), "--extract")
    assert code == 0 and len(out["imported"]) == 2
    return home


def test_cli_import_and_context(imported, capsys):
    code, out = run(capsys, imported, "context", "Project A 现在什么时候上线")
    assert code == 0
    [m] = [m for m in out["memories"] if m["slot"] == SLOT]
    assert "12 月 10 日" in m["content"]


def test_cli_json_flag_after_subcommand(imported, capsys):
    code = main(["--home", str(imported), "search", "上线", "--json"])
    assert code == 0 and json.loads(capsys.readouterr().out)["memories"]


def test_cli_human_context_output(imported, capsys):
    assert main(["--home", str(imported), "context", "Project A 现在什么时候上线"]) == 0
    text = capsys.readouterr().out
    assert "Current" in text and "History" in text and "12 月 10 日" in text and "source_backed" in text


def test_cli_note_and_search(home, capsys):
    code, out = run(capsys, home, "note", "---\ndate: 2026-11-01\n---\n## 决策\n- 周报改到周四发", "--extract")
    assert code == 0 and out["extraction"]["applied"] == 1
    code, out = run(capsys, home, "search", "周报")
    assert [m["content"] for m in out["memories"]] == ["周报改到周四发"]


def test_cli_propose_file(home, tmp_path, capsys):
    ops = tmp_path / "ops.json"
    ops.write_text(json.dumps([{"op": "create", "memory": {"kind": "preference", "content": "先说结论"}}]),
                   encoding="utf-8")
    code, out = run(capsys, home, "propose", str(ops), "--reason", "manual")
    assert code == 0 and out["applied"] == 1


def test_cli_review_flow(imported, capsys):
    with Memory.open(imported, embedder=None) as m:
        current = m.truth.execute("select id from memories where slot=? and status='active'", (SLOT,)).fetchone()[0]
        m.confirm(current)
        s = m.add_source(text="Slack: 上线改到 1 月", actor=Actor.agent("bot"), source_time="2026-11-05")
        r = m.propose([{"op": "create", "memory": {"kind": "decision", "content": "上线改到 1 月", "slot": SLOT,
                        "evidence": [{"source_id": s["source_id"], "quote": "上线改到 1 月"}]}}],
                      actor=Actor.agent("bot"))["results"][0]
    code, out = run(capsys, imported, "review", "list")
    assert code == 0 and [p["id"] for p in out["items"]] == [r["proposal_id"]]
    code, out = run(capsys, imported, "review", "approve", r["proposal_id"])
    assert code == 0 and out["status"] == "applied"
    code, out = run(capsys, imported, "show", r["memory_id"])
    assert out["items"][0]["verification"] == "user_confirmed"


def test_cli_forget_refuses_then_cascade(imported, capsys):
    with Memory.open(imported, embedder=None) as m:
        sid = m.truth.execute("select id from sources limit 1").fetchone()[0]
    code, out = run(capsys, imported, "forget", sid, "--reason", "sensitive")
    assert code == 1 and out["error"] == "conflict" and out["details"]["affected_memories"]
    code, out = run(capsys, imported, "forget", sid, "--reason", "sensitive", "--cascade")
    assert code == 0 and out["forgotten_sources"] == [sid]


def test_cli_verify_exit_code(imported, capsys):
    assert run(capsys, imported, "verify")[0] == 0
    with Memory.open(imported, embedder=None) as m:
        path = m.truth.execute("select file_path from sources where file_path is not null").fetchone()[0]
        (m.paths.data / path).write_text("tampered")
    code, out = run(capsys, imported, "verify")
    assert code == 1 and out["ok"] is False


def test_cli_backup_restore(imported, tmp_path, capsys):
    archive = tmp_path / "b.tar.gz"
    assert run(capsys, imported, "backup", "--out", str(archive))[0] == 0
    code, out = run(capsys, imported, "restore", str(archive), "--to", str(tmp_path / "restored"))
    assert code == 0 and out["verify"]["ok"] is True
    assert run(capsys, imported, "restore", str(archive))[0] == 1  # current home has data, no --force


def test_cli_archive_and_entity_alias(imported, capsys):
    with Memory.open(imported, embedder=None) as m:
        mid = m.truth.execute("select id from memories where kind='episodic' limit 1").fetchone()[0]
        eid = m.list("entities", kind="project")["items"][0]["id"]
    assert run(capsys, imported, "archive", mid, "--reason", "noise")[1]["results"][0]["status"] == "archived"
    assert run(capsys, imported, "restore-memory", mid)[1]["results"][0]["outcome"] == "applied"
    assert "PA" in run(capsys, imported, "entity", "alias", eid, "PA")[1]["aliases"]
    assert run(capsys, imported, "entity", "list", "--kind", "project")[1]["items"][0]["id"] == eid


def test_cli_usage_error_exit_2(home, capsys):
    assert main(["--home", str(home), "bogus"]) == 2
    assert main(["--home", str(home), "forget", "src_x"]) == 2  # --reason is required
