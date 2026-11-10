import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "hooks"))
import agy_guard  # noqa: E402

BLOCK = [
    "agy -p 'summarise this'",
    "agy --print x",
    "agy -m gemini-4-argon-high --conversation c1 -p go",
    'agy-delegate "do the task"',
    "/Users/x/.claude/plugins/cache/antigravity-for-claude-code/antigravity/0.28.0/scripts/agy-delegate.sh -t pro task",
    "bash -c 'agy -p x'",
    "ls\nagy -p x",
    "cd /tmp && agy-job start --tier pro task",
    "echo hi | agy --prompt-interactive",
    "agy \\\n  -p 'question'",
    "agy --model \"$(printf '%s' gemini-4-argon-high; :)\" -p question",
    "agy --model \"$(printf '%s' gemini-4-argon-high | cat)\" -p question",
    "grep agy \\\n  -i README.md",
    "grep agy -i README.md",
    "cat <<'EOF' > run.sh\nagy -p go\nEOF",
]
ALLOW = [
    "agy-call run --job a --prompt-file f",
    "agy-call batch jobs.jsonl --workers 15",
    "agy models",
    "agy --version",
    "agy mcp list",
    "agy --help",
    "agy-call run --job a --model m --prompt-file f",
    "grep -i 'ag[y]' file",
    "agy models && mkdir -p output",
    "agy --version; python3 -i",
    "ls ~/.agy-call/jobs/default",
    "python3 question_pass.py --workers 15",
    "grep -n conversation_id results/x.json",
]


@pytest.mark.parametrize("cmd", BLOCK)
def test_blocks(cmd):
    assert agy_guard.blocked(cmd)


@pytest.mark.parametrize("cmd", ALLOW)
def test_allows(cmd):
    assert not agy_guard.blocked(cmd)


def test_hook_protocol():
    hook = os.path.join(os.path.dirname(HERE), "hooks", "agy_guard.py")
    r = subprocess.run([sys.executable, hook], input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "agy -p x"}}),
                       capture_output=True, text=True)
    assert r.returncode == 2 and "agy-call run" in r.stderr
    r = subprocess.run([sys.executable, hook], input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "agy models"}}),
                       capture_output=True, text=True)
    assert r.returncode == 0
    r = subprocess.run([sys.executable, hook], input="not json", capture_output=True, text=True)
    assert r.returncode == 0
