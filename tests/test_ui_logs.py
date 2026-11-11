import os
import time

import pytest

from pmi.errors import NotFoundError
from pmi.ui.config import LogSpec
from pmi.ui.logs import log_files, log_status, read_since, read_tail, resolve_log_file


def spec(d, **kw):
    return LogSpec(name="bot", path=str(d / "session-*.log"), **kw)


def test_files_sorted_newest_name_first(tmp_path):
    for n in ("session-2026-11-10.log", "session-2026-11-11.log", "other.txt"):
        (tmp_path / n).write_text("x\n")
    assert [p.name for p in log_files(spec(tmp_path))] == ["session-2026-11-11.log", "session-2026-11-10.log"]


def test_tilde_expanded(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "a.log").write_text("x\n")
    assert [p.name for p in log_files(LogSpec("bot", "~/logs/*.log"))] == ["a.log"]


@pytest.mark.parametrize("bad", ["../secret.log", "other.txt", "x/../session-1.log", "", "session-9.log"])
def test_resolve_rejects_names_outside_matches(tmp_path, bad):
    (tmp_path / "session-1.log").write_text("x\n")
    (tmp_path / "other.txt").write_text("x\n")
    with pytest.raises(NotFoundError):
        resolve_log_file(spec(tmp_path), bad)


def test_resolve_accepts_a_match(tmp_path):
    (tmp_path / "session-1.log").write_text("x\n")
    assert resolve_log_file(spec(tmp_path), "session-1.log") == tmp_path / "session-1.log"


def test_status_states(tmp_path):
    s = spec(tmp_path)
    assert log_status(s)["state"] == "missing"
    f = tmp_path / "session-1.log"
    f.write_text("x\n")
    now = time.time()
    os.utime(f, (now - 60, now - 60))
    st = log_status(s, now=now)
    assert st["state"] == "active" and st["file"] == "session-1.log" and 59 <= st["age_seconds"] <= 61
    os.utime(f, (now - 11 * 60, now - 11 * 60))
    assert log_status(s, now=now)["state"] == "stale"


def test_tail_returns_last_complete_lines(tmp_path):
    f = tmp_path / "a.log"
    body = "".join(f"line {i}\n" for i in range(1500))
    f.write_text(body + "partial")
    out = read_tail(f, max_lines=1000)
    assert out["text"].splitlines() == [f"line {i}" for i in range(500, 1500)]
    assert out["offset"] == len(body.encode())


def test_tail_across_small_blocks(tmp_path):
    f = tmp_path / "a.log"
    lines = [("行" * (i % 7)) + f" entry {i}" for i in range(3000)]
    f.write_text("\n".join(lines) + "\n")
    assert read_tail(f, max_lines=1000, block_size=97)["text"].splitlines() == lines[-1000:]


def test_tail_of_short_and_empty_files(tmp_path):
    f = tmp_path / "a.log"
    f.write_text("")
    assert read_tail(f)["text"] == "" and read_tail(f)["offset"] == 0
    f.write_text("only\n")
    assert read_tail(f, block_size=3)["text"] == "only\n"


def test_since_returns_only_new_complete_lines(tmp_path):
    f = tmp_path / "a.log"
    f.write_text("a\n")
    off = read_tail(f)["offset"]
    with f.open("a") as h:
        h.write("b\nc")
    out = read_since(f, off)
    assert out["text"] == "b\n" and out["offset"] == off + 2 and not out["reset"]
    with f.open("a") as h:
        h.write("\n")
    assert read_since(f, out["offset"])["text"] == "c\n"


def test_since_never_splits_multibyte(tmp_path):
    f = tmp_path / "a.log"
    f.write_bytes("第一行\n".encode())
    off = read_tail(f)["offset"]
    rest = "第二行\n".encode()
    with f.open("ab") as h:
        h.write(rest[:-2])
    out = read_since(f, off)
    assert out["text"] == "" and out["offset"] == off
    with f.open("ab") as h:
        h.write(rest[-2:])
    assert read_since(f, off)["text"] == "第二行\n"


def test_since_resets_when_file_shrinks(tmp_path):
    f = tmp_path / "a.log"
    f.write_text("new\n")
    out = read_since(f, 10_000)
    assert out["reset"] and out["text"] == "new\n"


def test_invalid_utf8_replaced(tmp_path):
    f = tmp_path / "a.log"
    f.write_bytes(b"ok\n\xff\xfe bad\n")
    text = read_tail(f)["text"]
    assert "ok" in text and "�" in text


def test_since_far_behind_returns_fresh_tail(tmp_path):
    f = tmp_path / "a.log"
    f.write_text("start\n")
    off = read_tail(f)["offset"]
    with f.open("a") as h:
        h.write("".join(f"filler line {i:07d}\n" for i in range(80_000)))  # ~1.6 MB
    out = read_since(f, off)
    assert out["reset"] and len(out["text"].splitlines()) == 1000
