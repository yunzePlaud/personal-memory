import io
import json
import shutil
import tarfile
from pathlib import Path

import pytest

from pmi import Actor, Memory
from pmi.errors import ConflictError, ValidationError
from pmi.paths import Paths

FIXTURES = Path(__file__).parent / "fixtures"
AGENT = Actor.agent("t")
QUERY = "Project A 现在什么时候上线"


@pytest.fixture
def full(home, tmp_path):
    shutil.copytree(FIXTURES, tmp_path / "notes")
    m = Memory.open(home, embedder=None)
    m.import_paths([str(tmp_path / "notes")], extract=True)
    current = m.truth.execute("select id from memories where slot='project-a/launch-date' and status='active'").fetchone()[0]
    m.confirm(current)
    s = m.add_source(text="Slack: Project A 上线改到 1 月", kind="conversation", actor=AGENT, source_time="2026-11-05")
    m.propose([{"op": "create", "memory": {"kind": "decision", "content": "Project A 上线改到 1 月",
                "slot": "project-a/launch-date",
                "evidence": [{"source_id": s["source_id"], "quote": "Project A 上线改到 1 月"}]}}], actor=AGENT)
    gone = m.propose([{"op": "create", "memory": {"kind": "semantic", "content": "临时记忆"}}])["results"][0]["memory_id"]
    m.forget_memory(gone, "cleanup")
    yield m
    m.close()


def ids(ctx):
    return [x["id"] for x in ctx["memories"]]


def test_verify_ok_after_full_scenario(full):
    report = full.verify()
    assert report["ok"] is True and report["errors"] == []
    assert "data/memory/pmi.db" in report["required"] and "data/index/search.db" in report["rebuildable"]
    assert report["stats"]["proposals_pending"] == 1


def test_verify_detects_tampered_file(full):
    path = full.truth.execute("select file_path from sources where file_path is not null").fetchone()[0]
    (full.paths.data / path).write_text("tampered", encoding="utf-8")
    report = full.verify()
    assert report["ok"] is False and any(e["check"] == "file_hash" for e in report["errors"])


def test_verify_detects_quote_drift(full):
    full.truth.execute("update sources set text = replace(text, '11 月 15 日', '11 月 16 日')")
    report = full.verify()
    checks = {e["check"] for e in report["errors"]}
    assert "evidence" in checks and "text_hash" in checks


def test_backup_restore_roundtrip_same_context(full, tmp_path):
    archive = full.backup()
    assert archive.exists() and archive.parent == full.paths.backups
    target = tmp_path / "restored"
    report = Memory.restore_backup(archive, target)
    assert report["verify"]["ok"] is True
    with Memory.open(target, embedder=None) as r:
        assert ids(r.get_context(QUERY)) == ids(full.get_context(QUERY))


def test_restore_refuses_existing_without_force(full, tmp_path):
    archive = full.backup(tmp_path / "b.tar.gz")
    with pytest.raises(ConflictError):
        Memory.restore_backup(archive, full.paths.home)


def test_restore_rejects_path_traversal(tmp_path):
    bad = tmp_path / "bad.tar.gz"
    with tarfile.open(bad, "w:gz") as tar:
        data = b"x"
        info = tarfile.TarInfo("../evil.txt")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    with pytest.raises(ValidationError):
        Memory.restore_backup(bad, tmp_path / "t")
    assert not (tmp_path / "evil.txt").exists()


def test_portability_copy_home(full, tmp_path):
    before = ids(full.get_context(QUERY))
    old_home = str(full.paths.home)
    copy = tmp_path / "moved-home"
    shutil.copytree(full.paths.home, copy, ignore=shutil.ignore_patterns("index"))
    with Memory.open(copy, embedder=None) as moved:
        assert ids(moved.get_context(QUERY)) == before
        assert moved.verify()["ok"] is True
        for (table,) in moved.truth.execute("select name from sqlite_master where type='table'"):
            for row in moved.truth.execute(f"select * from {table}"):
                assert not any(isinstance(v, str) and old_home in v for v in tuple(row)), table
        assert all(not p.startswith("/") for (p,) in moved.truth.execute(
            "select file_path from sources where file_path is not null"))


def test_export_counts(full, tmp_path):
    out = full.export(tmp_path / "export.jsonl")
    lines = [json.loads(x) for x in (tmp_path / "export.jsonl").read_text(encoding="utf-8").splitlines()]
    assert out["counts"]["memory"] == sum(1 for x in lines if x["type"] == "memory") > 0
    assert {"source", "memory", "memory_version", "evidence", "entity", "link", "proposal"} <= {x["type"] for x in lines}


def test_doctor_reports_fts5_and_embedder(full):
    report = full.doctor()
    assert report["checks"]["fts5"] is True and report["checks"]["sqlite_version"]
    assert report["checks"]["embedder"]["available"] is False
