import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))


@pytest.fixture(autouse=True)
def agy_home(tmp_path, monkeypatch):
    """Every test gets its own ~/.agy-call."""
    home = tmp_path / "home"
    monkeypatch.setenv("AGYCALL_HOME", str(home))
    return home


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """Point agy-call at tests/fake_agy.py; returns (set_turns, launches)."""
    from agycall import runner
    exe = tmp_path / "agy"
    exe.write_text("#!%s\n" % sys.executable + open(os.path.join(HERE, "fake_agy.py")).read())
    exe.chmod(0o755)
    for k, v in {"AGYCALL_AGY": str(exe), "FAKE_SCENARIO": str(tmp_path / "scenario.json"),
                 "FAKE_STATE": str(tmp_path / "state"), "FAKE_LOG": str(tmp_path / "log.jsonl")}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(runner, "RETRY_WAIT", 0)

    def set_turns(*turns):
        (tmp_path / "scenario.json").write_text(json.dumps(list(turns)))

    def launches():
        p = tmp_path / "log.jsonl"
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

    return set_turns, launches
