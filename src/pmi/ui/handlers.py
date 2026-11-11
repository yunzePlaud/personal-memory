"""API handlers for the web UI. Each takes (app, path params, query params, JSON body) and returns
a JSON-able payload; failures are raised as PmiError subclasses and mapped to HTTP by the server."""
from __future__ import annotations

import re
from typing import Any, Callable

from ..errors import ConflictError, NotFoundError, ValidationError
from ..review import list_proposals
from .logs import log_files, log_status, read_since, read_tail, resolve_log_file
from .storage import storage_usage

Handler = Callable[[Any, dict[str, str], dict[str, str], dict[str, Any]], Any]
ROUTES: list[tuple[str, re.Pattern[str], Handler]] = []


def route(method: str, pattern: str) -> Callable[[Handler], Handler]:
    def register(func: Handler) -> Handler:
        ROUTES.append((method, re.compile(pattern), func))
        return func
    return register


# ---------------------------------------------------------------------------- storage and logs

@route("GET", r"/api/storage")
def get_storage(app: Any, params: dict[str, str], query: dict[str, str], body: dict[str, Any]) -> Any:
    return storage_usage(app.paths)


@route("GET", r"/api/logs")
def get_logs(app: Any, params: dict[str, str], query: dict[str, str], body: dict[str, Any]) -> Any:
    return {"logs": [{"name": s.name, "path": s.path, "stale_minutes": s.stale_minutes,
                      "hide_pattern": s.hide_pattern, "files": [p.name for p in log_files(s)],
                      "status": log_status(s)} for s in app.logs]}


@route("GET", r"/api/logs/(?P<name>[^/]+)/(?P<file>[^/]+)")
def get_log_file(app: Any, params: dict[str, str], query: dict[str, str], body: dict[str, Any]) -> Any:
    path = resolve_log_file(app.log_spec(params["name"]), params["file"])
    since = query.get("since")
    try:
        if since is None:
            out = read_tail(path)
        else:
            try:
                offset = int(since)
            except ValueError:
                raise ValidationError("since must be an integer byte offset") from None
            out = read_since(path, offset)
    except FileNotFoundError:  # rotated away between listing and reading
        raise NotFoundError(f"log file not found: {params['file']!r}") from None
    return {"file": path.name, **out}


# ---------------------------------------------------------------------------- memories

_MEM_ID = re.compile(r"mem_[0-9A-Za-z]+")
_PRP_ID = re.compile(r"prp_[0-9A-Za-z]+")
_STATUSES = {"active", "superseded", "disputed", "archived", "all"}


def _mem_id(params: dict[str, str]) -> str:
    if not _MEM_ID.fullmatch(params["id"]):
        raise NotFoundError(f"memory not found: {params['id']!r}")
    return params["id"]


def _prp_id(params: dict[str, str]) -> str:
    if not _PRP_ID.fullmatch(params["id"]):
        raise NotFoundError(f"proposal not found: {params['id']!r}")
    return params["id"]


def _int(query: dict[str, str], key: str, default: int, lo: int, hi: int) -> int:
    raw = query.get(key)
    if raw in (None, ""):
        return default
    try:
        return max(lo, min(int(raw), hi))
    except ValueError:
        raise ValidationError(f"{key} must be an integer") from None


def _reason(body: dict[str, Any]) -> str:
    reason = body.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ValidationError("a reason is required")
    return reason.strip()


def _with_times(m: Any, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not items:
        return []
    ids = [i["id"] for i in items]
    rows = {r["id"]: r for r in m.truth.execute(
        f"select id, updated_at, created_by from memories where id in ({','.join('?' * len(ids))})", ids)}
    return [{**i, "updated_at": rows[i["id"]]["updated_at"], "created_by": rows[i["id"]]["created_by"]}
            for i in items if i["id"] in rows]


def _live_memory(m: Any, memory_id: str) -> dict[str, Any]:
    found = m.get([memory_id], include_history=True)["items"]
    if not found or found[0]["status"] == "forgotten":
        raise NotFoundError(f"memory not found: {memory_id}")
    return found[0]


def _pending_proposals_for(m: Any, memory_id: str) -> list[str]:
    out = []
    for p in list_proposals(m.truth, "pending"):
        for op in p["ops"]:
            refs = {op.get("memory_id"), op.get("id"), *(op.get("old_ids") or [])}
            if memory_id in refs:
                out.append(p["id"])
                break
    return out


def _actions(mem: dict[str, Any]) -> dict[str, bool]:
    status = mem["status"]
    return {"confirm": status in ("active", "superseded") and mem["verification"] != "user_confirmed",
            "resolve": status == "disputed",
            "archive": status in ("active", "disputed", "superseded"),
            "restore": status == "archived",
            "forget": True}


@route("GET", r"/api/memories")
def list_memories(app: Any, params: dict[str, str], query: dict[str, str], body: dict[str, Any]) -> Any:
    status = query.get("status") or "active"
    if status not in _STATUSES:
        raise ValidationError(f"status must be one of {sorted(_STATUSES)}")
    status_filter = None if status == "all" else status
    limit = _int(query, "limit", 50, 1, 200)
    offset = _int(query, "offset", 0, 0, 1_000_000)
    kind = query.get("kind") or None
    q = (query.get("q") or "").strip()
    with app.memory() as m:
        if q:  # search understands "all" (every visible status); its own default is active + disputed
            items = m.search(q, scope="memories", kind=kind, status=status, limit=limit)["memories"]
        else:
            items = m.list("memories", kind=kind, status=status_filter, slot=query.get("slot") or None,
                           limit=limit, offset=offset)["items"]
        return {"items": _with_times(m, items)}


@route("GET", r"/api/memories/(?P<id>[^/]+)")
def get_memory_detail(app: Any, params: dict[str, str], query: dict[str, str], body: dict[str, Any]) -> Any:
    memory_id = _mem_id(params)
    with app.memory() as m:
        mem = _live_memory(m, memory_id)
        urls: dict[str, str | None] = {}
        for ev in mem["evidence"]:
            sid = ev["source_id"]
            if sid not in urls:
                src = m.get([sid], text_limit=0)["items"]
                urls[sid] = (src[0].get("metadata") or {}).get("url") if src else None
            ev["url"] = urls[sid]
        mem["supersedes"] = [dict(r) for r in m.truth.execute(
            "select id, title from memories where superseded_by = ? order by updated_at", (memory_id,))]
        mem["pending_proposals"] = _pending_proposals_for(m, memory_id)
        mem["actions"] = _actions(mem)
        return mem


@route("POST", r"/api/memories/(?P<id>[^/]+)/confirm")
def confirm_memory(app: Any, params: dict[str, str], query: dict[str, str], body: dict[str, Any]) -> Any:
    memory_id = _mem_id(params)
    with app.memory() as m:
        _live_memory(m, memory_id)
        return m.confirm(memory_id)


@route("POST", r"/api/memories/(?P<id>[^/]+)/archive")
def archive_memory(app: Any, params: dict[str, str], query: dict[str, str], body: dict[str, Any]) -> Any:
    memory_id = _mem_id(params)
    with app.memory() as m:
        _live_memory(m, memory_id)
        return m.propose([{"op": "archive", "id": memory_id}], reason="archived in web UI")


@route("POST", r"/api/memories/(?P<id>[^/]+)/restore")
def restore_memory(app: Any, params: dict[str, str], query: dict[str, str], body: dict[str, Any]) -> Any:
    memory_id = _mem_id(params)
    with app.memory() as m:
        _live_memory(m, memory_id)
        return m.propose([{"op": "restore", "id": memory_id}], reason="restored in web UI")


@route("POST", r"/api/memories/(?P<id>[^/]+)/forget")
def forget_memory(app: Any, params: dict[str, str], query: dict[str, str], body: dict[str, Any]) -> Any:
    memory_id = _mem_id(params)
    reason = _reason(body)
    with app.memory() as m:
        _live_memory(m, memory_id)
        return m.forget_memory(memory_id, reason)


# ---------------------------------------------------------------------------- proposals

@route("GET", r"/api/proposals")
def get_proposals(app: Any, params: dict[str, str], query: dict[str, str], body: dict[str, Any]) -> Any:
    with app.memory() as m:
        return m.list("proposals")


@route("POST", r"/api/proposals/(?P<id>[^/]+)/approve")
def approve_proposal(app: Any, params: dict[str, str], query: dict[str, str], body: dict[str, Any]) -> Any:
    proposal_id = _prp_id(params)
    with app.memory() as m:
        result = m.approve(proposal_id)
    if result.get("status") == "failed":  # it left the queue, but nothing was applied
        failure = result.get("result") or {}
        raise ConflictError(f"not applied: {failure.get('message') or 'the change could not be applied'}",
                            failure.get("details") or {})
    return result


@route("POST", r"/api/proposals/(?P<id>[^/]+)/reject")
def reject_proposal(app: Any, params: dict[str, str], query: dict[str, str], body: dict[str, Any]) -> Any:
    proposal_id = _prp_id(params)
    reason = _reason(body)
    with app.memory() as m:
        return m.reject(proposal_id, reason)


# ---------------------------------------------------------------------------- overview

def _latest_log(app: Any) -> dict[str, Any] | None:
    if not app.logs:
        return None
    spec = app.logs[0]
    files = log_files(spec)
    if not files:
        return None
    try:
        text = read_tail(files[0], max_lines=5)["text"]
    except FileNotFoundError:
        return None
    return {"name": spec.name, "file": files[0].name, "lines": text.splitlines()}


@route("GET", r"/api/overview")
def get_overview(app: Any, params: dict[str, str], query: dict[str, str], body: dict[str, Any]) -> Any:
    with app.memory() as m:
        counts = m.counts()
        recent = _with_times(m, m.list("memories", limit=5)["items"])
    usage = storage_usage(app.paths)
    return {"memories": counts["memories"], "pending_proposals": counts["pending_proposals"],
            "disk": {"total_bytes": usage["total_bytes"],
                     "top": sorted(usage["items"], key=lambda i: -i["bytes"])[:2]},
            "logs": [{"name": s.name, "status": log_status(s)} for s in app.logs],
            "recent_memories": recent, "latest_log": _latest_log(app)}
