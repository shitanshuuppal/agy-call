import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, "hooks", "session_start.py")


def run_hook(stdin, env_file):
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_ENV_FILE"}
    if env_file is not None:
        env["CLAUDE_ENV_FILE"] = str(env_file)
    return subprocess.run([sys.executable, HOOK], input=stdin, capture_output=True, text=True, env=env, timeout=30)


def test_the_session_id_is_appended_to_the_env_file(tmp_path):
    env_file = tmp_path / "env.sh"
    env_file.write_text("export OTHER=1\n")
    r = run_hook(json.dumps({"session_id": "abc-123", "hook_event_name": "SessionStart", "source": "startup"}), env_file)
    assert r.returncode == 0
    assert env_file.read_text() == "export OTHER=1\nexport AGYCALL_SESSION=abc-123\n"


def test_a_session_id_with_shell_characters_is_quoted(tmp_path):
    env_file = tmp_path / "env.sh"
    session = "x'; touch %s; '$(touch %s)`touch %s`" % (tmp_path / "a", tmp_path / "b", tmp_path / "c")
    assert run_hook(json.dumps({"session_id": session}), env_file).returncode == 0
    r = subprocess.run(["sh", "-c", '. "$1"; printf %s "$AGYCALL_SESSION"', "sh", str(env_file)],
                       capture_output=True, text=True, timeout=30)
    assert r.stdout == session
    assert not any((tmp_path / name).exists() for name in ("a", "b", "c"))


@pytest.mark.parametrize("stdin", ["", "not json", "[1, 2]", json.dumps({}), json.dumps({"session_id": ""}),
                                   json.dumps({"session_id": 7})])
def test_input_without_a_session_id_writes_nothing(tmp_path, stdin):
    env_file = tmp_path / "env.sh"
    r = run_hook(stdin, env_file)
    assert r.returncode == 0
    assert not env_file.exists()


def test_a_missing_or_unwritable_env_file_still_exits_0(tmp_path):
    assert run_hook(json.dumps({"session_id": "s1"}), None).returncode == 0
    assert run_hook(json.dumps({"session_id": "s1"}), tmp_path / "no-such-dir" / "env.sh").returncode == 0


def test_hooks_json_registers_the_session_hook_beside_the_guard():
    with open(os.path.join(ROOT, "hooks", "hooks.json")) as fh:
        hooks = json.load(fh)["hooks"]
    start = hooks["SessionStart"][0]["hooks"][0]
    assert start["type"] == "command"
    assert start["command"] == 'python3 "${CLAUDE_PLUGIN_ROOT}/hooks/session_start.py"'
    assert hooks["PreToolUse"][0]["hooks"][0]["command"] == 'python3 "${CLAUDE_PLUGIN_ROOT}/hooks/agy_guard.py"'
