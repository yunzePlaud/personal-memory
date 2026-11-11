import os
import tarfile

from pmi import Memory
from pmi.ui.token import load_or_create_token, token_path


def mode(p):
    return p.stat().st_mode & 0o777


def test_token_created_private(paths):
    t = load_or_create_token(paths)
    assert len(t) >= 40 and mode(token_path(paths)) == 0o600


def test_token_reused(paths):
    assert load_or_create_token(paths) == load_or_create_token(paths)


def test_renew_changes_token(paths):
    a = load_or_create_token(paths)
    b = load_or_create_token(paths, renew=True)
    assert a != b and load_or_create_token(paths) == b


def test_blank_token_file_regenerated(paths):
    token_path(paths).write_text("  \n")
    os.chmod(token_path(paths), 0o644)
    t = load_or_create_token(paths)
    assert t.strip() == t and len(t) >= 40 and mode(token_path(paths)) == 0o600


def test_loose_permissions_tightened(paths):
    t = load_or_create_token(paths)
    os.chmod(token_path(paths), 0o644)
    assert load_or_create_token(paths) == t and mode(token_path(paths)) == 0o600


def test_backup_excludes_token(home):
    with Memory.open(home, embedder=None) as m:
        load_or_create_token(m.paths)
        archive = m.backup()
    with tarfile.open(archive) as tar:
        assert not any("ui-token" in n for n in tar.getnames())
