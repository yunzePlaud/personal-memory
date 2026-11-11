"""Shared fixtures for the web UI tests: a live server on a free port and a raw HTTP client."""
import http.client
import json
import threading

import pytest

from pmi.ui.config import LogSpec
from pmi.ui.server import UiApp, make_server

TOKEN = "t" * 43


class Client:
    def __init__(self, port):
        self.port = port

    def call(self, method, path, body=None, *, token=TOKEN, host=None, ctype="application/json", raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Host": host or f"127.0.0.1:{self.port}"}
        if token:
            headers["X-PMI-Token"] = token
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        if data is not None:
            headers["Content-Type"] = ctype
        conn.request(method, path, body=data, headers=headers)
        r = conn.getresponse()
        payload = r.read()
        conn.close()
        ctype_out = r.getheader("Content-Type") or ""
        return r.status, (json.loads(payload) if "json" in ctype_out else payload.decode()), dict(r.getheaders())


@pytest.fixture
def logdir(tmp_path):
    d = tmp_path / "logs"
    d.mkdir()
    (d / "session-2026-11-11.log").write_text("[Workflow] 本轮完成：扫描 0 条\n[DM] hi\n", encoding="utf-8")
    return d


@pytest.fixture
def ui(home, logdir):
    app = UiApp(home, token=TOKEN, embedder=None,
                logs=(LogSpec("bot", str(logdir / "session-*.log"), hide_pattern="扫描 0 条"),))
    srv = make_server(app, port=0)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    yield Client(srv.server_address[1])
    srv.shutdown()
    srv.server_close()
