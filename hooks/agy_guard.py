#!/usr/bin/env python3
"""PreToolUse hook: refuse a Bash command whose text could run agy print mode, agy-delegate or the plugin's agy-job.

The match is on the whole command text (quoted strings, bash -c arguments and heredoc bodies included), so a command
that only mentions these names is refused too; that is deliberate: anything that could run agy goes through agy-call.
Tests: uvx pytest tests/test_guard.py
"""
import json
import re
import sys

DELEGATE = re.compile(r"(?<![\w.-])(?:[^\s'\"]*/)?agy-(?:delegate|job)(?:\.sh)?(?![\w-])")
PRINT = re.compile(r"(?<![\w.-])(?:[^\s'\"]*/)?agy(?![\w-])[^\n]*?(?<![\w-])(?:-p|--print|--prompt|-i|--prompt-interactive)(?![\w-])")
SAFE = re.compile(r"(?<![\w.-])(?:[^\s'\"]*/)?agy(?![\w-])[ \t]+(?:models|mcp|--version|-v|--help|-h)(?![\w-])")
REASON = ("Direct agy, agy-delegate and agy-job calls are blocked: they lose paid replies. Use agy-call, which saves every reply, "
          "resumes the same conversation and honours the shared quota hold:\n"
          "  agy-call run --job <id> --store <project dir>/agy-jobs --model gemini-4-argon-high --prompt-file <file> [--expect json --require a,b]\n"
          "Run it with run_in_background for long jobs. See the agy-call skill.")


def blocked(command):
    command = command.replace("\\\n", " ")
    safe = SAFE.sub("AGYSAFE", command)
    return bool(DELEGATE.search(command) or PRINT.search(safe))


def main():
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return 0
    command = (data.get("tool_input") or {}).get("command") or ""
    if blocked(command):
        print(REASON, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
