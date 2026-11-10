"""Regression tests for the final whole-branch review findings (C1, I1-I9 and re-graded minors)."""
import pytest

from pmi.actors import Actor
from pmi.config import Settings
from pmi.errors import ValidationError
from pmi.evolution import propose
from pmi.memories import get_memory
from tests.helpers import seed_source

OWNER, AGENT = Actor.owner(), Actor.agent("t")
S = Settings()
SLOT = "project-a/launch-date"


def create(truth, content, ev, actor=AGENT, slot=SLOT, **kw):
    m = {"kind": "decision", "content": content, "slot": slot, **kw}
    if ev:
        m["evidence"] = [{"source_id": s, "quote": q} for s, q in ev]
    return propose(truth, S, [{"op": "create", "memory": m}], actor=actor)["results"][0]


@pytest.fixture
def owner_backed(truth):
    doc = seed_source(truth, "PRD：Project A 计划于 12 月 10 日上线。负责人 Alice。", source_time="2026-10-20")
    current = create(truth, "Project A 计划于 12 月 10 日上线", [(doc, "Project A 计划于 12 月 10 日上线")])
    slack = seed_source(truth, "Carol: Project A 上线改到 1 月了", trust="external", source_time="2026-11-06")
    return doc, current["memory_id"], slack


# --- C1: an extra owner quote must not lift a Slack-only claim over owner-backed memory -------------

def test_c1_trivial_owner_quote_does_not_bypass_protection_on_supersede(truth, owner_backed):
    doc, current, slack = owner_backed
    r = propose(truth, S, [{"op": "supersede", "old_ids": [current], "memory": {
        "kind": "decision", "content": "Project A 上线改到 1 月", "slot": SLOT,
        "evidence": [{"source_id": slack, "quote": "Project A 上线改到 1 月了"},
                     {"source_id": doc, "quote": "负责人 Alice"}]}}], actor=AGENT)["results"][0]
    assert r["outcome"] == "pending" and get_memory(truth, current)["status"] == "active"


def test_c1_trivial_owner_quote_does_not_bypass_protection_on_create(truth, owner_backed):
    doc, current, slack = owner_backed
    r = create(truth, "Project A 上线改到 1 月", [(slack, "Project A 上线改到 1 月了"), (doc, "负责人 Alice")])
    assert (r["outcome"], r["status"]) == ("pending", "disputed")


def test_c1_new_owner_document_still_supersedes(truth, owner_backed):
    _, current, _ = owner_backed
    minutes = seed_source(truth, "11-06 周会纪要：Project A 上线改到 1 月 15 日。", source_time="2026-11-06")
    r = create(truth, "Project A 上线改到 1 月 15 日", [(minutes, "Project A 上线改到 1 月 15 日")])
    assert r["outcome"] == "applied" and get_memory(truth, current)["status"] == "superseded"


def test_c1_quote_too_short_rejected(truth, owner_backed):
    doc, _, _ = owner_backed
    with pytest.raises(ValidationError):
        create(truth, "随便", [(doc, "A")], slot=None)


def test_c1_agent_supersede_without_evidence_needs_review(truth):
    old = create(truth, "我的笔记：每周五发周报", None, actor=OWNER, slot=None)["memory_id"]
    r = propose(truth, S, [{"op": "supersede", "old_ids": [old], "memory": {"kind": "procedure",
                "content": "周报改到周四"}}], actor=AGENT)["results"][0]
    assert r["outcome"] == "pending" and get_memory(truth, old)["status"] == "active"


# --- I1: agents cannot take over the version chain of a more trusted document ------------------------

def test_i1_agent_cannot_take_over_owner_document_chain(home, tmp_path, monkeypatch):
    from pathlib import Path

    from pmi import Memory
    from pmi.errors import PolicyDenied

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    with Memory.open(home, embedder=None) as m:
        first = m.add_source(text="PRD v1：推送方案 A", origin_key="file:docs/prd.md")
        with pytest.raises(PolicyDenied):
            m.add_source(text="被替换的 PRD", origin_key="file:docs/prd.md", actor=AGENT)
        docs = tmp_path / "docs"
        docs.mkdir()
        f = docs / "plan.md"
        f.write_text("# Plan\n第一版", encoding="utf-8")
        m.import_paths([str(f)])
        f.write_text("# Plan\n第二版", encoding="utf-8")
        with pytest.raises(PolicyDenied):
            m.add_source(path=str(f), actor=AGENT)  # same default origin_key as the owner's import
        assert m.get([first["source_id"]])["items"][0]["is_latest"] is True
        own = m.add_source(text="agent 笔记 v1", origin_key="agent:notes", actor=AGENT)
        assert m.add_source(text="agent 笔记 v2", origin_key="agent:notes", actor=AGENT)["outcome"] == "new_version"
        assert own["outcome"] == "created"


# --- I2: insufficient_evidence must mean something for known and unknown projects -------------------

@pytest.fixture
def meetings(home, tmp_path):
    import shutil
    from pathlib import Path

    from pmi import Memory

    shutil.copytree(Path(__file__).parent / "fixtures", tmp_path / "notes")
    m = Memory.open(home, embedder=None)
    m.import_paths([str(tmp_path / "notes")], extract=True)
    yield m
    m.close()


def test_i2_known_project_unknown_topic_is_insufficient(meetings):
    ctx = meetings.get_context("Project A 的预算是多少")
    assert ctx["insufficient_evidence"] is True
    assert all(m["why_included"] == "entity" for m in ctx["memories"])


def test_i2_unknown_project_does_not_borrow_another_projects_facts(meetings):
    ctx = meetings.get_context("Project Z 什么时候上线")
    assert ctx["insufficient_evidence"] is True
    assert not [m for m in ctx["memories"] if m["slot"] == SLOT]


def test_i2_known_project_known_topic_still_sufficient(meetings):
    ctx = meetings.get_context("Project A 现在什么时候上线")
    assert ctx["insufficient_evidence"] is False
    assert [m for m in ctx["history"] if m["slot"] == SLOT]


# --- I3: forgotten content must not survive in database bytes or later backups -----------------------

def _raw_hits(root, needles):
    hits = []
    for f in root.rglob("*"):
        if f.is_file() and "backups" not in f.parts:
            data = f.read_bytes()
            hits += [(f.name, n) for n in needles if n in data]
    return hits


def test_i3_forget_leaves_no_recoverable_bytes(home):
    import gzip

    from pmi import Memory

    secret = "SECRET-PLAN-ZEBRA 机密预算一百万元"
    needles = [b"SECRET-PLAN-ZEBRA", "机密预算一百万元".encode()]
    with Memory.open(home, embedder=None) as m:
        for i in range(30):  # enough rows that pages are reused rather than dropped
            m.add_source(text=f"普通笔记 {i}：" + "填充内容" * 50)
        s = m.add_source(text=f"会议记录：{secret}。其他内容。")
        m.propose([{"op": "create", "memory": {"kind": "semantic", "content": f"记住：{secret}",
                    "evidence": [{"source_id": s["source_id"], "quote": secret}]}}])
        m.get_context("机密预算")  # make sure the index holds it
        assert _raw_hits(m.paths.data, needles)
        m.forget_source(s["source_id"], "sensitive", mode="cascade")
        assert _raw_hits(m.paths.data, needles) == []
        archive = m.backup()
        assert not any(n in gzip.decompress(archive.read_bytes()) for n in needles)


def test_i3_verify_fix_removes_orphan_files(home):
    from pmi import Memory

    with Memory.open(home, embedder=None) as m:
        orphan = m.paths.sources / "ab" / "orphan.pdf"
        orphan.parent.mkdir(parents=True, exist_ok=True)
        orphan.write_bytes(b"left behind by a crash")
        assert any(w["check"] == "orphan_file" for w in m.verify()["warnings"])
        report = m.verify(fix=True)
        assert not orphan.exists() and not any(w["check"] == "orphan_file" for w in report["warnings"])


# --- I4: extraction must survive real-world notes and be retryable ----------------------------------

def test_i4_chinese_and_slash_dates_normalise():
    from pmi.clock import normalize_time

    assert normalize_time("2026年10月1日") == "2026-10-01T00:00:00Z"
    assert normalize_time("2026/10/1") == "2026-10-01T00:00:00Z"
    assert normalize_time("2026.10.01") == "2026-10-01T00:00:00Z"


def test_i4_chinese_date_frontmatter_extracts(home):
    from pmi import Memory

    note = "---\nproject: Apollo\ndate: 2026年10月1日\n---\n## 决策\n- [slot: pricing] 定价采用年付优惠方案"
    with Memory.open(home, embedder=None) as m:
        out = m.add_source(text=note, extract=True)
        assert out["extraction"]["applied"] == 1
        [mem] = m.list("memories")["items"]
        assert mem["valid_from"] == "2026-10-01T00:00:00Z" and mem["slot"] == "apollo/pricing"


def test_i4_more_than_fifty_bullets(home):
    from pmi import Memory

    note = "## 决策\n" + "\n".join(f"- 第 {i} 条决定的具体内容" for i in range(60))
    with Memory.open(home, embedder=None) as m:
        assert m.add_source(text=note, extract=True)["extraction"]["applied"] == 60


def test_i4_extraction_retried_on_reimport(home, tmp_path):
    from pmi import Memory

    f = tmp_path / "minutes.md"
    f.write_text("## 决策\n- 每周五发周报给全组", encoding="utf-8")
    with Memory.open(home, embedder=None) as m:
        m.import_paths([str(f)])                 # forgot --extract
        again = m.import_paths([str(f)], extract=True)
        assert again["imported"][0]["outcome"] == "duplicate" and again["imported"][0]["applied"] == 1
        third = m.import_paths([str(f)], extract=True)
        assert third["imported"][0]["applied"] == 0  # already extracted: not repeated


def test_i4_extraction_failure_is_reported_not_raised(home, monkeypatch):
    from pmi import Memory, api

    monkeypatch.setattr(api, "extract_ops", lambda src: [{"op": "create", "memory": {"kind": "rumour", "content": "x"}}])
    with Memory.open(home, embedder=None) as m:
        out = m.add_source(text="一份会被错误提取的笔记", extract=True)
        assert out["outcome"] == "created" and out["extraction"]["error"] == "validation"
        assert any("extraction failed" in n for n in out["notes"])


# --- I5: one odd file never aborts a batch; I9: PDFs that open without a password are read ----------

def test_i5_unreadable_file_does_not_abort_batch(home, tmp_path):
    import os

    from pmi import Memory

    d = tmp_path / "docs"
    d.mkdir()
    (d / "a-locked.md").write_text("locked", encoding="utf-8")
    os.chmod(d / "a-locked.md", 0)
    (d / "b-ok.md").write_text("# OK\n正常文件", encoding="utf-8")
    try:
        with Memory.open(home, embedder=None) as m:
            out = m.import_paths([str(d)])
        assert [e["path"].split("/")[-1] for e in out["errors"]] == ["a-locked.md"]
        assert [i["path"].split("/")[-1] for i in out["imported"]] == ["b-ok.md"]
    finally:
        os.chmod(d / "a-locked.md", 0o644)


def test_i5_any_pdf_library_error_becomes_validation_error(monkeypatch):
    import pypdf

    from pmi.parsers import parse_bytes
    from tests.helpers import make_pdf

    def boom(*a, **k):
        raise TypeError("internal pypdf failure")
    monkeypatch.setattr(pypdf, "PdfReader", boom)
    with pytest.raises(ValidationError, match="cannot read PDF"):
        parse_bytes(make_pdf(["hello"]), "x.pdf")


def _encrypted_pdf(user_password: str) -> bytes:
    import io

    from pypdf import PdfReader, PdfWriter

    from tests.helpers import make_pdf

    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(make_pdf(["Owner restricted text"]))))
    writer.encrypt(user_password=user_password, owner_password="owner-secret", algorithm="RC4-128")
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_i9_owner_password_only_pdf_is_read():
    from pmi.parsers import parse_bytes

    assert "Owner restricted text" in parse_bytes(_encrypted_pdf(""), "restricted.pdf").text


def test_i9_user_password_pdf_is_rejected():
    from pmi.parsers import parse_bytes

    with pytest.raises(ValidationError, match="encrypted"):
        parse_bytes(_encrypted_pdf("open-sesame"), "locked.pdf")


# --- I6: index trouble never deletes a shared index or fails a committed write ----------------------

def test_i6_locked_index_neither_rebuilds_nor_fails_the_write(home, monkeypatch):
    import sqlite3

    from pmi import Memory

    with Memory.open(home, embedder=None) as m:
        m.add_source(text="第一条：已经进入索引的内容")
        inode = m.paths.index_db.stat().st_ino

        def locked(truth):
            raise sqlite3.OperationalError("database is locked")
        monkeypatch.setattr(m.indexer, "sync", locked)
        out = m.add_source(text="第二条：索引暂时被其他进程锁住")
        assert out["outcome"] == "created" and any("index" in n for n in out["notes"])
        ctx = m.get_context("已经进入索引的内容")
        assert any("index" in n for n in ctx["notes"])
        assert m.paths.index_db.stat().st_ino == inode


def test_i6_embedder_failure_keeps_text_index(home):
    from pmi import Memory

    class Broken:
        model_id, dim = "broken-1", 8

        def embed(self, texts):
            raise RuntimeError("onnx exploded")

    with Memory.open(home, embedder=Broken()) as m:
        out = m.add_source(text="嵌入模型坏了也能写入")
        assert out["outcome"] == "created" and any("embedding" in n for n in out["notes"])
        inode = m.paths.index_db.stat().st_ino
        hits = m.search("嵌入模型坏了也能写入", scope="sources")["sources"]
        assert hits and m.paths.index_db.stat().st_ino == inode


# --- I7: duplicates are the same claim about the same thing, not merely similar strings ----------------

def test_i7_numbers_keep_their_punctuation():
    from pmi.textnorm import content_key

    assert content_key("预算 35 万") != content_key("预算 3.5 万")
    assert content_key("版本 1.2") != content_key("版本 12")
    assert content_key("上线：11 月") == content_key("上线11月")


def test_i7_same_text_different_slot_is_not_a_duplicate(truth):
    a = seed_source(truth, "Project A 状态：已完成验收", source_time="2026-11-01")
    b = seed_source(truth, "Project B 状态：已完成验收", source_time="2026-11-01")
    ra = create(truth, "已完成验收", [(a, "Project A 状态：已完成验收")], slot="project-a/status")
    rb = create(truth, "已完成验收", [(b, "Project B 状态：已完成验收")], slot="project-b/status")
    assert rb["outcome"] == "applied" and rb["memory_id"] != ra["memory_id"]
    assert len(get_memory(truth, ra["memory_id"])["evidence"]) == 1


def test_i7_same_text_different_entities_is_not_a_duplicate(truth):
    a = seed_source(truth, "Alice：本周任务已完成")
    b = seed_source(truth, "Bob：本周任务已完成")
    ra = create(truth, "本周任务已完成", [(a, "本周任务已完成")], slot=None, entities=[{"name": "Alice", "kind": "person"}])
    rb = create(truth, "本周任务已完成", [(b, "本周任务已完成")], slot=None, entities=[{"name": "Bob", "kind": "person"}])
    assert rb["outcome"] == "applied" and rb["memory_id"] != ra["memory_id"]


# --- I8: backfilling an older claim rethreads the history chain -------------------------------------

def test_i8_out_of_order_backfill_rethreads_history(truth):
    from pmi.search import search_memories  # noqa: F401  (as_of logic lives in validity windows)

    srcs = {d: seed_source(truth, f"{d} 周会：上线日期定为 {d} 版本", source_time=d)
            for d in ("2026-10-20", "2026-10-01", "2026-10-10")}
    ids = {d: create(truth, f"上线日期定为 {d} 版本", [(s, f"上线日期定为 {d} 版本")])["memory_id"]
           for d, s in srcs.items()}  # imported 10-20, then 10-01, then 10-10
    first, middle, last = (get_memory(truth, ids[d]) for d in ("2026-10-01", "2026-10-10", "2026-10-20"))
    assert last["status"] == "active"
    assert (first["superseded_by"], first["valid_until"]) == (middle["id"], "2026-10-10T00:00:00Z")
    assert (middle["superseded_by"], middle["valid_until"]) == (last["id"], "2026-10-20T00:00:00Z")
    rows = truth.execute("select id from memories where slot = ? and (valid_from <= ?) and"
                         " (valid_until is null or valid_until > ?)", (SLOT, "2026-10-15T00:00:00Z",
                                                                       "2026-10-15T00:00:00Z")).fetchall()
    assert [r[0] for r in rows] == [middle["id"]]


# --- re-graded minors: review previews; agent file access limits ------------------------------------

def test_r1_review_list_shows_what_will_be_approved(truth, owner_backed):
    from pmi import Memory  # noqa: F401
    from pmi.review import list_proposals, proposal_preview

    doc, current, slack = owner_backed
    r = create(truth, "Project A 上线改到 1 月", [(slack, "Project A 上线改到 1 月了")])
    [p] = list_proposals(truth)
    preview = proposal_preview(truth, p)
    text = str(preview)
    assert "Project A 上线改到 1 月" in text and "Project A 计划于 12 月 10 日上线" in text
    assert "external" in text and r["memory_id"] in text


def test_r2_agent_file_access_is_limited(home, tmp_path, monkeypatch):
    from pathlib import Path

    from pmi import Memory
    from pmi.config import Settings
    from pmi.errors import PolicyDenied

    assert Settings().import_roots == ("~/Documents", "~/Desktop", "~/Downloads")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))  # expanduser reads $HOME
    (tmp_path / "Desktop" / ".secret").mkdir(parents=True)
    (tmp_path / "Desktop" / ".secret" / "key.md").write_text("private key material", encoding="utf-8")
    (tmp_path / ".ssh").mkdir()
    (tmp_path / ".ssh" / "id.md").write_text("ssh key", encoding="utf-8")
    (tmp_path / "Desktop" / "notes.md").write_text("ok to read", encoding="utf-8")
    (home / "inbox").mkdir()
    (home / "inbox" / "drop.md").write_text("dropped into the PMI inbox", encoding="utf-8")
    (home / "config").mkdir(exist_ok=True)
    (home / "config" / "settings.toml").write_text('[capture]\nimport_roots = ["~/Desktop", "inbox"]\n')
    monkeypatch.chdir(tmp_path)
    with Memory.open(home, embedder=None) as m:
        for bad in (tmp_path / "Desktop" / ".secret" / "key.md", tmp_path / ".ssh" / "id.md"):
            with pytest.raises(PolicyDenied):
                m.add_source(path=str(bad), actor=AGENT)
        assert m.add_source(path=str(tmp_path / "Desktop" / "notes.md"), actor=AGENT)["outcome"] == "created"
        assert m.add_source(path=str(home / "inbox" / "drop.md"), actor=AGENT)["outcome"] == "created"


# --- I2 (semantic): shared entity names must not make unrelated passages look relevant --------------

class KeywordEmbedder:
    """Vectors over fixed keywords: texts sharing only an entity name still look 'similar'."""
    model_id, dim = "kw-4", 4
    KEYS = ("project a", "上线", "预算", "方案")

    def embed(self, texts):
        import math
        out = []
        for t in texts:
            v = [float(t.lower().count(k)) for k in self.KEYS]
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / n for x in v])
        return out


def test_i2_entity_names_do_not_drive_semantic_recall(home, tmp_path):
    import shutil
    from pathlib import Path

    from pmi import Memory

    shutil.copytree(Path(__file__).parent / "fixtures", tmp_path / "notes")
    with Memory.open(home, embedder=KeywordEmbedder()) as m:
        m.import_paths([str(tmp_path / "notes")], extract=True)
        ctx = m.get_context("Project A 的预算是多少")
        assert ctx["insufficient_evidence"] is True
        assert not any("vector" in s.get("via", []) for s in ctx["sources"])
        assert m.get_context("Project A 现在什么时候上线")["insufficient_evidence"] is False
