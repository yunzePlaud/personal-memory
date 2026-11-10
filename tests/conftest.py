from pathlib import Path

import pytest

from pmi.paths import Paths


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """A fresh, initialised PMI home directory."""
    h = tmp_path / "pmi-home"
    Paths(h).ensure()
    return h


@pytest.fixture
def paths(home: Path) -> Paths:
    return Paths(home)


@pytest.fixture
def truth(paths: Paths):
    from pmi.db import open_truth

    conn = open_truth(paths)
    yield conn
    conn.close()
