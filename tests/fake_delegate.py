#!/usr/bin/env python3
"""Stand-in for agy-delegate. FAKE_SCENARIO is a JSON list of turns: {rc, stdout, stderr, conversation, sleep};
the Nth launch plays turn N (the last turn repeats). Every launch is logged to FAKE_LOG."""
import fcntl
import json
import os
import sys
import time

turns = json.load(open(os.environ["FAKE_SCENARIO"]))
state = os.environ["FAKE_STATE"]
with open(state + ".lock", "w") as lk:  # parallel launches (batch) must each get their own turn number
    fcntl.flock(lk, fcntl.LOCK_EX)
    n = int(open(state).read() or 0) if os.path.exists(state) else 0
    open(state, "w").write(str(n + 1))
prompt = sys.stdin.read() if sys.argv[-1:] == ["-"] else ""
with open(os.environ["FAKE_LOG"], "a") as fh:
    fh.write(json.dumps({"argv": sys.argv[1:], "prompt": prompt, "cwd": os.getcwd(),
                         "structured": os.environ.get("CLAUDE_PLUGIN_OPTION_STRUCTURED_OUTPUT")}) + "\n")
t = turns[min(n, len(turns) - 1)]
time.sleep(t.get("sleep", 0))
if t.get("conversation"):
    sys.stderr.write("AGY_USAGE " + json.dumps({"conversation_id": t["conversation"], "usage": {"output": 10, "input": 5}}) + "\n")
sys.stderr.write(t.get("stderr", ""))
sys.stdout.buffer.write(t.get("stdout", "").encode("utf-8") if "stdout_bytes" not in t else bytes.fromhex(t["stdout_bytes"]))
sys.exit(t.get("rc", 0))
