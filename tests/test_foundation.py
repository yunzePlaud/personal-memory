import re
import sqlite3

import pytest

from pmi.cli import main
from pmi.clock import normalize_time
from pmi.config import Settings, load_settings
from pmi.db import open_truth
from pmi.errors import SchemaTooNew, ValidationError
from pmi.ids import new_id
from pmi.paths import Paths


def test_open_truth_creates_schema_and_wal(home):
    conn = open_truth(Paths(home))
    assert conn.execute("pragma user_version").fetchone()[0] == 1
    assert conn.execute("pragma journal_mode").fetchone()[0] == "wal"
    tables = {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
    assert {"sources", "memories", "memory_versions", "evidence", "entities", "memory_entities",
            "links", "proposals", "audit_log", "changes"} <= tables


def test_schema_too_new_refuses(home):
    conn = open_truth(Paths(home))
    conn.execute("pragma user_version=99")
    conn.close()
    with pytest.raises(SchemaTooNew):
        open_truth(Paths(home))


def _insert_memory(conn, mid, status, slot):
    conn.execute(
        "insert into memories(id, kind, title, content, content_key, slot, status, verification,"
        " created_by, created_at, updated_at) values (?,?,?,?,?,?,?,?,?,?,?)",
        (mid, "decision", "t", "c", "k-" + mid, slot, status, "source_backed", "owner:cli",
         "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    )


def test_active_slot_unique(home):
    conn = open_truth(Paths(home))
    _insert_memory(conn, "mem_a", "active", "p/launch")
    _insert_memory(conn, "mem_b", "superseded", "p/launch")
    with pytest.raises(sqlite3.IntegrityError):
        _insert_memory(conn, "mem_c", "active", "p/launch")


def test_normalize_time():
    assert normalize_time("2026-10-01") == "2026-10-01T00:00:00Z"
    assert normalize_time("2026-10-01T08:30+08:00") == "2026-10-01T00:30:00Z"
    assert normalize_time("2026-10-01T08:30:15Z") == "2026-10-01T08:30:15Z"
    assert normalize_time(None) is None
    with pytest.raises(ValidationError):
        normalize_time("next tuesday")


def test_new_id_sortable_and_prefixed():
    ids = [new_id("mem") for _ in range(200)]
    for i in ids:
        assert re.fullmatch(r"mem_[0-9A-HJKMNP-TV-Z]{26}", i)
    assert ids == sorted(ids) and len(set(ids)) == 200


def test_settings_defaults_and_override(home):
    paths = Paths(home)
    assert load_settings(paths) == Settings()
    paths.config_file.parent.mkdir(parents=True, exist_ok=True)
    paths.config_file.write_text("[retrieval]\nmax_tokens = 1000\n", encoding="utf-8")
    assert load_settings(paths).context_max_tokens == 1000
    paths.config_file.write_text("[retrieval]\nbogus = 1\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_settings(paths)


def test_cli_init(home):
    # --home is a global option and precedes the subcommand
    assert main(["--home", str(home), "init"]) == 0
    assert Paths(home).db.exists()
