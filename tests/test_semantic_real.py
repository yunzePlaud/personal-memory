"""Real local embedding model (slow; needs `pmi model download` first). Run: pytest -m slow"""
import pytest

from pmi import Memory
from pmi.index.embed import get_embedder

pytestmark = pytest.mark.slow


@pytest.fixture
def real(home):
    from pathlib import Path

    from pmi.config import load_settings
    from pmi.paths import Paths, resolve_home

    package = Paths(resolve_home(None))  # the repo's own data/models cache
    embedder = get_embedder(load_settings(package), package)
    if embedder is None:
        pytest.skip("embedding model not downloaded (run ./pmi model download)")
    m = Memory.open(home, embedder=embedder)
    yield m
    m.close()


def test_cross_lingual_paraphrase_is_found(real):
    real.propose([{"op": "create", "memory": {"kind": "decision", "content": "Project A 上线时间调整至 12 月 10 日"}}])
    real.propose([{"op": "create", "memory": {"kind": "preference", "content": "我喜欢喝不加糖的咖啡"}}])
    hits = real.search("When was the product release postponed to?", scope="memories")["memories"]
    assert hits and "12 月 10 日" in hits[0]["content"] and "vector" in hits[0]["via"]


def test_semantic_available_note_absent(real):
    real.propose([{"op": "create", "memory": {"kind": "semantic", "content": "团队每周五发周报"}}])
    assert "semantic search unavailable" not in real.get_context("周报什么时候发")["notes"]


@pytest.fixture
def real_meetings(real, tmp_path):
    import shutil
    from pathlib import Path

    shutil.copytree(Path(__file__).parent / "fixtures", tmp_path / "notes")
    real.import_paths([str(tmp_path / "notes")], extract=True)
    return real


def test_known_project_unrelated_question_insufficient_with_real_model(real_meetings):
    assert real_meetings.get_context("Project A 的预算是多少")["insufficient_evidence"] is True


def test_unknown_project_insufficient_with_real_model(real_meetings):
    assert real_meetings.get_context("Project Z 什么时候上线")["insufficient_evidence"] is True


def test_known_question_sufficient_with_real_model(real_meetings):
    ctx = real_meetings.get_context("Project A 现在什么时候上线")
    assert ctx["insufficient_evidence"] is False
    assert [m for m in ctx["memories"] if m["slot"] == "project-a/launch-date"]
