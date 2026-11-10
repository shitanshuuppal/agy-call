import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def agy_home(tmp_path, monkeypatch):
    """Every test gets its own ~/.agy-call."""
    home = tmp_path / "home"
    monkeypatch.setenv("AGYCALL_HOME", str(home))
    return home
