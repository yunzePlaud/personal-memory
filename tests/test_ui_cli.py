import socket

from pmi.cli import main
from pmi.ui import server as ui_server
from pmi.ui.token import token_path


def test_port_in_use(home, capsys):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen()
    try:
        assert main(["--home", str(home), "ui", "--port", str(s.getsockname()[1]), "--no-open"]) == 1
    finally:
        s.close()
    assert "already in use" in capsys.readouterr().err


def test_bad_ui_config_fails_only_ui(home, capsys):
    cfg = home / "config" / "settings.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text('[[ui.logs]]\nname = 3\n')
    assert main(["--home", str(home), "ui", "--port", "0", "--no-open"]) == 1
    assert "settings.toml: ui" in capsys.readouterr().err
    assert main(["--home", str(home), "--json", "list", "memories"]) == 0


def test_serve_prints_link_and_opens_browser(home, monkeypatch, capsys):
    opened = []
    monkeypatch.setattr(ui_server.webbrowser, "open", opened.append)
    monkeypatch.setattr(ui_server.ThreadingHTTPServer, "serve_forever",
                        lambda self, *a, **k: (_ for _ in ()).throw(KeyboardInterrupt))
    ui_server.serve(home, port=0, open_browser=True)
    out = capsys.readouterr().out
    token = token_path(ui_server.Paths(home)).read_text().strip()
    assert f"/#token={token}" in out and opened and opened[0].endswith(f"/#token={token}")
    assert "127.0.0.1:0/" not in out


def test_new_token_replaces_the_old_one(home, monkeypatch, capsys):
    monkeypatch.setattr(ui_server.ThreadingHTTPServer, "serve_forever",
                        lambda self, *a, **k: (_ for _ in ()).throw(KeyboardInterrupt))
    ui_server.serve(home, port=0, open_browser=False)
    first = token_path(ui_server.Paths(home)).read_text().strip()
    assert main(["--home", str(home), "ui", "--port", "0", "--no-open", "--new-token"]) == 0
    assert token_path(ui_server.Paths(home)).read_text().strip() != first
