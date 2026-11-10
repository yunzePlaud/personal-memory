import hashlib
from pathlib import Path

import pytest

from pmi.actors import Actor
from pmi.config import Settings
from pmi.errors import ValidationError
from pmi.sources import add_source, get_source, list_versions

OWNER = Actor.owner()
S = Settings()


def test_note_text_dedup(truth, paths):
    a = add_source(truth, paths, S, actor=OWNER, text="Project A 计划 11 月上线", kind="note")
    b = add_source(truth, paths, S, actor=OWNER, text="Project A 计划 11 月上线", kind="note")
    assert a["outcome"] == "created" and b["outcome"] == "duplicate"
    assert b["source_id"] == a["source_id"]


def test_origin_versions_and_revert(truth, paths):
    outs = [add_source(truth, paths, S, actor=OWNER, text=t, origin_key="doc:prd")
            for t in ("第一版内容", "第二版内容", "第一版内容")]
    assert [o["outcome"] for o in outs] == ["created", "new_version", "new_version"]
    assert [o["version"] for o in outs] == [1, 2, 3]
    latest = [v["id"] for v in list_versions(truth, "doc:prd") if v["is_latest"]]
    assert latest == [outs[2]["source_id"]]
    again = add_source(truth, paths, S, actor=OWNER, text="第一版内容", origin_key="doc:prd")
    assert again["outcome"] == "duplicate" and again["source_id"] == outs[2]["source_id"]
    assert get_source(truth, outs[2]["source_id"])["versions"] == [o["source_id"] for o in outs]


def test_file_stored_content_addressed_relative(truth, paths, tmp_path):
    f = tmp_path / "prd.md"
    f.write_text("# PRD\n需求背景", encoding="utf-8")
    out = add_source(truth, paths, S, actor=OWNER, path=f)
    src = get_source(truth, out["source_id"])
    assert not src["file_path"].startswith("/")
    stored = paths.data / src["file_path"]
    digest = hashlib.sha256(stored.read_bytes()).hexdigest()
    row = truth.execute("select file_hash from sources where id=?", (out["source_id"],)).fetchone()
    assert digest == row["file_hash"] and src["title"] == "PRD"
    assert add_source(truth, paths, S, actor=OWNER, path=f)["outcome"] == "duplicate"


def test_default_origin_key_relative_to_home(truth, paths, tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    (tmp_path / "docs").mkdir()
    f = tmp_path / "docs" / "a.md"
    f.write_text("hello", encoding="utf-8")
    assert add_source(truth, paths, S, actor=OWNER, path=f)["origin_key"] == "file:docs/a.md"


def test_source_time_from_frontmatter(truth, paths):
    text = "---\ntitle: 周会\ndate: 2026-10-01\n---\n正文"
    out = add_source(truth, paths, S, actor=OWNER, text=text)
    src = get_source(truth, out["source_id"])
    assert src["source_time"] == "2026-10-01T00:00:00Z" and src["title"] == "周会"
    assert src["metadata"]["frontmatter"]["date"] == "2026-10-01"
    out2 = add_source(truth, paths, S, actor=OWNER, text=text + "!", source_time="2026-10-02")
    assert get_source(truth, out2["source_id"])["source_time"] == "2026-10-02T00:00:00Z"


def test_stale_citations_reported(truth, paths):
    v1 = add_source(truth, paths, S, actor=OWNER, text="A 计划 11 月上线。B 不采用。", origin_key="doc:x")
    for mid, quote in (("mem_gone", "A 计划 11 月上线"), ("mem_kept", "B 不采用")):
        truth.execute(
            "insert into memories(id, kind, title, content, content_key, status, verification,"
            " created_by, created_at, updated_at) values (?,?,?,?,?,?,?,?,?,?)",
            (mid, "decision", "t", quote, "k" + mid, "active", "source_backed", "owner:cli", "x", "x"))
        truth.execute(
            "insert into evidence(memory_id, source_id, quote, span_start, span_end, added_by, added_at)"
            " values (?,?,?,?,?,?,?)", (mid, v1["source_id"], quote, 0, 1, "owner:cli", "x"))
    v2 = add_source(truth, paths, S, actor=OWNER, text="A 计划 12 月上线。B 不采用。", origin_key="doc:x")
    assert [c["memory_id"] for c in v2["stale_citations"]] == ["mem_gone"]


def test_oversized_file_rejected(truth, paths, tmp_path):
    f = tmp_path / "big.txt"
    f.write_text("x" * 10, encoding="utf-8")
    with pytest.raises(ValidationError, match="too large"):
        add_source(truth, paths, Settings(max_file_mb=0), actor=OWNER, path=f)
    assert truth.execute("select count(*) from sources").fetchone()[0] == 0


def test_changes_and_audit_written(truth, paths):
    add_source(truth, paths, S, actor=OWNER, text="v1", origin_key="k")
    add_source(truth, paths, S, actor=OWNER, text="v2", origin_key="k")
    changes = truth.execute("select target_type, op from changes").fetchall()
    assert [tuple(r) for r in changes] == [("source", "upsert")] * 3
    actions = [r[0] for r in truth.execute("select action from audit_log order by seq")]
    assert actions == ["source.create", "source.new_version"]


def test_get_source_paging(truth, paths):
    body = "".join(f"{i:05d}" for i in range(2000))
    out = add_source(truth, paths, S, actor=OWNER, text=body)
    src = get_source(truth, out["source_id"], text_offset=4000, text_limit=100)
    assert src["text"] == body[4000:4100] and src["text_length"] == 10000


def test_input_validation(truth, paths, tmp_path):
    with pytest.raises(ValidationError):
        add_source(truth, paths, S, actor=OWNER)
    with pytest.raises(ValidationError):
        add_source(truth, paths, S, actor=OWNER, text="x", kind="email")
    with pytest.raises(ValidationError):
        add_source(truth, paths, S, actor=OWNER, text="x", trust="admin")
    with pytest.raises(ValidationError):
        add_source(truth, paths, S, actor=OWNER, text="   ")
