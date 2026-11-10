"""Several processes writing to the same PMI home at once (design §11 Q7/Q8)."""
import multiprocessing as mp

from pmi import Actor, Memory

WORKERS, ROUNDS = 6, 10


def worker(home: str, w: int) -> None:
    with Memory.open(home, actor=Actor.agent(f"w{w}"), embedder=None) as m:
        for i in range(ROUNDS):
            text = f"worker {w} round {i}: 记录第 {i} 条并发写入"
            sid = m.add_source(text=text, trust="agent")["source_id"]
            r = m.propose([{"op": "create", "memory": {"kind": "episodic", "content": f"并发写入 w{w} r{i}",
                            "evidence": [{"source_id": sid, "quote": text}]}}])
            assert r["results"][0]["outcome"] == "applied"


def test_concurrent_writers(home):
    ctx = mp.get_context("spawn")
    procs = [ctx.Process(target=worker, args=(str(home), w)) for w in range(WORKERS)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=120)
    assert [p.exitcode for p in procs] == [0] * WORKERS
    with Memory.open(home, embedder=None) as m:
        assert m.truth.execute("select count(*) from sources").fetchone()[0] == WORKERS * ROUNDS
        assert m.truth.execute("select count(*) from memories").fetchone()[0] == WORKERS * ROUNDS
        report = m.verify()
        assert report["ok"] is True, report["errors"]
        assert m.indexer.status(m.truth)["fresh"] is True or m.search("并发写入")["memories"]
