import pytest

from pmi import Memory
from pmi.config import load_settings
from pmi.errors import ValidationError
from pmi.ui.config import LogSpec, load_ui_config


def write_settings(paths, body):
    paths.config_file.parent.mkdir(parents=True, exist_ok=True)
    paths.config_file.write_text(body, encoding="utf-8")


def test_no_ui_section_means_no_logs(paths):
    assert load_ui_config(paths) == ()


def test_logs_parsed_with_defaults(paths):
    write_settings(paths, '[[ui.logs]]\nname = "bot"\npath = "~/x/session-*.log"\n')
    assert load_ui_config(paths) == (LogSpec(name="bot", path="~/x/session-*.log"),)


def test_all_fields(paths):
    write_settings(paths, '[[ui.logs]]\nname = "bot"\npath = "/l/*.log"\nstale_minutes = 5\n'
                          'hide_pattern = "扫描 0 条"\n')
    assert load_ui_config(paths)[0] == LogSpec("bot", "/l/*.log", 5, "扫描 0 条")


@pytest.mark.parametrize("body, needle", [
    ('[[ui.logs]]\npath = "a"\n', "name"),
    ('[[ui.logs]]\nname = "a"\n', "path"),
    ('[[ui.logs]]\nname = "a"\npath = "x"\n[[ui.logs]]\nname = "a"\npath = "y"\n', "unique"),
    ('[[ui.logs]]\nname = "a"\npath = "x"\nstale_minutes = 0\n', "stale_minutes"),
    ('[[ui.logs]]\nname = "a"\npath = "x"\nstale_minutes = true\n', "stale_minutes"),
    ('[[ui.logs]]\nname = "a"\npath = "x"\nhide_pattern = 3\n', "hide_pattern"),
    ('[[ui.logs]]\nname = "a"\npath = "x"\ncolour = "red"\n', "colour"),
    ('[ui]\ntheme = "dark"\n', "theme"),
    ('[ui]\nlogs = "x"\n', "logs"),
])
def test_invalid_ui_config(paths, body, needle):
    write_settings(paths, body)
    with pytest.raises(ValidationError, match=needle):
        load_ui_config(paths)


def test_bad_ui_section_does_not_break_other_settings(paths):
    write_settings(paths, '[retrieval]\nmax_tokens = 1234\n[[ui.logs]]\nname = 3\n')
    assert load_settings(paths).context_max_tokens == 1234


def test_doctor_reports_ui_config(home, paths):
    write_settings(paths, '[[ui.logs]]\nname = 3\n')
    with Memory.open(home, embedder=None) as m:
        out = m.doctor()
    assert "error" in out["checks"]["ui_config"]
    write_settings(paths, '[[ui.logs]]\nname = "a"\npath = "x"\n')
    with Memory.open(home, embedder=None) as m:
        assert m.doctor()["checks"]["ui_config"] == {"logs": 1}
