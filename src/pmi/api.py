"""The Memory facade: the only business entry point. CLI and MCP are thin wrappers around it."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Literal

from . import evolution, forget, maintenance, review
from .actors import Actor
from .config import Settings, load_settings
from .context import get_context
from .db import checkpoint, open_truth, write_tx
from .entities import add_alias, get_entity, list_entities, norm_name
from .errors import NotFoundError, PmiError, PolicyDenied, ValidationError
from .extractor import extract_ops
from .index.embed import Embedder, download_model, get_embedder
from .index.indexer import Indexer
from .journal import log_change
from .memories import get_memory
from .origins import origin_key_from_url
from .parsers import SUPPORTED_EXTENSIONS
from .paths import Paths, resolve_home
from .search import search_chunks, search_memories
from .sources import add_source, get_source

AGENT_TRUSTS = ("agent", "external")
SIMILAR_CANDIDATES = 3


def _summary(m: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"id": m["id"], "kind": m["kind"], "title": m["title"], "content": m["content"],
            "status": m["status"], "verification": m["verification"], "slot": m["slot"],
            "valid_from": m["valid_from"], "valid_until": m["valid_until"], **extra}


class Memory:
    def __init__(self, paths: Paths, settings: Settings, actor: Actor, embedder: Embedder | None):
        self.paths = paths
        self.settings = settings
        self.actor = actor
        self.embedder = embedder
        self.truth = open_truth(paths)
        self.indexer = Indexer(paths, settings, embedder)

    @classmethod
    def open(cls, home: str | Path | None = None, *, actor: Actor | None = None,
             embedder: Embedder | None | Literal["auto"] = "auto") -> "Memory":
        paths = Paths(resolve_home(home))
        paths.ensure()
        settings = load_settings(paths)
        emb = get_embedder(settings, paths) if embedder == "auto" else embedder
        return cls(paths, settings, actor or Actor.owner(), emb)

    def close(self) -> None:
        self.indexer.close()
        self.truth.close()

    def __enter__(self) -> "Memory":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ------------------------------------------------------------------ internals

    def _who(self, actor: Actor | None) -> Actor:
        return actor or self.actor

    def _owner(self, actor: Actor | None, action: str) -> Actor:
        who = self._who(actor)
        if not who.is_owner:
            raise PolicyDenied(f"only the owner can {action}")
        return who

    def _sync_index(self) -> list[str]:
        """Catch the index up. Busy → skip with a note (never delete a shared index); corrupt →
        rebuild; anything else propagates. Returns notes for the caller's result."""
        try:
            self.indexer.sync(self.truth)
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc) or "busy" in str(exc):
                return ["index busy (another process is writing); results may miss the latest changes"]
            raise
        except sqlite3.DatabaseError:
            self.indexer.rebuild(self.truth)
            return ["index was unreadable and has been rebuilt"]
        return [self.indexer.last_error] if self.indexer.last_error else []

    def _sync_after_write(self) -> list[str]:
        """The write is committed; an index problem must not be reported as a failed write."""
        try:
            return self._sync_index()
        except Exception as exc:
            return [f"saved; index update deferred ({type(exc).__name__})"]

    def _entity_ids(self, entity: str | None) -> list[str] | None:
        if not entity:
            return None
        if entity.startswith("ent_"):
            return [entity]
        wanted = norm_name(entity)
        ids = [e["id"] for e in list_entities(self.truth)
               if e["name"] and (norm_name(e["name"]) == wanted or wanted in map(norm_name, e["aliases"]))]
        return ids or ["ent_none"]

    def _check_agent_path(self, path: Path) -> Path:
        """Agents may only point PMI at files under import_roots, and never into hidden folders."""
        resolved = Path(path).expanduser().resolve()
        for raw in self.settings.import_roots:
            root = Path(raw).expanduser()
            root = (root if root.is_absolute() else self.paths.home / root).resolve()
            if resolved.is_relative_to(root):
                if any(part.startswith(".") for part in resolved.relative_to(root).parts):
                    raise PolicyDenied("agents may not read hidden files or folders")
                return resolved
        raise PolicyDenied("path is outside the configured import_roots")

    # ------------------------------------------------------------------ read

    def get_context(self, query: str, *, project: str | None = None, participants: list[str] | None = None,
                    time_range: tuple[str | None, str | None] | None = None, as_of: str | None = None,
                    include_history: bool = False, max_tokens: int | None = None) -> dict[str, Any]:
        notes = self._sync_index()
        out = get_context(self.truth, self.indexer.open(), self.settings, query=query, project=project,
                          participants=participants, time_range=time_range, as_of=as_of,
                          include_history=include_history, max_tokens=max_tokens, embedder=self.embedder)
        out["notes"] = notes + out["notes"]
        return out

    def search(self, query: str, *, scope: str = "all", kind: str | None = None, status: str | None = None,
               entity: str | None = None, time_range: tuple[str | None, str | None] | None = None,
               limit: int = 10) -> dict[str, Any]:
        if scope not in ("all", "memories", "sources"):
            raise ValidationError("scope must be all, memories or sources")
        notes = self._sync_index()
        idx = self.indexer.open()
        out: dict[str, Any] = {"memories": [], "sources": [], "notes": notes}
        if scope in ("all", "memories"):
            hits = search_memories(idx, self.truth, query, kinds=[kind] if kind else None,
                                   statuses=(status,) if status else ("active", "disputed"),
                                   entity_ids=self._entity_ids(entity), time_range=time_range, limit=limit,
                                   embedder=self.embedder, min_similarity=self.settings.vector_min_similarity)
            out["memories"] = [_summary(get_memory(self.truth, h["memory_id"]), score=round(h["score"], 6),
                                        via=h["via"]) for h in hits]
        if scope in ("all", "sources"):
            for h in search_chunks(idx, self.truth, query, limit=limit, embedder=self.embedder,
                                   min_similarity=self.settings.vector_min_similarity, time_range=time_range):
                src = get_source(self.truth, h["source_id"], text_limit=0)
                out["sources"].append({"source_id": h["source_id"], "title": src["title"], "kind": src["kind"],
                                       "trust": src["trust"], "source_time": src["source_time"],
                                       "excerpt": h["text"][:600], "span": [h["start"], h["end"]],
                                       "score": round(h["score"], 6)})
        return out

    def get(self, ids: list[str], *, include_history: bool = False, text_offset: int = 0,
            text_limit: int = 4000) -> dict[str, Any]:
        items, missing = [], []
        for i in ids:
            try:
                if i.startswith("mem_"):
                    items.append({"type": "memory", **get_memory(self.truth, i, include_history=include_history)})
                elif i.startswith("src_"):
                    items.append({"type": "source", **get_source(self.truth, i, text_offset=text_offset,
                                                                 text_limit=text_limit)})
                elif i.startswith("ent_"):
                    e = get_entity(self.truth, i)
                    mids = [r[0] for r in self.truth.execute(
                        "select memory_id from memory_entities where entity_id = ?", (i,))]
                    items.append({"type": "entity", **e, "memory_ids": mids})
                elif i.startswith("prp_"):
                    items.append({"type": "proposal", **review.get_proposal(self.truth, i)})
                else:
                    missing.append(i)
            except NotFoundError:
                missing.append(i)
        return {"items": items, "not_found": missing}

    def list(self, what: str = "recent", *, kind: str | None = None, status: str | None = None,
             entity: str | None = None, source: str | None = None, limit: int = 50,
             slot: str | None = None, offset: int = 0) -> dict[str, Any]:
        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))
        if what == "entities":
            return {"items": list_entities(self.truth, kind)[:limit]}
        if what == "slots":
            rows = self.truth.execute("select slot, id, title from memories where slot is not null"
                                      " and status = 'active' order by slot limit ?", (limit,))
            return {"items": [{"slot": r["slot"], "memory_id": r["id"], "title": r["title"]} for r in rows]}
        if what == "proposals":
            items = review.list_proposals(self.truth, status or "pending")[:limit]
            return {"items": [{**p, "preview": review.proposal_preview(self.truth, p)} for p in items]}
        if what == "recent":
            rows = self.truth.execute("select at, actor, action, target_type, target_id from audit_log"
                                      " order by seq desc limit ?", (limit,))
            return {"items": [dict(r) for r in rows]}
        if what == "memories":
            sql, params = "select distinct m.id from memories m", []
            where = ["m.status != 'forgotten'"]
            if entity:
                sql += " join memory_entities me on me.memory_id = m.id"
                ids = self._entity_ids(entity) or []
                where.append(f"me.entity_id in ({','.join('?' * len(ids))})")
                params += ids
            if source:  # memories citing this source (e.g. "was this page already extracted?")
                where.append("m.id in (select memory_id from evidence where source_id = ?)")
                params.append(source)
            if kind:
                where.append("m.kind = ?")
                params.append(kind)
            if status:
                where.append("m.status = ?")
                params.append(status)
            if slot:
                where.append("m.slot = ?")
                params.append(slot)
            # ids break ties between rows updated in the same second, so pages never overlap
            sql += " where " + " and ".join(where) + " order by m.updated_at desc, m.id desc limit ? offset ?"
            rows = self.truth.execute(sql, (*params, limit, offset)).fetchall()
            return {"items": [_summary(get_memory(self.truth, r[0])) for r in rows]}
        raise ValidationError("what must be one of memories, entities, slots, proposals, recent")

    def counts(self) -> dict[str, Any]:
        """Memories per visible status, and pending proposals."""
        by_status = {s: 0 for s in ("active", "superseded", "disputed", "archived")}
        for status, n in self.truth.execute("select status, count(*) from memories group by status"):
            if status in by_status:
                by_status[status] = n
        pending = self.truth.execute("select count(*) from proposals where status = 'pending'").fetchone()[0]
        return {"memories": by_status, "pending_proposals": pending}

    # ------------------------------------------------------------------ write (agents too)

    def add_source(self, *, text: str | None = None, path: str | Path | None = None, title: str | None = None,
                   kind: str = "document", origin_key: str | None = None, source_time: str | None = None,
                   trust: str | None = None, metadata: dict[str, Any] | None = None, extract: bool = False,
                   url: str | None = None, actor: Actor | None = None) -> dict[str, Any]:
        who = self._who(actor)
        notes: list[str] = []
        if url:  # e.g. a Notion page fetched by the agent: the URL gives a stable identity for versions
            origin_key = origin_key or origin_key_from_url(url)
            metadata = {**(metadata or {}), "url": url.strip()}
        if who.is_owner:
            trust = trust or "owner"
        else:
            if trust == "owner":
                trust = "agent"
                notes.append("trust capped to agent")
            trust = trust or "external"
            if trust not in AGENT_TRUSTS:
                raise ValidationError(f"agents may set trust to {list(AGENT_TRUSTS)}")
            if path is not None:
                path = self._check_agent_path(Path(path))
        out = add_source(self.truth, self.paths, self.settings, actor=who, text=text,
                         path=Path(path) if path is not None else None, title=title, kind=kind,
                         origin_key=origin_key, source_time=source_time, trust=trust, metadata=metadata)
        out["notes"] = notes
        out["extraction"] = None
        if extract and (out["outcome"] != "duplicate" or not self._extracted(out["source_id"])):
            out["extraction"] = self._extract(out["source_id"], notes)
        notes += self._sync_after_write()
        return out

    def _extracted(self, source_id: str) -> bool:
        return self.truth.execute(
            "select 1 from evidence e join memories m on m.id = e.memory_id where e.source_id = ?"
            " and m.created_by = 'extractor' limit 1", (source_id,)).fetchone() is not None

    def _extract(self, source_id: str, notes: list[str]) -> dict[str, Any] | None:
        """Run the structured extractor in batches; a failure is reported, never raised (the source
        is already stored, and re-importing with extract=True retries)."""
        ops = extract_ops(get_source(self.truth, source_id, text_limit=10**12))
        if not ops:
            return None
        total: dict[str, Any] = {"results": [], "applied": 0, "pending": 0}
        try:
            for i in range(0, len(ops), evolution.MAX_OPS):
                part = evolution.propose(self.truth, self.settings, ops[i:i + evolution.MAX_OPS],
                                         actor=Actor.extractor(), reason=f"structured extraction of {source_id}")
                total["results"] += part["results"]
                total["applied"] += part["applied"]
                total["pending"] += part["pending"]
        except PmiError as exc:
            notes.append(f"source stored; extraction failed: {exc.message}")
            return {**exc.to_dict(), "applied": total["applied"], "pending": total["pending"]}
        return total

    def propose(self, ops: Any, *, reason: str | None = None, actor: Actor | None = None) -> dict[str, Any]:
        result = evolution.propose(self.truth, self.settings, ops, actor=self._who(actor), reason=reason)
        result["notes"] = self._sync_after_write()
        idx = self.indexer.open()
        for r in result["results"]:
            if r["op"] != "create" or r["outcome"] != "applied" or not r["memory_id"]:
                continue
            m = get_memory(self.truth, r["memory_id"])
            if m["slot"]:
                continue
            seen = {c["memory_id"] for c in r["candidates"]} | {m["id"]}
            similar = [h for h in search_memories(idx, self.truth, m["content"], limit=6, embedder=self.embedder,
                                                  min_similarity=self.settings.vector_min_similarity)
                       if h["memory_id"] not in seen][:SIMILAR_CANDIDATES]
            for h in similar:
                other = get_memory(self.truth, h["memory_id"])
                r["candidates"].append({"memory_id": other["id"], "title": other["title"],
                                        "status": other["status"], "score": round(h["score"], 6)})
        return result

    # ------------------------------------------------------------------ owner only

    def import_paths(self, paths: list[str], *, recursive: bool = True, extract: bool = False,
                     trust: str = "owner", kind: str = "document", actor: Actor | None = None) -> dict[str, Any]:
        who = self._owner(actor, "import files")
        out: dict[str, list] = {"imported": [], "skipped": [], "errors": []}

        def handle(f: Path) -> None:
            if f.name.startswith("."):
                out["skipped"].append({"path": str(f), "reason": "hidden"})
            elif f.suffix.lower() not in SUPPORTED_EXTENSIONS:
                out["skipped"].append({"path": str(f), "reason": "unsupported"})
            else:
                try:
                    r = self.add_source(path=f, kind=kind, trust=trust, extract=extract, actor=who)
                    ext = r["extraction"]
                    out["imported"].append({"path": str(f), "source_id": r["source_id"], "outcome": r["outcome"],
                                            "applied": ext["applied"] if ext else 0,
                                            "pending": ext["pending"] if ext else 0})
                except PmiError as exc:
                    out["errors"].append({"path": str(f), "error": exc.message})
                except Exception as exc:  # one odd file must never abort the batch
                    out["errors"].append({"path": str(f), "error": f"{type(exc).__name__}: {exc}"})

        for raw in paths:
            p = Path(raw).expanduser()
            if p.is_file():
                handle(p)
            elif p.is_dir():
                for root, dirs, files in os.walk(p):
                    for d in sorted(dirs):
                        if d.startswith("."):
                            out["skipped"].append({"path": str(Path(root) / d), "reason": "hidden"})
                    dirs[:] = sorted(d for d in dirs if not d.startswith(".")) if recursive else []
                    for name in sorted(files):
                        handle(Path(root) / name)
            else:
                out["errors"].append({"path": str(p), "error": "not found"})
        return out

    def confirm(self, memory_id: str, *, actor: Actor | None = None) -> dict[str, Any]:
        result = review.confirm_memory(self.truth, memory_id, actor=self._owner(actor, "confirm memories"))
        self._sync_after_write()
        return result

    def approve(self, proposal_id: str, *, actor: Actor | None = None) -> dict[str, Any]:
        result = review.approve_proposal(self.truth, self.settings, proposal_id,
                                         actor=self._owner(actor, "approve proposals"))
        self._sync_after_write()
        return result

    def reject(self, proposal_id: str, reason: str, *, actor: Actor | None = None) -> dict[str, Any]:
        result = review.reject_proposal(self.truth, proposal_id, actor=self._owner(actor, "reject proposals"),
                                        reason=reason)
        self._sync_after_write()
        return result

    def _after_forget(self, result: dict[str, Any]) -> dict[str, Any]:
        notes = self._sync_after_write()
        try:
            self.indexer.purge()
            checkpoint(self.truth)
        except sqlite3.Error as exc:
            notes.append(f"forgotten, but compaction was deferred ({exc}); run `pmi reindex` when agents are idle")
        result["notes"] = notes
        return result

    def forget_memory(self, memory_id: str, reason: str, *, actor: Actor | None = None) -> dict[str, Any]:
        result = forget.forget_memory(self.truth, memory_id, actor=self._owner(actor, "forget"), reason=reason)
        return self._after_forget(result)

    def forget_source(self, source_id: str, reason: str, *, mode: str = "refuse", all_versions: bool = True,
                      actor: Actor | None = None) -> dict[str, Any]:
        result = forget.forget_source(self.truth, self.paths, source_id, actor=self._owner(actor, "forget"),
                                      reason=reason, mode=mode, all_versions=all_versions)  # type: ignore[arg-type]
        return self._after_forget(result)

    def add_alias(self, entity_id: str, alias: str, *, actor: Actor | None = None) -> dict[str, Any]:
        who = self._owner(actor, "edit entities")
        with write_tx(self.truth):
            entity = add_alias(self.truth, entity_id, alias, actor=who)
            mids = [r[0] for r in self.truth.execute("select memory_id from memory_entities where entity_id = ?",
                                                     (entity_id,))]
            for mid in mids:  # aliases are part of the memory index text
                log_change(self.truth, "memory", mid, "upsert")
        self._sync_after_write()
        return entity

    def reindex(self, *, vectors_only: bool = False, actor: Actor | None = None) -> dict[str, Any]:
        self._owner(actor, "rebuild the index")
        if vectors_only:
            idx = self.indexer.open()
            with write_tx(idx):
                idx.execute("delete from vectors")
            return self.indexer.sync(self.truth)
        return self.indexer.rebuild(self.truth)

    # ------------------------------------------------------------------ maintenance

    def verify(self, *, fix: bool = False) -> dict[str, Any]:
        """Check integrity; fix=True also deletes orphan files under data/sources (owner only)."""
        if fix:
            self._owner(None, "fix the data folder")
        return maintenance.verify(self.truth, self.paths, self.indexer, fix=fix)

    def doctor(self) -> dict[str, Any]:
        return maintenance.doctor(self.paths, self.settings)

    def backup(self, out: str | Path | None = None, *, actor: Actor | None = None) -> Path:
        self._owner(actor, "back up")
        return maintenance.backup(self.truth, self.paths, Path(out) if out else None)

    def export(self, out: str | Path, *, actor: Actor | None = None) -> dict[str, Any]:
        self._owner(actor, "export")
        return maintenance.export_jsonl(self.truth, Path(out))

    def download_model(self, *, actor: Actor | None = None) -> dict[str, Any]:
        self._owner(actor, "download models")
        result = download_model(self.settings, self.paths)
        self.embedder = get_embedder(self.settings, self.paths)
        self.indexer.embedder = self.embedder
        return result

    @staticmethod
    def restore_backup(archive: str | Path, target_home: str | Path, *, force: bool = False) -> dict[str, Any]:
        return maintenance.restore(Path(archive), Path(target_home), force=force)
