#!/usr/bin/env python3
"""SessionStart hook: append `export AGYCALL_SESSION=<session id>` to the file named by CLAUDE_ENV_FILE, so the jobs
agy-call starts record the Claude session that started them (spec 3.2, 7.3).
It never fails the session: on any problem it writes nothing and exits 0.
Tests: uvx pytest tests/test_session_start.py
"""
import json
import os
import shlex
import sys


def main():
    try:
        session = json.load(sys.stdin).get("session_id")
        env_file = os.environ.get("CLAUDE_ENV_FILE")
        if isinstance(session, str) and session and env_file:
            with open(env_file, "a", encoding="utf-8") as fh:
                fh.write("export AGYCALL_SESSION=%s\n" % shlex.quote(session))
    except Exception:  # a session must start even when this hook cannot record its id
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
