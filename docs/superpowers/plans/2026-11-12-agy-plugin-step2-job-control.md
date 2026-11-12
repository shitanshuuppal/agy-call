# agy plugin, build step 2: job control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `run` jobs can be cancelled, recovered from their saved output after a crash, inspected with `status` and `result`, and started in the background, so steps 3 and 4 (tasks and reviews) reuse one job-control layer.

**Architecture:** `agy.run` and `checks.run_cmd` both run their process in its own group with the job lock descriptors inherited, record it in an ownership file, and poll a `should_stop` callable that stops the group on a cancel request.
`runner.call` is split into a request phase (`_prepare`, `_admit`), a driver (`_drive`) and one per-outcome function (`_settle`) that both a live attempt and an attempt recovered from a saved `agy.out` go through.
A new `agycall/control.py` holds `status`, `result`, `cancel`, the background start and the worker; `cli.py` exposes them as subcommands.

**Tech Stack:** Python 3.9+ standard library only; tests with pytest run as `uvx pytest -q tests/`.

**Spec:** `docs/superpowers/specs/2026-11-12-agy-plugin-design.md`, sections 5.2 (the `cancelled` row), 5.4, 7.1 to 7.4 and 13 (exit 27).
This is build step 2 of section 12.
The controller's decisions in `docs/superpowers/plans/notes-step2.md` bind this step and extend the spec where they differ (`run --background`, the worker's command line, `status`/`result`/`cancel` on `--store`/`--namespace`).
Task and review jobs do not exist yet; everything here works on `run` jobs.

## Global Constraints

- Python 3.9+ standard library only in agycall/.
- macOS and Linux only.
- Never the em dash character (U+2014) or the word "honest" in any file.
- One sentence per line in long Markdown.
- Public repo, so no personal paths or private data.
- Tests never run real agy (every runner/worker/CLI test uses the `fake` fixture, and subprocesses inherit AGYCALL_AGY).
- A PreToolUse hook refuses Bash commands whose text contains "agy-delegate", "agy-job", or the word agy followed by -p/--print/-i, so implementers write files with the Write/Edit tools and commit with `git commit -F "$TMPDIR/agycall-msg.txt"`.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_013coVpN7if3z6kaMYLTrz3c`.
- Linux caps one argument at 131071 bytes (`agy.MAX_PROMPT` handles the prompt); add no new argv payload: the background worker gets only `--store` and `--job`, and reads everything else from `background.json`.
- No script, test or worker relies on the installed `agy-call` on the PATH: tests run `bin/agy-call` with `sys.executable`, and the worker runs `sys.executable -m agycall.cli` with this checkout first on `PYTHONPATH`.
- Every test that uses real processes waits with a bounded loop (`wait_for`, 30 seconds at most) and the suite is run three times at the end of each task that adds one.
- `run` and `batch` keep their options, the Python `call()` and `batch()` signatures and their behaviour, except: a cancel request stops them with exit 27; a reply recovered from a crashed attempt is `reply-with-error` (spec 7.4, accepted only by a passing check); a reply in another conversation stops the job with exit 20; the check command runs in the job's `cwd` (spec 4.5).
- The two step 1 guards stay: no follow-up after a `reply-with-error` or `output-limit` reply that fails its check, both in the per-outcome code and on the check-error recheck; and a saved `failed` result of those kinds raises `GaveUp` on rerun without a launch.

## Review Focus

- A controller killed before agy wrote a complete envelope (empty or cut-off `agy.out`): the attempt is `interrupted` and the turn restarts; it must not be classified `malformed` and stop the job as a setup error (Task 2, `test_truncated_output_is_interrupted_and_the_turn_restarts`).
- A check command that never reads its stdin while the reply is larger than a pipe buffer: the check finishes at once instead of hanging until its timeout (Task 1, `test_check_cmd_that_ignores_a_large_reply_does_not_hang`).
- A rerun after a conversation-conflict stop, even with a higher `--max-turns`: nothing is sent in either conversation (Task 2, `test_reply_in_another_conversation_stops_the_job`).
- A cancel request that lands after the check passed but before the answer is saved: no `answer.txt`, state `cancelled`, the reply kept in `reply.txt` (Task 3, `test_cancel_after_the_check_passes_saves_no_answer`).
- `run --background` from a shell whose PATH has no `agy-call`, with a relative `--store` and `--schema`: the worker runs this checkout, finds the same files, and a foreground rerun of the same request returns the saved answer without a launch (Task 5, `test_background_run_finishes_in_a_detached_worker`).

## Spec clarifications made by this plan

- `call()` records `controller: {pid, pstart}` in `status.json` for every run (foreground, batch thread or background worker) once it holds the job lock, and clears it when it returns.
  The notes say the worker records it; recording it in `call()` covers the worker and also tells `cancel` that a foreground run is alive, so `cancel` never signals agy while a live controller will stop it itself.
- A cancel request stays in place for `run` jobs: a rerun of a cancelled job exits 27 without a launch, and the work is started again under a new job id.
  Spec 4.7 renames the request when a task segment starts; that belongs to step 3.
  Consuming the request on rerun would race a worker that has not started yet and run a job the user just cancelled.
- Recovery (spec 7.4) applies the passed-deadline rule only to an attempt that has `agy.out` but no complete envelope; the record then says `timeout` instead of `interrupted`, and for `run` and `batch` both lead to the same restart turn.
  An attempt of the old layout (no `agy.out`) is marked `interrupted` as before, so its conversation id is still read from `delegate.err`.
- After a conversation-conflict stop, a rerun raises `GaveUp` without a launch, the same way as the step 1 guard for `reply-with-error`; spec 7.2 says the job stops, and this keeps it stopped.
- `run` jobs keep the state name `fatal` for setup errors (spec 5.4's `setup-error` is for task and review jobs); `status.json` gains `code` for that state so `result` exits 11, 13, 14 or 22.
- `status` lists one store; `--all` across repositories needs the repository stores of step 3.
- `result` and `cancel` take an optional job id as spec 7.3 describes: without one, `result` uses the newest finished job of the current session and `cancel` the only running one, refusing and listing them when several run.
- `result` exits 1 for a state that has no code (`interrupted`, or no status yet).

## File map

| File | Change | Responsibility |
|---|---|---|
| `agycall/errors.py` | modify | `Cancelled` (exit 27) |
| `agycall/__init__.py` | modify | export `Cancelled` |
| `agycall/agy.py` | modify | `should_stop` in `run`, `stop_group`, `owns` |
| `agycall/checks.py` | modify | the check command in its own process group, with `cwd`, inherited descriptors, a start callback and cancel polling |
| `agycall/runner.py` | modify | request phase, driver, per-outcome function, crash recovery from the saved envelope, conversation-conflict stop, cancel points, controller record, session, `enqueue` |
| `agycall/control.py` | create | `status`, `result`, `cancel`, `start` (background) and `work` (the worker) |
| `agycall/cli.py` | modify | `status`, `result`, `cancel`, `worker` subcommands, `run --background`, `python -m agycall.cli` |
| `tests/agyturns.py` | modify | `wait_for` |
| `tests/conftest.py` | modify | clear `AGYCALL_SESSION` in every test |
| `tests/test_agy.py`, `tests/test_checks.py`, `tests/test_runner.py` | modify | new tests; two existing runner tests updated |
| `tests/test_control.py` | create | status, result and cancel |
| `tests/test_background.py` | create | background start and worker |
| `skills/agy-call/SKILL.md`, `README.md` | modify | the new commands, states and exit code |

---

### Task 1: Cancellable launcher and check command

**Files:**
- Modify: `agycall/errors.py`, `agycall/__init__.py`, `agycall/agy.py`, `agycall/checks.py`
- Test: `tests/test_agy.py`, `tests/test_checks.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `errors.Cancelled(AgyCallError)` with `code = 27`, exported from `agycall`.
  - `agy.run(att, cmd, *, cwd, deadline, fds=(), on_start=None, should_stop=None) -> Run`; `should_stop` is a no-argument callable polled every `agy.POLL` seconds; when it returns `"cancel"` the group is stopped and `Run.stopped == "cancel"`, `Run.exit is None`.
  - `agy.stop_group(pgid: int, reap=None) -> bool`: SIGTERM, then SIGKILL after `KILL_AFTER`; `reap` (for example `p.poll`) is called while waiting; `True` once no member of the group is left, `False` if one outlived SIGKILL by 5 seconds.
  - `agy.owns(rec: dict | None) -> bool`: `True` while `rec["pid"]` is alive with the start time `rec["pstart"]`.
  - `checks.run_cmd(cmd, text, timeout, cwd=None, fds=(), on_start=None, should_stop=None) -> list[str]`: same 0/1/other contract as before; the command runs in its own process group with `fds` inherited and reads `text` from a temporary file; `on_start(pid, pstart)` is called once it runs; `"cancel"` from `should_stop` stops the group and raises `Cancelled`.
  - The checker returned by `checks.make_check(...)` is now `check(text, **run)`, where `run` holds `run_cmd`'s `cwd`, `fds`, `on_start` and `should_stop`; `Cancelled` passes through it unwrapped.

- [ ] **Step 1: Write the failing tests**

In `tests/test_agy.py`, add `import subprocess` after `import os`, then append:

```python
def test_cancel_stops_the_whole_process_group(tmp_path, monkeypatch):
    monkeypatch.setattr(agy, "POLL", 0.1)
    exe = script(tmp_path, "subprocess.Popen(['sleep', '30']); print('started', flush=True); time.sleep(30)")
    att = attempt(tmp_path)
    out = att / "agy.out"
    pids = []
    t0 = time.monotonic()
    r = agy.run(str(att), [exe, "-p", "x"], cwd=str(tmp_path), deadline=60, on_start=lambda pid, when: pids.append(pid),
                should_stop=lambda: "cancel" if out.exists() and out.read_bytes().startswith(b"started") else None)
    assert r.stopped == "cancel" and r.exit is None and time.monotonic() - t0 < 10
    assert r.out == b"started\n" and not agy.alive(pids[0])


def test_no_stop_request_lets_agy_finish(tmp_path, monkeypatch):
    monkeypatch.setattr(agy, "POLL", 0.1)
    exe = script(tmp_path, "time.sleep(0.5); sys.stdout.write('done')")
    r = agy.run(str(attempt(tmp_path)), [exe, "-p", "x"], cwd=str(tmp_path), deadline=30, should_stop=lambda: None)
    assert r.exit == 0 and r.stopped is None and r.out == b"done"


def test_owns_needs_the_same_pid_and_start_time():
    me = {"pid": os.getpid(), "pstart": agy.start_time(os.getpid())}
    assert me["pstart"] and agy.owns(me)
    assert not agy.owns(dict(me, pstart="Thu Jan  1 00:00:00 1970"))
    assert not agy.owns({"pid": os.getpid(), "pstart": None}) and not agy.owns(None) and not agy.owns({})
    dead = subprocess.Popen(["true"])
    dead.wait()
    assert not agy.owns({"pid": dead.pid, "pstart": me["pstart"]})


def test_stop_group_stops_a_group_that_is_not_our_child():
    launcher = ("import subprocess; print(subprocess.Popen(['sleep', '30'], start_new_session=True, "
                "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).pid)")  # the sleep must not hold our pipe open
    out = subprocess.run([sys.executable, "-c", launcher], capture_output=True, text=True, timeout=10).stdout
    pgid = int(out)
    assert agy.alive(pgid)
    t0 = time.monotonic()
    assert agy.stop_group(pgid) and not agy.alive(pgid) and time.monotonic() - t0 < 10
```

In `tests/test_checks.py`, replace the import block with:

```python
import json
import time

import pytest

from agycall import agy, checks
from agycall.errors import Cancelled, CheckError
```

and append:

```python
def test_check_cmd_runs_in_cwd_with_inherited_fds_and_reports_its_start(tmp_path):
    (tmp_path / "marker").write_text("")
    seen = []
    with open(str(tmp_path / "lock"), "w") as lk:
        fd = lk.fileno()
        assert checks.run_cmd("test -f marker && test -e /dev/fd/%d" % fd, "text", 30, cwd=str(tmp_path), fds=(fd,),
                              on_start=lambda pid, when: seen.append((pid, when))) == []
        assert checks.run_cmd("test -e /dev/fd/%d" % fd, "text", 30) != []  # not inherited unless passed
    assert seen[0][0] > 0 and seen[0][1]


def test_check_cmd_that_ignores_a_large_reply_does_not_hang():
    t0 = time.monotonic()
    assert checks.run_cmd("exit 0", "x" * 2_000_000, 30) == []
    assert checks.run_cmd("grep -q end", "x" * 2_000_000 + " end", 30) == []
    assert time.monotonic() - t0 < 10


def test_check_cmd_cancel_stops_its_process_group(monkeypatch):
    monkeypatch.setattr(agy, "POLL", 0.1)
    seen = []
    t0 = time.monotonic()
    with pytest.raises(Cancelled) as e:
        checks.run_cmd("sleep 30 & sleep 30", "text", 60, on_start=lambda pid, when: seen.append(pid),
                       should_stop=lambda: "cancel" if seen else None)
    assert e.value.code == 27 and time.monotonic() - t0 < 10 and not agy.alive(seen[0])


def test_make_check_passes_run_options_and_does_not_wrap_a_cancel(tmp_path, monkeypatch):
    monkeypatch.setattr(agy, "POLL", 0.1)
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "marker").write_text("")
    check, _ = checks.make_check(check_cmd="test -f marker")
    assert check("text", cwd=str(tmp_path / "a")).ok
    assert not check("text", cwd=str(tmp_path / "b")).ok
    check, _ = checks.make_check(check_cmd="sleep 30")
    with pytest.raises(Cancelled):
        check("text", should_stop=lambda: "cancel")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uvx pytest -q tests/test_agy.py tests/test_checks.py`
Expected: the new tests fail (`ImportError: cannot import name 'Cancelled'` for `test_checks.py`, `TypeError: run() got an unexpected keyword argument 'should_stop'` and `AttributeError: ... 'owns'` / `'stop_group'` for `test_agy.py`).

- [ ] **Step 3: Add `Cancelled`**

In `agycall/errors.py`, append:

```python


class Cancelled(AgyCallError):
    code = 27
```

In `agycall/__init__.py`, replace the errors import line with:

```python
from .errors import AgyCallError, Cancelled, CheckError, Fatal, GaveUp, Mismatch, NoConversation, Quota, Running  # noqa: F401
```

- [ ] **Step 4: Replace `agycall/agy.py`**

Write the whole file:

```python
"""Launch one agy run (spec section 6): agy's stdout and stderr go straight into the attempt folder, the prompt is one
argument with stdin closed, and a deadline or a cancel request stops the whole process group, SIGTERM first and SIGKILL
after KILL_AFTER."""
import dataclasses
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time

from .errors import Fatal

MAX_PROMPT = 131071 if sys.platform.startswith("linux") else 512 * 1024  # Linux caps one argument at 32 pages including its NUL; macOS caps only the total
GRACE = 60               # seconds past agy's own --print-timeout before agy-call stops the run
KILL_AFTER = 10          # seconds between SIGTERM and SIGKILL
POLL = 1                 # seconds between checks while agy or a check command runs


@dataclasses.dataclass
class Run:
    exit: object     # agy's exit status, negative for a signal; None when agy-call stopped it
    stopped: object  # "deadline" or "cancel" when agy-call stopped it, else None
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


def owns(rec):
    """True while the process an ownership file records (its pid and start time) is still that same process."""
    return bool(rec and rec.get("pid") and rec.get("pstart")) and start_time(rec["pid"]) == rec["pstart"]


def stop_group(pgid, reap=None):
    """Stop a process group: SIGTERM, then SIGKILL after KILL_AFTER seconds. reap is called while waiting, so an exited
    child of ours does not keep its group looking alive. True once no member is left; False if one outlived SIGKILL by
    5 seconds."""
    for sig, patience in ((signal.SIGTERM, KILL_AFTER), (signal.SIGKILL, 5)):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return True
        end = time.monotonic() + patience
        while time.monotonic() < end:
            if reap:
                reap()
            if not alive(pgid):
                return True
            time.sleep(0.1)
    return False


def run(att, cmd, *, cwd, deadline, fds=(), on_start=None, should_stop=None):
    """Run agy until it exits, the deadline passes, or should_stop() returns "cancel" (polled every POLL seconds)."""
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
        try:
            on_start(p.pid, start_time(p.pid))
        except BaseException:  # without a recorded pid nothing can find this agy later
            stop_group(p.pid, p.poll)
            p.wait()
            raise
    end = time.monotonic() + deadline
    while True:
        try:
            rc, stopped = p.wait(timeout=POLL), None
            break
        except subprocess.TimeoutExpired:
            if should_stop and should_stop() == "cancel":
                stopped = "cancel"
            elif time.monotonic() >= end:
                stopped = "deadline"
            else:
                continue
            stop_group(p.pid, p.poll)
            p.wait()
            rc = None
            break
    with open(out_path, "rb") as fh:
        data = fh.read()
    with open(err_path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    return Run(rc, stopped, data, text)
```

`_stop` is gone; `stop_group(p.pid, p.poll)` replaces both of its uses.

- [ ] **Step 5: Update `agycall/checks.py`**

Replace the import block (lines 2 to 10) with:

```python
import collections
import hashlib
import json
import os
import re
import signal
import subprocess
import tempfile
import time

from . import agy
from .errors import Cancelled, CheckError
```

Replace the whole `run_cmd` function with:

```python
def run_cmd(cmd, text, timeout, cwd=None, fds=(), on_start=None, should_stop=None):
    """Run a check command in its own process group, with text on stdin and the descriptors fds inherited.
    on_start(pid, start time) is called once it runs; should_stop() is polled every agy.POLL seconds, and "cancel" stops
    the group and raises Cancelled. Exit 0 passes ([]), exit 1 rejects (its stdout is the finding), and any other exit,
    a timeout or a failure to start is a CheckError."""
    with tempfile.TemporaryFile() as inp, tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        inp.write(text.encode("utf-8"))
        inp.seek(0)  # a file, not a pipe: a command that never reads its stdin cannot block on a large reply
        try:
            p = subprocess.Popen(cmd, shell=True, stdin=inp, stdout=out, stderr=err, cwd=cwd, start_new_session=True,
                                 pass_fds=tuple(fds))
        except OSError as e:
            raise CheckError("check command could not start: %s" % e)
        if on_start:
            try:
                on_start(p.pid, agy.start_time(p.pid))
            except BaseException:  # without a recorded pid nothing can find this command later
                agy.stop_group(p.pid, p.poll)
                p.wait()
                raise
        end = time.monotonic() + timeout
        while True:
            try:
                p.wait(timeout=agy.POLL)
                break
            except subprocess.TimeoutExpired:
                if should_stop and should_stop() == "cancel":
                    agy.stop_group(p.pid, p.poll)
                    p.wait()
                    raise Cancelled("the check command was stopped by a cancel request")
                if time.monotonic() >= end:
                    os.killpg(p.pid, signal.SIGKILL)
                    p.wait()
                    raise CheckError("check command timed out after %ss: %s" % (timeout, cmd))
        out.seek(0)
        err.seek(0)
        stdout, stderr = (f.read().decode("utf-8", "replace") for f in (out, err))
    if p.returncode == 0:
        return []
    if p.returncode == 1:
        return [stdout.strip() or "the check command rejected the reply"]
    raise CheckError("check command exited %s: %s" % (p.returncode, (stderr or stdout).strip()[-500:]))
```

In `make_check`, replace the inner `check` function and the first line of `_check` (`def _check(text):`) with:

```python
    def check(text, **run):
        """Check one reply; run holds run_cmd's cwd, fds, on_start and should_stop for a check command."""
        try:
            return _check(text, run)
        except (CheckError, Cancelled):
            raise
        except Exception as e:
            raise CheckError("check crashed: %s: %s" % (type(e).__name__, e))

    def _check(text, run):
```

and inside `_check` replace `findings += run_cmd(check_cmd, answer, check_timeout)` with:

```python
            findings += run_cmd(check_cmd, answer, check_timeout, **run)
```

- [ ] **Step 6: Run the tests**

Run: `uvx pytest -q tests/test_agy.py tests/test_checks.py`
Expected: all pass.
Run: `for i in 1 2 3; do uvx pytest -q tests/ || break; done`
Expected: every run passes (152 tests); the new tests use real processes.

- [ ] **Step 7: Commit**

```bash
git add agycall/errors.py agycall/__init__.py agycall/agy.py agycall/checks.py tests/test_agy.py tests/test_checks.py
printf '%s\n' "Stop launches and check commands on a cancel request" "" \
  "The launcher and the check command each run in their own process group," \
  "report their start, and poll for a cancel request. The check command now" \
  "runs in a given directory with the lock descriptors inherited and reads" \
  "its input from a file. Cancelled exits 27." "" \
  "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" "Claude-Session: https://claude.ai/code/session_013coVpN7if3z6kaMYLTrz3c" > "$TMPDIR/agycall-msg.txt"
git commit -F "$TMPDIR/agycall-msg.txt"
```

---

### Task 2: One per-outcome function, crash recovery and the conversation-conflict stop

**Files:**
- Modify: `agycall/runner.py` (whole file), `tests/agyturns.py`
- Test: `tests/test_runner.py`

**Interfaces:**
- Consumes: Task 1's `checker(text, **run)`, `checks.run_cmd(..., cwd, fds, on_start)`.
- Produces:
  - `runner.pending(job) -> str | None`: the newest attempt folder when it has `launch.json` and no `result.json`.
  - `runner.recover(job)`: renames an unlaunched newest attempt; raises `Running` while the newest attempt's recorded agy or check process group is alive; writes nothing else.
  - `runner._prepare(job_id, prompt, *, store, namespace, model, timeout, cwd, dirs, mode, conversation, expect, require, schema, check_cmd, check_timeout, check, check_id) -> types.SimpleNamespace` with fields `job, prompt, model, key, timeout, mode, cwd, dirs, seed, checker, cident, checked, ident, conv, fds, stack`.
  - `runner._admit(c, stack, new_version)`: takes the job lock in `stack`, runs `recover`, refuses or archives a changed request, writes `request.json` for a new one.
  - `runner._drive(c, max_turns, ignore_quota, fresh) -> Result`, `runner._settle(c, att, oc, recovered=False) -> Result | None`, `runner._check(c, att, text) -> checks.Verdict`.
  - Attempt files: `attempts/NNN/check.json` `{pid, pgid, pstart, started}` while a check command runs; `result.json` gains `recovered: true` for an attempt settled from its saved output, and `conversationConflict` with outcome `failed` for a reply in another conversation.
  - `status.json` states written: `checking` (while a check command runs) and `interrupted` (an attempt the saved output did not settle).
  - `tests/agyturns.py`: `wait_for(predicate, timeout=30)` returns the first truthy value of `predicate()` or raises `AssertionError`.

- [ ] **Step 1: Add `wait_for` to `tests/agyturns.py`**

Replace the module docstring line with:

```python
"""Builders for tests/fake_agy.py turns, one per shape of agy run the classifier knows, and a bounded wait for tests
that drive real processes."""
import time
```

and append:

```python


def wait_for(predicate, timeout=30):
    """Poll predicate until it returns something truthy and return that; fail after timeout seconds."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("condition not met within %s seconds" % timeout)
```

- [ ] **Step 2: Write the failing tests**

In `tests/test_runner.py`, replace the import block (lines 1 to 11) with:

```python
import datetime as dt
import json
import os
import shlex
import subprocess
import sys
import time

import pytest

from agycall import agy, quota, runner, store
from agycall.errors import CheckError, Fatal, GaveUp, Mismatch, NoConversation, Quota, Running
from agyturns import auth_error, empty, denied, error_reply, malformed, model_error, partial, quota_error, reply, wait_for
```

Two existing tests change because their scenario now meets a spec rule.
In `test_conversation_id_stranded_in_result_json`, the reply came back in conversation `c1` while the turn resumed `c5`, which is now a conflict stop; replace `set_turns(GOOD)` in that test with:

```python
    set_turns(reply(GOOD["response"], "c5"))
```

In `test_locks_outlive_a_killed_caller`, the orphaned agy finishes and writes its envelope after the caller died, so the rerun now recovers that reply instead of restarting the turn (spec 7.4).
Replace the last four lines of that test (from `r = runner.call("j1", "q", store=S(tmp_path), model="m", conversation="cX")` to the end) with:

```python
    with pytest.raises(GaveUp) as e:  # the orphaned agy's reply is recovered, but its exit status was lost
        runner.call("j1", "q", store=S(tmp_path), model="m", conversation="cX")
    att = store.Job(S(tmp_path), "j1").launched()[0]
    res = store.read_json(os.path.join(att, "result.json"))
    assert res["classified"] == "reply-with-error" and res["recovered"] is True
    assert "only by a check" in str(e.value) and len(launches()) == 2  # nothing was resent
```

Append the new tests:

```python
def _cident(cmd=None):
    return {"json": False, "require": [], "schema": None, "cmd": cmd, "check_id": None}


def _pending(tmp_path, out, check_cmd=None, started=None, conversation=None):
    """A job whose controller died after agy exited: launch.json and agy.out, but no result.json."""
    job = store.Job(S(tmp_path), "j1")
    os.makedirs(job.dir, exist_ok=True)
    store.write_atomic(job.path("request.json"), {"identity": store.identity(
        "q", "m", os.path.abspath(os.getcwd()), [], conversation, _cident(check_cmd), None)})
    dead = subprocess.Popen(["true"], start_new_session=True)
    dead.wait()
    att = job.new_attempt()
    store.write_atomic(os.path.join(att, "launch.json"), {"pid": dead.pid, "pgid": dead.pid, "kind": "first", "message": "q",
                                                         "conversation": conversation, "generation": 0,
                                                         "started": started or runner._now()})
    with open(os.path.join(att, "agy.out"), "wb") as fh:
        fh.write(out)
    open(os.path.join(att, "agy.err"), "w").close()
    return job, att


def _env(response, conv="c7", status="SUCCESS"):
    return json.dumps({"status": status, "response": response, "conversation_id": conv}).encode("utf-8")


def test_recovered_reply_is_accepted_by_a_passing_check(tmp_path, fake):
    set_turns, launches = fake
    set_turns(GOOD)
    _, att = _pending(tmp_path, _env("the reply"), check_cmd="grep -q reply")
    r = runner.call("j1", "q", store=S(tmp_path), model="m", check_cmd="grep -q reply")
    assert r.answer == "the reply" and r.conversation == "c7" and launches() == []
    res = store.read_json(os.path.join(att, "result.json"))
    assert res["classified"] == "reply-with-error" and res["recovered"] is True and res["outcome"] == "accepted"
    assert "exit status" in res["agyError"]
    assert open(os.path.join(att, "reply.txt")).read() == "the reply"


def test_recovered_reply_failing_its_check_sends_no_follow_up(tmp_path, fake):
    set_turns, launches = fake
    set_turns(GOOD)
    _pending(tmp_path, _env("the reply"), check_cmd="exit 1")
    for _ in range(2):  # a rerun keeps the stop
        with pytest.raises(GaveUp) as e:
            runner.call("j1", "q", store=S(tmp_path), model="m", check_cmd="exit 1")
        assert "no follow-up was sent" in str(e.value)
    assert launches() == []


def test_truncated_output_is_interrupted_and_the_turn_restarts(tmp_path, fake):
    set_turns, launches = fake
    set_turns(reply("full answer", "c7"))
    job, att = _pending(tmp_path, b'{"status": "SUCC', conversation="c7")
    r = runner.call("j1", "q", store=S(tmp_path), model="m", conversation="c7")
    assert r.answer == "full answer" and store.read_json(os.path.join(att, "result.json"))["outcome"] == "interrupted"
    assert "from the beginning" in launches()[0]["prompt"]
    assert launches()[0]["argv"][launches()[0]["argv"].index("--conversation") + 1] == "c7"


def test_incomplete_output_after_the_deadline_is_a_timeout(tmp_path, fake):
    set_turns, launches = fake
    set_turns(reply("full answer", "c7"))
    old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)).isoformat()
    _, att = _pending(tmp_path, b"", started=old, conversation="c7")
    r = runner.call("j1", "q", store=S(tmp_path), model="m", conversation="c7")
    res = store.read_json(os.path.join(att, "result.json"))
    assert res["outcome"] == "timeout" and res["recovered"] is True and r.answer == "full answer"
    assert "from the beginning" in launches()[0]["prompt"]


def test_reply_in_another_conversation_stops_the_job(tmp_path, fake):
    set_turns, launches = fake
    set_turns(reply("ok", "c2"))
    with pytest.raises(GaveUp) as e:
        runner.call("j1", "q", store=S(tmp_path), model="m", conversation="c1")
    assert "agy answered in conversation c2 instead of c1" in str(e.value)
    job = store.Job(S(tmp_path), "j1")
    assert job.status()["state"] == "failed" and not os.path.exists(job.path("answer.txt"))
    assert store.read_json(os.path.join(job.launched()[0], "result.json"))["conversationConflict"] == "c2"
    with pytest.raises(GaveUp):
        runner.call("j1", "q", store=S(tmp_path), model="m", conversation="c1", max_turns=8)
    assert len(launches()) == 1


def test_check_runs_in_the_job_cwd_while_the_job_shows_checking(tmp_path, fake):
    set_turns, _ = fake
    set_turns(reply("x"))
    work = tmp_path / "work"
    work.mkdir()
    (work / "marker").write_text("")
    status = os.path.join(S(tmp_path), "j1", "status.json")
    cmd = "test -f marker && grep -q '\"state\": \"checking\"' " + shlex.quote(status)
    r = runner.call("j1", "q", store=S(tmp_path), model="m", cwd=str(work), check_cmd=cmd)
    rec = store.read_json(os.path.join(store.Job(S(tmp_path), "j1").launched()[0], "check.json"))
    assert r.answer == "x" and rec["pid"] == rec["pgid"] and rec["pstart"]


def test_check_left_running_by_a_killed_controller_blocks_then_runs_again(tmp_path, fake, monkeypatch):
    import signal
    set_turns, launches = fake
    set_turns(reply("the reply", "c1"))
    flag = tmp_path / "flag"
    monkeypatch.setenv("FLAGFILE", str(flag))
    cmd = 'test -f "$FLAGFILE" || sleep 30; grep -q reply'
    code = ("import sys; sys.path.insert(0, %r); from agycall import runner; "
            "runner.call('j1', 'q', store=%r, model='m', cwd=%r, check_cmd=%r)" % (os.path.dirname(HERE), S(tmp_path), str(tmp_path), cmd))
    p = subprocess.Popen([sys.executable, "-c", code], env=dict(os.environ))
    job = store.Job(S(tmp_path), "j1")

    def check_rec():
        atts = job.launched()
        return store.read_json(os.path.join(atts[0], "check.json")) if atts else None

    rec = wait_for(check_rec)
    os.kill(p.pid, signal.SIGKILL)
    p.wait(timeout=10)
    try:
        with pytest.raises(Running):  # the orphaned check still holds the job lock
            runner.call("j1", "q", store=S(tmp_path), model="m", cwd=str(tmp_path), check_cmd=cmd)
    finally:
        assert agy.stop_group(rec["pgid"])
    flag.write_text("")
    r = runner.call("j1", "q", store=S(tmp_path), model="m", cwd=str(tmp_path), check_cmd=cmd)
    assert r.answer == "the reply" and len(launches()) == 1
    res = store.read_json(os.path.join(job.launched()[0], "result.json"))
    assert res["recovered"] is True and res["outcome"] == "accepted"
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uvx pytest -q tests/test_runner.py`
Expected: 7 failed: `test_locks_outlive_a_killed_caller` and six of the new tests (the recovered tests see `interrupted` or a restart, the conflict test gets an answer, the two check tests find no `check.json`; the killed-controller test fails after its 30-second wait).
`test_truncated_output_is_interrupted_and_the_turn_restarts` already passes: it pins the step 1 behaviour that the new recovery must keep.

- [ ] **Step 4: Replace `agycall/runner.py`**

Write the whole file:

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

RETRY_WAIT = 30
FIX = ("Your last reply could not be used. Problems: {findings}. "
       "Reply again with the complete answer in the required format; keep the work you already did.")
RESTART = ("Your last turn was cut off before it finished. Write your complete answer again from the beginning, "
           "in full, in the required format; do not continue mid-sentence.")
DENIED = ("Your last tool call was denied by the permission policy. Do not repeat that call; "
          "continue the task with the tools you are allowed, and give the complete answer.")
LOST = "\n\nThe original request was:\n\n{prompt}"
FIRST_LIKE = ("first", "retry-first", "fresh")
AGY_ERRORS = ("reply-with-error", "output-limit")  # a reply that came with an agy error: only a check accepts it, no follow-up


@dataclasses.dataclass
class Result:
    job: str
    answer: object
    text: str
    conversation: str
    attempts: int
    usage: dict
    dir: str


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def conv_lock(conversation):
    return os.path.join(st.home(), "locks", "conv-%s.lock" % hashlib.sha256(conversation.encode()).hexdigest()[:32])


def parse_usage(err_text):
    for line in reversed(err_text.splitlines()):
        if line.startswith("AGY_USAGE "):
            try:
                return json.loads(line[len("AGY_USAGE "):])
            except ValueError:
                return {}
    return {}


def pending(job):
    """The newest attempt folder when it has a launch.json and no result.json (its controller stopped before settling
    it), else None."""
    atts = job.launched()
    if atts and os.path.exists(os.path.join(atts[-1], "launch.json")) and not os.path.exists(os.path.join(atts[-1], "result.json")):
        return atts[-1]
    return None


def recover(job):
    """First thing under the job lock: set aside an attempt that never launched, and raise Running while a recorded agy
    or check process of the newest attempt is alive. Settling that attempt waits until the request is known (_drive)."""
    atts = job.launched()
    if atts and not os.path.exists(os.path.join(atts[-1], "launch.json")):
        os.rename(atts[-1], atts[-1] + ".unlaunched")
        return
    att = pending(job)
    if not att:
        return
    for name, doing in (("launch.json", "running"), ("check.json", "checking")):
        rec = st.read_json(os.path.join(att, name), {}) or {}
        if rec.get("pgid") and agy.alive(rec["pgid"]):
            raise Running("job %s is %s (pid %s)" % (job.id, doing, rec.get("pid")))


def _usage(job):
    total = {}
    for a in job.launched():
        for k, v in ((st.read_json(os.path.join(a, "result.json"), {}) or {}).get("usage") or {}).items():
            if isinstance(v, (int, float)):
                total[k] = total.get(k, 0) + v
    return total


def _result(job, want_json):
    text = _read(job.path("answer.txt"))
    return Result(job=job.id, answer=json.loads(text) if want_json else text, text=text,
                  conversation=job.status().get("conversation"), attempts=len(job.launched()), usage=_usage(job), dir=job.dir)


def _next(prompt, last, launch):
    if last is None:
        return prompt, "first"
    outcome = last.get("outcome")
    if outcome in ("retry", "quota", "fatal"):  # agy never gave a usable turn: send the same message again
        return launch["message"], "retry-first" if launch.get("kind") in FIRST_LIKE else "retry"
    if outcome in ("timeout", "interrupted"):
        return RESTART, "restart"
    if outcome == "denied":
        return DENIED, "denied"
    msg = FIX.format(findings="; ".join(last.get("findings") or ["the reply was not usable"]))
    if last.get("lostContext"):
        msg += LOST.format(prompt=prompt)
    return msg, "fix"


def _agy_runs(att):
    """The stdout of each captured agy invocation of this attempt, in order, skipping help probes."""
    for path in sorted(glob.glob(os.path.join(att, "agy-*.out")), key=lambda p: int(p.rsplit("-", 1)[1][:-4])):
        try:
            args = json.loads(_read(path[:-4] + ".args"))
        except ValueError:
            args = []
        if not (any(a in ("--help", "-h") for a in args) and not any(a in ("-p", "--print", "--prompt") for a in args)):
            yield _read(path)


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


def _agy_reply(att):
    """The reply in agy's JSON envelope; agy-delegate drops it when agy exits non-zero after writing one."""
    for out in reversed(list(_agy_runs(att))):
        try:
            envelope = json.loads(out)
        except ValueError:
            continue
        if isinstance(envelope, dict) and isinstance(envelope.get("response"), str) and envelope["response"].strip():
            return envelope["response"]
    return None


def _recovered(job, att, timeout):
    """The outcome of an attempt whose controller stopped before settling it, from what the attempt saved (spec 7.4),
    or None when the saved output does not settle it. agy has exited: it held the job lock, which we now hold."""
    try:
        with open(os.path.join(att, "agy.out"), "rb") as fh:
            out = fh.read()
    except OSError:  # the old attempt layout, or a crash before agy started: nothing to classify
        return None
    if classify.envelope(out) is not None:
        stopped = None
    else:
        started = (st.read_json(os.path.join(att, "launch.json"), {}) or {}).get("started")
        end = dt.datetime.fromisoformat(started) + dt.timedelta(seconds=agy.seconds(timeout) + agy.GRACE) if started else None
        if not end or end > dt.datetime.now(dt.timezone.utc):
            return None
        stopped = "deadline"
    oc = classify.classify(None, out, _read(os.path.join(att, "agy.err")), stopped)
    if stopped == "deadline":
        return dataclasses.replace(oc, reason="agy-call stopped before agy finished, and the turn's deadline has passed")
    if oc.name == "reply-with-error" and oc.status == "SUCCESS":
        return dataclasses.replace(oc, reason="agy-call stopped before it recorded agy's exit status")
    return oc


def _launch(c, att, message, kind, generation):
    cmd = agy.argv(agy.path(), message, model=c.model, timeout=c.timeout, mode=c.mode, dirs=c.dirs, conversation=c.conv)
    launch = {"pid": None, "pgid": None, "kind": kind, "message": message, "conversation": c.conv,
              "generation": generation, "started": _now(), "argv": agy.redact(cmd)}
    st.write_atomic(os.path.join(att, "launch.json"), launch)

    def started(pid, when):
        launch.update(pid=pid, pgid=pid, pstart=when)
        st.write_atomic(os.path.join(att, "launch.json"), launch)

    r = agy.run(att, cmd, cwd=c.cwd, deadline=agy.seconds(c.timeout) + agy.GRACE, fds=c.fds, on_start=started)
    return classify.classify(r.exit, r.out, r.err, r.stopped)


def _hold(c, conv):
    """Make conv the job's conversation and hold its lock until the job ends, so two turns never share a conversation."""
    c.conv = conv
    c.fds.append(c.stack.enter_context(st.flock(conv_lock(conv))).fileno())
    c.job.set_status(conversation=conv)


def _check(c, att, text):
    """Run the caller's check on a reply in the job's cwd; a check command records its process in check.json and the
    job shows `checking` while it runs."""
    def started(pid, when):
        st.write_atomic(os.path.join(att, "check.json"), {"pid": pid, "pgid": pid, "pstart": when, "started": _now()})
        c.job.set_status(state="checking")

    return c.checker(text, cwd=c.cwd, fds=c.fds, on_start=started)


def _settle(c, att, oc, recovered=False):
    """Act on one attempt's outcome, whether its run just ended or a rerun recovered it from the saved output: return a
    Result when the job is done, None when the loop should send the next turn, and raise for every stop."""
    job = c.job
    launch = st.read_json(os.path.join(att, "launch.json"), {}) or {}
    if oc.response:
        st.write_atomic(os.path.join(att, "reply.txt"), oc.response)
    got = oc.conversation
    res = {"exit": oc.exit, "agyStatus": oc.status, "classified": oc.name, "conversation": got,
           "usage": oc.usage, "ended": _now()}
    if recovered:
        res["recovered"] = True

    def finish(state=None, **extra):
        res.update(extra)
        st.write_atomic(os.path.join(att, "result.json"), res)
        if state:
            job.set_status(state=state, findings=res.get("findings", []))

    def give_up(findings):
        finish("failed", outcome="failed", findings=findings)
        raise GaveUp(findings, c.conv, _usage(job), job.dir)

    resumed = launch.get("conversation")
    if got and resumed and got != resumed:  # spec 7.2: a turn answered in another conversation stops the job
        res["conversationConflict"] = got
        give_up(["agy answered in conversation %s instead of %s" % (got, resumed)])
    if got and not c.conv:
        _hold(c, got)
    name = oc.name
    if name == "reply" or (name in AGY_ERRORS and oc.response.strip()):
        if name != "reply":
            res["agyError"] = oc.reason
            if not c.checked:
                give_up(["%s; the reply is saved in %s and is accepted after an agy error only by a check you ask for"
                         % (oc.reason, os.path.join(att, "reply.txt"))])
        try:
            verdict = _check(c, att, oc.response)
        except CheckError as e:
            finish("check-error", outcome="check-error", findings=[str(e)])
            raise
        if verdict.ok:
            st.write_atomic(job.path("answer.txt"), verdict.answer)  # the answer first: its presence means done
            finish(outcome="accepted")
            job.set_status(state="done", answer=True, conversation=c.conv, findings=[])
            if launch.get("generation") is not None:
                quota.clear(c.key, launch["generation"])
            return _result(job, c.cident["json"])
        if name != "reply":  # later turns in such a conversation failed the same way: send nothing more
            give_up(verdict.findings + ["no follow-up was sent because agy reported an error: %s" % oc.reason])
        finish(outcome="rejected", findings=verdict.findings, lostContext=verdict.lost_context)
    elif name == "output-limit":
        give_up(["agy stopped at its output token limit without a reply; its output is saved in %s. "
                 "Ask for the work as files on disk and a short reply" % att])
    elif name == "timeout":
        finish(outcome="timeout", partial=bool(oc.response.strip()))
    elif name == "denied":
        earlier = [st.read_json(os.path.join(a, "launch.json"), {}) or {} for a in job.launched() if a != att]
        if any(l.get("kind") == "denied" for l in earlier):
            give_up(["a tool call was denied twice"])
        finish(outcome="denied")
    elif name == "empty":
        if launch.get("kind") in ("retry", "retry-first"):
            finish(outcome="rejected", findings=[oc.reason])
        else:
            finish(outcome="retry", findings=[oc.reason])
            time.sleep(RETRY_WAIT)
    elif name == "quota":
        streams = [_read(os.path.join(att, "agy.err")), _read(os.path.join(att, "agy.out"))]
        until = quota.hold(c.key, oc.reason, quota.parse_reset(streams) or quota.DEFAULT_HOLD)
        finish("quota", outcome="quota", until=until.isoformat())
        raise Quota(c.key, until)
    else:  # auth, model, malformed
        reason = oc.reason
        if name == "malformed":
            reason += "; the raw output is in %s" % os.path.join(att, "agy.out")
        finish("fatal", outcome="fatal", findings=[reason])
        raise Fatal({"auth": 11, "model": 14}.get(name, 22), reason)
    return None


def _prepare(job_id, prompt, *, store=None, namespace=None, model=None, timeout="10m", cwd=None, dirs=(), mode=None,
             conversation=None, expect=None, require=None, schema=None, check_cmd=None, check_timeout=120, check=None,
             check_id=None):
    """Check a request before anything is written, and return the job's working state."""
    if not model:
        raise ValueError("model is required: pass the exact agy model name, e.g. gemini-4-argon-high")
    size = len(prompt.encode("utf-8"))
    job = st.Job(st.store_dir(store, namespace), job_id)
    if size > agy.MAX_PROMPT and not os.path.exists(job.path("answer.txt")):  # a saved answer is still returned
        raise ValueError("the prompt is %d bytes; agy takes it as one argument, and this system's limit is %d bytes: "
                         "put the material in a file under the working directory and refer to it" % (size, agy.MAX_PROMPT))
    agy.seconds(timeout)  # a bad timeout fails here, before any attempt folder exists
    checker, cident = checks.make_check(expect, require, schema, check_cmd, check_timeout, check, check_id)
    cwd = os.path.abspath(cwd or os.getcwd())
    dirs = [os.path.abspath(d) for d in dirs]
    c = types.SimpleNamespace(
        job=job, prompt=prompt, model=model, key=model, timeout=timeout, mode=mode, cwd=cwd, dirs=dirs,
        seed=conversation, checker=checker, cident=cident,
        checked=any(cident[k] for k in ("json", "require", "schema", "cmd")) or check is not None,
        ident=st.identity(prompt, model, cwd, dirs, conversation, cident, mode), conv=None, fds=[], stack=None)
    return c


def _admit(c, stack, new_version):
    """Take the job lock (held until stack closes), refuse or archive a changed request, and write request.json for a
    new one."""
    job = c.job
    c.stack = stack
    try:
        c.fds.append(stack.enter_context(st.flock(job.lock_path, blocking=False)).fileno())
    except st.Busy:
        raise Running("job %s is running in another process" % job.id)
    recover(job)  # before any archive: a live child must never lose its folder
    req = st.read_json(job.path("request.json"))
    if req and req.get("identity") != c.ident:
        if not new_version:
            raise Mismatch("job %s exists with a different request; pass new_version to replace it (%s)" % (job.id, job.dir))
        job.archive()
        req = None
    if not req:
        os.makedirs(job.dir, exist_ok=True)
        st.write_atomic(job.path("request.json"), {"identity": c.ident, "prompt": c.prompt, "key": c.key, "cwd": c.cwd,
                                                   "dirs": c.dirs, "seed": c.seed, "check": c.cident, "mode": c.mode,
                                                   "created": _now()})


def _drive(c, max_turns, ignore_quota, fresh):
    """Everything call() does once it holds the job lock and request.json matches the request."""
    job, prompt = c.job, c.prompt
    if os.path.exists(job.path("answer.txt")):
        if job.status().get("state") != "done":
            job.set_status(state="done", answer=True)
        return _result(job, c.cident["json"])
    att = pending(job)
    oc = _recovered(job, att, c.timeout) if att else None
    if att and oc is None:  # the saved output does not settle the attempt; run and batch restart the turn
        got = _saved_conversation(att)
        st.write_atomic(os.path.join(att, "result.json"), {"exit": None, "outcome": "interrupted", "conversation": got, "ended": _now()})
        job.set_status(state="interrupted")
        if got and not job.status().get("conversation"):
            job.set_status(conversation=got)
    conv = job.status().get("conversation") or c.seed
    for a in reversed(job.launched()):  # an id can sit in a result or launch file when the status write was lost
        if conv:
            break
        conv = (st.read_json(os.path.join(a, "result.json"), {}) or {}).get("conversation") or \
            (st.read_json(os.path.join(a, "launch.json"), {}) or {}).get("conversation")
    if conv:
        _hold(c, conv)
    if oc is not None:  # settled exactly like a live attempt, without a launch
        r = _settle(c, att, oc, recovered=True)
        if r:
            return r
    done = job.launched()
    last_res = (st.read_json(os.path.join(done[-1], "result.json"), {}) or {}) if done else {}
    if last_res.get("outcome") == "check-error":  # the reply is saved: check it again before any new turn
        try:
            verdict = _check(c, done[-1], _saved_reply(done[-1]))
        except CheckError as e:
            job.set_status(state="check-error", findings=[str(e)])
            raise
        if verdict.ok:
            st.write_atomic(job.path("answer.txt"), verdict.answer)
            job.set_status(state="done", answer=True, conversation=c.conv, findings=[])
            return _result(job, c.cident["json"])
        if last_res.get("classified") in AGY_ERRORS:  # same guard as in _settle: no follow-up after an agy error
            findings = verdict.findings + ["no follow-up was sent because agy reported an error: %s" % last_res.get("agyError")]
            last_res.update(outcome="failed", findings=findings)
            st.write_atomic(os.path.join(done[-1], "result.json"), last_res)
            job.set_status(state="failed", findings=findings)
            raise GaveUp(findings, c.conv, _usage(job), job.dir)
        last_res.update(outcome="rejected", findings=verdict.findings, lostContext=verdict.lost_context)
        st.write_atomic(os.path.join(done[-1], "result.json"), last_res)
    elif last_res.get("outcome") == "failed" and (last_res.get("classified") in AGY_ERRORS or last_res.get("conversationConflict")):
        findings = last_res.get("findings") or ["agy reported an error"]  # a rerun never sends a follow-up after these stops
        job.set_status(state="failed", findings=findings)
        raise GaveUp(findings, c.conv, _usage(job), job.dir)
    while True:
        atts = job.launched()
        launches = [st.read_json(os.path.join(a, "launch.json"), {}) or {} for a in atts]
        results = [st.read_json(os.path.join(a, "result.json"), {}) or {} for a in atts]
        message, kind = _next(prompt, results[-1] if results else None, launches[-1] if launches else None)
        if kind == "retry-first" and not c.conv and results[-1].get("outcome") == "retry" and _agy_output(atts[-1]):
            kind = "retry-needs-conversation"  # agy may have opened a conversation we never saw
        if (kind not in FIRST_LIKE) and not c.conv:
            if not fresh:
                job.set_status(state="no-conversation")
                raise NoConversation("job %s needs a follow-up but no conversation id was captured; replies are in %s; "
                                     "pass fresh to start a new conversation" % (job.id, job.path("attempts")))
            message, kind = (message if kind == "retry-needs-conversation" else prompt + "\n\n" + message), "fresh"
        turns = sum(1 for l in launches if l.get("kind") not in ("retry", "retry-first"))
        if kind not in ("retry", "retry-first") and turns >= max_turns:
            findings = (results[-1].get("findings") if results else None) or ["turn limit reached"]
            job.set_status(state="failed", findings=findings)
            raise GaveUp(findings, c.conv, _usage(job), job.dir)
        size = len(message.encode("utf-8"))
        if size > agy.MAX_PROMPT:
            findings = ["the next message would be %d bytes, over this system's single-argument limit of %d bytes; "
                        "rerun with --new-version and a shorter prompt" % (size, agy.MAX_PROMPT)]
            job.set_status(state="failed", findings=findings)
            raise GaveUp(findings, c.conv, _usage(job), job.dir)
        try:
            generation = quota.generation(c.key) if ignore_quota else quota.admit(c.key)
        except Quota:
            job.set_status(state="quota")
            raise
        att = job.new_attempt()
        job.set_status(state="running", attempts=len(atts) + 1, conversation=c.conv)
        try:
            oc = _launch(c, att, message, kind, generation)
        except Fatal as e:
            st.write_atomic(os.path.join(att, "result.json"), {"exit": None, "outcome": "fatal", "findings": [e.reason], "ended": _now()})
            job.set_status(state="fatal", findings=[e.reason])
            raise
        r = _settle(c, att, oc)
        if r:
            return r


def call(job_id, prompt, *, store=None, namespace=None, model=None, timeout="10m", cwd=None, dirs=(),
         mode=None, conversation=None, expect=None, require=None, schema=None, check_cmd=None, check_timeout=120,
         check=None, check_id=None, max_turns=4, ignore_quota=False, fresh=False, new_version=False):
    c = _prepare(job_id, prompt, store=store, namespace=namespace, model=model, timeout=timeout, cwd=cwd, dirs=dirs,
                 mode=mode, conversation=conversation, expect=expect, require=require, schema=schema,
                 check_cmd=check_cmd, check_timeout=check_timeout, check=check, check_id=check_id)
    with contextlib.ExitStack() as stack:
        _admit(c, stack, new_version)
        return _drive(c, max_turns, ignore_quota, fresh)
```

Check against the step 1 code: `_settle` is the old per-outcome block of `call()` with `conv` replaced by `c.conv`, the denied history read from the other attempts' `launch.json`, the attempt kind and quota generation read from this attempt's `launch.json`, and the conflict stop added before the dispatch.
Both step 1 guards are kept: `give_up` with "no follow-up was sent" after a failing check of an `AGY_ERRORS` reply in `_settle` and in the check-error recheck of `_drive`, and the `failed` + `AGY_ERRORS` rerun guard in `_drive`, which now also covers `conversationConflict`.

- [ ] **Step 5: Run the tests**

Run: `uvx pytest -q tests/test_runner.py`
Expected: all pass.
Run: `for i in 1 2 3; do uvx pytest -q tests/ || break; done`
Expected: every run passes (159 tests).
Then run `grep -n "conversationConflict\|AGY_ERRORS\|no follow-up was sent" agycall/runner.py`; expected: the conflict stop and its rerun guard, and three uses of the follow-up guard (two in `_settle`'s reply branch and `give_up` text, one in `_drive`'s recheck).

- [ ] **Step 6: Commit**

```bash
git add agycall/runner.py tests/test_runner.py tests/agyturns.py
printf '%s\n' "Recover a crashed attempt from its saved output; stop on a conversation conflict" "" \
  "The per-outcome handling of call() is one function, used for a live attempt" \
  "and for an attempt whose controller died after agy wrote its envelope. Such" \
  "a reply is accepted only by a check, and nothing is resent. A reply in" \
  "another conversation stops the job, and a rerun keeps it stopped. The check" \
  "command runs in the job's directory and is recorded in check.json." "" \
  "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" "Claude-Session: https://claude.ai/code/session_013coVpN7if3z6kaMYLTrz3c" > "$TMPDIR/agycall-msg.txt"
git commit -F "$TMPDIR/agycall-msg.txt"
```

---

### Task 3: Cancel requests in the runner

**Files:**
- Modify: `agycall/runner.py`
- Test: `tests/test_runner.py`

**Interfaces:**
- Consumes: Task 1's `agy.run(..., should_stop)`, `checker(text, should_stop=...)`, `Cancelled`; Task 2's `_prepare`, `_check`, `_settle`, `_drive`, `_recovered`.
- Produces:
  - `runner.CANCELLED = "cancelled by request"` (the finding recorded for a cancel).
  - `runner._cancel_requested(job) -> bool`: `<job>/cancel.request` exists.
  - `runner._cancel(c, att=None, res=None)`: writes `res` with `outcome: cancelled` to the attempt's `result.json` when `att` is given, sets state `cancelled`, raises `Cancelled`.
  - `runner._check(c, att, text, res)`: gains `res` (the attempt result to record a cancel in).
  - `c.should_stop`: the callable passed to `agy.run` and the check command.
  - Behaviour: `call()` raises `Cancelled` (27) when `cancel.request` exists before a launch, during a run or a check (polled every `agy.POLL` seconds), immediately before saving an answer, or when a rerun recovers a crashed attempt; the request stays, so every later rerun of the job is cancelled too.

- [ ] **Step 1: Write the failing tests**

In `tests/test_runner.py`, add `import threading` after `import sys`, and replace the errors import line with:

```python
from agycall.errors import Cancelled, CheckError, Fatal, GaveUp, Mismatch, NoConversation, Quota, Running
```

Append:

```python
def _request_cancel(job):
    store.write_atomic(job.path("cancel.request"), "{}")
    return True


def test_cancel_request_stops_a_running_turn(tmp_path, fake, monkeypatch):
    set_turns, launches = fake
    set_turns(reply("late", sleep=30))
    monkeypatch.setattr(agy, "POLL", 0.1)
    job = store.Job(S(tmp_path), "j1")
    threading.Thread(target=lambda: wait_for(launches) and _request_cancel(job), daemon=True).start()
    t0 = time.monotonic()
    with pytest.raises(Cancelled) as e:
        runner.call("j1", "q", store=S(tmp_path), model="m")
    assert e.value.code == 27 and time.monotonic() - t0 < 20
    att = job.launched()[0]
    res = store.read_json(os.path.join(att, "result.json"))
    assert res["classified"] == "cancelled" and res["outcome"] == "cancelled" and job.status()["state"] == "cancelled"
    assert not agy.alive(store.read_json(os.path.join(att, "launch.json"))["pgid"])


def test_cancelled_job_stays_cancelled_and_launches_nothing(tmp_path, fake):
    set_turns, launches = fake
    set_turns(GOOD)
    job = store.Job(S(tmp_path), "j1")
    _request_cancel(job)
    for _ in range(2):
        with pytest.raises(Cancelled):
            runner.call("j1", "q", store=S(tmp_path), model="m")
    assert launches() == [] and job.status()["state"] == "cancelled"


def test_cancel_during_the_check_stops_it(tmp_path, fake, monkeypatch):
    set_turns, _ = fake
    set_turns(reply("x"))
    monkeypatch.setattr(agy, "POLL", 0.1)
    job = store.Job(S(tmp_path), "j1")
    checking = lambda: job.launched() and os.path.exists(os.path.join(job.launched()[0], "check.json"))
    threading.Thread(target=lambda: wait_for(checking) and _request_cancel(job), daemon=True).start()
    with pytest.raises(Cancelled):
        runner.call("j1", "q", store=S(tmp_path), model="m", check_cmd="sleep 30")
    att = job.launched()[0]
    assert store.read_json(os.path.join(att, "result.json"))["outcome"] == "cancelled"
    assert not agy.alive(store.read_json(os.path.join(att, "check.json"))["pgid"])
    assert not os.path.exists(job.path("answer.txt")) and job.status()["state"] == "cancelled"


def test_cancel_after_the_check_passes_saves_no_answer(tmp_path, fake):
    set_turns, _ = fake
    set_turns(reply("x"))
    req = os.path.join(S(tmp_path), "j1", "cancel.request")
    with pytest.raises(Cancelled):
        runner.call("j1", "q", store=S(tmp_path), model="m", check_cmd="touch " + shlex.quote(req))
    job = store.Job(S(tmp_path), "j1")
    assert not os.path.exists(job.path("answer.txt")) and job.status()["state"] == "cancelled"
    assert open(os.path.join(job.launched()[0], "reply.txt")).read() == "x"  # the reply is kept


def test_cancel_request_found_during_recovery(tmp_path, fake):
    set_turns, launches = fake
    set_turns(GOOD)
    job, att = _pending(tmp_path, _env("paid reply"))
    _request_cancel(job)
    with pytest.raises(Cancelled):
        runner.call("j1", "q", store=S(tmp_path), model="m")
    res = store.read_json(os.path.join(att, "result.json"))
    assert res["classified"] == "cancelled" and res["recovered"] is True and launches() == []
    assert open(os.path.join(att, "reply.txt")).read() == "paid reply"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uvx pytest -q tests/test_runner.py -k cancel`
Expected: the five tests fail, with `DID NOT RAISE <class 'agycall.errors.Cancelled'>` or, for the recovery test, `GaveUp`; the running-turn and during-check tests take about 30 seconds because nothing stops the fake's sleep yet.

- [ ] **Step 3: Add the cancel points to `agycall/runner.py`**

Make these edits to the Task 2 file.

Replace the errors import line with:

```python
from .errors import Cancelled, CheckError, Fatal, GaveUp, Mismatch, NoConversation, Quota, Running
```

After the `AGY_ERRORS = ...` line, add:

```python
CANCELLED = "cancelled by request"
```

After `parse_usage`, add:

```python
def _cancel_requested(job):
    return os.path.exists(job.path("cancel.request"))


def _cancel(c, att=None, res=None):
    """Stop the job for a cancel request: record it as the attempt's outcome when there is one, set state cancelled,
    raise Cancelled. Saved replies stay where they are."""
    if att is not None:
        st.write_atomic(os.path.join(att, "result.json"), dict(res or {}, outcome="cancelled", findings=[CANCELLED]))
    c.job.set_status(state="cancelled", findings=[CANCELLED])
    raise Cancelled("job %s was cancelled; its attempts are kept in %s" % (c.job.id, c.job.path("attempts")))
```

In `_recovered`, replace:

```python
    if classify.envelope(out) is not None:
        stopped = None
```

with:

```python
    if _cancel_requested(job):
        stopped = "cancel"
    elif classify.envelope(out) is not None:
        stopped = None
```

In `_launch`, replace the `agy.run` line with:

```python
    r = agy.run(att, cmd, cwd=c.cwd, deadline=agy.seconds(c.timeout) + agy.GRACE, fds=c.fds, on_start=started,
                should_stop=c.should_stop)
```

Replace the whole `_check` function with:

```python
def _check(c, att, text, res):
    """Run the caller's check on a reply in the job's cwd; a check command records its process in check.json, the job
    shows `checking` while it runs, and a cancel request stops it and is recorded in res."""
    def started(pid, when):
        st.write_atomic(os.path.join(att, "check.json"), {"pid": pid, "pgid": pid, "pstart": when, "started": _now()})
        c.job.set_status(state="checking")

    try:
        return c.checker(text, cwd=c.cwd, fds=c.fds, on_start=started, should_stop=c.should_stop)
    except Cancelled:
        _cancel(c, att, res)
```

In `_settle`, replace:

```python
    resumed = launch.get("conversation")
```

with:

```python
    if oc.name == "cancelled":
        _cancel(c, att, res)
    resumed = launch.get("conversation")
```

and in the same function replace:

```python
        try:
            verdict = _check(c, att, oc.response)
        except CheckError as e:
            finish("check-error", outcome="check-error", findings=[str(e)])
            raise
        if verdict.ok:
            st.write_atomic(job.path("answer.txt"), verdict.answer)  # the answer first: its presence means done
```

with:

```python
        try:
            verdict = _check(c, att, oc.response, res)
        except CheckError as e:
            finish("check-error", outcome="check-error", findings=[str(e)])
            raise
        if verdict.ok:
            if _cancel_requested(job):  # the last cancel point: nothing is saved as done after a request
                _cancel(c, att, res)
            st.write_atomic(job.path("answer.txt"), verdict.answer)  # the answer first: its presence means done
```

In `_prepare`, replace `    return c` with:

```python
    c.should_stop = lambda: "cancel" if _cancel_requested(job) else None
    return c
```

In `_drive`, replace:

```python
        try:
            verdict = _check(c, done[-1], _saved_reply(done[-1]))
        except CheckError as e:
            job.set_status(state="check-error", findings=[str(e)])
            raise
        if verdict.ok:
            st.write_atomic(job.path("answer.txt"), verdict.answer)
```

with:

```python
        try:
            verdict = _check(c, done[-1], _saved_reply(done[-1]), last_res)
        except CheckError as e:
            job.set_status(state="check-error", findings=[str(e)])
            raise
        if verdict.ok:
            if _cancel_requested(job):
                _cancel(c, done[-1], last_res)
            st.write_atomic(job.path("answer.txt"), verdict.answer)
```

and replace:

```python
    while True:
        atts = job.launched()
```

with:

```python
    while True:
        if _cancel_requested(job):  # checked before every launch
            _cancel(c)
        atts = job.launched()
```

- [ ] **Step 4: Run the tests**

Run: `uvx pytest -q tests/test_runner.py`
Expected: all pass.
Run: `for i in 1 2 3; do uvx pytest -q tests/ || break; done`
Expected: every run passes (164 tests).

- [ ] **Step 5: Commit**

```bash
git add agycall/runner.py tests/test_runner.py
printf '%s\n' "Stop a job when its cancel request appears" "" \
  "The runner checks for cancel.request before each launch, while agy or the" \
  "check command runs, before saving an answer and during recovery; it records" \
  "the attempt as cancelled and exits 27. A cancelled job stays cancelled." "" \
  "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" "Claude-Session: https://claude.ai/code/session_013coVpN7if3z6kaMYLTrz3c" > "$TMPDIR/agycall-msg.txt"
git commit -F "$TMPDIR/agycall-msg.txt"
```

---

### Task 4: `status`, `result` and `cancel`

**Files:**
- Create: `agycall/control.py`
- Modify: `agycall/runner.py`, `agycall/cli.py` (whole file), `tests/conftest.py`
- Test: `tests/test_control.py`

**Interfaces:**
- Consumes: Task 1's `agy.owns`, `agy.stop_group`; Task 2's `runner.pending`, `runner._saved_reply`, `runner._read`, `runner._now`; Task 3's `runner.CANCELLED` and the runner's cancel handling.
- Produces:
  - `status.json` gains `controller: {pid, pstart}` while `call()` runs (`null` after it returns) and `code` when the state is `fatal`; `request.json` gains `session` (`AGYCALL_SESSION`, or `null`).
  - `control.ACTIVE = ("queued", "running", "checking")`, `control.CODES` (state to exit code), `control.CANCEL_WAIT = 20`.
  - `control.jobs(store) -> list[st.Job]`: jobs with a `request.json`, the current session's first, newest first in each group.
  - `control.live(job) -> list[tuple[str, int | None]]`: `("controller" | "agy" | "check", pid)` for each live process; pid `None` when agy started but its pid was never recorded.
  - `control.status(store, job_id=None) -> (str, int)`, `control.result(store, job_id=None) -> (str, int)`, `control.cancel(store, job_id=None, wait=CANCEL_WAIT) -> (str, int)`; an unknown job raises `ValueError("no job ... in ...")`.
  - CLI: `agy-call status [JOB] [--store D] [--namespace N]`, `agy-call result [JOB] ...`, `agy-call cancel [JOB] ...`; the text goes to stdout and the code is the exit status; a `ValueError` exits 2.

- [ ] **Step 1: Clear the session variable in every test**

In `tests/conftest.py`, in `agy_home`, after `monkeypatch.setenv("AGYCALL_HOME", str(home))` add:

```python
    monkeypatch.delenv("AGYCALL_SESSION", raising=False)  # a developer's own session must not leak into tests
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_control.py`:

```python
import os
import signal
import subprocess
import sys
import threading

import pytest

from agycall import agy, control, runner, store
from agycall.errors import Cancelled, Fatal, GaveUp
from agyturns import error_reply, model_error, reply, wait_for

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
AGYCALL = os.path.join(ROOT, "bin", "agy-call")


def S(tmp_path):
    return str(tmp_path / "store")


def run_cli(*args):
    return subprocess.run([sys.executable, AGYCALL, *args], capture_output=True, text=True, timeout=60)


def _fake_job(tmp_path, name="j1", session=None, **status):
    job = store.Job(S(tmp_path), name)
    store.write_atomic(job.path("request.json"), {"identity": "x", "created": runner._now(), "session": session})
    job.set_status(**status)
    return job


def _me():
    return {"pid": os.getpid(), "pstart": agy.start_time(os.getpid())}


def _launch_rec(job):
    atts = job.launched()
    rec = store.read_json(os.path.join(atts[0], "launch.json")) if atts else None
    return rec if rec and rec.get("pstart") else None


def test_status_lists_this_sessions_jobs_first(tmp_path, fake, monkeypatch):
    set_turns, _ = fake
    monkeypatch.setenv("AGYCALL_SESSION", "s1")
    set_turns(reply("a"))
    runner.call("mine", "q", store=S(tmp_path), model="m")
    monkeypatch.setenv("AGYCALL_SESSION", "s2")
    set_turns(error_reply("half", error="stream closed unexpectedly"))
    with pytest.raises(GaveUp):
        runner.call("other", "q", store=S(tmp_path), model="m")
    monkeypatch.setenv("AGYCALL_SESSION", "s1")
    text, code = control.status(S(tmp_path))
    lines = text.splitlines()
    assert code == 0 and len(lines) == 2
    assert lines[0].startswith("mine  done  ") and lines[1].startswith("other  failed  ")  # other is newer
    assert "stream closed unexpectedly" in lines[1]
    assert store.read_json(os.path.join(S(tmp_path), "mine", "request.json"))["session"] == "s1"
    assert control.status(str(tmp_path / "empty")) == ("no jobs in %s" % (tmp_path / "empty"), 0)


def test_status_of_one_job_shows_its_fields_and_processes(tmp_path, fake):
    set_turns, _ = fake
    set_turns(reply("a"))
    runner.call("j1", "q", store=S(tmp_path), model="m")
    text, code = control.status(S(tmp_path), "j1")
    assert code == 0 and "state: done" in text and "conversation: c1" in text and "processes: none" in text
    assert "controller: null" in text  # cleared when call() returned
    with pytest.raises(ValueError, match="no job nope"):
        control.status(S(tmp_path), "nope")


def test_result_prints_the_answer_or_the_state_findings_and_last_reply(tmp_path, fake, monkeypatch):
    set_turns, _ = fake
    monkeypatch.setenv("AGYCALL_SESSION", "s1")
    set_turns(reply("the answer"))
    runner.call("ok", "q", store=S(tmp_path), model="m")
    assert control.result(S(tmp_path), "ok") == ("the answer", 0)
    set_turns(error_reply("half a reply", error="stream closed unexpectedly"))
    with pytest.raises(GaveUp):
        runner.call("bad", "q", store=S(tmp_path), model="m")
    text, code = control.result(S(tmp_path), "bad")
    assert code == 20 and text.startswith("state: failed") and "stream closed" in text and text.endswith("half a reply")
    assert control.result(S(tmp_path))[1] == 20  # no id: the newest finished job of this session
    set_turns(model_error())
    with pytest.raises(Fatal):
        runner.call("setup", "q", store=S(tmp_path), model="m")
    assert control.result(S(tmp_path), "setup")[1] == 14


def test_cancel_of_a_finished_job_is_a_no_op(tmp_path, fake):
    set_turns, _ = fake
    set_turns(reply("a"))
    runner.call("j1", "q", store=S(tmp_path), model="m")
    assert control.cancel(S(tmp_path), "j1") == ("done", 0)
    assert not os.path.exists(os.path.join(S(tmp_path), "j1", "cancel.request"))


def test_cancel_leaves_the_stopping_to_a_live_controller(tmp_path, fake):
    set_turns, launches = fake
    set_turns(reply("late", sleep=30))
    p = subprocess.Popen([sys.executable, AGYCALL, "run", "--job", "j1", "--store", S(tmp_path), "--model", "m",
                          "--prompt", "q"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        job = store.Job(S(tmp_path), "j1")
        wait_for(lambda: launches() and _launch_rec(job) and agy.owns(job.status().get("controller")))
        text = control.status(S(tmp_path), "j1")[0]
        assert "controller pid %d" % p.pid in text and "agy pid %d" % _launch_rec(job)["pid"] in text
        assert control.cancel(S(tmp_path), "j1") == ("cancelled", 0)
        assert p.wait(timeout=30) == 27
        assert control.result(S(tmp_path), "j1")[1] == 27
        assert control.cancel(S(tmp_path), "j1") == ("cancelled", 0)  # repeated: a no-op
    finally:
        p.kill()
        p.wait()


def test_cancel_stops_agy_left_running_by_a_killed_controller(tmp_path, fake):
    set_turns, launches = fake
    set_turns(reply("late", sleep=30))
    code = "import sys; sys.path.insert(0, %r); from agycall import runner; runner.call('j1', 'q', store=%r, model='m')" % (ROOT, S(tmp_path))
    p = subprocess.Popen([sys.executable, "-c", code], env=dict(os.environ))
    job = store.Job(S(tmp_path), "j1")
    rec = wait_for(lambda: launches() and _launch_rec(job))
    os.kill(p.pid, signal.SIGKILL)
    p.wait(timeout=10)
    try:
        assert agy.owns(rec) and not agy.owns(job.status().get("controller"))
        assert control.cancel(S(tmp_path), "j1") == ("cancelled", 0)
        assert not agy.alive(rec["pgid"])
        assert store.read_json(os.path.join(job.launched()[0], "result.json"))["outcome"] == "cancelled"
        with pytest.raises(Cancelled):
            runner.call("j1", "q", store=S(tmp_path), model="m")
        assert len(launches()) == 1
    finally:
        agy.stop_group(rec["pgid"])


def test_cancel_reports_a_job_that_finished_first(tmp_path):
    job = _fake_job(tmp_path, state="running", controller=_me())
    threading.Timer(0.5, lambda: job.set_status(state="done", controller=None)).start()
    assert control.cancel(S(tmp_path), "j1", wait=10) == ("finished before cancel (done)", 0)


def test_cancel_that_runs_out_of_time_says_still_stopping(tmp_path):
    _fake_job(tmp_path, state="running", controller=_me())
    assert control.cancel(S(tmp_path), "j1", wait=1) == ("still stopping (pid %d)" % os.getpid(), 25)


def test_cancel_without_an_id_needs_one_running_job_of_this_session(tmp_path, monkeypatch):
    monkeypatch.setenv("AGYCALL_SESSION", "s1")
    for name in ("a", "b"):
        _fake_job(tmp_path, name, session="s1", state="queued")
    _fake_job(tmp_path, "theirs", session="s2", state="running")
    with pytest.raises(ValueError, match="several jobs.*a, b$"):
        control.cancel(S(tmp_path))
    store.Job(S(tmp_path), "b").set_status(state="done")
    assert control.cancel(S(tmp_path)) == ("cancelled", 0)
    assert store.Job(S(tmp_path), "a").status()["state"] == "cancelled"
    assert store.Job(S(tmp_path), "theirs").status()["state"] == "running"


def test_cli_status_result_and_cancel(tmp_path, fake):
    set_turns, _ = fake
    set_turns(reply("the answer"))
    assert run_cli("run", "--job", "j1", "--store", S(tmp_path), "--model", "m", "--prompt", "q").returncode == 0
    r = run_cli("status", "--store", S(tmp_path))
    assert r.returncode == 0 and r.stdout.startswith("j1  done  ")
    r = run_cli("result", "j1", "--store", S(tmp_path))
    assert r.returncode == 0 and r.stdout == "the answer\n"
    r = run_cli("cancel", "j1", "--store", S(tmp_path))
    assert r.returncode == 0 and r.stdout == "done\n"
    r = run_cli("result", "nope", "--store", S(tmp_path))
    assert r.returncode == 2 and "no job nope" in r.stderr
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uvx pytest -q tests/test_control.py`
Expected: FAIL at collection with `ImportError: cannot import name 'control' from 'agycall'`.

- [ ] **Step 4: Record the controller, the session and the setup-error code in `agycall/runner.py`**

In `_admit`, replace the `request.json` write with:

```python
        st.write_atomic(job.path("request.json"), {"identity": c.ident, "prompt": c.prompt, "key": c.key, "cwd": c.cwd,
                                                   "dirs": c.dirs, "seed": c.seed, "check": c.cident, "mode": c.mode,
                                                   "created": _now(), "session": os.environ.get("AGYCALL_SESSION") or None})
```

The session is informational and not part of the identity, so existing jobs keep their identity.

In `_settle`, replace:

```python
        finish("fatal", outcome="fatal", findings=[reason])
        raise Fatal({"auth": 11, "model": 14}.get(name, 22), reason)
```

with:

```python
        code = {"auth": 11, "model": 14}.get(name, 22)
        finish("fatal", outcome="fatal", findings=[reason])
        job.set_status(code=code)
        raise Fatal(code, reason)
```

In `_drive`, replace `job.set_status(state="fatal", findings=[e.reason])` with:

```python
            job.set_status(state="fatal", findings=[e.reason], code=e.code)
```

In `call`, replace:

```python
    with contextlib.ExitStack() as stack:
        _admit(c, stack, new_version)
        return _drive(c, max_turns, ignore_quota, fresh)
```

with:

```python
    with contextlib.ExitStack() as stack:
        _admit(c, stack, new_version)
        # cancel reads this to know whether a live process will act on cancel.request; the callback runs before the
        # job lock is released, because the stack unwinds in reverse order
        c.job.set_status(controller={"pid": os.getpid(), "pstart": agy.start_time(os.getpid())})
        stack.callback(c.job.set_status, controller=None)
        return _drive(c, max_turns, ignore_quota, fresh)
```

- [ ] **Step 5: Create `agycall/control.py`**

```python
"""Job control (spec 7.3): status, result and cancel for the jobs of a store."""
import datetime as dt
import json
import os
import time

from . import agy, runner
from . import store as st

ACTIVE = ("queued", "running", "checking")
CODES = {"done": 0, "quota": 10, "failed": 20, "check-error": 21, "no-conversation": 23,
         "queued": 25, "running": 25, "checking": 25, "cancelled": 27}
CANCEL_WAIT = 20  # seconds cancel waits for the job to stop


def _job(store, job_id):
    job = st.Job(store, job_id)
    if not os.path.exists(job.path("request.json")):
        raise ValueError("no job %s in %s" % (job_id, store))
    return job


def _request(job):
    return st.read_json(job.path("request.json"), {}) or {}


def _session():
    return os.environ.get("AGYCALL_SESSION") or None


def jobs(store):
    """The store's jobs: those recorded with the current session (AGYCALL_SESSION) first, each group newest first."""
    names = os.listdir(store) if os.path.isdir(store) else []
    found = [st.Job(store, n) for n in names if st.JOB_ID.match(n) and os.path.exists(os.path.join(store, n, "request.json"))]
    found.sort(key=lambda j: _request(j).get("created") or "", reverse=True)
    return [j for j in found if _request(j).get("session") == _session()] + \
        [j for j in found if _request(j).get("session") != _session()]


def _mine(store):
    return [j for j in jobs(store) if _request(j).get("session") == _session()]


def live(job):
    """(what, pid) for each of the job's processes still running: its controller, then the newest attempt's agy and
    check command; pid is None when agy started but its pid was never recorded (spec 7.4)."""
    procs = []
    ctl = job.status().get("controller")
    if agy.owns(ctl):
        procs.append(("controller", ctl["pid"]))
    att = runner.pending(job)
    if att:
        launch = st.read_json(os.path.join(att, "launch.json"), {}) or {}
        if not launch.get("pid"):
            procs.append(("agy", None))
        elif agy.owns(launch):
            procs.append(("agy", launch["pid"]))
        check = st.read_json(os.path.join(att, "check.json"))
        if agy.owns(check):
            procs.append(("check", check["pid"]))
    return procs


def _procs(procs):
    return ", ".join("%s pid %s" % (what, pid or "unknown") for what, pid in procs)


def _state(job):
    """The recorded state; for a queued, running or checking job also what still runs for it."""
    state = job.status().get("state") or "unknown"
    if state not in ACTIVE:
        return state
    return "%s (%s)" % (state, _procs(live(job)) or "no live process; rerun the job to recover it")


def _age(job):
    created = _request(job).get("created")
    if not created:
        return "?"
    secs = max(0, int((dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(created)).total_seconds()))
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if secs >= size:
            return "%d%s" % (secs // size, unit)
    return "%ds" % secs


def status(store, job_id=None):
    """(text, exit code) for `agy-call status [JOB]`."""
    if job_id:
        job = _job(store, job_id)
        lines = ["job: %s" % job.id, "state: %s" % _state(job)]
        lines += ["%s: %s" % (k, v if isinstance(v, str) else json.dumps(v))
                  for k, v in sorted(job.status().items()) if k != "state"]
        lines.append("processes: %s" % (_procs(live(job)) or "none"))
        return "\n".join(lines), 0
    rows = []
    for job in jobs(store):
        finding = ((job.status().get("findings") or [""])[-1] or "").replace("\n", " ")[:100]
        rows.append(("%s  %s  %s  %s" % (job.id, _state(job), _age(job), finding)).rstrip())
    return "\n".join(rows) or "no jobs in %s" % store, 0


def _newest_finished(store):
    done = [j for j in _mine(store) if os.path.exists(j.path("status.json")) and j.status().get("state") not in ACTIVE]
    if not done:
        raise ValueError("no finished job of this session in %s" % store)
    return max(done, key=lambda j: os.path.getmtime(j.path("status.json")))


def result(store, job_id=None):
    """(text, exit code) for `agy-call result [JOB]`; without JOB, the newest finished job of the current session."""
    job = _job(store, job_id) if job_id else _newest_finished(store)
    s = job.status()
    state = s.get("state")
    if state == "done" and os.path.exists(job.path("answer.txt")):
        return runner._read(job.path("answer.txt")), 0
    lines = ["state: %s" % _state(job)] + ["finding: %s" % f for f in s.get("findings") or []]
    for att in reversed(job.launched()):
        reply = runner._saved_reply(att)
        if reply.strip():
            lines += ["last reply (attempt %s):" % att, reply]
            break
    return "\n".join(lines), (s.get("code", 22) if state == "fatal" else CODES.get(state, 1))


def _only_running(store):
    running = [j for j in _mine(store) if j.status().get("state") in ACTIVE]
    if not running:
        raise ValueError("no job of this session is running in %s" % store)
    if len(running) > 1:
        raise ValueError("several jobs of this session are running; name one: %s" % ", ".join(sorted(j.id for j in running)))
    return running[0]


def _stop_orphans(job):
    """No controller is alive, so nothing will act on cancel.request: stop the newest attempt's check and agy process
    groups while they are still the recorded processes, record the attempt and the job as cancelled, and return the pid
    of a group that outlived SIGKILL (else None)."""
    att = runner.pending(job)
    if att:
        for name in ("check.json", "launch.json"):
            rec = st.read_json(os.path.join(att, name))
            if agy.owns(rec) and not agy.stop_group(rec["pgid"]):
                return rec["pid"]
        if not os.path.exists(os.path.join(att, "result.json")):  # a rerun that started meanwhile may have settled it
            st.write_atomic(os.path.join(att, "result.json"), {"exit": None, "outcome": "cancelled", "classified": "cancelled",
                                                               "findings": [runner.CANCELLED], "ended": runner._now()})
    job.set_status(state="cancelled", findings=[runner.CANCELLED])
    return None


def cancel(store, job_id=None, wait=CANCEL_WAIT):
    """(text, exit code) for `agy-call cancel [JOB]`: write cancel.request without taking the job lock, then wait up to
    `wait` seconds for the job to stop. A job that is not running is left alone."""
    job = _job(store, job_id) if job_id else _only_running(store)
    s = job.status()
    if s.get("state") == "cancelled" or not (s.get("state") in ACTIVE or agy.owns(s.get("controller"))):
        return s.get("state") or "unknown", 0
    st.write_atomic(job.path("cancel.request"), {"requested": runner._now()})
    if not agy.owns(job.status().get("controller")):
        stuck = _stop_orphans(job)
        if stuck:
            return "still stopping (pid %s)" % stuck, 25
    end = time.monotonic() + wait
    while True:
        s = job.status()
        if s.get("state") == "cancelled":
            return "cancelled", 0
        if s.get("state") not in ACTIVE and not agy.owns(s.get("controller")):
            return "finished before cancel (%s)" % s.get("state"), 0
        if time.monotonic() >= end:
            procs = live(job)
            return "still stopping (pid %s)" % ((procs[-1][1] if procs else None) or "unknown"), 25
        time.sleep(0.2)
```

A job counts as running while its state is queued, running or checking or while its recorded controller is alive; the second half covers a rerun whose state still shows the previous outcome while it waits for its conversation lock.

- [ ] **Step 6: Replace `agycall/cli.py`**

```python
"""agy-call run | batch | status | result | cancel. Prints the accepted answer or the report on stdout; errors on
stderr; exit codes in SKILL.md."""
import argparse
import collections
import json
import sys

from . import control
from . import store as st
from .batch import batch
from .errors import AgyCallError, Quota
from .runner import call


def _common(p):
    p.add_argument("--store")
    p.add_argument("--namespace")
    p.add_argument("--model")
    p.add_argument("--timeout", default="10m")
    p.add_argument("--cwd")
    p.add_argument("--dir", action="append", default=[], dest="dirs")
    p.add_argument("--mode")
    p.add_argument("--expect", choices=["json"])
    p.add_argument("--require", type=lambda s: [k for k in s.split(",") if k])
    p.add_argument("--schema")
    p.add_argument("--check-cmd")
    p.add_argument("--check-timeout", type=float, default=120)
    p.add_argument("--max-turns", type=int, default=4)
    p.add_argument("--ignore-quota", action="store_true")
    p.add_argument("--fresh", action="store_true")


def _opts(a):
    keys = ["store", "namespace", "model", "timeout", "cwd", "dirs", "mode", "expect", "require", "schema",
            "check_cmd", "check_timeout", "max_turns", "ignore_quota", "fresh"]
    return {k: getattr(a, k) for k in keys}


def _run(a):
    if a.prompt is not None:
        prompt = a.prompt
    elif a.prompt_file:
        with open(a.prompt_file, encoding="utf-8") as fh:
            prompt = fh.read()
    else:
        prompt = sys.stdin.read()
    res = call(a.job, prompt, conversation=a.conversation, new_version=a.new_version, **_opts(a))
    sys.stdout.write(res.text if res.text.endswith("\n") else res.text + "\n")
    return 0


def _batch(a):
    with open(a.jobs_file, encoding="utf-8") as fh:
        jobs = [json.loads(l) for l in fh if l.strip()]
    try:
        res = batch(jobs, workers=a.workers, **_opts(a))
    except ValueError as e:  # duplicate job ids
        print("agy-call: %s" % e, file=sys.stderr)
        return 2
    states = collections.Counter("done" if not isinstance(v, Exception) else type(v).__name__ for v in res.values())
    for s, n in sorted(states.items()):
        print("%s %d" % (s, n))
    for job_id, v in res.items():
        if isinstance(v, Exception):
            print("  %s: %s" % (job_id, v))
    if all(not isinstance(v, Exception) for v in res.values()):
        return 0
    return 10 if any(isinstance(v, Quota) for v in res.values()) else 20


def main(argv):
    ap = argparse.ArgumentParser(prog="agy-call", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--job", required=True)
    src = r.add_mutually_exclusive_group()
    src.add_argument("--prompt-file")
    src.add_argument("--prompt")
    r.add_argument("--conversation")
    r.add_argument("--new-version", action="store_true")
    _common(r)
    b = sub.add_parser("batch")
    b.add_argument("jobs_file")
    b.add_argument("--workers", type=int, default=4)
    _common(b)
    for name in ("status", "result", "cancel"):
        p = sub.add_parser(name)
        p.add_argument("job", nargs="?")
        p.add_argument("--store")
        p.add_argument("--namespace")
    a = ap.parse_args(argv)
    if a.cmd == "batch":
        return _batch(a)
    try:
        if a.cmd == "run":
            return _run(a)
        text, code = getattr(control, a.cmd)(st.store_dir(a.store, a.namespace), a.job)
    except AgyCallError as e:
        print("agy-call: %s" % e, file=sys.stderr)
        return e.code
    except ValueError as e:  # a bad argument, job id, timeout, prompt size, or an unknown job
        print("agy-call: %s" % e, file=sys.stderr)
        return 2
    sys.stdout.write(text if text.endswith("\n") else text + "\n")
    return code
```

`run` and `batch` behave as before; `_run` and `_batch` are the old branches moved into functions.

- [ ] **Step 7: Run the tests**

Run: `uvx pytest -q tests/test_control.py`
Expected: all pass.
Run: `for i in 1 2 3; do uvx pytest -q tests/ || break; done`
Expected: every run passes (174 tests).

- [ ] **Step 8: Commit**

```bash
git add agycall/control.py agycall/runner.py agycall/cli.py tests/conftest.py tests/test_control.py
printf '%s\n' "Add status, result and cancel" "" \
  "status lists a store's jobs with this session's first, or one job's fields" \
  "and live processes. result prints the answer or the findings and the last" \
  "reply, with the state's exit code. cancel writes a request and waits for the" \
  "job, and stops agy or the check itself when the process running the job has" \
  "died. Jobs record their controller process and the session that started them." "" \
  "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" "Claude-Session: https://claude.ai/code/session_013coVpN7if3z6kaMYLTrz3c" > "$TMPDIR/agycall-msg.txt"
git commit -F "$TMPDIR/agycall-msg.txt"
```

---

### Task 5: `run --background` and the worker

**Files:**
- Modify: `agycall/runner.py`, `agycall/control.py`, `agycall/cli.py`
- Test: `tests/test_background.py`

**Interfaces:**
- Consumes: Task 2's `_prepare`, `_admit`; Task 4's `control.py`, `cli.py` and the controller record in `call()`.
- Produces:
  - `runner.enqueue(job_id, prompt, *, max_turns=4, ignore_quota=False, fresh=False, new_version=False, **kw) -> st.Job`: `kw` takes `call()`'s other keyword arguments; validates as `call()` does (a Python `check` raises `ValueError`), takes the job lock without blocking (`Running` when held), archives or refuses a changed request, writes `request.json`, `background.json` and state `queued`, releases the lock.
  - `background.json`: `call()`'s keyword arguments with `job_id`, `prompt`, absolute `store`, `cwd`, `dirs` and `schema`, `namespace: null` and `new_version: false`.
  - `control.start(job_id, prompt, **kw) -> str`: `enqueue`, then a detached `sys.executable -m agycall.cli worker --store S --job J` (new session, stdin `/dev/null`, stdout and stderr appended to `<job>/worker.log`, working directory the job folder, this checkout first on `PYTHONPATH`); returns the job id.
  - `control.work(store, job_id) -> int`: `runner.call(**background.json)`; writes one line to stdout (the log) and returns the exit code.
  - CLI: `agy-call run --background ...` prints the job id and exits 0; `agy-call worker --store S --job J` (internal); `python -m agycall.cli` runs `main`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_background.py`:

```python
import json
import os
import subprocess
import sys

import pytest

from agycall import agy, control, runner, store
from agyturns import reply, wait_for

HERE = os.path.dirname(os.path.abspath(__file__))
AGYCALL = os.path.join(os.path.dirname(HERE), "bin", "agy-call")


def S(tmp_path):
    return str(tmp_path / "store")


def run_cli(*args, **kw):
    return subprocess.run([sys.executable, AGYCALL, *args], capture_output=True, text=True, timeout=60, **kw)


def test_background_run_finishes_in_a_detached_worker(tmp_path, fake):
    set_turns, launches = fake
    set_turns(reply('{"answer": "B"}', sleep=3))
    (tmp_path / "schema.json").write_text(json.dumps({"type": "object", "required": ["answer"]}))
    env = dict(os.environ, PATH="/usr/bin:/bin")  # no agy-call on the PATH: the worker must use this checkout
    r = run_cli("run", "--background", "--job", "j1", "--store", "s", "--cwd", str(tmp_path), "--schema", "schema.json",
                "--model", "m", "--prompt", "q", cwd=str(tmp_path), env=env)
    assert r.returncode == 0 and r.stdout == "j1\n"
    job = store.Job(str(tmp_path / "s"), "j1")
    assert job.status()["state"] in ("queued", "running")  # the command returned while the turn still runs
    wait_for(lambda: job.status().get("state") == "done")
    text, code = control.result(job.store, "j1")
    assert code == 0 and json.loads(text) == {"answer": "B"}
    bg = store.read_json(job.path("background.json"))
    assert os.path.realpath(bg["store"]) == os.path.realpath(job.store) and bg["cwd"] == str(tmp_path)
    assert os.path.realpath(bg["schema"]) == os.path.realpath(str(tmp_path / "schema.json"))
    assert "agy-call worker: done" in open(job.path("worker.log")).read()
    again = runner.call("j1", "q", store=job.store, model="m", cwd=str(tmp_path), schema=str(tmp_path / "schema.json"))
    assert again.answer == {"answer": "B"} and len(launches()) == 1


def test_background_refuses_a_bad_request_before_writing_anything(tmp_path, fake):
    set_turns, launches = fake
    r = run_cli("run", "--background", "--job", "j1", "--store", S(tmp_path), "--prompt", "q")
    assert r.returncode == 2 and "model is required" in r.stderr
    (tmp_path / "bad.json").write_text(json.dumps({"type": "int"}))
    r = run_cli("run", "--background", "--job", "j1", "--store", S(tmp_path), "--model", "m", "--prompt", "q",
                "--schema", str(tmp_path / "bad.json"))
    assert r.returncode == 2 and "invalid schema" in r.stderr
    assert not os.path.exists(os.path.join(S(tmp_path), "j1")) and launches() == []
    with pytest.raises(ValueError, match="background"):
        runner.enqueue("j2", "q", store=S(tmp_path), model="m", check=lambda v: [], check_id="c:1")


def test_background_job_is_cancelled_through_the_cli(tmp_path, fake):
    set_turns, launches = fake
    set_turns(reply("late", sleep=30))
    assert run_cli("run", "--background", "--job", "j1", "--store", S(tmp_path), "--model", "m", "--prompt", "q").returncode == 0
    job = store.Job(S(tmp_path), "j1")
    wait_for(lambda: job.status().get("state") == "running" and launches())
    r = run_cli("cancel", "j1", "--store", S(tmp_path))
    assert r.returncode == 0 and r.stdout == "cancelled\n"
    wait_for(lambda: not agy.owns(job.status().get("controller")))  # the worker has exited
    assert run_cli("result", "j1", "--store", S(tmp_path)).returncode == 27
    assert "was cancelled" in open(job.path("worker.log")).read()


def test_background_start_refuses_a_job_that_is_running(tmp_path, fake):
    set_turns, launches = fake
    set_turns(reply("late", sleep=30))
    args = ("run", "--background", "--job", "j1", "--store", S(tmp_path), "--model", "m", "--prompt", "q")
    assert run_cli(*args).returncode == 0
    try:
        wait_for(launches)
        r = run_cli(*args)
        assert r.returncode == 25 and "running" in r.stderr
    finally:
        run_cli("cancel", "j1", "--store", S(tmp_path))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uvx pytest -q tests/test_background.py`
Expected: FAIL (`unrecognized arguments: --background`, exit 2; `AttributeError: ... 'enqueue'`).

- [ ] **Step 3: Add `enqueue` to `agycall/runner.py`**

Append after `call`:

```python


def enqueue(job_id, prompt, *, max_turns=4, ignore_quota=False, fresh=False, new_version=False, **kw):
    """Check a run exactly as call() does, record it as queued and save the arguments a background worker passes to
    call(); kw takes call()'s other keyword arguments. Returns the job."""
    if kw.get("check") is not None:
        raise ValueError("a Python check function cannot run in the background; use check_cmd, expect, require or schema")
    c = _prepare(job_id, prompt, **kw)
    with contextlib.ExitStack() as stack:
        _admit(c, stack, new_version)
        args = dict(kw, job_id=job_id, prompt=prompt, store=c.job.store, namespace=None, cwd=c.cwd, dirs=c.dirs,
                    schema=os.path.abspath(kw["schema"]) if kw.get("schema") else None, max_turns=max_turns,
                    ignore_quota=ignore_quota, fresh=fresh,
                    new_version=False)  # _admit has archived the old version already
        st.write_atomic(c.job.path("background.json"), args)
        c.job.set_status(state="queued")
    return c.job
```

The prompt travels in `background.json`, never on the worker's command line.

- [ ] **Step 4: Add `start` and `work` to `agycall/control.py`**

Replace the module docstring and import block with:

```python
"""Job control (spec 7): status, result and cancel for the jobs of a store, and the detached background worker."""
import datetime as dt
import json
import os
import subprocess
import sys
import time

from . import agy, runner
from . import store as st
from .errors import AgyCallError
```

After the `CANCEL_WAIT` line, add:

```python
ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))  # this checkout; the worker never uses PATH
```

Append:

```python


def start(job_id, prompt, **kw):
    """`run --background`: record the run as queued and start a detached worker for it (spec 7.1); returns the job id."""
    job = runner.enqueue(job_id, prompt, **kw)
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(p for p in (ROOT, os.environ.get("PYTHONPATH")) if p))
    with open(job.path("worker.log"), "ab") as log:
        subprocess.Popen([sys.executable, "-m", "agycall.cli", "worker", "--store", job.store, "--job", job.id],
                         stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, cwd=job.dir, env=env,
                         start_new_session=True)
    return job.id


def work(store, job_id):
    """The worker: run the saved request; the outcome is in status.json, and one line goes to worker.log."""
    job = st.Job(store, job_id)
    try:
        runner.call(**st.read_json(job.path("background.json")))
    except AgyCallError as e:
        print("agy-call worker: %s" % e, flush=True)
        return e.code
    print("agy-call worker: done", flush=True)
    return 0
```

- [ ] **Step 5: Wire the CLI**

In `agycall/cli.py`, in `_run`, replace:

```python
    res = call(a.job, prompt, conversation=a.conversation, new_version=a.new_version, **_opts(a))
```

with:

```python
    kw = dict(conversation=a.conversation, new_version=a.new_version, **_opts(a))
    if a.background:
        print(control.start(a.job, prompt, **kw))
        return 0
    res = call(a.job, prompt, **kw)
```

In `main`, after `r.add_argument("--new-version", action="store_true")` add:

```python
    r.add_argument("--background", action="store_true")
```

after the `for name in ("status", "result", "cancel"):` block add:

```python
    w = sub.add_parser("worker")  # started by run --background; not for direct use
    w.add_argument("--store", required=True)
    w.add_argument("--job", required=True)
```

replace:

```python
    try:
        if a.cmd == "run":
            return _run(a)
```

with:

```python
    try:
        if a.cmd == "worker":
            return control.work(a.store, a.job)
        if a.cmd == "run":
            return _run(a)
```

and append at the end of the file:

```python


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

In the module docstring, replace `agy-call run | batch | status | result | cancel.` with `agy-call run [--background] | batch | status | result | cancel.`

- [ ] **Step 6: Run the tests**

Run: `uvx pytest -q tests/test_background.py`
Expected: all pass.
Run: `for i in 1 2 3; do uvx pytest -q tests/ || break; done`
Expected: every run passes (178 tests).
Then run `ps -ax -o pid=,command= | grep "agycall.cli worker" | grep -v grep`; expected: no output (no worker outlived the suite).

- [ ] **Step 7: Commit**

```bash
git add agycall/runner.py agycall/control.py agycall/cli.py tests/test_background.py
printf '%s\n' "Run a job in a detached background worker" "" \
  "run --background checks the request as a foreground run does, records the" \
  "job as queued with its arguments in background.json, starts a worker from" \
  "this checkout in its own session, prints the job id and exits 0." "" \
  "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" "Claude-Session: https://claude.ai/code/session_013coVpN7if3z6kaMYLTrz3c" > "$TMPDIR/agycall-msg.txt"
git commit -F "$TMPDIR/agycall-msg.txt"
```

---

### Task 6: Docs

**Files:**
- Modify: `skills/agy-call/SKILL.md`, `README.md`

**Interfaces:**
- Consumes: Tasks 1 to 5.
- Produces: user-facing docs that match the code.

This task is mechanical: it has no test of its own and gets no task reviewer; the final review covers it.

- [ ] **Step 1: Update `skills/agy-call/SKILL.md`**

Make these exact replacements (each line is one sentence; keep one sentence per line):

| Old | New |
|---|---|
| `Run it with run_in_background for long jobs.` | `Run it with run_in_background for long jobs, or pass \`--background\` to hand the job to a detached worker (see Status, result and cancel).` |
| `` - `--check-cmd CMD`: shell command that receives the candidate answer on stdin; `` (start of the bullet) | `` - `--check-cmd CMD`: shell command, run in the working directory of the call (`--cwd`), that receives the candidate answer on stdin; `` (rest of the bullet unchanged) |
| `` | 20 | gave up: no usable reply within `--max-turns`, a tool call denied twice, or agy hit its output limit without a reply | `` | `` | 20 | gave up: no usable reply within `--max-turns`, a tool call denied twice, agy hit its output limit without a reply, or agy answered in another conversation | `` |
| `` | 25 | the job is running in another process | `` | `` | 25 | the job is running in another process; from `result`, the job is still queued, running or checking; from `cancel`, it is still stopping | `` |
| `` `GaveUp` (20, with `.findings`, `.conversation`, `.usage`, `.job_dir`), `CheckError` (21), `NoConversation` (23). `` | `` `GaveUp` (20, with `.findings`, `.conversation`, `.usage`, `.job_dir`), `CheckError` (21), `NoConversation` (23), `Cancelled` (27). `` |

After the 25 row of the exit-code table, add the row:

```markdown
| 27 | the job was cancelled |
```

After the bullet `` - `--new-version`: ... `` in "Options of `run` only", add:

```markdown
- `--background`: check the request as a normal run would, record the job as `queued`, start a detached worker that runs it, print the job id and exit 0; the worker keeps running after the Claude session ends, and its own messages go to `<store>/<job>/worker.log` [off].
```

Insert this section immediately before `## Exit codes`:

```markdown
## Status, result and cancel

`agy-call status [JOB] [--store DIR] [--namespace NAME]` lists the jobs of a store, one line each: the id, the state, the age and the last finding.
Jobs started in the current Claude session (the `AGYCALL_SESSION` variable, recorded in `request.json`) come first, then the others, newest first in each group.
With a job id it prints every field of the job's `status.json` and the processes still running for it: the controller (the agy-call process running the job), agy and the check command.
A queued, running or checking job with none of these alive lost its process; rerun the same command to recover it.

`agy-call result [JOB]` prints the accepted answer of a `done` job and exits 0.
For any other state it prints the state, the findings and the last saved reply, and exits with that state's code: 10 quota, 20 failed, 21 check error, 23 no conversation, 25 queued, running or checking, 27 cancelled, and the setup codes 11, 13, 14 and 22.
Without a job id it uses the newest finished job of the current session.

`agy-call cancel [JOB]` asks a queued, running or checking job to stop by writing `<store>/<job>/cancel.request`, without waiting for the job's lock.
It then waits up to 20 seconds and prints `cancelled`, `finished before cancel (STATE)` when the job ended another way first, or `still stopping (pid N)` with exit 25.
On a job that is not running it changes nothing and prints the job's state.
Without a job id it cancels the only running job of the current session, and refuses when there are several.
agy-call looks for the request before each launch, every second while agy or the check command runs, and once more just before it saves the answer; agy and the check command get SIGTERM, then SIGKILL after 10 seconds.
When the process running the job has died but agy or the check command still runs, `cancel` stops them itself.
A cancelled job stays cancelled: a rerun exits 27 without a launch, so to do the work anyway run it under a new job id.
Replies already received are kept.

### Job states

| State | Meaning |
|-------|---------|
| `queued` | a background worker was started; agy has not been launched yet |
| `running` | agy is running |
| `checking` | the check command is running |
| `done` | a reply passed its checks; `answer.txt` holds the answer |
| `failed` | gave up (exit 20); the replies are kept |
| `quota` | a quota hold stopped the job |
| `check-error` | the check itself failed to run |
| `no-conversation` | a follow-up was needed but no conversation id was captured |
| `fatal` | agy is missing or not signed in, the model is unknown, or agy's output was not a JSON envelope |
| `cancelled` | stopped by `cancel` |
| `interrupted` | the process running the job died and the saved output did not settle the turn; the rerun that found it restarts the turn |

```

In "## Where replies are", after the line `When a call fails, read \`reply.txt\` and \`agy.err\` of the last attempt to see what agy actually said.`, add:

```markdown
`check.json` in the attempt folder records the check command's process while it runs.
A cancelled job has `cancel.request`, and a background job has `background.json` (the arguments its worker passes to `call()`) and `worker.log`, beside `request.json`.
When the process running a job died, the next run of the same job reads the saved `agy.out` before launching anything.
A complete reply found there is checked as if it had just arrived, but agy's exit status was lost, so it is accepted only by a check you asked for and no follow-up is sent after it.
Output that is not a complete reply restarts the turn.
```

In "## What to do per exit", at the end of the bullet that starts `- 20:`, add the sentence ` After a stop because agy answered in another conversation, a rerun sends nothing; start a new job.`
After the bullet that starts `- 25:`, add:

```markdown
- 27: the job was cancelled and stays cancelled; its replies are kept under `attempts/`; to do the work anyway, run it under a new job id.
```

- [ ] **Step 2: Update `README.md`**

After the bullet `- It can check answers for you, for example requiring valid JSON with certain fields, and sends a reply back when the format is wrong.`, add:

```markdown
- A call can run in the background; `agy-call status`, `result` and `cancel` show how it is going, print its answer and stop it.
```

- [ ] **Step 3: Check and commit**

Run: `grep -c "$(printf '\342\200\224')" README.md skills/agy-call/SKILL.md docs/superpowers/plans/2026-11-12-agy-plugin-step2-job-control.md`; expected: `0` for each.
Run: `grep -rn "status\|result\|cancel" skills/agy-call/SKILL.md | head -40` and read the new lines once for one sentence per line.
Run: `uvx pytest -q tests/`; expected: all pass (178 tests).

```bash
git add README.md skills/agy-call/SKILL.md
printf '%s\n' "Document background runs, status, result, cancel and job states" "" "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" "Claude-Session: https://claude.ai/code/session_013coVpN7if3z6kaMYLTrz3c" > "$TMPDIR/agycall-msg.txt"
git commit -F "$TMPDIR/agycall-msg.txt"
```

---

## Self-review

- Spec coverage: 5.2 `cancelled` row (Task 3); 5.4 states `queued` (Task 5), `checking` and `interrupted` (Task 2), `cancelled` (Task 3); 7.1 background worker (Task 5); 7.2 conversation-conflict stop (Task 2); 7.3 `status`, `result`, `cancel`, the four cancel points, ownership files for agy and the check, cancel of an orphaned group (Tasks 1, 3, 4); 7.4 recovery under a non-blocking lock, a complete envelope as `reply-with-error`, a cancel request or passed deadline during recovery, a check that died re-run on the saved reply, `running (pid unknown)` shown as `agy pid unknown` (Tasks 2, 3, 4); exit 27 (Tasks 1, 3).
  Spec 10.2 cancel cases: during a run (Tasks 3, 4), during a check (Task 3), worker dead and agy alive (Task 4), finishing before the cancel (Task 4), a repeated cancel (Task 4).
  Spec 10.2 recovery cases: complete envelope (Task 2), incomplete output (Task 2), a check process still alive and one that died without a result (Task 2), a cancel request found during recovery (Task 3).
  Not in this step by design: `status --all` and the repository store (step 3), `setup-error` naming for tasks (step 3), the cancel-request rename on `--continue` (step 3).
- Placeholders: none; every code step has the complete code or an exact old/new pair taken from the Task 2 file.
- Names used across tasks: `pending`, `_prepare`, `_admit`, `_drive`, `_settle(c, att, oc, recovered=False)`, `_check(c, att, text)` becoming `_check(c, att, text, res)` in Task 3 with both call sites edited, `_cancel(c, att=None, res=None)`, `CANCELLED`, `c.should_stop`, `enqueue`, `control.start`, `control.work`, `control.status/result/cancel(store, job_id=None, ...)`, `agy.owns`, `agy.stop_group(pgid, reap=None)`.
- Review Focus: each of the five lines has its test in the named task.

## After this plan

- Build step 3 (worktrees and `task`) reuses `_prepare`/`_admit`/`_settle`, `control.start`/`work` and the cancel points, adds the repository store, `--continue` segments with the cancel-request rename, and the task column of spec 5.2.
- Nothing in this plan pushes; pushing the branch is the user's call.
