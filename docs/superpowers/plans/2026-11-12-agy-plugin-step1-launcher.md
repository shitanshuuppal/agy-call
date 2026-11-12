# agy plugin, build step 1: launcher and classifier Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** agy-call launches agy itself and classifies every run from agy's JSON envelope and stderr, so the Antigravity plugin's wrapper and the capture shim are no longer needed, while `run` and `batch` keep their behaviour except the changes the spec lists.

**Architecture:** A new `agycall/agy.py` runs one agy process with its streams written straight into the attempt folder and a deadline that stops the whole process group.
A new `agycall/classify.py` turns that run into exactly one named outcome.
`agycall/runner.py` keeps its job loop, locks, quota hold and checks, and switches its per-attempt dispatch from the wrapper's exit codes to those outcomes.

**Tech Stack:** Python 3.9+ standard library only; tests with pytest run as `uvx pytest -q tests/`.

**Spec:** `docs/superpowers/specs/2026-11-12-agy-plugin-design.md`, sections 5.1, 5.2 (the `run` and `batch` column), 6 and 11.
This is build step 1 of section 12.
Steps 2 to 5 get their own plans, each written after the previous step lands, because their code builds on what this step produces.

## Global Constraints

- Python 3.9 or newer, standard library only inside `agycall/`; macOS and Linux only (process groups and `fcntl`).
- agy 1.5.6 or newer is the target; agy-call passes `--output-format json` on every run.
- `run` and `batch` never pass `--settings`; their options, the Python `call()` and `batch()` signatures, the job store layout, quota holds and job identities stay unchanged.
- The prompt travels as one argument; prompts over 512 KiB of UTF-8 are refused before any attempt folder exists.
- `AGY_DELEGATE` and `AGYCALL_REAL_AGY` are no longer read; `AGYCALL_AGY` names the agy executable, else `agy` on PATH.
- Never use the em dash character (U+2014) or the word "honest" in any file; in long Markdown files put each sentence on its own line.
- The repository is public: no personal paths, names of private projects or reply text from real jobs in code, tests or docs.
- A PreToolUse hook refuses any Bash command whose text contains `agy-delegate`, `agy-job`, or the word agy followed by `-p`/`--print`/`-i`; put such text in files (for example `git commit -F <file>`) instead of the command line.
- Commit messages end with:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_013coVpN7if3z6kaMYLTrz3c`.

## Review Focus

- agy writes a warning line before its JSON on stdout: the run is `malformed`, nothing is accepted, and the error names the saved `agy.out` so the reply is not lost (Task 1 and Task 3 tests).
- agy is killed by a signal (negative exit status, no stdout): the error says which signal instead of a generic failure (Task 1 test).
- A timeout written as `90`, `0`, `ten minutes` or `5d`: bare numbers mean seconds and are sent to agy as `90s`; the others are refused before any attempt folder exists (Task 2 and Task 3 tests).
- A prompt made of multi-byte characters: the 512 KiB limit counts UTF-8 bytes, not characters (Task 3 test).
- agy ignores SIGTERM at the deadline: agy-call sends SIGKILL after `KILL_AFTER` seconds and returns instead of hanging (Task 2 test).

## Spec clarifications made by this plan

- Each attempt also gets `reply.txt` holding the envelope's `response` text, so a person can read the reply without parsing JSON; section 6 of the spec is updated in the same commit as this plan.
- Section 5.1 row 13 (`unknown`) cannot be reached once rows 1 to 12 exist (a blank reply is `empty`, a non-blank one is `reply` or `reply-with-error`, a non-string reply makes the envelope `malformed`), so it is not implemented; the spec is updated to say so.
- Row 3 (`missing`) is raised by the launcher as `Fatal(13)` before or at start, as today, rather than returned by the classifier.

## File map

| File | Change | Responsibility |
|---|---|---|
| `agycall/classify.py` | create | one finished run in, one `Outcome` out |
| `agycall/agy.py` | create | find agy, build its arguments, run it with a deadline, stop a process group |
| `agycall/runner.py` | modify | launch through `agy.py`, dispatch on `Outcome.name`, read old and new attempt layouts |
| `agycall/cli.py` | modify | a `ValueError` (bad argument, bad id, oversized prompt) exits 2 with a message |
| `agycall/shim.py` | delete | replaced by `agy.py` |
| `tests/test_classify.py` | create | one test per classification rule |
| `tests/test_agy.py` | create | launcher tests |
| `tests/fake_agy.py` | replace | scenario-driven stand-in for the agy binary |
| `tests/agyturns.py` | create | builders for fake agy turns |
| `tests/conftest.py` | modify | shared `fake` fixture |
| `tests/test_runner.py`, `tests/test_batch_cli.py` | modify | ported to the fake agy |
| `tests/fake_delegate.py`, `tests/test_shim.py` | delete | the wrapper and shim are gone |
| `skills/agy-call/SKILL.md`, `README.md`, `docs/demo/demo.sh`, `docs/demo.gif` | modify | new attempt files, env var and requirements |

---

### Task 1: Classifier

**Files:**
- Create: `agycall/classify.py`
- Test: `tests/test_classify.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `classify.envelope(out: bytes) -> dict | None`: agy's envelope, or `None` when stdout is not one JSON object with a string `status` and a string (or absent) `response`.
  - `classify.classify(exit: int | None, out: bytes, err: str, stopped: str | None = None) -> Outcome`; `stopped` is `"deadline"`, `"cancel"` or `None`.
  - `classify.Outcome` dataclass with fields `name: str`, `response: str`, `conversation: str | None`, `usage: dict`, `denied: list`, `status: str | None`, `exit: int | None`, `reason: str`.
  - `Outcome.name` is one of `cancelled`, `timeout`, `denied`, `output-limit`, `quota`, `auth`, `model`, `malformed`, `reply`, `reply-with-error`, `empty`.
  - `Outcome.usage` uses the keys `input`, `output`, `thinking`, `cache_read`, `total` (the keys agy-call has always stored).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_classify.py`:

```python
import json

import pytest

from agycall.classify import classify, envelope

CUT_OFF = ("Your previous response was cut off because it exceeded the output token limit\n"
           "Please continue from where you left off, keeping your response shorter\nRetries remaining: 3")
DENIED_NOTICE = ('jetski: no output produced, a tool required the "write_file" permission that headless mode cannot '
                 "prompt for, so it was auto-denied.\n")


def env(**fields):
    fields.setdefault("status", "SUCCESS")
    return json.dumps(fields).encode("utf-8")


def test_reply():
    o = classify(0, env(response="the answer", conversation_id="c1",
                        usage={"input_tokens": 5, "output_tokens": 10, "thinking_tokens": 2, "bogus": "x"}), "")
    assert o.name == "reply" and o.response == "the answer" and o.conversation == "c1"
    assert o.usage == {"input": 5, "output": 10, "thinking": 2} and o.status == "SUCCESS" and o.exit == 0


def test_denied_from_denied_actions_beats_everything_after_it():
    o = classify(0, env(response="", conversation_id="c1",
                        denied_actions=[{"action": "write_file", "display_name": "WriteFile"}]), DENIED_NOTICE + "quota\n")
    assert o.name == "denied" and o.denied == ["write_file"] and o.conversation == "c1"


def test_denied_from_stderr_without_json():
    assert classify(1, b"", "Error: permission check failed for RunCommand\n").name == "denied"


def test_output_limit_keeps_the_reply():
    o = classify(1, env(status="ERROR", response="- Files changed: a.py", error=CUT_OFF, conversation_id="c1"), "")
    assert o.name == "output-limit" and o.response == "- Files changed: a.py"
    assert "output token limit" in o.reason


def test_quota_auth_and_model_from_error_text():
    assert classify(1, env(status="ERROR", response="", error="RESOURCE_EXHAUSTED: quota exceeded. Resets in 10m"), "").name == "quota"
    assert classify(1, b"", "429 Rate Limit reached\n").name == "quota"
    assert classify(1, b"", "Error: unauthenticated; run agy once to sign in\n").name == "auth"
    o = classify(1, b"", 'Error: invalid --model "nope"\n')
    assert o.name == "model" and o.reason == 'Error: invalid --model "nope"'


def test_timeouts():
    assert classify(None, b"", "", stopped="deadline").name == "timeout"
    o = classify(0, env(response="half an ans", conversation_id="c1"), "print timeout after 10m0s, returning partial output\n")
    assert o.name == "timeout" and o.response == "half an ans"
    assert classify(1, b"", "context deadline exceeded\n").name == "timeout"


def test_cancel_wins():
    assert classify(None, env(response="x"), "quota", stopped="cancel").name == "cancelled"


def test_reply_with_error():
    assert classify(1, env(status="ERROR", response="partial work", error="stream closed"), "").name == "reply-with-error"
    assert classify(1, env(response="ok"), "").name == "reply-with-error"  # SUCCESS but a non-zero exit


def test_empty():
    assert classify(0, env(response="", conversation_id="c1"), "").name == "empty"
    assert classify(1, env(status="ERROR", response=""), "").name == "empty"


def test_malformed_keeps_nothing_accepted():
    noisy = b"Warning: update available\n" + env(response="x")
    assert classify(0, noisy, "").name == "malformed"
    assert classify(0, env(response=["not", "a", "string"]), "").name == "malformed"
    assert classify(2, b"", "flag provided but not defined: -zzz\n").name == "malformed"


def test_signal_kill_is_named():
    o = classify(-9, b"", "")
    assert o.name == "malformed" and "killed by signal 9" in o.reason


def test_envelope_tolerates_raw_newlines_and_bad_bytes():
    assert envelope(b'{"status": "SUCCESS", "response": "a\nb \xff"}')["response"] == "a\nb �"
    assert envelope(b"[1, 2]") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uvx pytest -q tests/test_classify.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'agycall.classify'`.

- [ ] **Step 3: Write the implementation**

Create `agycall/classify.py`:

```python
"""Turn one finished agy run into exactly one outcome (spec section 5.1).
Only agy's stderr and the envelope's status, error and denied_actions fields are searched; the reply text never is."""
import dataclasses
import json
import re

PARTIAL = re.compile(r"print timeout after .*returning partial output", re.IGNORECASE)
TIMEOUT_WORDS = ("timed out", "deadline exceeded")
RULES = (  # checked in this order against the envelope's error followed by stderr, case-insensitively
    ("denied", ("user denied permission", "permission check failed", "auto-denied", "permission that headless")),
    ("output-limit", ("exceeded the output token limit",)),
    ("quota", ("quota", "rate limit", "resource exhausted")),
    ("auth", ("unauthenticated", "unauthorized", "sign in", "please authenticate", "reauth")),
    ("model", ("invalid --model", "is not recognized as a known model", "not a known model")),
)
USAGE_KEYS = {"input_tokens": "input", "output_tokens": "output", "thinking_tokens": "thinking",
              "cache_read_tokens": "cache_read", "total_tokens": "total"}


@dataclasses.dataclass
class Outcome:
    name: str            # cancelled, timeout, denied, output-limit, quota, auth, model, malformed, reply, reply-with-error, empty
    response: str        # the envelope's reply text, "" when there is none
    conversation: object # the envelope's conversation id, or None
    usage: dict          # token counts under input, output, thinking, cache_read, total
    denied: list         # action kinds from denied_actions, for example ["command"]
    status: object       # the envelope's status string, or None
    exit: object         # agy's exit status (negative for a signal), or None when agy-call stopped the run
    reason: str          # one line for findings and error messages


def envelope(out):
    """agy's JSON envelope, or None when stdout is not one JSON object with a string status and a string reply."""
    try:
        env = json.loads(out.decode("utf-8", "replace"), strict=False)  # agy has put raw newlines inside strings
    except ValueError:
        return None
    if not isinstance(env, dict) or not isinstance(env.get("status"), str) or not isinstance(env.get("response", ""), str):
        return None
    return env


def _line(text, needle):
    for line in text.splitlines():
        if needle in line.lower():
            return line.strip()[:300]
    return needle


def classify(exit, out, err, stopped=None):
    env = envelope(out)
    e = env or {}
    response = e.get("response") or ""
    usage = e.get("usage") if isinstance(e.get("usage"), dict) else {}
    usage = {USAGE_KEYS[k]: v for k, v in usage.items() if k in USAGE_KEYS and isinstance(v, (int, float))}
    denied = [d["action"] for d in e.get("denied_actions") or [] if isinstance(d, dict) and d.get("action")]
    text = "%s\n%s" % (e.get("error") or "", err)
    low = text.lower()

    def outcome(name, reason):
        return Outcome(name, response, e.get("conversation_id") or None, usage, denied, e.get("status"), exit, reason)

    if stopped == "cancel":
        return outcome("cancelled", "cancelled by request")
    if stopped == "deadline":
        return outcome("timeout", "agy-call stopped agy at its deadline")
    m = PARTIAL.search(err)
    if m:
        return outcome("timeout", m.group(0)[:300])
    for word in TIMEOUT_WORDS:
        if word in low:
            return outcome("timeout", _line(text, word))
    if denied:
        return outcome("denied", "agy refused a %s action: headless mode cannot ask for permission" % "/".join(denied))
    for name, needles in RULES:
        for needle in needles:
            if needle in low:
                return outcome(name, _line(text, needle))
    if env is None:
        if isinstance(exit, int) and exit < 0:
            return outcome("malformed", "agy was killed by signal %d" % -exit)
        return outcome("malformed", "agy's output was not a JSON envelope (agy exited %s)" % exit)
    if response.strip():
        if exit == 0 and env["status"] == "SUCCESS":
            return outcome("reply", "agy replied")
        first = (str(e.get("error") or "").splitlines() or ["exit %s" % exit])[0]
        return outcome("reply-with-error", "agy reported %s: %s" % (env["status"], first[:300]))
    return outcome("empty", "agy returned no reply")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uvx pytest -q tests/test_classify.py`
Expected: 12 passed.

- [ ] **Step 5: Commit**

```bash
git add agycall/classify.py tests/test_classify.py
printf '%s\n' "Add the classifier for finished agy runs" "" "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" "Claude-Session: https://claude.ai/code/session_013coVpN7if3z6kaMYLTrz3c" > "$TMPDIR/agycall-msg.txt"
git commit -F "$TMPDIR/agycall-msg.txt"
```

---

### Task 2: Launcher

**Files:**
- Create: `agycall/agy.py`
- Test: `tests/test_agy.py`

**Interfaces:**
- Consumes: `agycall.errors.Fatal(code, reason)`.
- Produces:
  - `agy.MAX_PROMPT = 512 * 1024`, `agy.GRACE = 60`, `agy.KILL_AFTER = 10`, `agy.POLL = 1`.
  - `agy.path() -> str`: `AGYCALL_AGY` or `agy` on PATH; raises `Fatal(13, ...)` when missing.
  - `agy.seconds(t) -> int`: seconds in `"90"`, `"90s"`, `"10m"`, `"1h30m"`; `ValueError` for anything else or for zero.
  - `agy.argv(agy, prompt, *, model, timeout, mode=None, dirs=(), conversation=None, settings=None, effort=None, json_schema=None) -> list`.
  - `agy.redact(cmd) -> list`: `cmd` with the final prompt argument replaced by `"<prompt: N bytes, sha256 H>"`.
  - `agy.alive(pgid) -> bool`.
  - `agy.start_time(pid) -> str | None`: `ps -o lstart=` output.
  - `agy.run(att, cmd, *, cwd, deadline, fds=(), on_start=None) -> agy.Run`; `Run(exit, stopped, out: bytes, err: str)`; writes `att/agy.args`, `att/agy.out`, `att/agy.err`; `on_start(pid, start_time)` is called right after the process starts.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_agy.py`:

```python
import os
import sys
import time

import pytest

from agycall import agy
from agycall.errors import Fatal


def script(tmp_path, body):
    p = tmp_path / "agy"
    p.write_text("#!%s\nimport os, signal, subprocess, sys, time\n%s\n" % (sys.executable, body))
    p.chmod(0o755)
    return str(p)


def attempt(tmp_path):
    att = tmp_path / "att"
    att.mkdir()
    return att


def test_seconds():
    assert agy.seconds("90") == 90 and agy.seconds("90s") == 90 and agy.seconds("10m") == 600
    assert agy.seconds("1h30m") == 5400 and agy.seconds(" 2h ") == 7200
    for bad in ("", "0", "s", "ten minutes", "5d", "10 m"):
        with pytest.raises(ValueError):
            agy.seconds(bad)


def test_argv_order_and_redaction():
    cmd = agy.argv("/x/agy", "hello", model="m", timeout="10m", mode="plan", dirs=["/d"], conversation="c1",
                   settings="/g.json", effort="high", json_schema="/s.json")
    assert cmd == ["/x/agy", "--model", "m", "--print-timeout", "600s", "--output-format", "json", "--settings", "/g.json",
                   "--effort", "high", "--json-schema", "/s.json", "--mode", "plan", "--add-dir", "/d",
                   "--conversation", "c1", "-p", "hello"]
    assert agy.argv("/x/agy", "q", model="m", timeout="90") == [
        "/x/agy", "--model", "m", "--print-timeout", "90s", "--output-format", "json", "-p", "q"]
    red = agy.redact(cmd)
    assert red[:-1] == cmd[:-1] and red[-1].startswith("<prompt: 5 bytes, sha256 2cf24dba")


def test_run_saves_streams_closes_stdin_and_reports_the_start(tmp_path):
    exe = script(tmp_path, "time.sleep(0.5); sys.stdout.write(repr(sys.stdin.read())); sys.stderr.write('E'); sys.exit(3)")
    att = attempt(tmp_path)
    seen = []
    r = agy.run(str(att), [exe, "-p", "secret"], cwd=str(tmp_path), deadline=30, on_start=lambda pid, when: seen.append((pid, when)))
    assert r.exit == 3 and r.stopped is None and r.out == b"''" and r.err == "E"
    assert (att / "agy.out").read_bytes() == b"''" and (att / "agy.err").read_text() == "E"
    assert "secret" not in (att / "agy.args").read_text()
    assert seen[0][0] > 0 and seen[0][1]


def test_deadline_stops_the_whole_process_group(tmp_path, monkeypatch):
    monkeypatch.setattr(agy, "POLL", 0.1)
    exe = script(tmp_path, "subprocess.Popen(['sleep', '30']); print('started', flush=True); time.sleep(30)")
    att = attempt(tmp_path)
    pids = []
    t0 = time.monotonic()
    r = agy.run(str(att), [exe, "-p", "x"], cwd=str(tmp_path), deadline=1, on_start=lambda pid, when: pids.append(pid))
    assert r.stopped == "deadline" and r.exit is None and time.monotonic() - t0 < 10
    assert r.out == b"started\n" and not agy.alive(pids[0])


def test_sigterm_ignored_gets_sigkill(tmp_path, monkeypatch):
    monkeypatch.setattr(agy, "POLL", 0.1)
    monkeypatch.setattr(agy, "KILL_AFTER", 1)
    exe = script(tmp_path, "signal.signal(signal.SIGTERM, signal.SIG_IGN); print('up', flush=True); time.sleep(30)")
    t0 = time.monotonic()
    r = agy.run(str(attempt(tmp_path)), [exe, "-p", "x"], cwd=str(tmp_path), deadline=1)
    assert r.stopped == "deadline" and time.monotonic() - t0 < 8


def test_missing_agy_is_fatal_13(tmp_path, monkeypatch):
    monkeypatch.setenv("AGYCALL_AGY", str(tmp_path / "nope"))
    with pytest.raises(Fatal) as e:
        agy.path()
    assert e.value.code == 13


def test_unstartable_file_is_fatal_13(tmp_path):
    bad = tmp_path / "agy"
    bad.write_text("not executable")
    with pytest.raises(Fatal) as e:
        agy.run(str(attempt(tmp_path)), [str(bad), "-p", "x"], cwd=str(tmp_path), deadline=5)
    assert e.value.code == 13
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uvx pytest -q tests/test_agy.py`
Expected: FAIL with `ImportError: cannot import name 'agy' from 'agycall'`.

- [ ] **Step 3: Write the implementation**

Create `agycall/agy.py`:

```python
"""Launch one agy run (spec section 6): agy's stdout and stderr go straight into the attempt folder, the prompt is one
argument with stdin closed, and a deadline stops the whole process group, SIGTERM first and SIGKILL after KILL_AFTER."""
import dataclasses
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import time

from .errors import Fatal

MAX_PROMPT = 512 * 1024  # bytes of UTF-8
GRACE = 60               # seconds past agy's own --print-timeout before agy-call stops the run
KILL_AFTER = 10          # seconds between SIGTERM and SIGKILL
POLL = 1                 # seconds between checks while agy runs


@dataclasses.dataclass
class Run:
    exit: object     # agy's exit status, negative for a signal; None when agy-call stopped it
    stopped: object  # "deadline", or None
    out: bytes
    err: str


def path():
    p = os.environ.get("AGYCALL_AGY") or shutil.which("agy")
    if not p or not os.path.exists(p):
        raise Fatal(13, "agy not found (set AGYCALL_AGY or put agy on PATH)")
    return p


def seconds(t):
    """Seconds in a duration such as "90", "90s", "10m" or "1h30m"."""
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s?)?", str(t).strip())
    total = sum(int(x) * f for x, f in zip(m.groups(), (3600, 60, 1)) if x) if m else 0
    if not total:
        raise ValueError("timeout must look like 90s, 10m or 1h30m and be above zero: %r" % (t,))
    return total


def argv(agy, prompt, *, model, timeout, mode=None, dirs=(), conversation=None, settings=None, effort=None, json_schema=None):
    cmd = [agy, "--model", model, "--print-timeout", "%ds" % seconds(timeout), "--output-format", "json"]
    for flag, value in (("--settings", settings), ("--effort", effort), ("--json-schema", json_schema), ("--mode", mode)):
        if value:
            cmd += [flag, value]
    for d in dirs:
        cmd += ["--add-dir", d]
    if conversation:
        cmd += ["--conversation", conversation]
    return cmd + ["-p", prompt]


def redact(cmd):
    """The argument list with the prompt replaced by its size and hash; the prompt itself is kept in launch.json."""
    prompt = cmd[-1].encode("utf-8")
    return cmd[:-1] + ["<prompt: %d bytes, sha256 %s>" % (len(prompt), hashlib.sha256(prompt).hexdigest())]


def alive(pgid):
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def start_time(pid):
    try:
        return subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, timeout=5).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _stop(p):
    try:
        os.killpg(p.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    end = time.monotonic() + KILL_AFTER
    while time.monotonic() < end:
        p.poll()  # reap agy, or its exited leader keeps the group looking alive
        if not alive(p.pid):
            return
        time.sleep(0.1)
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def run(att, cmd, *, cwd, deadline, fds=(), on_start=None):
    with open(os.path.join(att, "agy.args"), "w", encoding="utf-8") as fh:
        json.dump(redact(cmd), fh)
    out_path, err_path = os.path.join(att, "agy.out"), os.path.join(att, "agy.err")
    with open(out_path, "wb") as out, open(err_path, "wb") as err:
        try:  # agy inherits the lock descriptors, so the job stays locked while agy lives
            p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=out, stderr=err, cwd=cwd,
                                 start_new_session=True, pass_fds=tuple(fds))
        except OSError as e:
            raise Fatal(13, "agy could not start: %s" % e)
    if on_start:
        on_start(p.pid, start_time(p.pid))
    end = time.monotonic() + deadline
    while True:
        try:
            rc, stopped = p.wait(timeout=POLL), None
            break
        except subprocess.TimeoutExpired:
            if time.monotonic() >= end:
                _stop(p)
                p.wait()
                rc, stopped = None, "deadline"
                break
    with open(out_path, "rb") as fh:
        data = fh.read()
    with open(err_path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    return Run(rc, stopped, data, text)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uvx pytest -q tests/test_agy.py`
Expected: 7 passed.
Run it three times; the deadline tests use real processes and must not flake.

- [ ] **Step 5: Commit**

```bash
git add agycall/agy.py tests/test_agy.py
printf '%s\n' "Add the agy launcher with a process-group deadline" "" "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" "Claude-Session: https://claude.ai/code/session_013coVpN7if3z6kaMYLTrz3c" > "$TMPDIR/agycall-msg.txt"
git commit -F "$TMPDIR/agycall-msg.txt"
```

---

### Task 3: Runner on the launcher

**Files:**
- Modify: `agycall/runner.py` (imports, helper functions, `recover`, `_launch`, `call`)
- Modify: `agycall/cli.py`
- Delete: `agycall/shim.py`, `tests/fake_delegate.py`, `tests/test_shim.py`
- Replace: `tests/fake_agy.py`
- Create: `tests/agyturns.py`
- Modify: `tests/conftest.py`, `tests/test_runner.py`, `tests/test_batch_cli.py`

**Interfaces:**
- Consumes: Task 1's `classify.classify`, `classify.envelope`, `Outcome`; Task 2's `agy.path`, `agy.argv`, `agy.redact`, `agy.run`, `agy.seconds`, `agy.alive`, `agy.GRACE`, `agy.MAX_PROMPT`.
- Produces (used by build steps 2 to 4):
  - Each attempt folder holds `launch.json` (now with `pstart`, the agy start time), `agy.args`, `agy.out`, `agy.err`, `reply.txt` (when there was a reply) and `result.json`.
  - `result.json` gains `exit` (agy's exit status), `agyStatus` (envelope status) and `classified` (the `Outcome.name`); `outcome` and `findings` keep their meaning.
  - `runner._saved_reply(att) -> str` and `runner._saved_conversation(att) -> str | None` read both the new and the old attempt layouts.
  - `tests/agyturns.py` builders and the shared `fake` fixture `(set_turns, launches)` in `tests/conftest.py`; `launches()` entries are `{"argv", "prompt", "cwd"}`.

- [ ] **Step 1: Replace the fake agy and add the turn builders**

Replace the whole of `tests/fake_agy.py` with:

```python
#!/usr/bin/env python3
"""Stand-in for the agy binary. FAKE_SCENARIO is a JSON list of turns; the Nth print-mode launch plays turn N (the last
turn repeats). A turn has exit (default 0), stderr, sleep, and either the envelope fields status (default SUCCESS),
response, error, denied_actions, conversation and usage, or raw_hex (the exact stdout bytes, for malformed output).
Every print-mode launch is logged to FAKE_LOG as {"argv", "prompt", "cwd"}."""
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
with open(os.environ["FAKE_LOG"], "a") as fh:
    fh.write(json.dumps({"argv": args, "prompt": args[args.index("-p") + 1], "cwd": os.getcwd()}) + "\n")
t = turns[min(n, len(turns) - 1)]
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
```

Create `tests/agyturns.py`:

```python
"""Builders for tests/fake_agy.py turns, one per shape of agy run the classifier knows."""

CUT_OFF = ("Your previous response was cut off because it exceeded the output token limit\n"
           "Please continue from where you left off, keeping your response shorter\nRetries remaining: 3")
DENIED_NOTICE = ('jetski: no output produced, a tool required the "command" permission that headless mode cannot '
                 "prompt for, so it was auto-denied.\n")


def reply(text, conv="c1", **extra):
    return {"response": text, "conversation": conv, **extra}


def error_reply(text, error=CUT_OFF, conv="c1"):
    return {"exit": 1, "status": "ERROR", "response": text, "error": error, "conversation": conv}


def empty(conv=None):
    return {"response": "", "conversation": conv}


def denied(kind="command", conv="c1"):
    return {"response": "", "conversation": conv, "stderr": DENIED_NOTICE,
            "denied_actions": [{"action": kind, "display_name": "RunCommand"}]}


def partial(text, conv="c1"):
    return {"response": text, "conversation": conv, "stderr": "print timeout after 10m0s, returning partial output\n"}


def quota_error(reset="Resets in 10m"):
    return {"exit": 1, "status": "ERROR", "response": "", "error": "RESOURCE_EXHAUSTED: quota exceeded for this model. " + reset}


def auth_error():
    return {"exit": 1, "raw_hex": "", "stderr": "Error: unauthenticated; run agy once to sign in\n"}


def model_error(name="m"):
    return {"exit": 1, "raw_hex": "", "stderr": 'Error: invalid --model "%s"\n' % name}


def malformed(raw=b"", stderr="flag provided but not defined: -zzz\n", exit=2):
    return {"exit": exit, "raw_hex": raw.hex(), "stderr": stderr}
```

Replace `tests/conftest.py` with:

```python
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
```

- [ ] **Step 2: Port `tests/test_runner.py` to the fake agy**

At the top of `tests/test_runner.py`, delete `import stat`, delete the whole local `fake` fixture (the `@pytest.fixture` `def fake(...)` block), and add after the `from agycall.errors import ...` line:

```python
from agyturns import CUT_OFF, auth_error, empty, denied, error_reply, malformed, model_error, partial, quota_error, reply
```

Replace these exact literals (each occurs where shown; nothing else in the test bodies changes unless listed in Step 3):

| Old | New |
|---|---|
| `GOOD = {"rc": 0, "stdout": '```json\n{"answer": "B", "confidence": 97}\n```', "conversation": "c1"}` | `GOOD = reply('```json\n{"answer": "B", "confidence": 97}\n```')` |
| `BAD = {"rc": 0, "stdout": "I would choose B.", "conversation": "c1"}` | `BAD = reply("I would choose B.")` |
| `{"rc": 12, "stdout": "half an ans", "conversation": "c1"}, {"rc": 0, "stdout": "the complete answer", "conversation": "c1"}` | `partial("half an ans"), reply("the complete answer")` |
| `{"rc": 15, "conversation": "c1"}, {"rc": 0, "stdout": "ok", "conversation": "c1"}` | `denied(), reply("ok")` |
| `{"rc": 15, "conversation": "c2"}` | `denied(conv="c2")` |
| `{"rc": 3}, {"rc": 0, "stdout": "fine", "conversation": "c1"}` | `empty(), reply("fine")` |
| `{"rc": 10, "stderr": 'AGY_SIGNAL {"status":"QUOTA_EXHAUSTED","reason":"agy quota / rate limit"}\nResets in 1h12m27s\n'}` | `quota_error("Resets in 1h12m27s")` |
| `{"rc": 0, "stdout": "ok", "conversation": "c1", "sleep": 1.5}` | `reply("ok", sleep=1.5)` |
| `{"rc": 0, "stdout": '{"error": "missing_initial_prompt"}', "conversation": "c1"}` | `reply('{"error": "missing_initial_prompt"}')` |
| `{"rc": 0, "stdout": '{"action": "revise"}', "conversation": "c1"}` | `reply('{"action": "revise"}')` |
| `{"rc": 0, "stdout": "full answer", "conversation": "c9"}` | `reply("full answer", "c9")` |
| `{"rc": 0, "stdout": "ok", "conversation": "shared"}` | `reply("ok", "shared")` |
| `{"rc": 0, "stdout": "x", "conversation": "c1"}` | `reply("x")` |
| `{"rc": 0, "stdout": "the reply", "conversation": "c1"}` | `reply("the reply")` |
| `{"rc": 0, "stdout": "not json"}, {"rc": 0, "stdout": '{"answer": 1}'}` | `reply("not json", None), reply('{"answer": 1}', None)` |
| `{"rc": 0, "stdout": "late", "conversation": "cX", "sleep": 4}, {"rc": 0, "stdout": "ok", "conversation": "cX"}` | `reply("late", "cX", sleep=4), reply("ok", "cX")` |

The tests that build old-layout attempt folders by hand (`_fake_attempt`, `_retry_attempt`, `test_retry_after_agy_output_does_not_start_a_new_conversation`, `test_conversation_id_stranded_in_result_json`, `test_quota_first_attempt_with_output_resends_the_prompt`) stay unchanged: they are the tests that old attempts still recover.

- [ ] **Step 3: Rewrite the tests whose assertions change, and add the new ones**

In `test_success_then_rerun_does_not_launch`, replace the line
`assert launches()[0]["structured"] == "on" and "--output-format" not in launches()[0]["argv"]`
and the line after it with:

```python
    a = launches()[0]["argv"]
    assert a[:2] == ["--model", "m"] and a[a.index("--output-format") + 1] == "json" and launches()[0]["prompt"] == "question?"
```

Replace `test_fatal_codes` and `test_missing_delegate_is_fatal_13` with:

```python
def test_fatal_codes(tmp_path, fake):
    set_turns, _ = fake
    set_turns(model_error())
    with pytest.raises(Fatal) as e:
        runner.call("j1", "q", store=S(tmp_path), model="m")
    assert e.value.code == 14 and 'invalid --model "m"' in e.value.reason
    set_turns(auth_error())
    with pytest.raises(Fatal) as e:
        runner.call("j2", "q", store=S(tmp_path), model="m")
    assert e.value.code == 11
    set_turns(malformed())
    with pytest.raises(Fatal) as e:
        runner.call("j3", "q", store=S(tmp_path), model="m")
    assert e.value.code == 22 and "not a JSON envelope" in e.value.reason


def test_missing_agy_is_fatal_13(tmp_path, monkeypatch):
    monkeypatch.setenv("AGYCALL_AGY", str(tmp_path / "nope"))
    with pytest.raises(Fatal) as e:
        runner.call("j1", "q", store=S(tmp_path), model="m")
    assert e.value.code == 13
    job = store.Job(S(tmp_path), "j1")
    assert job.status()["state"] == "fatal"
    assert store.read_json(os.path.join(job.launched()[-1], "result.json"))["outcome"] == "fatal"


def test_noisy_stdout_is_malformed_and_kept(tmp_path, fake):
    set_turns, _ = fake
    raw = b'Warning: update available\n{"status": "SUCCESS", "response": "paid text"}'
    set_turns(malformed(raw=raw, stderr="", exit=0))
    with pytest.raises(Fatal) as e:
        runner.call("j1", "q", store=S(tmp_path), model="m")
    att = store.Job(S(tmp_path), "j1").launched()[0]
    assert e.value.code == 22 and os.path.join(att, "agy.out") in e.value.reason
    assert open(os.path.join(att, "agy.out"), "rb").read() == raw
```

In `test_large_prompt_and_non_utf8_output`, replace the `set_turns(...)` line with:

```python
    set_turns({"raw_hex": b'{"status": "SUCCESS", "conversation_id": "c1", "response": "ok \xff end"}'.hex()})
```

In `test_cwd_and_dirs_are_passed`, replace the last assertion line with:

```python
    a = l["argv"]
    assert a[:4] == ["--model", "pro", "--print-timeout", "900s"] and a[a.index("--add-dir") + 1] == str(work)
    assert a[a.index("--mode") + 1] == "plan" and a[-2:] == ["-p", "q"]
```

Delete the `CUT_OFF` constant, the `_cut_off` helper and the three `test_output_limit_*` tests at the end of the file, and append:

```python
def test_output_limit_error_reply_is_checked_and_accepted(tmp_path, fake):
    set_turns, launches = fake
    set_turns(error_reply(GOOD["response"]))
    r = runner.call("j1", "q", store=S(tmp_path), model="m", expect="json", require=["answer"])
    assert r.answer["answer"] == "B" and len(launches()) == 1
    att = store.Job(S(tmp_path), "j1").launched()[0]
    assert "output token limit" in store.read_json(os.path.join(att, "result.json"))["agyError"]


def test_output_limit_error_reply_that_fails_checks_stops(tmp_path, fake):
    set_turns, launches = fake
    set_turns(error_reply(BAD["response"]), GOOD)
    with pytest.raises(GaveUp) as e:
        runner.call("j1", "q", store=S(tmp_path), model="m", expect="json", require=["answer"])
    assert len(launches()) == 1 and "no follow-up was sent" in str(e.value)


def test_output_limit_error_without_a_reply_stops_at_once(tmp_path, fake):
    set_turns, launches = fake
    set_turns(error_reply(""))
    with pytest.raises(GaveUp) as e:
        runner.call("j1", "q", store=S(tmp_path), model="m", max_turns=6)
    assert len(launches()) == 1 and "output token limit" in str(e.value)


def test_reply_after_an_agy_error_needs_a_check(tmp_path, fake):
    set_turns, launches = fake
    set_turns(error_reply("Implemented the change.", error="stream closed unexpectedly"))
    with pytest.raises(GaveUp) as e:
        runner.call("j1", "q", store=S(tmp_path), model="m")
    att = store.Job(S(tmp_path), "j1").launched()[0]
    assert len(launches()) == 1 and "accepted after an agy error only by a check" in str(e.value)
    assert open(os.path.join(att, "reply.txt")).read() == "Implemented the change."
    assert store.Job(S(tmp_path), "j1").status()["state"] == "failed"


def test_attempt_files_of_the_new_layout(tmp_path, fake):
    set_turns, _ = fake
    set_turns(GOOD)
    runner.call("j1", "secret question", store=S(tmp_path), model="m")
    att = store.Job(S(tmp_path), "j1").launched()[0]
    assert sorted(os.listdir(att)) == ["agy.args", "agy.err", "agy.out", "launch.json", "reply.txt", "result.json"]
    assert "secret question" not in open(os.path.join(att, "agy.args")).read()
    res = store.read_json(os.path.join(att, "result.json"))
    assert res["exit"] == 0 and res["agyStatus"] == "SUCCESS" and res["classified"] == "reply" and res["outcome"] == "accepted"
    assert "pstart" in store.read_json(os.path.join(att, "launch.json"))  # the value itself is tested in test_agy.py


def test_oversized_prompt_and_bad_timeout_launch_nothing(tmp_path, fake):
    set_turns, launches = fake
    set_turns(GOOD)
    with pytest.raises(ValueError, match="512 KiB"):
        runner.call("j1", "é" * (256 * 1024 + 1), store=S(tmp_path), model="m")  # 2 bytes each in UTF-8
    with pytest.raises(ValueError, match="timeout"):
        runner.call("j2", "q", store=S(tmp_path), model="m", timeout="ten minutes")
    assert launches() == [] and not os.path.exists(os.path.join(S(tmp_path), "j1"))
```

- [ ] **Step 4: Port `tests/test_batch_cli.py`**

Delete `import stat`, the local `fake` fixture, and the `pytest_plugins = []` line; add `from agyturns import quota_error, reply` after the existing imports.
Replace these exact literals:

| Old | New |
|---|---|
| `{"rc": 0, "stdout": "a", "conversation": "c1"}, {"rc": 0, "stdout": "b", "conversation": "c2"},` and the next line `{"rc": 10, "stderr": "Resets in 10m\n"})` | `reply("a"), reply("b", "c2"), quota_error())` |
| `{"rc": 0, "stdout": "a", "conversation": "c1"}` (every remaining occurrence) | `reply("a")` |
| `{"rc": 0, "stdout": '{"answer": "B"}', "conversation": "c1"}` | `reply('{"answer": "B"}')` |
| `{"rc": 10, "stderr": "Resets in 5m\n"}` | `quota_error("Resets in 5m")` |
| `{"rc": 0, "stdout": "ok", "conversation": "c1"}` | `reply("ok")` |

Append:

```python
def test_cli_value_errors_exit_2(tmp_path, fake):
    r = run_cli("run", "--job", "j1", "--store", str(tmp_path / "s"), "--prompt", "q")
    assert r.returncode == 2 and "model is required" in r.stderr
    r = run_cli("run", "--job", "bad/id", "--store", str(tmp_path / "s"), "--model", "m", "--prompt", "q")
    assert r.returncode == 2 and "job id" in r.stderr
```

- [ ] **Step 5: Delete the wrapper-era files and run the suite to see it fail**

```bash
git rm -q agycall/shim.py tests/fake_delegate.py tests/test_shim.py
uvx pytest -q tests/ 2>&1 | tail -5
```

Expected: FAIL, starting with `ImportError` in `agycall/runner.py` (`cannot import name 'shim'`).

- [ ] **Step 6: Switch `agycall/runner.py` to the launcher**

Replace the module docstring and imports (lines 1 to 17) with:

```python
"""One agy-call job: launch agy, check the reply, follow up in the same conversation."""
import contextlib
import dataclasses
import datetime as dt
import glob
import hashlib
import json
import os
import time
import types

from . import agy, checks, classify, quota
from . import store as st
from .errors import CheckError, Fatal, GaveUp, Mismatch, NoConversation, Quota, Running
```

Delete the line `_SHIM_DIR = None` and the functions `_delegate`, `_real_agy`, `_shim_dir`, `_reason` and `_alive`.
Keep `parse_usage`: it reads `delegate.err` of attempts made before this change.

Replace `recover` with:

```python
def recover(job):
    """Reconcile the newest attempt after a crash; raises Running when its child is still alive."""
    atts = job.launched()
    if not atts:
        return
    last = atts[-1]
    if not os.path.exists(os.path.join(last, "launch.json")):
        os.rename(last, last + ".unlaunched")
        return
    if os.path.exists(os.path.join(last, "result.json")):
        return
    launch = st.read_json(os.path.join(last, "launch.json"), {}) or {}
    if launch.get("pgid") and agy.alive(launch["pgid"]):
        raise Running("job %s is running (pid %s)" % (job.id, launch.get("pid")))
    got = _saved_conversation(last)
    st.write_atomic(os.path.join(last, "result.json"), {"exit": None, "outcome": "interrupted", "conversation": got, "ended": _now()})
    if got and not job.status().get("conversation"):
        job.set_status(conversation=got)
```

Replace `_agy_output` with these four functions (keep `_agy_runs` and `_agy_reply`, which read the old `agy-N.out` files):

```python
def _envelope(att):
    try:
        with open(os.path.join(att, "agy.out"), "rb") as fh:
            return classify.envelope(fh.read())
    except OSError:
        return None


def _saved_conversation(att):
    """The conversation id an attempt saw; delegate.err holds it for attempts made before agy-call launched agy itself."""
    return (_envelope(att) or {}).get("conversation_id") or \
        parse_usage(_read(os.path.join(att, "delegate.err"))).get("conversation_id") or None


def _saved_reply(att):
    """The reply text an attempt received, in the new layout (reply.txt) or the old one (delegate.out, agy-N.out)."""
    return _read(os.path.join(att, "reply.txt")) or _read(os.path.join(att, "delegate.out")) or _agy_reply(att) or ""


def _agy_output(att):
    """True when an agy run of this attempt produced output that may belong to a conversation we did not capture."""
    if _read(os.path.join(att, "agy.out")).strip():
        return _envelope(att) is None  # a parsed envelope already told us its conversation id
    return any(out.strip() for out in _agy_runs(att))
```

Replace `_launch` with:

```python
def _launch(att, message, conv, o, kind, generation, fds):
    cmd = agy.argv(agy.path(), message, model=o.model, timeout=o.timeout, mode=o.mode, dirs=o.dirs, conversation=conv)
    launch = {"pid": None, "pgid": None, "kind": kind, "message": message, "conversation": conv,
              "generation": generation, "started": _now(), "argv": agy.redact(cmd)}
    st.write_atomic(os.path.join(att, "launch.json"), launch)

    def started(pid, when):
        launch.update(pid=pid, pgid=pid, pstart=when)
        st.write_atomic(os.path.join(att, "launch.json"), launch)

    r = agy.run(att, cmd, cwd=o.cwd, deadline=agy.seconds(o.timeout) + agy.GRACE, fds=fds, on_start=started)
    return classify.classify(r.exit, r.out, r.err, r.stopped)
```

In `call`, after the `raise ValueError("model is required ...")` line and before `checker, cident = ...`, insert:

```python
    size = len(prompt.encode("utf-8"))
    if size > agy.MAX_PROMPT:
        raise ValueError("the prompt is %d bytes; agy takes it as one argument, so the limit is 512 KiB: "
                         "put the material in a file under the working directory and refer to it" % size)
    agy.seconds(timeout)  # a bad timeout fails here, before any attempt folder exists
```

and after the `checker, cident = ...` line insert:

```python
    checked = any(cident[k] for k in ("json", "require", "schema", "cmd", "check_id"))
```

In the check-error rerun block, replace
`verdict = checker(_read(os.path.join(done[-1], "delegate.out")) or _agy_reply(done[-1]) or "")`
with:

```python
            verdict = checker(_saved_reply(done[-1]))
```

In the loop, replace everything from `rc = _launch(att, message, conv, o, kind, generation, fds)` to the end of the function with:

```python
                oc = _launch(att, message, conv, o, kind, generation, fds)
            except Fatal as e:
                st.write_atomic(os.path.join(att, "result.json"), {"exit": None, "outcome": "fatal", "findings": [e.reason], "ended": _now()})
                job.set_status(state="fatal", findings=[e.reason])
                raise
            if oc.response:
                st.write_atomic(os.path.join(att, "reply.txt"), oc.response)
            got = oc.conversation
            res = {"exit": oc.exit, "agyStatus": oc.status, "classified": oc.name, "conversation": got,
                   "usage": oc.usage, "ended": _now()}
            if got and not conv:
                conv = got
                fds.append(stack.enter_context(st.flock(conv_lock(conv))).fileno())
                job.set_status(conversation=conv)
            elif got and got != conv:
                res["conversationConflict"] = got

            def finish(state=None, **extra):
                res.update(extra)
                st.write_atomic(os.path.join(att, "result.json"), res)
                if state:
                    job.set_status(state=state, findings=res.get("findings", []))

            def give_up(findings):
                finish("failed", outcome="failed", findings=findings)
                raise GaveUp(findings, conv, _usage(job), job.dir)

            name = oc.name
            if name == "reply" or (name in ("reply-with-error", "output-limit") and oc.response.strip()):
                if name != "reply":
                    res["agyError"] = oc.reason
                    if not checked:
                        give_up(["%s; the reply is saved in %s and is accepted after an agy error only by a check you ask for"
                                 % (oc.reason, os.path.join(att, "reply.txt"))])
                try:
                    verdict = checker(oc.response)
                except CheckError as e:
                    finish("check-error", outcome="check-error", findings=[str(e)])
                    raise
                if verdict.ok:
                    st.write_atomic(job.path("answer.txt"), verdict.answer)  # the answer first: its presence means done
                    finish(outcome="accepted")
                    job.set_status(state="done", answer=True, conversation=conv, findings=[])
                    if generation is not None:
                        quota.clear(key, generation)
                    return _result(job, cident["json"])
                if name != "reply":  # later turns in such a conversation failed the same way: send nothing more
                    give_up(verdict.findings + ["no follow-up was sent because agy reported an error: %s" % oc.reason])
                finish(outcome="rejected", findings=verdict.findings, lostContext=verdict.lost_context)
            elif name == "output-limit":
                give_up(["agy stopped at its output token limit without a reply; its output is saved in %s. "
                         "Ask for the work as files on disk and a short reply" % att])
            elif name == "timeout":
                finish(outcome="timeout", partial=bool(oc.response.strip()))
            elif name == "denied":
                if any(l.get("kind") == "denied" for l in launches):
                    give_up(["a tool call was denied twice"])
                finish(outcome="denied")
            elif name == "empty":
                if kind in ("retry", "retry-first"):
                    finish(outcome="rejected", findings=[oc.reason])
                else:
                    finish(outcome="retry", findings=[oc.reason])
                    time.sleep(RETRY_WAIT)
            elif name == "quota":
                streams = [_read(os.path.join(att, "agy.err")), _read(os.path.join(att, "agy.out"))]
                until = quota.hold(key, oc.reason, quota.parse_reset(streams) or quota.DEFAULT_HOLD)
                finish("quota", outcome="quota", until=until.isoformat())
                raise Quota(key, until)
            else:  # auth, model, malformed
                reason = oc.reason
                if name == "malformed":
                    reason += "; the raw output is in %s" % os.path.join(att, "agy.out")
                finish("fatal", outcome="fatal", findings=[reason])
                raise Fatal({"auth": 11, "model": 14}.get(name, 22), reason)
```

The `try:` line above `oc = _launch(...)` stays as it is.

- [ ] **Step 7: Make a `ValueError` exit 2 in `agycall/cli.py`**

In `main`, replace

```python
        try:
            res = call(a.job, prompt, conversation=a.conversation, new_version=a.new_version, **_opts(a))
        except AgyCallError as e:
            print("agy-call: %s" % e, file=sys.stderr)
            return e.code
```

with:

```python
        try:
            res = call(a.job, prompt, conversation=a.conversation, new_version=a.new_version, **_opts(a))
        except AgyCallError as e:
            print("agy-call: %s" % e, file=sys.stderr)
            return e.code
        except ValueError as e:  # a bad argument, job id, timeout or prompt size
            print("agy-call: %s" % e, file=sys.stderr)
            return 2
```

and replace `res = batch(jobs, workers=a.workers, **_opts(a))` with:

```python
    try:
        res = batch(jobs, workers=a.workers, **_opts(a))
    except ValueError as e:  # duplicate job ids
        print("agy-call: %s" % e, file=sys.stderr)
        return 2
```

- [ ] **Step 8: Run the full suite**

Run: `uvx pytest -q tests/`
Expected: all pass (about 140 tests).
Run it three times; `test_locks_outlive_a_killed_caller`, `test_older_generation_success_does_not_clear_hold` and the Task 2 deadline tests use real processes and timing.
Then run `grep -rn "shim\|AGYCALL_REAL_AGY\|AGY_DELEGATE\|_reason(\|_alive(" agycall tests`; expected: no output.

- [ ] **Step 9: Commit**

```bash
git add -A agycall tests
printf '%s\n' "Launch agy directly and dispatch on classified outcomes" "" \
  "The capture shim and the Antigravity wrapper are no longer used. A reply" \
  "recovered after an agy error is accepted only by a check the caller asked" \
  "for, and no follow-up is sent in that conversation. A bad argument exits 2." "" \
  "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" "Claude-Session: https://claude.ai/code/session_013coVpN7if3z6kaMYLTrz3c" > "$TMPDIR/agycall-msg.txt"
git commit -F "$TMPDIR/agycall-msg.txt"
```

---

### Task 4: Docs and demo

**Files:**
- Modify: `skills/agy-call/SKILL.md`, `README.md`, `docs/demo/demo.sh`, `docs/demo.gif`

**Interfaces:**
- Consumes: Task 3's attempt layout and `AGYCALL_AGY`.
- Produces: user-facing docs that match the code.

This task is mechanical: it has no test of its own, and it gets no task reviewer; the final review covers it.

- [ ] **Step 1: Update `skills/agy-call/SKILL.md`**

Make these exact replacements (each line is one sentence; keep one sentence per line):

| Old | New |
|---|---|
| `` - `--timeout T`: wall-clock limit per turn, passed to agy-delegate [`10m`]. `` | `` - `--timeout T`: limit per turn, passed to agy as `--print-timeout`; agy-call stops agy 60 seconds after it; a bare number means seconds [`10m`]. `` |
| `` - `--dir DIR`: extra directory the agent may read; repeat the option for several [none]. `` | `` - `--dir DIR`: extra directory the agent may read, passed as `--add-dir`; repeat the option for several [none]. `` |
| `` - `--mode MODE`: agy-delegate mode [agy-delegate's default]. `` | `` - `--mode MODE`: agy's execution mode, `accept-edits`, `auto` or `plan` [agy's default]. `` |
| `` | 11 | agy is not authenticated (agy-delegate code passed through) | `` | `` | 11 | agy is not authenticated | `` |
| `` | 13 | agy is missing (agy-delegate code passed through) | `` | `` | 13 | agy is missing: set `AGYCALL_AGY` or put agy on PATH | `` |
| `` | 14 | model unavailable (agy-delegate code passed through) | `` | `` | 14 | model unavailable | `` |
| `` | 22 | unknown agy-delegate exit | `` | `` | 22 | agy's output was not a JSON envelope; the raw output is kept in `agy.out` | `` |

Replace the whole "## Where replies are" section body with:

```markdown
Under `<store>/<job>/attempts/NNN/` each launch keeps `agy.out` (agy's JSON envelope, byte for byte), `agy.err` (agy's stderr), `agy.args` (the arguments, with the prompt replaced by its size and hash), `reply.txt` (the reply text, when there was one), `launch.json` (the prompt, process ids and start time) and `result.json` (agy's exit status, the envelope status, the classified outcome and what agy-call did).
Attempts made before agy-call launched agy itself keep their older files (`delegate.out`, `delegate.err`, `agy-N.out`), and agy-call still reads them.
The accepted answer is `<store>/<job>/answer.txt`; `request.json` and `status.json` sit beside it.
Nothing is ever deleted: a replaced job moves under `.versions`, and failed attempts stay in place.
When a call fails, read `reply.txt` and `agy.err` of the last attempt to see what agy actually said.
```

In the "What to do per exit" list, in the bullet starting `- 21:`, replace `the saved reply is in \`attempts/NNN/delegate.out\` and the raw output in \`agy-N.out\`` with `the saved reply is in \`attempts/NNN/reply.txt\` and the raw output in \`agy.out\``.
In the "Coding tasks" section, replace `(it is in agy's JSON in \`agy-N.out\`)` with `(it is in \`reply.txt\`)`, and after that sentence add the sentence: `A reply that comes back with an agy error is accepted only by a check you asked for (\`--expect\`, \`--require\`, \`--schema\` or \`--check-cmd\`), and no follow-up is sent in that conversation.`
Add to the end of the "CLI" section's first paragraph the sentence: `A bad argument, job id, timeout or a prompt over 512 KiB exits 2 before anything is launched.`

- [ ] **Step 2: Update `README.md`**

Replace `` `docs/demo/` regenerates the demo GIF with the test suite's fake delegate, so it spends no Gemini quota. `` with `` `docs/demo/` regenerates the demo GIF with the test suite's stand-in for agy, so it spends no Gemini quota. ``
Replace the "Requirements:" sentence with: `Requirements: Python 3.9 or newer (standard library only), macOS or Linux, and the \`agy\` CLI 1.5.6 or newer, installed and signed in; agy-call finds it on the PATH or through \`AGYCALL_AGY\`.`

- [ ] **Step 3: Update `docs/demo/demo.sh`**

Replace lines 2 to 8 (the comment through the `export` line) with:

```bash
# Real agy-call runs against the test suite's stand-in for agy, so the demo spends no Gemini quota.
set -u
R=$(cd "$(dirname "$0")/../.." && pwd)
W=$(mktemp -d)
cd "$W"
cp "$R/tests/fake_agy.py" "$W/agy" && chmod +x "$W/agy"
export AGYCALL_HOME="$W/home" AGYCALL_AGY="$W/agy" FAKE_STATE="$W/state" FAKE_LOG="$W/log.jsonl"
```

Replace the scene 1 scenario (the two lines between `cat > "$W/s1.json" <<'EOF'` and `EOF`) with:

```json
[{"response": "Sure! The capital of France is Paris.\n", "conversation": "conv-7f3a"},
 {"response": "```json\n{\"country\": \"France\", \"capital\": \"Paris\"}\n```", "conversation": "conv-7f3a"}]
```

Replace `show 'cat jobs/capital-fr/attempts/001/delegate.out'` with `show 'cat jobs/capital-fr/attempts/001/reply.txt'`.
Replace the scene 2 scenario line with:

```json
[{"exit": 1, "status": "ERROR", "response": "", "error": "RESOURCE_EXHAUSTED: quota exceeded for this model. Resets in 1h12m27s"}]
```

- [ ] **Step 4: Run the demo and regenerate the GIF**

```bash
bash docs/demo/demo.sh > "$TMPDIR/agycall-demo.txt"; cat "$TMPDIR/agycall-demo.txt"
```

Expected: scene 1 ends with the JSON answer `{"country": "France", "capital": "Paris"}`, the `ls` shows `001` and `002`, `cat .../001/reply.txt` shows the prose reply, and the `launch.json` lines show `"kind": "fix"` and `"conversation": "conv-7f3a"`; scene 2 prints `Quota 5` and `1` launch.

```bash
uv run --no-project --with pillow python docs/demo/render.py "$TMPDIR/agycall-demo.txt" docs/demo.gif
```

Expected: `docs/demo.gif` rewritten; open it and check every frame is readable and no line is cut off.

- [ ] **Step 5: Check and commit**

Run: `grep -rn "delegate\|shim" README.md skills docs/demo`; expected: only the guard hook's sentences in `SKILL.md` that name the blocked commands.
Run: `grep -c "$(printf '\342\200\224')" README.md skills/agy-call/SKILL.md`; expected: `0` for both.

```bash
git add README.md skills/agy-call/SKILL.md docs/demo/demo.sh docs/demo.gif
printf '%s\n' "Document the new attempt files and requirements; refresh the demo" "" "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" "Claude-Session: https://claude.ai/code/session_013coVpN7if3z6kaMYLTrz3c" > "$TMPDIR/agycall-msg.txt"
git commit -F "$TMPDIR/agycall-msg.txt"
```

---

## After this plan

- Build step 2 (background worker, `status`, `result`, `cancel`, crash recovery from a saved envelope, the conversation-conflict stop of spec 7.2) gets its own plan once this one is merged.
- Nothing in this plan pushes; pushing the branch is the user's call.
