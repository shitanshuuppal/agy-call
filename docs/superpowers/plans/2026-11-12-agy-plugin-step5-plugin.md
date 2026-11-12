# agy plugin, build step 5: plugin surface, `agy-call setup` and the session hook Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The repository becomes the Claude Code plugin `agy` (version 0.2.0) with seven slash commands, a forwarding subagent, a rewritten skill, a SessionStart hook that records the Claude session, `agy-call setup`, a README for the new install and migration, and the 0.2.0 release notes.

**Architecture:** `agycall/setup.py` probes agy with `--version`, `--help` and `models` (never a prompt) and is wired into `agycall/cli.py` as `agy-call setup` through insertions at anchors that build steps 3 and 4 leave untouched.
`hooks/session_start.py` appends `export AGYCALL_SESSION=<id>` to `$CLAUDE_ENV_FILE` and is registered in `hooks/hooks.json` beside the existing guard hook.
The plugin surface is Markdown: `commands/*.md` tell Claude which `agy-call` command to run and what to do with its output, `agents/agy.md` forwards exactly one command, and `skills/agy-call/SKILL.md` is the full reference; `tests/test_plugin.py` pins the manifests, the front matter and the shipped-text rules, and `claude plugin validate` checks the layout.

**Tech Stack:** Python 3.9+ standard library only in `agycall/` and `hooks/`; tests with pytest run as `uvx pytest -q tests/`; `claude plugin validate` (Claude Code 2.1.296 was used for the dry run; it is a local check with no network).

**Spec:** `docs/superpowers/specs/2026-11-12-agy-plugin-design.md`, sections 3.2 (plugin surface), 3.3 (where files live, documented), 4.1 (what the grant guarantees), 4.6 (blocked), 4.8 (hand-back and merge), 9 (setup), the 10.4 results, 11 (migration) and 13 (exit codes).
This is build step 5 of section 12.
It applies on top of build steps 3 (`docs/superpowers/plans/2026-11-12-agy-plugin-step3-tasks.md`) and 4 (`docs/superpowers/plans/2026-11-12-agy-plugin-step4-reviews.md`), whose command lines the commands, the skill and the README describe; Tasks 1 to 3 also apply cleanly to main without them (dry-run below).
The controller's decisions in `docs/superpowers/plans/notes-step5.md` bind this step and win where they differ from the spec.

## Global Constraints

- Python 3.9+ standard library only in agycall/ (and in hooks/).
- macOS and Linux only.
- Never the em dash character (U+2014) or the word "honest" in any file.
- One sentence per line in long Markdown.
- Public repo, so no personal paths or private data.
- Tests never run real agy and never read the real ~/.gemini config (every test that reaches agy uses the `fake` fixture or sets `AGYCALL_AGY` to a script of its own; `agy-call setup` is never run by hand against the real agy in this step).
- A PreToolUse hook refuses Bash commands whose text contains the old wrapper's delegate or job script names, or the word agy followed by -p/--print/-i, so implementers write files with the Write/Edit tools and commit with `git commit -F "$TMPDIR/agycall-msg.txt"`.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_013coVpN7if3z6kaMYLTrz3c`.
- Never run the `agy-call` found on PATH (old installed code), use `python3 bin/agy-call` or `python3 -m agycall.cli`.
- `run`, `batch`, `task` and `review` behaviour stays unchanged: this step adds `setup` and changes no other code path, and every existing test passes without edits.
- Every test that starts a process bounds it (`timeout=30` on `subprocess.run`, or `setup.TIMEOUT` set to 1), and the suite is run three times at the end of each task.

## Review Focus

- `agy --version` reports a two-digit minor version or a prefix (`agy 1.10.0`): it is compared as numbers and passes (Task 1, `test_a_newer_minor_version_is_compared_as_a_number`).
- `agy models` hangs because there is no network: each probe is stopped after `TIMEOUT`, its line says `no answer within`, and setup exits 1 instead of hanging (Task 1, `test_an_agy_that_does_not_answer_is_stopped_after_the_timeout`).
- `AGYCALL_AGY` names a file that exists but is not executable: each probe prints a `FAIL ... could not start it` line and setup exits 1 without a traceback (Task 1, `test_an_agy_that_is_not_executable_fails_without_a_traceback`).
- `agy models` lists only a longer name such as `gemini-4-argon-high-preview`: names are compared whole, so the model check fails (Task 1, `test_a_model_listed_only_under_a_longer_name_is_not_listed`).
- A session id with quotes, `$(...)` or backticks: the env-file line is quoted, so sourcing it sets the literal id and runs nothing (Task 2, `test_a_session_id_with_shell_characters_is_quoted`).

## Rulings made by this plan

- `/agy:task` always passes `--background` to `agy-call task`, as notes-step5.md gives the command, so a task survives the session and is not cut off by the 10-minute limit of a Bash call.
  The `--background` of the slash command (spec 3.2) then decides only whether Claude waits: without it, Claude polls `agy-call result JOB` (exit 25 while queued, running or checking) in a Bash call with `run_in_background: true` and continues with the hand-back; with it, Claude reports the job id and stops.
- `/agy:task` starts the task through the `agy` subagent (spec 3.2); `/agy:review`, `/agy:adversarial-review`, `/agy:status`, `/agy:result`, `/agy:cancel` and `/agy:setup` run their one `agy-call` command with Bash directly, as notes-step5.md describes them.
  A foreground review runs with `run_in_background: true`, because a review turn may run 20 minutes and a Bash call 10.
- All seven commands carry `disable-model-invocation: true`, so Claude cannot start one on its own; a request in plain words goes through the skill, whose description now says "Use only when the user asks".
- The commands call `agy-call` by name (the plugin puts its `bin/` on the PATH), not `${CLAUDE_PLUGIN_ROOT}/bin/agy-call`: the plugin cache path contains a folder named `agy`, and the guard hook refuses a command line that has `/agy/` followed later by a `-p` or `-i` token, which focus text can contain.
- `/agy:task` pre-approves only `agy-call`, `git status` and `git diff` among Bash commands; commits and the user's build and test commands go through the normal permission prompts.
- The `agy` subagent uses `model: haiku`, since it only forwards one command and returns its output.
- `agy-call setup` reads `--help` from stdout and stderr together and takes the first word of each stdout line of `agy models` as a model name; both match agy 1.5.6 as measured for this plan (help on stderr with exit 0; models as `name<TAB>label` lines on stdout, with "Fetching available models..." on stderr).
  When agy cannot be found, the probes are not run; otherwise every probe runs even after another failed, each with a 30-second timeout.
- `setup` prints its report and returns its code before the shared `try` block of `cli.main`, and the module docstring of `cli.py` is left as it is, because steps 3 and 4 each rewrite those lines; argparse lists `setup` among the subcommands anyway.
- The no-em-dash and banned-word test covers the shipped files only (README, release notes, commands, agents, skills, hooks, manifests), because the plan files quote the banned word in their own constraints.
- The README's migration section also tells manual installers to remove the guard hook's entry from `~/.claude/settings.json` (their install added it), so the guard is not run twice.

## File map

| File | Change | Responsibility |
|---|---|---|
| `agycall/setup.py` | create | the checks of spec 9, one line each, exit 0 or 1 |
| `agycall/cli.py` | modify | `agy-call setup` (three insertions) |
| `tests/fake_agy.py` | modify | answers `--version`, `--help` and `models` |
| `tests/test_setup.py` | create | a passing setup and each failing check |
| `hooks/session_start.py` | create | `export AGYCALL_SESSION=<id>` into `$CLAUDE_ENV_FILE` |
| `hooks/hooks.json` | modify | registers the SessionStart hook beside the guard |
| `tests/test_session_start.py` | create | the hook protocol and its failure cases |
| `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json` | modify | plugin `agy` 0.2.0 in the marketplace `agy-call` |
| `commands/task.md`, `review.md`, `adversarial-review.md`, `status.md`, `result.md`, `cancel.md`, `setup.md` | create | the slash commands |
| `agents/agy.md` | create | the forwarding subagent |
| `tests/test_plugin.py` | create | manifests, front matter, shipped-text rules, skill and README checks |
| `skills/agy-call/SKILL.md` | rewrite | the reference, organised around the commands |
| `README.md` | rewrite | install, migration, commands, the grant's guarantee, requirements |
| `docs/release-notes-0.2.0.md` | create | the 0.2.0 release notes the controller publishes |

## Dry run

Every file of this plan was written into a scratch copy of main (`d206db4`, steps 1 and 2 merged) outside the repository, and there:
`uvx pytest -q tests/` passed three times in a row (219 tests: 194 on main plus the 25 of this plan), no `agycall.cli worker` process outlived the suite, `claude plugin validate .` passed with no advice once the README was rewritten, and `claude plugin validate --strict commands`, `agents` and `skills` passed.
The three `cli.py` anchors and the `fake_agy.py` probe block appear in no old text of the step 3 or step 4 plans, so the edits apply before or after those steps.

---

### Task 1: `agy-call setup`

**Files:**
- Create: `agycall/setup.py`, `tests/test_setup.py`
- Modify: `agycall/cli.py` (three insertions), `tests/fake_agy.py` (the probe block)

**Interfaces:**
- Consumes: `agy.path()` from step 1 (`AGYCALL_AGY`, else `agy` on the PATH; raises `errors.Fatal(13, "agy not found (set AGYCALL_AGY or put agy on PATH)")`), `errors.Fatal.reason`, and the `fake` fixture of `tests/conftest.py` (sets `AGYCALL_AGY` to the stand-in and returns `(set_turns, launches)`).
- Produces:
  - `setup.check() -> (report: str, exit_code: int)`: one line per check, each starting `ok   ` or `FAIL `, exit code 0 when every line is `ok`, else 1.
  - `setup.TIMEOUT` (30, seconds per probe), `setup.MIN_AGY` (`(1, 5, 6)`), `setup.FLAGS`, `setup.MODELS`.
  - `agy-call setup` on the command line, printing the report and exiting with its code.
  - The stand-in answers `--version`, `--help` and `models`, varied by `FAKE_VERSION`, `FAKE_HELP_DROP`, `FAKE_MODELS` and `FAKE_MODELS_FAIL`.

- [ ] **Step 1: Teach the stand-in the setup probes**

Before you start, run `uvx pytest -q tests/ | tail -1` and note the number of tests (M); the task ends with M + 9.

In `tests/fake_agy.py`, replace:

```python
if "-p" not in args:  # --help, --version and models probes
    print("agy stand-in")
    sys.exit(0)
```

with:

```python
if "-p" not in args:  # the probes of agy-call setup; FAKE_VERSION, FAKE_HELP_DROP, FAKE_MODELS, FAKE_MODELS_FAIL vary them
    if args == ["--version"]:
        print(os.environ.get("FAKE_VERSION", "1.5.6"))
    elif args == ["--help"]:  # real agy prints its help on stderr
        drop = os.environ.get("FAKE_HELP_DROP")
        flags = [f for f in ("--settings", "--json-schema", "--effort", "--output-format") if f != drop]
        sys.stderr.write("Usage of agy:\n" + "".join("  %s  stand-in flag\n" % f for f in flags))
    elif args == ["models"]:
        if os.environ.get("FAKE_MODELS_FAIL"):
            sys.stderr.write("Error: unauthenticated; run agy once to sign in\n")
            sys.exit(1)
        sys.stderr.write("Fetching available models...\n")
        for name in os.environ.get("FAKE_MODELS", "gemini-4-argon-high gemini-3.8-flash-low").split():
            print("%s\t%s" % (name, name))
    else:
        print("agy stand-in")
    sys.exit(0)
```

The stand-in prints its help on stderr because agy 1.5.6 does, so the tests prove that setup reads stderr.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_setup.py`:

```python
import time

from agycall import cli, setup


def run_setup(capsys):
    code = cli.main(["setup"])
    return code, capsys.readouterr().out


def failed(out):
    return [line for line in out.splitlines() if line.startswith("FAIL")]


def test_setup_passes_with_a_signed_in_agy_and_sends_no_prompt(fake, capsys):
    _, launches = fake
    code, out = run_setup(capsys)
    assert code == 0, out
    assert failed(out) == []
    lines = out.splitlines()
    assert len(lines) == 8
    assert lines[3] == "ok   agy version 1.5.6 (1.5.6 or newer)"
    assert lines[4] == "ok   agy --help lists --settings, --json-schema, --effort, --output-format"
    assert lines[5] == "ok   agy models (agy is signed in)"
    assert lines[6:] == ["ok   model gemini-4-argon-high listed", "ok   model gemini-3.8-flash-low listed"]
    assert launches() == []


def test_an_old_agy_fails_the_version_check(fake, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_VERSION", "1.5.5")
    code, out = run_setup(capsys)
    assert code == 1
    assert failed(out) == ["FAIL agy version 1.5.5 (1.5.6 or newer)"]


def test_a_newer_minor_version_is_compared_as_a_number(fake, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_VERSION", "agy 1.10.0")
    code, out = run_setup(capsys)
    assert code == 0, out
    assert "ok   agy version 1.10.0 (1.5.6 or newer)" in out.splitlines()


def test_a_help_without_one_flag_fails_the_flag_check(fake, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_HELP_DROP", "--json-schema")
    code, out = run_setup(capsys)
    assert code == 1
    assert failed(out) == ["FAIL agy --help does not list --json-schema; update agy"]


def test_a_failing_agy_models_fails_the_login_check(fake, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_MODELS_FAIL", "1")
    code, out = run_setup(capsys)
    assert code == 1
    assert failed(out) == ["FAIL agy models (needs a signed-in agy): Error: unauthenticated; run agy once to sign in"]
    assert "model gemini" not in out


def test_a_model_listed_only_under_a_longer_name_is_not_listed(fake, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_MODELS", "gemini-4-argon-high-preview gemini-3.8-flash-low")
    code, out = run_setup(capsys)
    assert code == 1
    assert failed(out) == ["FAIL model gemini-4-argon-high not listed by agy models"]


def test_a_missing_agy_is_one_fail_line(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AGYCALL_AGY", str(tmp_path / "no-such-agy"))
    code, out = run_setup(capsys)
    assert code == 1
    assert failed(out) == ["FAIL agy not found (set AGYCALL_AGY or put agy on PATH)"]


def test_an_agy_that_is_not_executable_fails_without_a_traceback(tmp_path, monkeypatch, capsys):
    exe = tmp_path / "agy"
    exe.write_text("not a program\n")
    exe.chmod(0o644)
    monkeypatch.setenv("AGYCALL_AGY", str(exe))
    code, out = run_setup(capsys)
    assert code == 1
    assert [line.split(":")[0] for line in failed(out)] == ["FAIL agy --version", "FAIL agy --help", "FAIL agy models (needs a signed-in agy)"]
    assert "could not start it" in out


def test_an_agy_that_does_not_answer_is_stopped_after_the_timeout(tmp_path, monkeypatch, capsys):
    exe = tmp_path / "agy"
    exe.write_text("#!/bin/sh\nexec sleep 30\n")
    exe.chmod(0o755)
    monkeypatch.setenv("AGYCALL_AGY", str(exe))
    monkeypatch.setattr(setup, "TIMEOUT", 1)
    began = time.monotonic()
    code, out = run_setup(capsys)
    assert time.monotonic() - began < 20
    assert code == 1
    assert len(failed(out)) == 3
    assert "no answer within 1s" in out
```

- [ ] **Step 3: Run them to see them fail**

Run: `uvx pytest -q tests/test_setup.py`
Expected: FAIL at collection with `ImportError: cannot import name 'setup' from 'agycall'`.

- [ ] **Step 4: Create `agycall/setup.py`**

```python
"""agy-call setup (spec 9): check that this machine can run agy-call, without sending a prompt or spending quota.
It prints one line per check, "ok" or "FAIL", and exits 0 only when every check passes.
Tests: uvx pytest tests/test_setup.py"""
import platform
import re
import subprocess
import sys

from . import agy
from .errors import Fatal

MIN_AGY = (1, 5, 6)
FLAGS = ("--settings", "--json-schema", "--effort", "--output-format")
MODELS = ("gemini-4-argon-high", "gemini-3.8-flash-low")  # the model names the skill and the slash commands use
TIMEOUT = 30  # seconds for each agy probe; `agy models` needs the network


def _probe(exe, *args):
    """(exit status, stdout, stderr) of one agy probe; the exit status is None, and stderr says why, when agy could
    not be started or gave no answer within TIMEOUT."""
    try:
        r = subprocess.run([exe, *args], stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8",
                           errors="replace", timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return None, "", "no answer within %ds" % TIMEOUT
    except OSError as e:
        return None, "", "could not start it: %s" % e
    return r.returncode, r.stdout, r.stderr


def _said(code, out, err):
    """The last line agy printed, or its exit status, for a FAIL line."""
    text = (err.strip() or out.strip()).splitlines()
    return text[-1][:200] if text else "exit status %s" % code


def check():
    """(report, exit code) for `agy-call setup`."""
    lines = []

    def line(ok, text):
        lines.append(("ok   " if ok else "FAIL ") + text)

    line(sys.version_info >= (3, 9), "Python %s (3.9 or newer)" % platform.python_version())
    line(sys.platform == "darwin" or sys.platform.startswith("linux"), "operating system %s (macOS or Linux)" % sys.platform)
    try:
        exe = agy.path()
    except Fatal as e:
        line(False, e.reason)
        return "\n".join(lines), 1
    line(True, "agy found at %s" % exe)

    code, out, err = _probe(exe, "--version")
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", out) if code == 0 else None
    if m:
        version = tuple(int(x) for x in m.groups())  # compared as numbers, so 1.10.0 is newer than 1.5.6
        line(version >= MIN_AGY, "agy version %d.%d.%d (1.5.6 or newer)" % version)
    else:
        line(False, "agy --version: %s" % _said(code, out, err))

    code, out, err = _probe(exe, "--help")  # agy prints its help on stderr
    missing = [f for f in FLAGS if not re.search(r"(?<![\w-])%s(?![\w-])" % re.escape(f), out + err)]
    if code is None:
        line(False, "agy --help: %s" % err)
    else:
        line(not missing, "agy --help lists %s" % ", ".join(FLAGS) if not missing
             else "agy --help does not list %s; update agy" % ", ".join(missing))

    code, out, err = _probe(exe, "models")
    if code != 0:
        line(False, "agy models (needs a signed-in agy): %s" % _said(code, out, err))
    else:
        line(True, "agy models (agy is signed in)")
        listed = {row.split()[0] for row in out.splitlines() if row.split()}
        for name in MODELS:
            line(name in listed, "model %s %s" % (name, "listed" if name in listed else "not listed by agy models"))
    return "\n".join(lines), 0 if all(row.startswith("ok") for row in lines) else 1
```

- [ ] **Step 5: Add `agy-call setup` to `agycall/cli.py`**

Steps 3 and 4 rewrite the docstring, the `from . import control` line, the `task` and `review` subparsers and the body of the `try` block; these three insertions sit next to lines that neither step changes.

In `agycall/cli.py`, replace:

```python
from .runner import call
```

with:

```python
from .runner import call
from . import setup
```

In `agycall/cli.py`, replace:

```python
    b.add_argument("--workers", type=int, default=4)
    _common(b)
```

with:

```python
    b.add_argument("--workers", type=int, default=4)
    _common(b)
    sub.add_parser("setup")  # checks agy and this machine without sending a prompt
```

In `agycall/cli.py`, replace:

```python
    if a.cmd == "batch":
        return _batch(a)
```

with:

```python
    if a.cmd == "batch":
        return _batch(a)
    if a.cmd == "setup":
        text, code = setup.check()
        print(text)
        return code
```

- [ ] **Step 6: Run the tests**

Run: `uvx pytest -q tests/test_setup.py`
Expected: 9 passed.
Run: `for i in 1 2 3; do uvx pytest -q tests/ || break; done`
Expected: every run passes (M + 9 tests).

- [ ] **Step 7: Commit**

```bash
git add agycall/setup.py agycall/cli.py tests/fake_agy.py tests/test_setup.py
printf '%s\n' "Add agy-call setup" \
  "" \
  "agy-call setup checks Python, the operating system, the agy executable," \
  "its version, the flags agy-call passes, and that agy models succeeds and" \
  "lists the models the commands use, without sending a prompt. One line" \
  "per check; exit 0 only when all pass. Each probe has a 30-second timeout." \
  "" \
  "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" "Claude-Session: https://claude.ai/code/session_013coVpN7if3z6kaMYLTrz3c" > "$TMPDIR/agycall-msg.txt"
git commit -F "$TMPDIR/agycall-msg.txt"
```

---

### Task 2: The SessionStart hook

**Files:**
- Create: `hooks/session_start.py`, `tests/test_session_start.py`
- Modify: `hooks/hooks.json` (whole file)

**Interfaces:**
- Consumes: Claude Code's SessionStart hook input on stdin (a JSON object with `session_id`) and the `CLAUDE_ENV_FILE` variable, whose `export` lines Claude Code applies to the session's later Bash commands; `control._session()` and the runner already read `AGYCALL_SESSION` (step 2).
- Produces: one line `export AGYCALL_SESSION=<shlex-quoted id>` appended to `$CLAUDE_ENV_FILE`; exit 0 always.

- [ ] **Step 1: Write the failing tests**

Before you start, run `uvx pytest -q tests/ | tail -1` and note the number of tests (M); the task ends with M + 10.

Create `tests/test_session_start.py`:

```python
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
```

- [ ] **Step 2: Run them to see them fail**

Run: `uvx pytest -q tests/test_session_start.py`
Expected: FAIL; the hook runs end with exit 2 (`can't open file ... session_start.py`), and the `hooks.json` test with `KeyError: 'SessionStart'`.

- [ ] **Step 3: Create `hooks/session_start.py`**

```python
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
```

- [ ] **Step 4: Register it in `hooks/hooks.json`**

Replace the whole file with:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          { "type": "command", "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/hooks/session_start.py\"", "timeout": 5 }
        ]
      }
    ],
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          { "type": "command", "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/hooks/agy_guard.py\"", "timeout": 5 }
        ]
      }
    ]
  }
}
```

- [ ] **Step 5: Run the tests**

Run: `uvx pytest -q tests/test_session_start.py tests/test_guard.py`
Expected: all pass.
Run: `for i in 1 2 3; do uvx pytest -q tests/ || break; done`
Expected: every run passes (M + 10 tests).

- [ ] **Step 6: Commit**

```bash
git add hooks/session_start.py hooks/hooks.json tests/test_session_start.py
printf '%s\n' "Record the Claude session of each job with a SessionStart hook" \
  "" \
  "The hook appends export AGYCALL_SESSION=<session id> to CLAUDE_ENV_FILE," \
  "quoted for the shell, so jobs record the session that started them. It" \
  "writes nothing and still exits 0 when the input or the file is missing." \
  "" \
  "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" "Claude-Session: https://claude.ai/code/session_013coVpN7if3z6kaMYLTrz3c" > "$TMPDIR/agycall-msg.txt"
git commit -F "$TMPDIR/agycall-msg.txt"
```

---

### Task 3: The plugin `agy`: manifests, slash commands and the subagent

**Files:**
- Modify: `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json` (whole files)
- Create: `commands/task.md`, `commands/review.md`, `commands/adversarial-review.md`, `commands/status.md`, `commands/result.md`, `commands/cancel.md`, `commands/setup.md`, `agents/agy.md`, `tests/test_plugin.py`

**Interfaces:**
- Consumes: the command lines of `agy-call task` (step 3: `--job`, `--model`, `--effort`, `--allow` repeated, `--check-cmd`, `--check-timeout`, `--background`, `--prompt-file`, `--continue`, `--merge JOB`, `--cleanup JOB`), `agy-call review` (step 4: `--model`, `--effort`, `--scope`, `--base`, `--adversarial`, `--focus`, `--background`), `agy-call status [JOB] [--all]`, `result [JOB]`, `cancel [JOB]` (steps 2 and 3), `agy-call setup` (Task 1); `result` exits 25 while a job is queued, running or checking (step 2).
- Produces: the plugin `agy`, the commands `/agy:task`, `/agy:review`, `/agy:adversarial-review`, `/agy:status`, `/agy:result`, `/agy:cancel`, `/agy:setup`, and the subagent `agy:agy`; `tests/test_plugin.py` with `ROOT`, `COMMANDS`, `read(*parts)` and `front_matter(*parts)`, which Task 4 appends to.

The command files take their structure from the Codex plugin's commands (front matter with `description`, `argument-hint`, `disable-model-invocation` and `allowed-tools`, then the instructions); their text is written for agy-call.

- [ ] **Step 1: Write the failing tests**

Before you start, run `uvx pytest -q tests/ | tail -1` and note the number of tests (M); the task ends with M + 4.

Create `tests/test_plugin.py`:

```python
import glob
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMMANDS = ("task", "review", "adversarial-review", "status", "result", "cancel", "setup")


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def front_matter(*parts):
    """The `key: value` lines between the opening and closing `---` lines, as a dict."""
    text = read(*parts)
    assert text.startswith("---\n"), parts
    head = text.split("---\n", 2)[1]
    return dict(line.split(": ", 1) for line in head.splitlines() if ": " in line)


def test_the_plugin_is_named_agy_in_the_agy_call_marketplace():
    plugin = json.loads(read(".claude-plugin", "plugin.json"))
    assert (plugin["name"], plugin["version"]) == ("agy", "0.2.0")
    market = json.loads(read(".claude-plugin", "marketplace.json"))
    assert market["name"] == "agy-call"
    assert [(p["name"], p["source"]) for p in market["plugins"]] == [("agy", "./")]


def test_each_command_has_its_front_matter_and_runs_only_when_the_user_asks():
    assert sorted(os.listdir(os.path.join(ROOT, "commands"))) == sorted(c + ".md" for c in COMMANDS)
    for name in COMMANDS:
        fm = front_matter("commands", name + ".md")
        assert {"description", "argument-hint", "allowed-tools", "disable-model-invocation"} <= set(fm), name
        assert "only when the user asks" in fm["description"], name
        assert fm["disable-model-invocation"] == "true", name
        assert "Bash(agy-call:*)" in fm["allowed-tools"], name


def test_the_agent_forwards_with_bash_only_and_only_when_the_user_asks():
    fm = front_matter("agents", "agy.md")
    assert fm["name"] == "agy"
    assert fm["tools"] == "Bash"
    assert "only when the user asks" in fm["description"]


def test_the_shipped_text_has_no_em_dash_and_no_banned_word():
    banned = re.compile(r"\b" + "hon" + "est", re.I)  # the word is spelled in two parts so this file passes its own rule
    paths = ["README.md"] + [p for pattern in ("docs/release-notes-*.md", "commands/*.md", "agents/*.md", "skills/*/SKILL.md",
                                               "hooks/*", ".claude-plugin/*.json")
                             for p in glob.glob(os.path.join(ROOT, pattern)) if os.path.isfile(p)]
    for path in paths:
        text = read(path)
        assert "\u2014" not in text, path
        assert not banned.search(text), path
```

- [ ] **Step 2: Run them to see them fail**

Run: `uvx pytest -q tests/test_plugin.py`
Expected: FAIL in `test_the_plugin_is_named_agy_in_the_agy_call_marketplace` (`('agy-call', '0.1.0') != ('agy', '0.2.0')`), the command test (`FileNotFoundError` for `commands`) and the agent test (`FileNotFoundError` for `agents/agy.md`); the shipped-text test passes already.

- [ ] **Step 3: Rename the plugin**

Replace the whole of `.claude-plugin/plugin.json` with:

```json
{
  "name": "agy",
  "version": "0.2.0",
  "description": "Hand coding tasks and code reviews to Gemini through the agy (Antigravity) CLI from Claude Code, with slash commands, background jobs, a per-task permission grant in its own git worktree, and one runner that saves every reply, resumes the same conversation when a reply is unusable, and stops all callers on a quota error.",
  "author": {
    "name": "Shitanshu Uppal"
  },
  "homepage": "https://github.com/shitanshuuppal/agy-call",
  "repository": "https://github.com/shitanshuuppal/agy-call",
  "license": "MIT",
  "keywords": ["antigravity", "agy", "gemini", "quota", "retry", "claude-code"]
}
```

Replace the whole of `.claude-plugin/marketplace.json` with:

```json
{
  "$schema": "https://anthropic.com/claude-code/marketplace.schema.json",
  "name": "agy-call",
  "description": "agy-call: Gemini coding tasks and reviews from Claude Code through the agy (Antigravity) CLI, with a safe runner for every agy call.",
  "owner": {
    "name": "Shitanshu Uppal"
  },
  "plugins": [
    {
      "name": "agy",
      "description": "Slash commands for Gemini coding tasks in their own git worktree, reviews and adversarial reviews, with background jobs; every reply is saved, unusable replies are resumed in the same conversation, and a quota error stops all callers.",
      "source": "./",
      "category": "development",
      "keywords": ["antigravity", "agy", "gemini", "quota", "retry"]
    }
  ]
}
```

- [ ] **Step 4: Create the slash commands**

Create `commands/task.md`:

````markdown
---
description: Hand a coding task to Gemini through agy, in its own git worktree and branch. Runs only when the user asks for Gemini or agy.
argument-hint: "[--background] [--continue JOB] [--model M] [--effort E] <task text>"
disable-model-invocation: true
allowed-tools: Bash(agy-call:*), Bash(git status:*), Bash(git diff:*), Read, Write, Glob, Grep, Agent
---

Hand the user's coding task to Gemini with `agy-call task`.
The user asked for Gemini by running this command; it does not make Gemini the default for other work.

Raw arguments:
$ARGUMENTS

## Read the arguments

- `--background`: start the task and report its job id without waiting for it.
- `--continue JOB`: send a new message to the existing task JOB instead of starting a new task.
- `--model M` [`gemini-4-argon-high`] and `--effort E` [not passed]: the agy model and reasoning effort of a new task; a continued task keeps the ones it started with.
- Everything else is the task text.
  If there is none, ask the user what Gemini should do, and stop.

## Prepare

1. For a new task, run `git status --porcelain` in the repository.
   `agy-call task` refuses to start (exit 26) while it lists anything, so if it does, show the user the files, ask whether to commit, stash or ignore them, and stop.
2. Choose the job id of a new task: a short slug of the task in lowercase letters, digits and hyphens, then the date and time, for example `fix-login-redirect-20261112-1430`.
   For `--continue`, the job id is JOB.
3. Write the prompt file in your scratch directory, or in `$TMPDIR` when you have none, never inside the repository: a new file there makes the checkout unclean, and the task or its merge refuses with exit 26.
   It holds the task text, plus whatever from this conversation the task needs (files, the behaviour wanted, constraints), written for a reader who has not seen the conversation.
   Do not add instructions about commits, branches, permitted commands or the reply format; agy-call adds those.
4. Choose the `--allow` entries: the build, test, lint and read commands of this repository that Gemini needs, taken from its own files (README, Makefile, `package.json` scripts, `Package.swift`, `pyproject.toml`, `Cargo.toml`, CI workflows).
   Each entry is one command without newlines or parentheses, for example `--allow 'swift build' --allow 'swift test'`.
   `ls`, `cat`, `head`, `tail`, `wc`, `grep`, `rg`, `git status`, `git diff`, `git log` and `git show` are always allowed; do not repeat them.
   Never allow a command that reaches outside the repository: network access, package installs, deletion outside the worktree, `git commit`, `git push`, or anything that reads credentials.
5. Choose the `--check-cmd`: the command that builds the repository and runs its tests, for example `swift build && swift test`, or `true` when there is nothing to build or test.
   It runs in the task's worktree after every turn; a failure goes back to Gemini as a fix turn.
   Add `--check-timeout S` when the build and tests take longer than 600 seconds.
6. Tell the user in one or two lines which `--allow` entries and which check you chose.

## Start

Use the Agent tool with `subagent_type: "agy:agy"` and give it exactly one command, with each value in single quotes.

A new task:

```
agy-call task --job JOB --model gemini-4-argon-high [--effort E] --check-cmd 'CHECK' [--check-timeout S] --allow 'CMD' ... --background --prompt-file 'FILE'
```

A continued task:

```
agy-call task --continue --job JOB [--allow 'CMD'] ... --background --prompt-file 'FILE'
```

agy-call always runs the task in a detached worker (`--background`), so the task keeps running after this session ends and is not cut off by the time limit of a Bash call.
The subagent returns agy-call's output: the job id, or an error and its exit status.
On an error, show it to the user and stop: exit 26 names the files or the repository state to fix, exit 24 means the job id or its branch is taken (choose another id), and exit 2 is a bad argument.

## Wait, or not

- With `--background`: tell the user the job id, that `/agy:status` shows its progress and that `/agy:result JOB` prints the outcome, and stop.
- Otherwise run this with the Bash tool and `run_in_background: true`; `agy-call result` exits 25 while the job is queued, running or checking:

  ```
  until agy-call result JOB >/dev/null 2>&1; [ $? -ne 25 ]; do sleep 30; done
  ```

  When it finishes, run `agy-call result JOB` and continue below.

## When the task ends

The agy-call skill describes each case in full ("When a task ends", "Continuing a task" and "Hand-back and merge").

- `done` (exit 0): read the diff in the worktree (`git -C WORKTREE diff BASE` and the untracked files that `git -C WORKTREE status --porcelain` lists) and run the build and tests there yourself.
  When they pass and the change does what was asked, commit on `agy/JOB` in the worktree with the user's git identity and a message naming the job, run `agy-call task --merge JOB` from the main checkout, then `agy-call task --cleanup JOB`.
  Never push.
  When the change falls short, tell the user what you found and ask whether to send it back with `/agy:task --continue JOB <what to change>` or to fix it yourself.
- `blocked` (exit 28): `agy-call status JOB` shows the `NEED:` lines (`need`) and the refused action kinds (`denied`).
  When every command Gemini needs is a build, test or read command confined to this repository, continue the task: write a new prompt file that says those commands are now allowed and to carry on, and run the continued-task command with an `--allow` for each.
  Ask the user first for anything that reaches outside the repository: network access, deletion outside the worktree, package installs, `git push`, or credentials.
- `failed` (exit 20): show the findings and the last reply, and ask the user how to go on.
- Exit 10 (quota): show the time the hold ends; rerunning the same command after that time continues the task's conversation.
- Exit 11, 13, 14 or 22: suggest `/agy:setup`.
````

Create `commands/review.md`:

````markdown
---
description: Have Gemini review the current change of this repository through agy. Runs only when the user asks for a Gemini or agy review.
argument-hint: "[--background] [--scope auto|working-tree|branch] [--base REF] [--model M] [--effort E]"
disable-model-invocation: true
allowed-tools: Bash(agy-call:*)
---

Run a Gemini review of the repository's current change with `agy-call review`.
This command only reviews: do not edit files, and do not start a fix or another review.

Raw arguments:
$ARGUMENTS

If the arguments contain text that is not one of the options above, tell the user that focus text goes to `/agy:adversarial-review`, and stop.

Run this from the repository; the user's arguments come after the defaults, so a `--model` or `--effort` the user gives replaces the default:

```
agy-call review --model gemini-4-argon-high --effort high $ARGUMENTS
```

- Without `--background`, run it with the Bash tool and `run_in_background: true`, because a review can take longer than one Bash call may run, and wait until it finishes.
- With `--background`, run it in the foreground: agy-call takes the snapshot, starts a detached worker and prints the job id.
  Tell the user that `/agy:status` shows its progress and that `/agy:result JOB` prints the findings, and stop.

Show agy-call's output to the user unchanged: the verdict, the number of findings at each severity, one block per finding, and the path of the full JSON.
On exit 26 with "nothing to review", say that there is no change to review.
On any other non-zero exit, show the message; for exit 11, 13, 14 or 22 suggest `/agy:setup`.

Then stop.
What happens to the findings follows the user's own rules for review loops: do not fix anything, review again, or start another reviewer unless the user asks.
````

Create `commands/adversarial-review.md`:

````markdown
---
description: Have Gemini challenge the current change and its design choices through agy, with optional focus text. Runs only when the user asks for a Gemini or agy review.
argument-hint: "[--background] [--scope auto|working-tree|branch] [--base REF] [--model M] [--effort E] [focus text]"
disable-model-invocation: true
allowed-tools: Bash(agy-call:*)
---

Run an adversarial Gemini review of the repository's current change with `agy-call review --adversarial`.
Gemini is told to assume the change has defects and to challenge the approach and the design choices as well as the code.
This command only reviews: do not edit files, and do not start a fix or another review.

Raw arguments:
$ARGUMENTS

Split the arguments into the options shown above, kept as given, and the focus text, which is everything else.
Run this from the repository, with the user's options after the defaults, so a `--model` or `--effort` the user gives replaces the default, and with the focus text in single quotes (write each `'` inside it as `'\''`); leave out `--focus` when there is no focus text:

```
agy-call review --adversarial --model gemini-4-argon-high --effort high OPTIONS --focus 'FOCUS TEXT'
```

- Without `--background`, run it with the Bash tool and `run_in_background: true`, because a review can take longer than one Bash call may run, and wait until it finishes.
- With `--background`, run it in the foreground: agy-call takes the snapshot, starts a detached worker and prints the job id.
  Tell the user that `/agy:status` shows its progress and that `/agy:result JOB` prints the findings, and stop.

Show agy-call's output to the user unchanged: the verdict, the number of findings at each severity, one block per finding, and the path of the full JSON.
On exit 26 with "nothing to review", say that there is no change to review.
On any other non-zero exit, show the message; for exit 11, 13, 14 or 22 suggest `/agy:setup`.

Then stop.
What happens to the findings follows the user's own rules for review loops: do not fix anything, review again, or start another reviewer unless the user asks.
````

Create `commands/status.md`:

````markdown
---
description: Show the agy-call jobs (Gemini tasks and reviews) of this repository, or one job in full. Runs only when the user asks for it.
argument-hint: "[JOB] [--all]"
disable-model-invocation: true
allowed-tools: Bash(agy-call:*)
---

Run this with the Bash tool from the current directory:

```
agy-call status $ARGUMENTS
```

Show its stdout and stderr to the user unchanged, with the exit status when it is not 0.
Do not summarise it, poll, or run anything else.
````

Create `commands/result.md`:

````markdown
---
description: Print the outcome of an agy-call job (a Gemini task's hand-back or a review's findings). Runs only when the user asks for it.
argument-hint: "[JOB]"
disable-model-invocation: true
allowed-tools: Bash(agy-call:*)
---

Run this with the Bash tool from the current directory:

```
agy-call result $ARGUMENTS
```

Show its stdout and stderr to the user unchanged, with the exit status when it is not 0.
Do not summarise it or run anything else.
When the output is the hand-back of a `done` task, offer to check, commit and merge it as "Hand-back and merge" in the agy-call skill describes, and start only when the user agrees.
````

Create `commands/cancel.md`:

````markdown
---
description: Stop a queued or running agy-call job (a Gemini task or review). Runs only when the user asks for it.
argument-hint: "[JOB]"
disable-model-invocation: true
allowed-tools: Bash(agy-call:*)
---

Run this with the Bash tool from the current directory:

```
agy-call cancel $ARGUMENTS
```

Show its stdout and stderr to the user unchanged, with the exit status when it is not 0.
Do not summarise it or run anything else.
````

Create `commands/setup.md`:

````markdown
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
````

- [ ] **Step 5: Create the subagent**

Create `agents/agy.md`:

````markdown
---
name: agy
description: Runs exactly one agy-call command (a Gemini task, review, status, result or cancel) and returns its output unchanged. Use only when the user asks for Gemini or agy, by slash command or in plain words; never on your own initiative.
model: haiku
tools: Bash
---

You forward one command to agy-call and do nothing else.

- The prompt gives one command line that starts with `agy-call `.
  Run it exactly as given, from the current directory, with one Bash call and a timeout of 600000 milliseconds.
- When the prompt has no such command, or more than one, run nothing and reply `no single agy-call command given`.
- Reply with the command's stdout and stderr unchanged, then one line `exit status: N`.
  This includes a failure, and the job id that a `--background` command prints.
- Do not read files, inspect the repository, retry, poll the status, fetch results, cancel jobs, or summarise or comment on the output.
````

- [ ] **Step 6: Run the tests and validate the plugin**

Run: `uvx pytest -q tests/test_plugin.py`
Expected: 4 passed.
Run, from the repository root: `claude plugin validate .`
Expected: `✔ Validation passed`; until Task 4 rewrites the README it also prints advice that the README has no line installing `agy`, which does not change the verdict.
Run: `for d in commands agents skills; do claude plugin validate --strict $d || break; done`
Expected: `✔ Validation passed` three times.
Run: `for i in 1 2 3; do uvx pytest -q tests/ || break; done`
Expected: every run passes (M + 4 tests).

- [ ] **Step 7: Commit**

```bash
git add .claude-plugin/plugin.json .claude-plugin/marketplace.json commands agents tests/test_plugin.py
printf '%s\n' "Rename the plugin to agy and add its slash commands and subagent" \
  "" \
  "The plugin is agy 0.2.0 in the agy-call marketplace. /agy:task," \
  "/agy:review, /agy:adversarial-review, /agy:status, /agy:result," \
  "/agy:cancel and /agy:setup run the matching agy-call command and run" \
  "only when the user asks; the agy subagent forwards one agy-call command" \
  "and returns its output unchanged." \
  "" \
  "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" "Claude-Session: https://claude.ai/code/session_013coVpN7if3z6kaMYLTrz3c" > "$TMPDIR/agycall-msg.txt"
git commit -F "$TMPDIR/agycall-msg.txt"
```

---

### Task 4: Skill rewrite, README and release notes

**Files:**
- Modify: `skills/agy-call/SKILL.md`, `README.md` (whole files), `tests/test_plugin.py` (append)
- Create: `docs/release-notes-0.2.0.md`

**Interfaces:**
- Consumes: Tasks 1 to 3; `tests/test_plugin.py`'s `ROOT`, `COMMANDS`, `read` and `front_matter` (Task 3); the skill and README text that steps 3 and 4 merged.
- Produces: user-facing docs that match the code; nothing later depends on their wording.

The skill and README below fold in the text that the docs tasks of steps 3 and 4 add, sentence for sentence, around the new command sections.

- [ ] **Step 1: Reconcile with the merged docs of steps 3 and 4**

Run: `git log --oneline -- skills/agy-call/SKILL.md README.md` and read the diffs of the step 3 and step 4 docs commits and of any later fix commit that touched these two files.
Where a merged sentence states a fact differently from the matching sentence in Step 4 or Step 5 below (a review fix round can change a default, a message or an exit code), keep the merged fact in the new text and list each such change in your report.

- [ ] **Step 2: Write the failing tests**

Run `uvx pytest -q tests/ | tail -1` and note the number of tests (M); the task ends with M + 2.

Append to `tests/test_plugin.py`, after two blank lines:

```python
def test_the_skill_is_used_only_when_the_user_asks_and_covers_every_command():
    fm = front_matter("skills", "agy-call", "SKILL.md")
    assert fm["name"] == "agy-call"
    assert "only when the user asks" in fm["description"]
    text = read("skills", "agy-call", "SKILL.md")
    for name in COMMANDS:
        assert "/agy:%s" % name in text, name
    for heading in ("## Coding tasks", "## Reviews", "## Status, result and cancel", "## Setup", "## run and batch",
                    "## Exit codes", "## Where replies are", "### Job states"):
        assert "\n%s\n" % heading in text, heading


def test_the_readme_and_release_notes_give_the_new_install_line():
    for path in ("README.md", "docs/release-notes-0.2.0.md"):
        text = read(path)
        assert "/plugin install agy --marketplace shitanshuuppal/agy-call" in text, path
        assert "/plugin uninstall agy-call@agy-call" in text, path
```

- [ ] **Step 3: Run them to see them fail**

Run: `uvx pytest -q tests/test_plugin.py`
Expected: the two new tests FAIL: the skill's description has no "only when the user asks", and the README has no `/plugin install agy --marketplace shitanshuuppal/agy-call` (the release notes file does not exist yet).

- [ ] **Step 4: Rewrite `skills/agy-call/SKILL.md`**

Replace the whole file with:

````markdown
---
name: agy-call
description: "Use only when the user asks for work to go to Gemini or agy (Antigravity), by an /agy: slash command or in plain words: a coding task, a review, a question, a batch of items, or a script that calls Gemini. Every agy call goes through agy-call, which saves every reply, resumes the same conversation when a reply is unusable, and stops all callers on quota. Never hand work to Gemini on your own initiative."
---

# agy-call

Gemini gets work only when the user asks for it, by a slash command or in plain words; never hand work to Gemini on your own initiative.
Every agy call goes through `agy-call`, which saves every reply before it checks it, resumes the same conversation when a reply is unusable, and keeps a machine-wide quota hold.

## Which command

| The user asks for | Slash command | agy-call command |
|---|---|---|
| a coding task done by Gemini | `/agy:task [--background] [--continue JOB] [--model M] [--effort E] <task text>` | `agy-call task` (see Coding tasks) |
| a review of the current change | `/agy:review [--background] [--scope auto\|working-tree\|branch] [--base REF]` | `agy-call review` (see Reviews) |
| a review that challenges the design | `/agy:adversarial-review [--background] [--scope ...] [--base REF] [focus text]` | `agy-call review --adversarial --focus TEXT` |
| how a job is going | `/agy:status [JOB] [--all]` | `agy-call status` |
| the outcome of a job | `/agy:result [JOB]` | `agy-call result` |
| to stop a job | `/agy:cancel [JOB]` | `agy-call cancel` |
| to check the installation | `/agy:setup` | `agy-call setup` |
| an answer to a question, or to many items | none | `agy-call run`, `agy-call batch` (see run and batch) |

When the user asks in plain words, do what the matching slash command does, as this skill describes it.
The slash commands pass `--model gemini-4-argon-high`, and reviews also `--effort high`, unless the user names others.
The `agy` subagent (`agy:agy`) runs exactly one agy-call command and returns its output unchanged; `/agy:task` starts tasks through it.
Run every agy-call command from inside the repository it is about.

## Why it exists

Calling agy directly has lost paid replies in two ways.
A reply that failed a check was thrown away and the question was asked again in a new conversation, so the work already paid for was lost.
A quota error in one process did not stop the other processes, which kept launching calls that could not succeed.
agy-call saves the raw output of every attempt before it looks at it, resumes the same conversation with a fix message when a reply is unusable, and writes a machine-wide quota hold that every caller checks before launching.
A PreToolUse hook (`hooks/agy_guard.py`) refuses Bash commands that run `agy -p`, `agy --print`, `agy-delegate` or `agy-job` directly, so use `agy-call` for all of them.
`agy models`, `agy --version` and other non-print agy commands are not blocked.
The hook matches the whole command text, so a command that only mentions `agy-delegate` or `agy-job` (an echo, a grep pattern, a prompt text) is refused too; a command that mentions the word agy together with -p or -i on the same line (for example a grep) is refused too, so rephrase, e.g. `grep -i 'ag[y]' file`; put such text in a file and pass the file (for example `--prompt-file`) instead of writing it in the command.
A SessionStart hook exports `AGYCALL_SESSION` (the Claude session id) to the session's Bash commands, so each job records the session that started it and `status`, `result` and `cancel` put this session's jobs first.

## Coding tasks

`agy-call task` hands a coding task to Gemini in its own git worktree on a new branch `agy/<job>`, with a permission grant made for that task only; use it instead of `run` for any work that edits files.

```
agy-call task --job ID --model M [--effort E] [--allow CMD]... --check-cmd CMD [--check-timeout S] [--timeout T] [--max-turns N] [--deadline T] [--background] [--new-version] --prompt-file F
```

- It refuses with exit 26 while the checkout has staged, unstaged or untracked files that are not ignored, or a detached HEAD, and with exit 24 when the branch `agy/<job>` or the worktree folder already exists for another job.
- The job id follows the job id rule and must also make a branch name git accepts, so ids such as `a..b`, `a.lock` or `a.` exit 2.
- The worktree is `~/.agy-call/worktrees/<repo id>/<job>/` and the job record `~/.agy-call/jobs/<repo id>/<job>/`, where `<repo id>` is the first 12 hex characters of the SHA-256 of the repository's git common directory; both folders are readable by you only.
- `--check-cmd` is required; pass `--check-cmd true` when there is nothing to build or test.
  It runs in the worktree with Gemini's reply on stdin; exit 0 passes, and any other exit sends the last 4000 characters of its combined stdout and stderr back to Gemini as a fix turn in the same conversation.
  A turn that changed no file fails the check with "no file in the worktree changed".
  Only a timeout or a failure to start the check is a check error (21) [`--check-timeout` 600 seconds].
- `--max-turns` counts every launch, follow-ups and retries included [3]; `--deadline` bounds the whole run across turns [`2h`]; `--timeout` limits one turn [`60m`].
- Gemini receives your task text inside fixed instructions: the worktree path, the exact commands it may run, no commits, branches or pushes, one `NEED: <command>` line instead of trying a command that is not allowed, and a final summary of at most 15 lines without diffs or file contents.
- `--background` prints the job id and runs the task in a detached worker, which keeps running after the Claude session ends; follow it with `agy-call status` and `agy-call result JOB`.

### Starting a task for the user

1. Run `git status --porcelain`; when it lists anything, show the user the files and ask whether to commit, stash or ignore them, because the task refuses to start.
2. Choose a job id: a short slug of the task in lowercase letters, digits and hyphens, then the date and time, for example `fix-login-redirect-20261112-1430`.
3. Write the prompt file in your scratch directory, or in `$TMPDIR`, never inside the repository: a new file there makes the checkout unclean, and the task or its merge refuses with exit 26.
   It holds the task text and whatever from the conversation the task needs, written for a reader who has not seen the conversation, without instructions about commits, branches, permitted commands or the reply format.
4. Choose `--allow` entries: the build, test, lint and read commands of this repository that the task needs, taken from its own files (README, Makefile, `package.json` scripts, `Package.swift`, `pyproject.toml`, `Cargo.toml`, CI workflows).
   Never allow a command that reaches outside the repository: network access, package installs, deletion outside the worktree, `git commit`, `git push`, or anything that reads credentials.
5. Choose `--check-cmd`: the command that builds the repository and runs its tests, or `true`.
6. Tell the user the `--allow` entries and the check in one or two lines, then have the `agy` subagent run `agy-call task ... --background --prompt-file FILE` with each value in single quotes.
7. When the user asked to wait, run `until agy-call result JOB >/dev/null 2>&1; [ $? -ne 25 ]; do sleep 30; done` with the Bash tool and `run_in_background: true`, then `agy-call result JOB`; otherwise report the job id and stop.

### What the grant allows

agy-call writes `grant-<n>.json` in the job record and passes it to agy with `--settings`.
It allows writes inside the worktree, the read-only commands `ls`, `cat`, `head`, `tail`, `wc`, `grep`, `rg`, `git status`, `git diff`, `git log` and `git show`, and each `--allow` entry as `command(<entry>)`.
An `--allow` entry is one command without newlines or parentheses, for example `--allow "swift build" --allow "swift test"`; a compound command such as `swift build && swift test` runs when each part is allowed.
agy adds the Antigravity app's shared grants (`userSettings.globalPermissionGrants.allow` in `~/.gemini/config/config.json`) to every `--settings` run, so agy-call puts every shared grant that is not one of the task's own entries into the grant's `deny` list, and reads the file again each time a task runs.
A shared grant that is a prefix of one of the task's commands (a shared `command(git)` against the task's `command(git status)`) then blocks that command too; the task is refused on the safe side, and Gemini reports it as a `NEED:` line.
When that file cannot be read as JSON of the expected shape, `task` refuses to start (exit 2).
The grant keeps agy's own file-writing tool inside the worktree, but agy-call cannot limit what a granted command does: a test command runs the repository's code, which can do anything your account can, and git commands follow the repository's own configuration.

### When a task ends

- `done` (exit 0): agy-call prints the hand-back: Gemini's summary, `git status --porcelain` and `git diff --stat <base commit>` of the worktree, the worktree path, the branch and the base.
- `blocked` (exit 28): Gemini's reply had `NEED:` lines, or agy refused an action.
  When agy refused a command and the reply named none, agy-call first asks once, in the same conversation, for the `NEED:` lines; that turn counts against `--max-turns`.
  `status.json` records the refused action kinds (`denied`) and the `NEED:` lines (`need`), and agy-call never widens a grant by itself.
- `failed` (exit 20): the check still failed after `--max-turns`, the deadline passed, a turn timed out (its partial reply is kept), agy returned no reply even to the one turn that asks only for the summary, or a reply that came with an agy error failed the check.
- `setup-error` (exit 11, 13, 14 or 22): agy is not signed in or missing, the model is unknown, or agy's output was not a JSON envelope.
- A rerun of the same `task` command returns the saved result, or continues the conversation after a quota stop; it creates nothing and does not need a clean checkout.
  A different request under the same job id exits 24.
- When the process running a task died and its saved output does not settle the turn, the task ends `failed` and nothing is resent.

### Continuing a task

```
agy-call task --continue --job ID [--allow CMD]... [--max-turns N] [--deadline T] [--background] --prompt-file F
```

It sends a new message in the task's own conversation, in the same worktree, with its own `--max-turns` and `--deadline`; agy's own `--continue` flag, which picks the machine's most recent conversation, is never used.
`--allow` adds commands for this and later messages, in a new grant file.
The previous answer moves to `segments/NNN-answer.txt`, and a pending `cancel.request` is set aside, so neither ends the new turns.
It exits 25 while the task runs, 26 when its worktree is missing, and 23 when the task never got a conversation id.

After `blocked`: you may continue with `--allow <command>` for build, test or read commands confined to the repository, with a prompt file that says those commands are now allowed.
Ask the user first for anything that reaches outside it: network access, deletion outside the worktree, package installs, `git push`, or credentials.

### Hand-back and merge

1. Read the diff in the worktree and run the build and tests there yourself.
2. Commit on `agy/<job>` in the worktree with the user's git identity and a message naming the job.
3. Run `agy-call task --merge JOB` from the repository.
4. Never push.

When the change falls short, tell the user what you found and ask whether to send it back with `--continue` or to fix it yourself.
`--merge` takes the job lock (exit 25 while the task runs) and the repository lock.
It refuses with exit 26 when the worktree has uncommitted changes, when the main checkout is not on the base branch or not clean, when the branch has no commit that the base branch lacks, or when the base branch moved on; in that last case merge the base branch into `agy/<job>`, test, commit and run `--merge` again.
It runs the check command on the branch tip in the worktree; a failing check exits 20 with its output, and a check that changes the worktree or the branch exits 26.
It then runs `git merge --no-ff -m "Merge agy task <job>"` in the main checkout; when git fails (for example a hook refuses), it saves git's output in `merge-failed.json`, runs `git merge --abort`, and exits 26.
It writes `merge.json` once it has confirmed the merge commit.

`agy-call task --cleanup JOB` removes the worktree and deletes the branch once the branch is merged into the base branch.
`agy-call task --discard JOB` drops a task, merged or not, when its worktree has no uncommitted changes.
Neither passes `--force` to git or runs while the task runs, and ignored files such as build output do not stop them.
The job record is kept, and `task --new-version` starts the same job id again only after `--cleanup` or `--discard` (otherwise exit 26).

A turn's reply and its thinking share one output limit, so a reply that carries a long diff can hit it; agy then fails with "exceeded the output token limit", and later turns in that conversation have failed the same way.
agy-call still checks a reply agy wrote before that error (it is in `reply.txt`); when there is none, it exits 20 without resending.

## Reviews

`agy-call review` has Gemini review the current change of the repository and returns a list of findings with a severity each, tied to the exact code it reviewed.

```
agy-call review --model M [--effort E] [--scope auto|working-tree|branch] [--base REF] [--adversarial] [--focus TEXT] [--job ID] [--max-turns N] [--timeout T] [--background]
agy-call review --cleanup JOB
```

- `--scope working-tree` reviews the staged, unstaged and untracked files that are not ignored, against `HEAD`.
- `--scope branch` reviews `merge-base(BASE, HEAD)..HEAD`; `--base` names BASE and defaults to `origin/HEAD`, else `main`, else `master`; `--base` applies to the branch scope only.
- `--scope auto` is `working-tree` when `git status --porcelain` shows anything and `branch` otherwise [`auto`].
- When the target has no change, it exits 26 with "nothing to review".
- agy-call keeps a snapshot of the target as a commit under `refs/agy-call/<job>` and checks it out in a detached worktree, `~/.agy-call/worktrees/<repo id>/<job>-snap/`; your index, files and branches are not touched, and edits made while the review runs are not in it.
- Gemini reads the snapshot with a grant of the read-only commands only, with no file writes and no `--allow` entries, and with the same deny list of shared grants as a task.
- `--adversarial` tells Gemini to assume the change has defects and to challenge the approach and the design choices as well as the code; such a finding may have no file.
- `--focus TEXT` tells Gemini what to give particular attention to.
- Without `--job` the id is `review-standard-<16 hex>` or `review-adversarial-<16 hex>`, taken from the review's identity: the repository, the base commit, the snapshot's tree, the scope, the adversarial flag, the focus text, the model, the effort and the schema.
  The same code under the same settings returns the saved review without a launch, at any later time, and any change to the code or the settings starts a new review.
- `--max-turns` counts every launch [3]; a review may run 2 hours in all across its turns; `--timeout` limits one turn [`20m`].
- `--background` takes the snapshot, prints the job id and runs the review in a detached worker; follow it with `agy-call status` and `agy-call result JOB`.
  Without it, run the command with the Bash tool and `run_in_background: true`, because a review can take longer than one Bash call may run.

Gemini replies with one JSON object: a verdict (`approve`, `changes-needed` or `incomplete`), a summary, `files_reviewed`, and findings with a severity (`MUST-FIX`, `SHOULD-FIX` or `NIT`), a title, the file and lines, how the problem is reached and the fix.
agy passes the schema to Gemini with `--json-schema`, and agy-call checks the reply again.
A reply that breaks the schema, names a file outside the change, has `line_start` after `line_end`, or approves with a MUST-FIX or SHOULD-FIX finding gets a turn in the same conversation that asks only for the format to be fixed.
A reply that leaves changed files out of `files_reviewed` is kept with its verdict set to `incomplete` and the missing files listed, and no further turn is sent, because covering them is a new review that you have to ask for.
A reply that came with an agy error is accepted only when it passes these checks, and gets no format-repair turn.
`NEED:` lines or a refused command end the review `blocked` (exit 28), as for a task.
A review that ended `failed` or `blocked` keeps that stop on a rerun; to review again, pass another `--job`.

`agy-call result JOB` for a done review prints the verdict, the number of findings at each severity, one block per finding (severity and title, where, how it is reached, the fix) and the path of the full JSON.
When the code of the same scope has changed since the snapshot, it adds `code changed since this review (snapshot X, now Y)` with the two tree ids.
Show the findings to the user as agy-call prints them.
agy-call never starts a fix or a second review by itself, and neither do you: what happens next follows the user's own rules for review loops, so do not fix a finding, review again or start another reviewer unless the user asks.
`agy-call review --cleanup JOB` removes the snapshot worktree and the ref, and keeps the job record and its findings; it exits 25 while the review runs.

## Status, result and cancel

`agy-call status [JOB] [--all] [--store DIR] [--namespace NAME]` lists the jobs of a store, one line each: the id, the state, the kind (`run`, `task` or `review`), the age and the last finding.
Without `--store` or `--namespace`, `status`, `result` and `cancel` use the store of the current git repository when they run inside one (a task's worktree counts), and the default store otherwise; pass `--namespace default` for `run` jobs of the default store from inside a repository.
`--all` lists every store under `~/.agy-call/jobs`, repository stores and namespace stores, one block per store.
Jobs started in the current Claude session (the `AGYCALL_SESSION` variable, recorded in `request.json`) come first, then the others, newest first in each group.
With a job id it prints every field of the job's `status.json` and the processes still running for it: the controller (the agy-call process running the job), agy and the check command.
A queued, running or checking job with none of these alive lost its process; rerun the same command to recover it.

`agy-call result [JOB]` prints the accepted answer of a `done` job and exits 0.
For a task it then prints the hand-back (see Coding tasks), and for a `done` review it prints the findings instead of the JSON (see Reviews).
For any other state it prints the state, the findings and the last saved reply, and exits with that state's code: 10 quota, 20 failed, 21 check error, 23 no conversation, 25 queued, running or checking, 27 cancelled, 28 blocked, and for `fatal` and `setup-error` the setup code the job recorded (11, 13, 14 or 22).
An `interrupted` job exits 1.
Without a job id it uses the newest finished job of the current session.

`agy-call cancel [JOB]` asks a queued, running or checking job to stop by writing `<store>/<job>/cancel.request`, without waiting for the job's lock.
It then waits up to 20 seconds and prints `cancelled`, `finished before cancel (STATE)` when the job ended another way first, or `still stopping (pid N)` with exit 25.
When something still holds the job but no process owned by it can be found, it prints `still stopping (pid unknown)` and exits 25.
On a job that is not running it changes nothing and prints the job's state.
Without a job id it cancels the only running job of the current session, and refuses when there are several.
agy-call looks for the request before each launch, every second while agy or the check command runs, and once more just before it saves the answer; agy and the check command get SIGTERM, then SIGKILL after 10 seconds.
When the process running the job has died but agy or the check command still runs, `cancel` stops them itself.
A cancelled job stays cancelled: a rerun exits 27 without a launch, so to do the work anyway run it under a new job id, or continue a task with `task --continue`.
Replies already received are kept, and so is a task's worktree.

### Job states

| State | Meaning |
|-------|---------|
| `queued` | a background worker was started; agy has not been launched yet |
| `running` | agy is running |
| `checking` | the check command is running |
| `done` | a reply passed its checks; `answer.txt` holds the answer |
| `blocked` | a task's or a review's reply had `NEED:` lines or agy refused an action (exit 28); waiting for a wider grant |
| `failed` | gave up (exit 20); the replies are kept |
| `quota` | a quota hold stopped the job |
| `check-error` | the check itself failed to run |
| `no-conversation` | a follow-up was needed but no conversation id was captured |
| `fatal` | agy is missing or not signed in, the model is unknown, or agy's output was not a JSON envelope |
| `setup-error` | the same as `fatal`, for a task or a review |
| `cancelled` | stopped by `cancel` |
| `interrupted` | the process running the job died and the saved output did not settle the turn; the rerun that found it restarts the turn |

## Setup

`agy-call setup` checks, without sending a prompt or spending quota: Python 3.9 or newer, macOS or Linux, the agy executable (`AGYCALL_AGY`, else `agy` on the PATH), `agy --version` 1.5.6 or newer, that `agy --help` lists `--settings`, `--json-schema`, `--effort` and `--output-format`, that `agy models` succeeds (it needs a signed-in agy), and that it lists `gemini-4-argon-high` and `gemini-3.8-flash-low`.
It prints one line per check, `ok` or `FAIL`, and exits 0 only when every check passes, 1 otherwise.
Each agy probe gets 30 seconds.
When agy cannot be found, the checks that need it are not run.

## run and batch

`agy-call run --job ID [options]` reads the prompt from `--prompt-file`, `--prompt` or stdin (in that order of preference; the first two are mutually exclusive).
It prints the accepted answer on stdout, errors on stderr, and exits with the code in the table below.
Run it with run_in_background for long jobs, or pass `--background` to hand the job to a detached worker (see Status, result and cancel).
A bad argument, job id or timeout, or a prompt larger than one command-line argument allows (512 KiB on macOS, 128 KiB on Linux), exits 2 before anything is launched.
`run` and `batch` pass no grant, so agy applies the user's own settings to any tool call; use `task` for work that edits files.

Options of `run` and `batch` (defaults in brackets):

- `--store DIR`: the job store; put it inside the project, for example `<project>/agy-jobs` [`~/.agy-call/jobs/<namespace>`].
- `--namespace NAME`: the folder under `~/.agy-call/jobs` when no `--store` is given [`default`].
- `--model NAME`: required; the exact agy model name, for example `gemini-4-argon-high`; aliases and tiers are not accepted, because one name per model keeps the quota hold exact (the hold is kept per model string).
- `--timeout T`: limit per turn, passed to agy as `--print-timeout`; agy-call stops agy 60 seconds after it; a bare number means seconds [`10m`].
- `--cwd DIR`: working directory of the call [the current directory].
- `--dir DIR`: extra directory the agent may read, passed as `--add-dir`; repeat the option for several [none].
- `--mode MODE`: agy's execution mode, `accept-edits`, `auto` or `plan` [agy's default].
- `--expect json`: the reply must contain a JSON value (the last fenced `json` block that parses, the whole reply, or unfenced JSON surrounded by prose when it is the whole span from the first `{`/`[` to the last `}`/`]`); the accepted answer is that value re-printed as JSON.
- `--require a,b`: comma-separated top-level keys the JSON object must have (implies `--expect json`).
- `--schema FILE`: JSON Schema file the JSON value must satisfy (implies `--expect json`); it may use only `type` (a name or a list of names), `enum`, `required`, `properties`, `items`, `additionalProperties: false`, `minLength`, `minimum` and `maximum`, and any other keyword exits 2 before a launch.
- `--check-cmd CMD`: shell command, run in the working directory of the call (`--cwd`), that receives the candidate answer on stdin; exit 0 accepts it, exit 1 rejects it and its stdout becomes the finding sent back to agy, any other exit is a check error (21) [none].
- `--check-timeout SECONDS`: limit for the check command [120].
- `--max-turns N`: most turns per job, counting follow-ups but not plain retries [4].
- `--ignore-quota`: launch even while a quota hold exists [off].
- `--fresh`: when a follow-up is needed but no conversation id was captured, start a new conversation instead of exiting 23 [off].

Options of `run` only:

- `--job ID`: required; `[A-Za-z0-9][A-Za-z0-9._-]{0,119}` (the first character is a letter or digit).
- `--conversation ID`: seed the job with an existing conversation, to continue it [none].
- `--new-version`: when the job exists with a different request, move the old job folder under `.versions` and start again [off].
- `--background`: check the request as a normal run would, record the job as `queued`, start a detached worker that runs it, print the job id and exit 0; the worker keeps running after the Claude session ends, and its own messages go to `<store>/<job>/worker.log`; an argument that cannot be stored as JSON exits 2 [off].

`agy-call batch JOBS.jsonl [--workers N] [common options]` runs many jobs [`--workers` default 4].
Each line of the file is a JSON object with `job` (required, unique within the batch; duplicates are rejected before anything runs) and `prompt` or `prompt_file`.
A row that cannot be prepared (invalid job id, unreadable prompt file) is reported as that row's failure and the other rows still run.
Any other key on a line is an option for that job and overrides the common option of the same name, using the Python names: `store`, `namespace`, `model`, `timeout`, `cwd`, `dirs` (a list), `mode`, `conversation`, `expect`, `require` (a list), `schema`, `check_cmd`, `check_timeout`, `max_turns`, `ignore_quota`, `fresh`, `new_version`.
Batch prints one line per outcome with its count (`done 3`, `Quota 2`, ...), then one line per job that did not finish.
Jobs that already have an answer are returned without a launch even during a quota hold.
Once a model key has a quota hold, batch launches no further job for that key and lists those jobs as `Quota`.
Batch exits 0 when every job is done, 10 when any job hit a quota hold, 27 when none did and any job was cancelled, and 20 otherwise.

In shell scripts pass each option and its value as separate words: `--conversation "$ID"`, not one string holding both.

## Python

```python
import os, sys
# the folder that holds agycall/: your clone, or for a plugin install the parent of the folder holding $(realpath $(which agy-call))
sys.path.insert(0, os.path.expanduser("~/Code/agy-call"))
from agycall import call, batch, AgyCallError, Quota
```

`call(job_id, prompt, *, store=None, namespace=None, model, timeout="10m", cwd=None, dirs=(), mode=None, conversation=None, expect=None, require=None, schema=None, check_cmd=None, check_timeout=120, check=None, check_id=None, max_turns=4, ignore_quota=False, fresh=False, new_version=False)` returns a `Result` with `job`, `answer` (the parsed JSON value, or the text when no JSON was asked for), `text`, `conversation`, `attempts`, `usage` and `dir`.
`check` is a Python function that receives the parsed value (or the text) and returns a list of problems; it needs a stable `check_id` string, which is part of the job's identity, so change the `check_id` when the function's rules change.
`batch(jobs, *, workers=4, **common)` takes a list of dicts as in the JSONL format and returns `{job_id: Result or exception}`; it never raises for a single job.

Exceptions, all subclasses of `AgyCallError` with the CLI exit code in `.code`: `Running` (25), `Mismatch` (24), `Quota` (10, with `.key` and `.until`), `Fatal` (the failure's exit code, with `.reason`), `GaveUp` (20, with `.findings`, `.conversation`, `.usage`, `.job_dir`), `CheckError` (21), `NoConversation` (23), `RepoState` (26), `Cancelled` (27), `Blocked` (28, with `.findings`).

## Job ids and stores

A job id names one request and is how a rerun finds the saved work: calling again with the same id and the same request returns the saved answer without a launch.
Choose ids that are stable across reruns and include the version of the item asked about, for example `q-0412-3fa9c1` with a short sha of the source text.
A job whose prompt, model, directories, seed conversation, check or mode changed is a different request; agy-call refuses it with exit 24 unless you pass `--new-version`.
Keep the store of `run` and `batch` jobs inside the project (for example `<project>/agy-jobs`) so the replies live with the work and are not mixed with other projects.
Task and review jobs always live in the repository's own store, `~/.agy-call/jobs/<repo id>/`.

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | ok, answer printed; or a background job was started |
| 1 | `agy-call setup` found a failing check; from `result`, the job is `interrupted` |
| 2 | a bad argument, job id, timeout, schema or prompt size |
| 10 | quota: a hold exists for the model; the message prints the time it ends |
| 11 | agy is not authenticated |
| 13 | agy is missing: set `AGYCALL_AGY` or put agy on PATH |
| 14 | model unavailable |
| 20 | gave up: no usable reply within `--max-turns`, a tool call denied twice (`run`), agy hit its output limit without a reply, agy answered in another conversation, or a merge whose check failed |
| 21 | check error: the check itself failed to run; the reply is kept |
| 22 | agy's output was not a JSON envelope; the raw output is kept in `agy.out` |
| 23 | no conversation id was captured, so a follow-up cannot be sent |
| 24 | the job exists with a different request, or its branch or worktree belongs to another job |
| 25 | the job is running in another process; from `result`, the job is still queued, running or checking; from `cancel`, it is still stopping |
| 26 | repository state: not inside a git repository, uncommitted or untracked files, a detached HEAD, a missing worktree, nothing to review, or a merge, cleanup or discard that the repository's state refuses |
| 27 | the job was cancelled |
| 28 | a task or a review is blocked on permission: its reply had `NEED:` lines or agy refused an action |

## What to do per exit

- 10: wait until the printed time and rerun the same command, or ask the user; do not use `--ignore-quota` to get around it.
- 20: read the findings; after an output-limit stop, send a new prompt that asks for a short reply, as a new job; otherwise rerun with a higher `--max-turns` to continue the same conversation, or change the prompt and rerun with `--new-version`; for a task, continue it with `task --continue`. After a stop because agy answered in another conversation, a rerun sends nothing; start a new job.
- 21: a rerun checks the saved reply again before any new turn, but only when the request is unchanged: repair the checker's executable or environment, not the command string, schema file or `check_id`. After changing the check, the saved reply is in `attempts/NNN/reply.txt` and the raw output in `agy.out`; a rerun with `--new-version` starts a new paid job.
- 23: rerun with `--fresh` if starting a new conversation is acceptable; otherwise use the replies saved under `attempts/`.
- 24: the request changed; use a new job id, or `--new-version` to replace the old job.
- 25: another process holds the job; wait for it and rerun, which then returns its answer.
- 26: the repository is not in the state the command needs; the message says what to change (commit, stash or ignore files, check out a branch, merge the base into the task branch, or clean up a task before `--new-version`).
- 27: the job was cancelled and stays cancelled; its replies are kept under `attempts/`; to do the work anyway, run it under a new job id.
- 28: read the `NEED:` lines and the refused action kinds (`agy-call status JOB`), then follow "After `blocked`" under Continuing a task.
- 11, 13, 14, 22: fix the setup (login, install, model name; `agy-call setup` shows which) and rerun; the job is kept.

## Where replies are

Under `<store>/<job>/attempts/NNN/` each launch keeps `agy.out` (agy's JSON envelope, byte for byte), `agy.err` (agy's stderr), `agy.args` (the arguments, with the prompt replaced by its size and hash), `reply.txt` (the reply text, when there was one), `launch.json` (the prompt, process ids and start time) and `result.json` (agy's exit status, the envelope status, the classified outcome and what agy-call did).
Attempts made before agy-call launched agy itself keep their older files (`delegate.out`, `delegate.err`, `agy-N.out`), and agy-call still reads them.
The accepted answer is `<store>/<job>/answer.txt`; `request.json` and `status.json` sit beside it.
Nothing is ever deleted: a replaced job moves under `.versions`, and failed attempts stay in place.
When a call fails, read `reply.txt` and `agy.err` of the last attempt to see what agy actually said.
`check.json` in the attempt folder records the check command's process while it runs.
A cancelled job has `cancel.request`, and a background job has `background.json` (the arguments its worker passes to `call()`) and `worker.log`, beside `request.json`.
When the process running a job died, the next run of the same job reads the saved `agy.out` before launching anything.
A complete reply found there is checked as if it had just arrived, but agy's exit status was lost, so it is accepted only by a check you asked for and no follow-up is sent after it.
Output that is not a complete reply restarts the turn.
A task's record is `~/.agy-call/jobs/<repo id>/<job>/`; beside the files above it holds `grant-<n>.json` (the grant passed with `--settings`), `segments/NNN.json` (each segment's prompt, grant and budget), `segments/NNN-answer.txt` (an earlier segment's answer), and `merge.json`, `merge-failed.json` and `cleanup.json` once those commands ran.
A review's record is in the same repository store; beside the attempt files it holds `schema.json` (the findings schema passed with `--json-schema`), `grant-1.json` (its read-only grant) and, once it is done, `answer.txt` with the accepted findings as JSON.

## Multi-turn work

Each turn of a multi-turn exchange is its own job, seeded with `conversation=` (`--conversation`) from the previous result's `conversation`.
agy-call serialises jobs that share a conversation, so two turns never run at the same time in one conversation.
````

- [ ] **Step 5: Rewrite `README.md` and write the release notes**

Replace the whole of `README.md` with:

````markdown
# agy-call

![agy-call demo: a reply in the wrong format is resumed in the same conversation, and one quota error stops a whole batch](docs/demo.gif)

## What it does

Claude Code can hand work to Google's Gemini through the `agy` CLI (Antigravity): a coding task, a review, a question, or a batch of tasks.
This repository is the Claude Code plugin `agy` and the command-line runner `agy-call` behind it.
Calling `agy` directly caused two problems.
When a reply came back in the wrong format, it was thrown away and the question was asked again in a new conversation, so the paid-for answer was lost.
When one task ran out of Gemini quota, the other tasks running at the same time did not find out and kept sending requests that could not succeed.

agy-call sits between Claude and Gemini and handles both:

- It saves every reply to disk before checking it, so no paid reply is lost.
- When a reply is unusable, it goes back to the same conversation and tells Gemini what to fix, instead of starting over.
- When one call hits the quota limit, it records a hold for the whole machine; every other call checks the hold before launching and waits until the limit resets.
- It can check answers for you, for example requiring valid JSON with certain fields, and sends a reply back when the format is wrong.
- A call can run in the background; `agy-call status`, `result` and `cancel` show how it is going, print its answer and stop it.
- It can hand a coding task to Gemini in its own git worktree and branch, with a permission grant made for that task only, and merge the checked result locally.
- It can have Gemini review the current change in a read-only snapshot and return a checked list of findings, each with a severity, tied to the exact code reviewed.

Claude uses Gemini only when you ask for it, by a slash command or in plain words.
The guard hook stops Claude from calling `agy` directly, so every call goes through agy-call.

## Commands

| Slash command | What it does |
|---|---|
| `/agy:task [--background] [--continue JOB] [--model M] [--effort E] <task text>` | Hands a coding task to Gemini in its own worktree on branch `agy/<job>`; Claude picks the build and test commands Gemini may run, then checks, commits and merges the result locally and never pushes. |
| `/agy:review [--background] [--scope auto\|working-tree\|branch] [--base REF]` | Gemini reviews the current change and returns findings marked MUST-FIX, SHOULD-FIX or NIT. |
| `/agy:adversarial-review [--background] [--scope ...] [--base REF] [focus text]` | The same review, told to assume the change has defects and to challenge its design. |
| `/agy:status [JOB] [--all]` | Lists the jobs of this repository, or shows one job in full. |
| `/agy:result [JOB]` | Prints a job's outcome: a task's hand-back or a review's findings. |
| `/agy:cancel [JOB]` | Stops a queued or running job and keeps what it produced. |
| `/agy:setup` | Checks Python, the operating system, agy's version, flags and sign-in, and the model names, without spending quota. |

The commands use the model `gemini-4-argon-high`, and reviews `--effort high`, unless you name others.
Claude never starts a fix or a second review by itself after a review; that follows your own instructions.
`skills/agy-call/SKILL.md` documents every `agy-call` command and option, including `run` and `batch` for questions and scripts.

## What a task's permission grant does and does not do

Each task gets its own permission grant, passed to agy with `--settings`, so you never edit agy's own settings to let a task run.
The grant lets agy's file-writing tool write only inside the task's worktree, and lets Gemini run a fixed set of read-only commands (`ls`, `cat`, `head`, `tail`, `wc`, `grep`, `rg`, `git status`, `git diff`, `git log`, `git show`) plus the build and test commands Claude chose for that task.
A review's grant has the read-only commands only.
agy adds the Antigravity app's shared grants (`userSettings.globalPermissionGrants.allow` in `~/.gemini/config/config.json`) to every run that uses `--settings`, so agy-call lists each shared grant that is not one of the task's own entries in the grant's `deny` list, and agy refuses those for the task.
agy-call cannot limit what a granted command does: a test command runs the repository's own code, which can do anything your account can, and git commands follow the repository's own configuration.
agy's `--sandbox` flag did not stop a granted command from writing outside the workspace when measured, so agy-call does not use it.
Measured on agy 1.5.6: a write outside the granted folder was refused, a shared grant listed in `deny` was refused, and a resumed conversation used only its own run's grant.

## Install

Requirements: the `agy` CLI 1.5.6 or newer, installed and signed in; Python 3.9 or newer (standard library only); macOS or Linux.
agy-call finds agy on the PATH or through `AGYCALL_AGY`.
The Antigravity plugin is no longer needed: agy-call launches agy itself.

### As a Claude Code plugin

In Claude Code 2.1.275 or later:

```
/plugin install agy --marketplace shitanshuuppal/agy-call
```

In older versions:

```
/plugin marketplace add shitanshuuppal/agy-call
/plugin install agy@agy-call
```

Restart Claude Code afterwards, then run `/agy:setup` to check the installation.
The plugin installs the slash commands, the `agy` subagent and the skill, puts the `agy-call` command on the PATH inside Claude Code sessions, and turns on two hooks: the guard hook, which refuses Bash commands that call agy directly, and a session hook that lets agy-call tell which Claude session started each job.

### Moving from agy-call 0.1

The plugin was called `agy-call` up to version 0.1.0 and is called `agy` from 0.2.0; the marketplace keeps the name `agy-call`.
Uninstall the old plugin once, then install the new one as above:

```
/plugin uninstall agy-call@agy-call
```

If you installed by hand with symlinks, remove the skill and hook symlinks before installing the plugin, so the skill and the guard hook are not loaded twice:

```sh
rm ~/.claude/skills/agy-call/SKILL.md ~/.claude/hooks/agy_guard.py
rmdir ~/.claude/skills/agy-call
```

Also remove the guard hook's entry from `hooks.PreToolUse` in `~/.claude/settings.json`.
If you keep the `~/.local/bin/agy-call` link for use outside Claude Code, update your clone (`git pull`) so it runs the same version as the plugin.
`AGY_DELEGATE` and `AGYCALL_REAL_AGY` are no longer read; set `AGYCALL_AGY` if agy is not on the PATH.
The release notes (`docs/release-notes-0.2.0.md`) list every change.

### Manual install

Use this if you want `agy-call` on the PATH outside Claude Code without the plugin.
The steps assume the repo is cloned to `~/Code/agy-call`; use your own path if it differs.

```sh
git clone https://github.com/shitanshuuppal/agy-call.git ~/Code/agy-call
mkdir -p ~/.claude/skills/agy-call ~/.local/bin ~/.claude/hooks
ln -s ~/Code/agy-call/skills/agy-call/SKILL.md ~/.claude/skills/agy-call/SKILL.md
ln -s ~/Code/agy-call/bin/agy-call ~/.local/bin/agy-call
ln -s ~/Code/agy-call/hooks/agy_guard.py ~/.claude/hooks/agy_guard.py
```

`~/.local/bin` must be on the PATH; check with `agy-call setup`.
The slash commands and the session hook come with the plugin only.

The guard hook is optional but recommended.
To enable it, add this entry under `hooks.PreToolUse` in `~/.claude/settings.json`, with your own absolute home path:

```json
{
  "matcher": "Bash",
  "hooks": [
    {"type": "command", "command": "python3 /Users/<you>/.claude/hooks/agy_guard.py", "timeout": 5}
  ]
}
```

Restart Claude Code so it loads the skill and the hook.
Scripts can import the package with `sys.path.insert(0, os.path.expanduser("~/Code/agy-call"))`.

## Details

agy-call is standard-library Python.
Run the tests with `uvx pytest -q tests/`; they use a stand-in for agy and spend no Gemini quota.
`skills/agy-call/SKILL.md` is the user guide.
`docs/demo/` regenerates the demo GIF with the test suite's stand-in for agy, so it spends no Gemini quota.
````

Create `docs/release-notes-0.2.0.md`:

````markdown
# agy 0.2.0

The plugin is now called `agy` and hands coding tasks and reviews to Gemini the way the Codex plugin hands them to Codex.
The repository, the marketplace and the command-line runner keep the name `agy-call`.

## Upgrading

- Uninstall the old plugin once with `/plugin uninstall agy-call@agy-call`, then install the new one with `/plugin install agy --marketplace shitanshuuppal/agy-call`, or before Claude Code 2.1.275 with `/plugin marketplace add shitanshuuppal/agy-call` and `/plugin install agy@agy-call`.
- If you installed by hand with symlinks, remove the skill and guard hook symlinks and the guard hook's entry in `~/.claude/settings.json` before installing the plugin, so neither is loaded twice.
- Requirements: agy 1.5.6 or newer, Python 3.9 or newer, macOS or Linux.
- The Antigravity plugin is no longer needed: agy-call launches agy itself instead of going through agy-delegate.
- Run `/agy:setup` (or `agy-call setup`) after installing; it checks agy's version, flags, sign-in and model names without spending quota.

## New

- Slash commands `/agy:task`, `/agy:review`, `/agy:adversarial-review`, `/agy:status`, `/agy:result`, `/agy:cancel` and `/agy:setup`, and the `agy` subagent, which runs one agy-call command and returns its output unchanged.
  They run only when you ask for Gemini.
- `agy-call task` runs a coding task in its own git worktree on branch `agy/<job>`, with a permission grant made for that task and passed with `--settings`, a check command run after every turn, `--continue` for follow-up messages, and `--merge`, `--cleanup` and `--discard`.
- The grant lists the Antigravity app's shared grants from `~/.gemini/config/config.json` in its `deny` list, because agy adds them to every `--settings` run.
- `agy-call review` reviews the working tree or the branch in a read-only snapshot and returns findings marked MUST-FIX, SHOULD-FIX or NIT, checked against a schema; `--adversarial` and `--focus` have Gemini challenge the design.
- `--background` on `run`, `task` and `review` hands the job to a detached worker that keeps running after the Claude session ends; `agy-call status`, `result` and `cancel` follow it.
- `agy-call setup` checks the installation.
- A SessionStart hook records the Claude session in `AGYCALL_SESSION`, so `status`, `result` and `cancel` put the current session's jobs first.

## Changed

- `AGY_DELEGATE` and `AGYCALL_REAL_AGY` are replaced by `AGYCALL_AGY`, the path of the agy executable.
- `--mode` is passed to agy directly and also accepts `auto`.
- A reply that came back with an agy error, including an output-limit stop, is accepted only by a passing check you asked for (`--expect`, `--require`, `--schema` or `--check-cmd`), and no fix turn is sent in that conversation.
- `--check-cmd` runs in the job's working directory (`--cwd`).
- A `--schema` file may use only `type`, `enum`, `required`, `properties`, `items`, `additionalProperties: false`, `minLength`, `minimum` and `maximum`; any other keyword exits 2 before a launch.
- Exit 22 now means an unknown agy failure or output that is not a JSON envelope.
- New exit codes: 26 (repository state), 27 (cancelled) and 28 (blocked on permission).

## Unchanged

- The options of `run` and `batch`, the Python `call()` and `batch()` signatures, the job store layout, quota holds and existing job identities; saved answers are returned as before.
- Attempts saved by 0.1.0 (`delegate.out`, `agy-N.out`) are still read.
- The guard hook still refuses direct print-mode agy calls and the old wrapper scripts.
````

- [ ] **Step 6: Run the checks**

Run: `uvx pytest -q tests/test_plugin.py`
Expected: 6 passed.
Run: `grep -c "$(printf '\342\200\224')" README.md skills/agy-call/SKILL.md docs/release-notes-0.2.0.md commands/*.md agents/agy.md`; expected: `0` for each.
Read the new Markdown once for one sentence per line.
Run, from the repository root: `claude plugin validate .`
Expected: `✔ Validation passed` with no advice.
Run: `claude plugin validate --strict skills`
Expected: `✔ Validation passed`.
Run: `for i in 1 2 3; do uvx pytest -q tests/ || break; done`
Expected: every run passes (M + 2 tests).

- [ ] **Step 7: Commit**

```bash
git add skills/agy-call/SKILL.md README.md docs/release-notes-0.2.0.md tests/test_plugin.py
printf '%s\n' "Rewrite the skill and README for the agy plugin; add 0.2.0 notes" \
  "" \
  "The skill is organised around the slash commands: which to use, the task" \
  "hand-back and merge, blocked tasks, reviews and the review-loop rule," \
  "setup, and the full run and batch reference with exit codes, job states" \
  "and attempt files. The README gives the new install line, the move from" \
  "agy-call 0.1, the commands and what a task's grant does and does not do." \
  "" \
  "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" "Claude-Session: https://claude.ai/code/session_013coVpN7if3z6kaMYLTrz3c" > "$TMPDIR/agycall-msg.txt"
git commit -F "$TMPDIR/agycall-msg.txt"
```

---

## Self-review

- Spec coverage: 3.2 the manifest name `agy` in the marketplace `agy-call`, the seven commands, the subagent that returns output unchanged, no description that invites proactive use (Task 3, pinned by `tests/test_plugin.py`), the skill rewritten around the commands keeping `run` and `batch` (Task 4), the guard hook kept (Task 2 registers both hooks), the SessionStart hook (Task 2); 3.3 where records and worktrees live, documented in the skill (Task 4); 4.1 the guarantee and its limit, stated in the README and the skill (Task 4); 4.6 blocked handling in `/agy:task` and the skill (Tasks 3 and 4); 4.8 hand-back and merge in `/agy:task` and the skill (Tasks 3 and 4); 9 setup with every check, no prompt, exit 0 only when all pass (Task 1); the 10.4 results (shared grants denied, resumed conversations, compound commands, `--sandbox`) in the README and the skill (Task 4); 11 the rename, the install lines, the uninstall of `agy-call@agy-call`, the symlink removal and the `AGYCALL_AGY` change in the README and the release notes (Task 4); 13 exit codes 26, 27 and 28 and setup's exit 1 in the skill (Task 4).
  Not in this step by design: the live checks and the release (step 6), and removing the Antigravity plugin (11.1).
- Placeholders: none; every file is given whole, every code edit is an exact old/new pair checked against main, and the only instruction without fixed text is the Task 4 reconciliation, which compares against text that exists only after steps 3 and 4 merge.
- Names used across tasks: `setup.check/TIMEOUT/MIN_AGY/FLAGS/MODELS`, the `FAKE_VERSION`, `FAKE_HELP_DROP`, `FAKE_MODELS`, `FAKE_MODELS_FAIL` stand-in variables, `tests/test_plugin.py`'s `ROOT`, `COMMANDS`, `read`, `front_matter`, the subagent `agy:agy`.
- Review Focus: each of the five lines has its test in the named task.

## After this plan

- Step 6 runs the live checks of spec 10.4 with the user's approval, which gate the release; the controller then publishes v0.2.0 with `docs/release-notes-0.2.0.md`.
- `docs/superpowers/plans/notes-step5.md` asks to be deleted once this plan is written; that is the controller's call.
- Nothing in this plan pushes; pushing is the user's call.
