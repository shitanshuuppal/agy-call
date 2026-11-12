import json
import os
import sys

import pytest

from agyturns import git

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))


@pytest.fixture(autouse=True)
def agy_home(tmp_path, monkeypatch):
    """Every test gets its own ~/.agy-call."""
    home = tmp_path / "home"
    monkeypatch.setenv("AGYCALL_HOME", str(home))
    monkeypatch.delenv("AGYCALL_SESSION", raising=False)  # a developer's own session must not leak into tests
    monkeypatch.setenv("AGYCALL_AGY_CONFIG", str(tmp_path / "agy-config.json"))  # never the user's real shared grants
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


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A git repository with one commit on main (a.txt, and a .gitignore that ignores build/), a local identity, no
    global or system git configuration, and the test's working directory inside it."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.name", "Test")
    git(r, "config", "user.email", "test@example.com")
    (r / ".gitignore").write_text("build/\n")
    (r / "a.txt").write_text("a\n")
    git(r, "add", ".")
    git(r, "commit", "-q", "-m", "init")
    monkeypatch.chdir(r)
    return r
