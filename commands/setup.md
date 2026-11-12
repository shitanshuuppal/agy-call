---
description: Check that agy and this machine can run agy-call, without sending a prompt or spending quota. Runs only when the user asks for it.
argument-hint: ""
disable-model-invocation: true
allowed-tools: Bash(agy-call:*)
---

Run this with the Bash tool:

```
agy-call setup
```

Show its output to the user unchanged.
It prints one line per check, `ok` or `FAIL`, and exits 0 only when every check passes.
For each `FAIL` line, add the fix in one line:

- Python or operating system: agy-call needs Python 3.9 or newer on macOS or Linux.
- agy not found, or it could not be started: install the agy CLI and put it on the PATH, or set `AGYCALL_AGY` to its path.
- agy version, or a flag missing from `agy --help`: update agy to 1.5.6 or newer (`agy update`).
- agy models: sign in by running `agy` once in a terminal.
- A model not listed: this account cannot use that model; pass another `--model` to the agy commands.
