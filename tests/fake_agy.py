#!/usr/bin/env python3
"""Stand-in for the agy binary. FAKE_SCENARIO is a JSON list of turns; the Nth print-mode launch plays turn N (the last
turn repeats). A turn has exit (default 0), stderr, sleep, write (files to create in the working directory, as
{"relative/path": "content"}, written before the sleep), and either the envelope fields status (default SUCCESS),
response, error, denied_actions, conversation and usage, or raw_hex (the exact stdout bytes, for malformed output).
Every print-mode launch is logged to FAKE_LOG as {"argv", "prompt", "cwd"}, plus "settings" (the parsed --settings
file) when one is passed."""
import fcntl
import json
import os
import sys
import time

args = sys.argv[1:]
if "-p" not in args:  # --help, --version and models probes
    print("agy stand-in")
    sys.exit(0)
turns = json.load(open(os.environ["FAKE_SCENARIO"]))
state = os.environ["FAKE_STATE"]
with open(state + ".lock", "w") as lk:  # parallel launches (batch) each get their own turn number
    fcntl.flock(lk, fcntl.LOCK_EX)
    n = int(open(state).read() or 0) if os.path.exists(state) else 0
    open(state, "w").write(str(n + 1))
rec = {"argv": args, "prompt": args[args.index("-p") + 1], "cwd": os.getcwd()}
if "--settings" in args:
    with open(args[args.index("--settings") + 1]) as fh:
        rec["settings"] = json.load(fh)
with open(os.environ["FAKE_LOG"], "a") as fh:
    fh.write(json.dumps(rec) + "\n")
t = turns[min(n, len(turns) - 1)]
for name, content in t.get("write", {}).items():
    os.makedirs(os.path.dirname(os.path.abspath(name)), exist_ok=True)
    with open(name, "w") as fh:
        fh.write(content)
time.sleep(t.get("sleep", 0))
sys.stderr.write(t.get("stderr", ""))
if "raw_hex" in t:
    sys.stdout.buffer.write(bytes.fromhex(t["raw_hex"]))
else:
    env = {"status": t.get("status", "SUCCESS"), "response": t.get("response", ""),
           "usage": t.get("usage", {"input_tokens": 5, "output_tokens": 10})}
    if t.get("conversation"):
        env["conversation_id"] = t["conversation"]
    for k in ("error", "denied_actions"):
        if k in t:
            env[k] = t[k]
    sys.stdout.write(json.dumps(env))
sys.exit(t.get("exit", 0))
