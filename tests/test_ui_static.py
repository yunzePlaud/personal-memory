from pathlib import Path

from tests.ui_support import logdir, ui  # noqa: F401  (fixtures)

STATIC = Path(__file__).parents[1] / "src" / "pmi" / "ui" / "static"


def test_assets_served(ui):
    s, _, h = ui.call("GET", "/app.js", token=None)
    assert s == 200 and "javascript" in h["Content-Type"]
    assert ui.call("GET", "/app.css", token=None)[2]["Content-Type"].startswith("text/css")


def test_index_has_no_inline_code():
    html = (STATIC / "index.html").read_text()
    assert "<style" not in html and "style=" not in html
    assert html.count("<script") == html.count('<script src=')


def test_app_never_injects_html():
    js = (STATIC / "app.js").read_text()
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert banned not in js
    for route in ("#/overview", "#/memories", "#/review", "#/logs", "#/storage"):
        assert route in js
