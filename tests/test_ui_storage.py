from pathlib import Path

from pmi.ui.storage import storage_usage


def put(p: Path, n: int):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * n)


def test_storage_items_and_total(paths, tmp_path):
    put(paths.db, 100)
    put(Path(str(paths.db) + "-wal"), 50)
    put(paths.sources / "a" / "b.txt", 10)
    put(paths.index_db, 20)
    put(paths.backups / "b.tar.gz", 40)
    real_models = tmp_path / "models-elsewhere"
    put(real_models / "m" / "w.onnx", 30)
    paths.models.rmdir()
    paths.models.symlink_to(real_models)
    u = storage_usage(paths)
    assert [(i["key"], i["bytes"]) for i in u["items"]] == [
        ("memory_db", 150), ("sources", 10), ("index", 20), ("models", 30), ("backups", 40)]
    assert u["total_bytes"] == 250 and u["home"] == str(paths.home)
    assert u["items"][0]["path"] == "data/memory/pmi.db"
    assert [i["label"] for i in u["items"]] == [
        "Memory database", "Sources", "Search index", "Embedding model", "Backups"]


def test_missing_parts_count_as_zero(tmp_path):
    from pmi.paths import Paths

    u = storage_usage(Paths(tmp_path / "empty-home"))
    assert u["total_bytes"] == 0 and all(i["bytes"] == 0 for i in u["items"])
