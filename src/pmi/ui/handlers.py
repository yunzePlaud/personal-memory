"""API handlers for the web UI. Each takes (app, path params, query params, JSON body) and returns
a JSON-able payload; failures are raised as PmiError subclasses and mapped to HTTP by the server."""
from __future__ import annotations

import re
from typing import Any, Callable

from ..errors import NotFoundError, ValidationError
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
