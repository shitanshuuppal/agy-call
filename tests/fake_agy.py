#!/usr/bin/env python3
"""Stand-in for the agy binary under the real agy-delegate. Modes (FAKE_AGY_MODE):
fail - print a paid-looking reply and exit 1 (the delegate prints nothing);
ok   - print a reply and exit 0;
hang - print a reply, flush, then sleep 60."""
import os
import sys

args = sys.argv[1:]
if "--help" in args or "-h" in args:
    print("Usage: agy [options]\n  --output-format  Output format for print mode (text, json, stream-json)\n  --conversation   Resume\n  --print-timeout  t")
    sys.exit(0)
if args[:1] == ["models"]:
    print("gemini-4-argon-high\ngemini-3.8-flash-high\ngemini-3.8-flash-low")
    sys.exit(0)
if "--version" in args:
    print("1.5.3")
    sys.exit(0)
mode = os.environ.get("FAKE_AGY_MODE", "ok")
sys.stdout.write("PAID REPLY TEXT 12345\n")
sys.stdout.flush()
sys.stderr.write("fake agy stderr line\n")
if mode == "hang":
    import time
    time.sleep(60)
sys.exit(1 if mode == "fail" else 0)
