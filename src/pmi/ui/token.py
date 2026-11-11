"""The web UI's access token: created once, kept in data/ui-token (mode 600), reused across restarts.

Storing it on disk does not widen access: a program that can read this file can already run
`pmi` itself. What the token keeps out is other web pages in the browser, which cannot read local
files or this site's storage.
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

from ..paths import Paths

TOKEN_FILE = "ui-token"


def token_path(paths: Paths) -> Path:
    return paths.data / TOKEN_FILE


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def load_or_create_token(paths: Paths, *, renew: bool = False) -> str:
    path = token_path(paths)
    if not renew and path.is_file():
        token = path.read_text(encoding="utf-8").strip()
        if token:
            os.chmod(path, 0o600)
            return token
    token = secrets.token_urlsafe(32)
    _write_private(path, token)
    return token
