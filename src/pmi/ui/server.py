"""Local web UI server (standard library only).

Every /api/ request must come with the right Host (127.0.0.1 or localhost on our port, against DNS
rebinding) and the X-PMI-Token header; no CORS headers are ever sent, and POST bodies must be JSON.
Together these keep other web pages in the browser from reading or changing memories.
"""
from __future__ import annotations

import hmac
import json
import sys
import traceback
import webbrowser  # noqa: F401  (used by serve)
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit

from ..actors import Actor
from ..api import Memory
from ..errors import NotFoundError, PmiError, ValidationError
from ..paths import Paths
from . import handlers
from .config import LogSpec

UI_ACTOR = Actor.owner("ui")
STATIC_DIR = Path(__file__).parent / "static"
MAX_BODY_BYTES = 65536
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
}
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}
HTTP_STATUS = {"validation": 400, "unauthorized": 401, "policy_denied": 403, "forbidden_host": 403,
               "not_found": 404, "method_not_allowed": 405, "conflict": 409}


class UiApp:
    def __init__(self, home: Path | str, *, token: str, logs: tuple[LogSpec, ...], embedder: Any):
        self.home = Path(home)
        self.paths = Paths(self.home)
        self.token = token
        self.logs = tuple(logs)
        self.embedder = embedder

    def memory(self) -> Memory:
        """A fresh connection per request; callers close it with `with`."""
        return Memory.open(self.home, actor=UI_ACTOR, embedder=self.embedder)

    def log_spec(self, name: str) -> LogSpec:
        for spec in self.logs:
            if spec.name == name:
                return spec
        raise NotFoundError(f"log not configured: {name!r}")


class HttpError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class _Handler(BaseHTTPRequestHandler):
    server_version = "pmi-ui"
    sys_version = ""

    def log_message(self, format: str, *args: Any) -> None:  # keep the terminal quiet
        pass

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def _unsupported(self) -> None:
        self._error("method_not_allowed", "method not allowed")

    do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_HEAD = _unsupported

    # ------------------------------------------------------------------ dispatch

    def _dispatch(self, method: str) -> None:
        try:
            self._check_host()
            parts = urlsplit(self.path)
            if not parts.path.startswith("/api/"):
                self._static(method, parts.path)
                return
            self._check_token()
            func, params = self._route(method, parts.path)
            query = dict(parse_qsl(parts.query))
            body = self._body() if method == "POST" else {}
            self._json(200, func(self.server.app, params, query, body))
        except HttpError as exc:
            self._error(exc.code, exc.message)
        except PmiError as exc:
            self._error(exc.code, exc.message, exc.details)
        except Exception:  # noqa: BLE001 - never leak internals to the page
            traceback.print_exc(file=sys.stderr)
            self._error("internal", "internal error; see the terminal running pmi ui", status=500)

    def _check_host(self) -> None:
        port = self.server.server_address[1]
        host = (self.headers.get("Host") or "").strip().lower()
        if host not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            raise HttpError("forbidden_host", "unexpected Host header")

    def _check_token(self) -> None:
        given = self.headers.get("X-PMI-Token") or ""
        if not hmac.compare_digest(given.encode(), self.server.app.token.encode()):
            raise HttpError("unauthorized", "missing or invalid token; open the link printed by pmi ui")

    def _route(self, method: str, path: str) -> tuple[handlers.Handler, dict[str, str]]:
        path_known = False
        for route_method, pattern, func in handlers.ROUTES:
            match = pattern.fullmatch(path)
            if match is None:
                continue
            if route_method == method:
                return func, {k: unquote(v) for k, v in match.groupdict().items()}
            path_known = True
        if path_known:
            raise HttpError("method_not_allowed", f"{method} is not allowed here")
        raise NotFoundError(f"no such endpoint: {path}")

    def _body(self) -> dict[str, Any]:
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            raise ValidationError("POST bodies must be application/json")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ValidationError("invalid Content-Length") from None
        if length > MAX_BODY_BYTES:
            raise ValidationError(f"request body larger than {MAX_BODY_BYTES} bytes")
        raw = self.rfile.read(length) if length > 0 else b""
        if not raw.strip():
            return {}
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise ValidationError("request body is not valid JSON") from None
        if not isinstance(body, dict):
            raise ValidationError("request body must be a JSON object")
        return body

    # ------------------------------------------------------------------ responses

    def _static(self, method: str, path: str) -> None:
        if method != "GET":
            raise HttpError("method_not_allowed", f"{method} is not allowed here")
        entry = STATIC_FILES.get(path)
        if entry is None:
            raise NotFoundError(f"not found: {path}")
        name, ctype = entry
        self._send(200, (STATIC_DIR / name).read_bytes(), ctype)

    def _json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _error(self, code: str, message: str, details: dict[str, Any] | None = None, *,
               status: int | None = None) -> None:
        self._json(status or HTTP_STATUS.get(code, 400),
                   {"error": {"code": code, "message": message, "details": details or {}}})

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for key, value in SECURITY_HEADERS.items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, app: UiApp, address: tuple[str, int]):
        self.app = app
        super().__init__(address, _Handler)


def make_server(app: UiApp, *, port: int, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    return _Server(app, (host, port))
