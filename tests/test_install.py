"""The launcher works through symlinks, and scripts/install.sh sets a user up idempotently.
A fake `uv` stands in for the real one, so nothing is installed and the real HOME is untouched."""
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ("pmi-notion", "pmi-linear", "pmi-slack")


def fake_env(tmp_path: Path) -> dict:
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text('#!/usr/bin/env bash\necho "PMI_HOME=${PMI_HOME:-}"\necho "UV_ARGS=$*"\n')
    uv.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    env = {k: v for k, v in os.environ.items() if k != "PMI_HOME"}
    env.update(HOME=str(home), PATH=f"{bin_dir}:{os.environ['PATH']}")
    return env


def test_launcher_resolves_its_folder_through_chained_symlinks(tmp_path):
    env = fake_env(tmp_path)
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "pmi").symlink_to(ROOT / "pmi")
    (tmp_path / "b" / "pmi").symlink_to(tmp_path / "a" / "pmi")
    out = subprocess.run([str(tmp_path / "b" / "pmi"), "doctor"], capture_output=True, text=True, env=env).stdout
    assert f"PMI_HOME={ROOT}" in out and f"--project {ROOT}" in out


def run_install(env, *args):
    return subprocess.run([str(ROOT / "scripts" / "install.sh"), *args], capture_output=True, text=True, env=env)


def test_install_links_cli_and_skills_and_is_idempotent(tmp_path):
    env = fake_env(tmp_path)
    home = Path(env["HOME"])
    first = run_install(env)
    assert first.returncode == 0, first.stderr
    assert (home / ".local/bin/pmi").resolve() == ROOT / "pmi"
    for s in SKILLS:
        assert (home / ".claude/skills" / s).resolve() == ROOT / "integrations/claude-code/skills" / s
    assert "claude mcp add" in first.stdout
    second = run_install(env)
    assert second.returncode == 0 and "already linked" in second.stdout


def test_install_never_overwrites_a_real_folder(tmp_path):
    env = fake_env(tmp_path)
    mine = Path(env["HOME"]) / ".claude/skills/pmi-notion"
    mine.mkdir(parents=True)
    (mine / "SKILL.md").write_text("my own version")
    result = run_install(env)
    assert result.returncode == 0 and (mine / "SKILL.md").read_text() == "my own version"
    assert "left untouched" in result.stderr


def test_install_options(tmp_path):
    env = fake_env(tmp_path)
    result = run_install(env, "--no-skills", "--no-semantic")
    assert result.returncode == 0
    assert not (Path(env["HOME"]) / ".claude/skills/pmi-notion").exists()
    assert "--extra semantic" not in result.stdout
    assert "export PMI_NO_SEMANTIC=1" in result.stdout
    assert "PMI_NO_SEMANTIC" not in run_install(env, "--no-skills").stdout
    assert run_install(env, "--bogus").returncode == 2
