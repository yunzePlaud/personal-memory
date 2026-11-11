import pytest

from tests.ui_support import Client, logdir, ui  # noqa: F401  (fixtures)


def test_api_requires_token(ui):
    assert ui.call("GET", "/api/storage", token=None)[1]["error"]["code"] == "unauthorized"
    assert ui.call("GET", "/api/storage", token="wrong")[0] == 401
    assert ui.call("GET", "/api/storage")[0] == 200


def test_wrong_host_forbidden(ui):
    s, body, _ = ui.call("GET", "/api/storage", host=f"evil.example:{ui.port}")
    assert s == 403 and body["error"]["code"] == "forbidden_host"
    assert ui.call("GET", "/api/storage", host=f"localhost:{ui.port}")[0] == 200


def test_no_cors_and_security_headers(ui):
    _, _, h = ui.call("GET", "/api/storage")
    assert not any(k.lower().startswith("access-control-") for k in h)
    assert h["Content-Security-Policy"] == "default-src 'self'" and h["X-Content-Type-Options"] == "nosniff"
    s, _, h = ui.call("OPTIONS", "/api/storage")
    assert s != 200 and not any(k.lower().startswith("access-control-") for k in h)


def test_static_without_token(ui):
    s, html, h = ui.call("GET", "/", token=None)
    assert s == 200 and 'src="app.js"' in html and h["Content-Type"].startswith("text/html")
    assert ui.call("GET", "/nope", token=None)[0] == 404


def test_method_not_allowed(ui):
    assert ui.call("POST", "/api/storage", {})[0] == 405


def test_storage_endpoint(ui):
    s, body, _ = ui.call("GET", "/api/storage")
    assert s == 200 and [i["key"] for i in body["items"]][0] == "memory_db" and "total_bytes" in body


def test_logs_listing_and_reading(ui, logdir):
    s, body, _ = ui.call("GET", "/api/logs")
    log = body["logs"][0]
    assert log["name"] == "bot" and log["files"] == ["session-2026-11-11.log"]
    assert log["status"]["state"] == "active" and log["hide_pattern"] == "扫描 0 条"
    s, tail, _ = ui.call("GET", "/api/logs/bot/session-2026-11-11.log")
    assert tail["text"].splitlines()[-1] == "[DM] hi"
    with (logdir / "session-2026-11-11.log").open("a", encoding="utf-8") as f:
        f.write("[DM] new\n")
    s, inc, _ = ui.call("GET", f"/api/logs/bot/session-2026-11-11.log?since={tail['offset']}")
    assert inc["text"] == "[DM] new\n" and inc["file"] == "session-2026-11-11.log"
    assert ui.call("GET", "/api/logs/bot/session-2026-11-11.log?since=abc")[0] == 400


@pytest.mark.parametrize("path", ["/api/logs/bot/..%2F..%2Fetc%2Fpasswd", "/api/logs/nobody/x.log",
                                  "/api/logs/bot/other.log", "/api/unknown"])
def test_paths_outside_config_404(ui, path):
    s, body, _ = ui.call("GET", path)
    assert s == 404 and body["error"]["code"] == "not_found"


def test_internal_error_is_json_500(ui, monkeypatch):
    import pmi.ui.handlers as h
    monkeypatch.setattr(h, "storage_usage", lambda paths: 1 / 0)
    s, body, _ = ui.call("GET", "/api/storage")
    assert s == 500 and body["error"]["code"] == "internal" and "ZeroDivision" not in body["error"]["message"]
