# agy plugin, build step 3: task worktrees and `agy-call task` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `agy-call task` hands a coding task to Gemini in its own git worktree on a branch `agy/<job>`, with a per-job grant that mirrors agy's shared grants into `deny`, checks the result in the worktree, can be continued, run in the background, merged into the base branch, cleaned up and discarded.

**Architecture:** `agycall/worktree.py` owns git: the repository id and store, creating the worktree, the hand-back, and merge, cleanup and discard under a repository lock.
`agycall/task.py` builds the grant file and the wrapped prompt, records a task and its segments, and drives the latest segment through `runner._drive` with `policy="task"`.
The runner keeps one job loop; a handful of `c.policy == "task"` switches give the task column of spec 5.2, and every launch is tagged with its segment so budgets and rerun guards look only at the current segment.

**Tech Stack:** Python 3.9+ standard library only; git 2.32 or newer in the tests (`git init -b` and `GIT_CONFIG_GLOBAL`); tests with pytest run as `uvx pytest -q tests/`.

**Spec:** `docs/superpowers/specs/2026-11-12-agy-plugin-design.md`, sections 3.3, 4.1 to 4.9 (including the 4.2 deny mirror), the task column of 5.2, 5.3, 5.4 (`blocked`, `setup-error`), 7.2 (task identity), 10.4 results and 13 (exit codes 26 and 28).
This is build step 3 of section 12, and it applies on top of build step 2 (`docs/superpowers/plans/2026-11-12-agy-plugin-step2-job-control.md`), which must be merged first.
The controller's decisions in `docs/superpowers/plans/notes-step3.md` bind this step and extend the spec where they differ: the repository store as the default of `status`, `result` and `cancel`, `status --all`, the runner's keyword-only task parameters, `AGYCALL_AGY_CONFIG`, and the fake agy's `write` field.
Reviews (spec section 8) are step 4; nothing here creates review snapshots.

## Global Constraints

- Python 3.9+ standard library only in agycall/.
- macOS and Linux only.
- Never the em dash character (U+2014) or the word "honest" in any file.
- One sentence per line in long Markdown.
- Public repo, so no personal paths or private data.
- Tests never run real agy and never read the real ~/.gemini config (every runner/worker/CLI test uses the `fake` fixture, subprocesses inherit AGYCALL_AGY, and the shared-config path is overridden).
- Tests that use git create temporary repositories with local user.name/user.email.
- A PreToolUse hook refuses Bash commands whose text contains the old wrapper's delegate or job script names, or the word agy followed by -p/--print/-i, so implementers write files with the Write/Edit tools and commit with `git commit -F "$TMPDIR/agycall-msg.txt"`.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_013coVpN7if3z6kaMYLTrz3c`.
- Never run the `agy-call` found on PATH (old installed code), use `python3 bin/agy-call` or `python3 -m agycall.cli`.
- `run` and `batch` behaviour stays unchanged: every new runner parameter is keyword-only with a default that keeps today's code path, and `call()`, `batch()` and `enqueue()` keep their signatures.
- The `repo` fixture also sets `GIT_CONFIG_GLOBAL=/dev/null` and `GIT_CONFIG_NOSYSTEM=1`, so a developer's own git settings (commit signing, hooks paths) cannot change a test.
- The background worker still gets only `--store` and `--job` on its command line; a task worker reads everything else from the job record.
- Every test that uses real processes waits with a bounded loop (`wait_for`, 30 seconds at most), and the suite is run three times at the end of each code task.

## Review Focus

- Cleanup after the check left ignored build output (`build/`, ignored by `.gitignore`) in the worktree: the worktree and branch are removed without `--force` (Task 5, `test_merge_makes_the_merge_commit_and_cleanup_removes_the_worktree_and_branch`).
- A merge whose check command writes ignored build output: the worktree still counts as unchanged after the check and the merge goes ahead (Task 5, the same test, whose check is `mkdir -p build && touch build/out.o && test -f b.txt`).
- `agy-call status` and `agy-call result` run from inside the task's worktree, without `--store`: they find the task in the repository store, because a linked worktree shares the repository id (Task 4, `test_a_background_task_runs_in_a_detached_worker_and_is_found_from_its_worktree`).
- A shared grant added to `~/.gemini/config/config.json` between a task's start and its rerun after a quota stop: the rerun's grant denies it, and the request still matches, so the rerun does not exit 24 (Task 2, `test_a_grant_added_to_the_shared_config_is_denied_on_the_rerun`).
- `task --continue` after the process running the previous turn died with agy's complete reply on disk: that turn is recorded `interrupted` first, so its reply is never accepted as the new segment's answer and the new message is sent (Task 4, `test_continue_after_the_controller_died_settles_the_old_turn_first`).

## Spec clarifications made by this plan

- A task's identity (spec 7.2) is the SHA-256 of the task text, model, effort, check command, repository id, base commit and the SHA-256 of the grant's `allow` list.
  The `deny` mirror is left out: it follows the user's shared grants, and a rerun must not exit 24 because they changed.
  A rerun takes the base commit from the record, so it neither needs a clean checkout nor exits 24 after the base branch moved on.
- The grant file of the current segment is written again, with the deny mirror read afresh, each time a process drives the task (foreground start, rerun, background worker, `--continue`); each launch records the grant path and the SHA-256 of the file it used in `launch.json`.
- The first segment also gets `segments/001.json`, so every segment, the first included, is driven the same way from the record.
- Rerunning the same `task` command drives the latest segment; `--continue` always appends a new one.
  `--continue` takes `--allow`, `--max-turns`, `--deadline`, `--background` and `--prompt-file`; model, effort, timeout and check come from the record.
- `--continue` records an attempt that a dead controller left unsettled as `interrupted` before it writes the new segment; its saved output stays in `agy.out`.
- `--continue` verifies the conversation by requiring that the task has one recorded (exit 23 otherwise); the repository id matches by construction, because the record is found through the current repository's store.
- A renamed cancel request is `cancel.request.<NNN>.done`, where NNN is the number of the segment that consumed it.
- A task whose controller died, when the saved output does not settle the turn, ends in state `failed` (exit 20) with the attempt's outcome `interrupted`, and a rerun keeps that stop.
- The `NEED:`-only follow-up (spec 4.6) always ends `blocked`, whatever it returns; when the segment's budget is already used up, the task ends `failed` with the turn limit instead.
- `--check-timeout` defaults to 600 seconds for a task (the spec gives no default; a build and its tests need more than `run`'s 120).
- `--merge` also refuses (26) a task branch that has no commit its base branch lacks, because git would print "Already up to date" and make no merge commit.
- Spec 10.2 asks for a merge conflict test, but step 2 of 4.8 requires the base tip to be an ancestor of the task branch, so `git merge` cannot conflict; the abort path is tested with a failing `pre-merge-commit` hook, and `merge-failed.json` still lists conflicting files if git ever reports any.
- The merge's check gets the task's saved answer on stdin, as during the task.
- The hand-back shows `git diff --stat <base commit>`, so it still lists the task's changes after Claude commits them.
- Cleanup and discard delete the branch with `git update-ref -d refs/heads/agy/<job> <tip>`, which removes it only while it still points at the tip read under the lock; `git branch -d` would test the merge against whatever branch the checkout has open.
- A crash between `git worktree add` and the write of `request.json` leaves a branch and a folder with no record; the next start exits 24 and names both, so the user can remove them.
- The repository lock is held through spec 4.3 step 5 as well, while the grant, segment and `request.json` are written.
- Task jobs use the state `setup-error` where `run` jobs keep `fatal`; `result` exits with the recorded code for both.
- The job store folder of a repository and `~/.agy-call/worktrees/<repo id>` are made mode 0700; files are written through `store.write_atomic`, whose temporary files are created 0600.

## File map

| File | Change | Responsibility |
|---|---|---|
| `agycall/errors.py`, `agycall/__init__.py` | modify | `RepoState` (26), `Blocked` (28) |
| `agycall/worktree.py` | create | repository id and store, worktree creation, hand-back, merge, cleanup, discard, repository lock |
| `agycall/task.py` | create | grant file and deny mirror, wrapped prompt, task identity, segments, `start`, `continue_`, `drive` |
| `agycall/runner.py` | modify | keyword-only task parameters in `_prepare`, `_lock`/`_own`, segment-scoped attempts, the task rows of spec 5.2, segment deadline |
| `agycall/checks.py` | modify | `run_cmd(..., combined=True)`: stderr joined to stdout, any non-zero exit is a finding of the last 4000 characters |
| `agycall/control.py` | modify | task hand-back in `result`, `blocked`/`setup-error` codes, `kind` column, `status_all`, `spawn`, task workers |
| `agycall/cli.py` | modify | `task` with `--continue`, `--background`, `--merge`, `--cleanup`, `--discard`; `status --all`; repository store default |
| `tests/fake_agy.py` | modify | `write` turn field; logs the `--settings` file |
| `tests/agyturns.py`, `tests/conftest.py` | modify | `git` helper, `repo` fixture, `AGYCALL_AGY_CONFIG` override |
| `tests/test_worktree.py`, `tests/test_task.py` | create | worktrees, grants, the task policy, continuation, background, merge |
| `skills/agy-call/SKILL.md`, `README.md` | modify | coding tasks, states, exit codes 26 and 28 |

---

### Task 1: Repository store, worktree creation, grant and wrapped prompt

**Files:**
- Create: `agycall/worktree.py`, `agycall/task.py`
- Modify: `agycall/errors.py`, `agycall/__init__.py`, `tests/agyturns.py`, `tests/conftest.py`, `tests/fake_agy.py`
- Test: `tests/test_worktree.py`, `tests/test_task.py`

**Interfaces:**
- Consumes: `store.home()`, `store.JOB_ID`, `store.flock(path, blocking=True)`, `store.write_atomic(path, data)`.
- Produces:
  - `errors.RepoState(AgyCallError)` with `code = 26`; `errors.Blocked(findings, job_dir=None)` with `code = 28`, `.findings` and `.job_dir`; both exported from `agycall`.
  - `worktree.Repo(top, id, store)`; `worktree.repo(cwd=None) -> Repo`, raising `RepoState("not inside a git repository: ...")` outside one; `id` is the first 12 hex of the SHA-256 of the real path of the git common directory, and `store` is `<AGYCALL_HOME>/jobs/<id>`.
  - `worktree.git(cwd, *args, env=None) -> str` (stdout; `RepoState` on a non-zero exit), `worktree.private(path) -> path` (mode 0700), `worktree.worktree_path(r, job_id) -> <AGYCALL_HOME>/worktrees/<id>/<job>`, `worktree.branch(job_id) -> "agy/<job>"`, `worktree.check_job_id(job_id)` (`ValueError`), `worktree.changes(path) -> list[str]` (porcelain lines), `worktree.current_branch(path) -> str` (`""` when detached), `worktree.lock(r)` (context manager on `<store>/repo.lock`), `worktree.create(r, job_id) -> (base_branch, base_commit)`.
  - `task.READ_ONLY`, `task.PROMPT`, `task.config_path()` (`AGYCALL_AGY_CONFIG` or `~/.gemini/config/config.json`), `task.shared_grants() -> list[str]` (`ValueError` for a file of another shape), `task.check_allow(allow)`, `task.commands(allow) -> list[str]`, `task.grant(worktree, allow) -> {"permissions": {"allow": [...], "deny": [...]}}`, `task.write_grant(path, worktree, allow) -> sha256 hex`, `task.prompt(worktree, allow, text) -> str`.
  - Test helpers: `agyturns.git(cwd, *args) -> stdout`; the `repo` fixture; `AGYCALL_AGY_CONFIG` set to `<tmp>/agy-config.json` in every test; a fake turn's `write` field; `settings` in each `FAKE_LOG` record.

- [ ] **Step 1: Write the failing tests**

These tests need the `repo` fixture and `agyturns.git`, so the test helpers come first.

In `tests/agyturns.py`, replace:

```python
"""Builders for tests/fake_agy.py turns, one per shape of agy run the classifier knows, and a bounded wait for tests
that drive real processes."""
import time
```

with:

```python
"""Builders for tests/fake_agy.py turns, one per shape of agy run the classifier knows, a bounded wait for tests
that drive real processes, and git for tests that use a repository."""
import subprocess
import time
```

Append to `tests/agyturns.py`, after two blank lines:

```python
def git(cwd, *args):
    """Run git in cwd for a test and return its stdout; a failure fails the test."""
    return subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True).stdout
```

In `tests/conftest.py`, replace:

```python
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
```

with:

```python
import pytest

from agyturns import git

HERE = os.path.dirname(os.path.abspath(__file__))
```

In `tests/conftest.py`, replace:

```python
    monkeypatch.delenv("AGYCALL_SESSION", raising=False)  # a developer's own session must not leak into tests
```

with:

```python
    monkeypatch.delenv("AGYCALL_SESSION", raising=False)  # a developer's own session must not leak into tests
    monkeypatch.setenv("AGYCALL_AGY_CONFIG", str(tmp_path / "agy-config.json"))  # never the user's real shared grants
```

Append to `tests/conftest.py`, after two blank lines:

```python
@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A git repository with one commit on main (a.txt, and a .gitignore that ignores build/), a local identity, no
    global or system git configuration, and the test's working directory inside it."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.name", "Test")
    git(r, "config", "user.email", "test@example.com")
    (r / ".gitignore").write_text("build/\n")
    (r / "a.txt").write_text("a\n")
    git(r, "add", ".")
    git(r, "commit", "-q", "-m", "init")
    monkeypatch.chdir(r)
    return r
```

In `tests/fake_agy.py`, the stand-in gains the `write` turn field and logs the `--settings` file.

In `tests/fake_agy.py`, replace:

```python
turn repeats). A turn has exit (default 0), stderr, sleep, and either the envelope fields status (default SUCCESS),
response, error, denied_actions, conversation and usage, or raw_hex (the exact stdout bytes, for malformed output).
Every print-mode launch is logged to FAKE_LOG as {"argv", "prompt", "cwd"}."""
```

with:

```python
turn repeats). A turn has exit (default 0), stderr, sleep, write (files to create in the working directory, as
{"relative/path": "content"}, written before the sleep), and either the envelope fields status (default SUCCESS),
response, error, denied_actions, conversation and usage, or raw_hex (the exact stdout bytes, for malformed output).
Every print-mode launch is logged to FAKE_LOG as {"argv", "prompt", "cwd"}, plus "settings" (the parsed --settings
file) when one is passed."""
```

In `tests/fake_agy.py`, replace:

```python
with open(os.environ["FAKE_LOG"], "a") as fh:
    fh.write(json.dumps({"argv": args, "prompt": args[args.index("-p") + 1], "cwd": os.getcwd()}) + "\n")
t = turns[min(n, len(turns) - 1)]
```

with:

```python
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
```

Create `tests/test_worktree.py`:

```python
import os
import stat

import pytest

from agycall import store, worktree
from agycall.errors import Mismatch, RepoState
from agyturns import git


def test_repo_id_is_shared_by_a_subfolder_and_a_linked_worktree(repo, tmp_path):
    r = worktree.repo()
    (repo / "sub").mkdir()
    assert worktree.repo(str(repo / "sub")) == r
    git(repo, "worktree", "add", "-q", "-b", "other", str(tmp_path / "linked"))
    linked = worktree.repo(str(tmp_path / "linked"))
    assert linked.id == r.id and linked.store == r.store and len(r.id) == 12
    assert r.store == os.path.join(store.home(), "jobs", r.id)
    with pytest.raises(RepoState, match="not inside a git repository"):
        worktree.repo(str(tmp_path))


def test_create_adds_a_private_worktree_on_a_new_branch(repo):
    r = worktree.repo()
    assert worktree.create(r, "j1") == ("main", git(repo, "rev-parse", "HEAD").strip())
    wt = worktree.worktree_path(r, "j1")
    assert git(wt, "rev-parse", "--abbrev-ref", "HEAD").strip() == "agy/j1" and os.path.exists(os.path.join(wt, "a.txt"))
    assert stat.S_IMODE(os.stat(os.path.dirname(wt)).st_mode) == 0o700
    with worktree.lock(r):
        assert stat.S_IMODE(os.stat(r.store).st_mode) == 0o700
    with pytest.raises(Mismatch, match="agy/j1"):  # the branch and the folder belong to a job already
        worktree.create(r, "j1")


def test_create_refuses_a_checkout_with_changes(repo):
    r = worktree.repo()
    (repo / "new.txt").write_text("x")
    with pytest.raises(RepoState, match="new.txt"):  # untracked
        worktree.create(r, "j1")
    (repo / "new.txt").unlink()
    (repo / "a.txt").write_text("changed\n")
    with pytest.raises(RepoState, match="a.txt"):  # unstaged
        worktree.create(r, "j1")
    git(repo, "add", "a.txt")
    with pytest.raises(RepoState, match="a.txt"):  # staged
        worktree.create(r, "j1")
    assert not os.path.exists(worktree.worktree_path(r, "j1"))
    git(repo, "checkout", "-q", "HEAD", "--", "a.txt")
    (repo / "build").mkdir()
    (repo / "build" / "out.o").write_text("ignored")  # ignored files do not count
    worktree.create(r, "j1")


def test_create_refuses_a_detached_head_and_a_branch_or_folder_that_exists(repo):
    r = worktree.repo()
    git(repo, "checkout", "-q", "--detach")
    with pytest.raises(RepoState, match="detached"):
        worktree.create(r, "j1")
    git(repo, "checkout", "-q", "main")
    os.makedirs(worktree.worktree_path(r, "j2"))
    with pytest.raises(Mismatch):
        worktree.create(r, "j2")
    git(repo, "branch", "agy/j3")
    with pytest.raises(Mismatch):
        worktree.create(r, "j3")


def test_task_ids_must_make_a_branch_name_git_accepts():
    worktree.check_job_id("fix-1.2_x")
    for bad in ("a..b", "a.lock", "a.", "-x", "a/b", ""):
        with pytest.raises(ValueError):
            worktree.check_job_id(bad)
```

Create `tests/test_task.py`:

```python
import json

import pytest

from agycall import task


def _config(tmp_path, allow):
    """agy's shared config with these shared grants, at the path the conftest points AGYCALL_AGY_CONFIG to."""
    (tmp_path / "agy-config.json").write_text(json.dumps({"userSettings": {"globalPermissionGrants": {"allow": allow}}}))


def test_grant_mirrors_every_other_shared_grant_into_deny(tmp_path):
    _config(tmp_path, ["command(gcloud run)", "command(git status)", "write_file(/elsewhere)", "command(git)"])
    g = task.grant("/wt", ["npm test", "ls"])["permissions"]
    assert g["allow"] == ["write_file(/wt)"] + ["command(%s)" % c for c in task.READ_ONLY] + ["command(npm test)"]
    assert g["deny"] == ["command(gcloud run)", "write_file(/elsewhere)", "command(git)"]


def test_a_missing_shared_config_or_key_means_no_deny(tmp_path):
    assert task.grant("/wt", [])["permissions"]["deny"] == []
    (tmp_path / "agy-config.json").write_text(json.dumps({"userSettings": {"other": 1}}))
    assert task.grant("/wt", [])["permissions"]["deny"] == []


@pytest.mark.parametrize("content", [
    "{not json", "[]",
    json.dumps({"userSettings": {"globalPermissionGrants": {"allow": "command(x)"}}}),
    json.dumps({"userSettings": {"globalPermissionGrants": {"allow": [1]}}}),
    json.dumps({"userSettings": ["x"]})])
def test_an_unreadable_shared_config_is_refused(tmp_path, content):
    (tmp_path / "agy-config.json").write_text(content)
    with pytest.raises(ValueError, match="shared grants"):
        task.grant("/wt", [])


def test_allow_entries_and_the_wrapped_prompt():
    task.check_allow(["npm run build", "swift test"])
    for bad in ("", " ", "a\nb", "rm (x)"):
        with pytest.raises(ValueError):
            task.check_allow([bad])
    p = task.prompt("/wt", ["npm test", "ls"], "Add a flag.")
    assert "git worktree /wt." in p and "- npm test\n" in p and p.count("- ls\n") == 1 and p.endswith("\n\nAdd a flag.")
    assert "`NEED: <command>`" in p and "Do not commit, branch or push." in p and "at most 15 lines" in p
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uvx pytest -q tests/test_worktree.py tests/test_task.py`
Expected: FAIL at collection for both files (`ImportError: cannot import name 'worktree'` and `'task'` from `agycall`).

- [ ] **Step 3: Add the two exit codes**

Append to `agycall/errors.py`, after two blank lines:

```python
class RepoState(AgyCallError):
    code = 26


class Blocked(AgyCallError):
    code = 28

    def __init__(self, findings, job_dir=None):
        super().__init__("blocked on permission: " + "; ".join(findings)[-2000:])
        self.findings, self.job_dir = list(findings), job_dir
```

In `agycall/__init__.py`, replace:

```python
from .errors import AgyCallError, Cancelled, CheckError, Fatal, GaveUp, Mismatch, NoConversation, Quota, Running  # noqa: F401
```

with:

```python
from .errors import (AgyCallError, Blocked, Cancelled, CheckError, Fatal, GaveUp, Mismatch, NoConversation,  # noqa: F401
                     Quota, RepoState, Running)
```

- [ ] **Step 4: Create `agycall/worktree.py`**

Create `agycall/worktree.py`:

```python
"""Task worktrees (spec 3.3, 4.3, 4.8, 4.9): the repository store, a task's worktree and branch, the hand-back, and
merge, cleanup and discard."""
import collections
import hashlib
import os
import subprocess

from . import store as st
from .errors import Mismatch, RepoState

Repo = collections.namedtuple("Repo", "top id store")  # the checkout's top folder, the repository id, its job store


def git(cwd, *args, env=None):
    """Run git in cwd and return its stdout; a failure raises RepoState with the end of git's message."""
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=env)
    if p.returncode:
        raise RepoState("git %s failed in %s: %s" % (" ".join(args), cwd, (p.stderr or p.stdout).strip()[-1000:]))
    return p.stdout


def _ok(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True).returncode == 0


def private(path):
    """Create path if needed and make it readable by this user only (spec 3.3)."""
    os.makedirs(path, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def repo(cwd=None):
    """The repository that holds cwd. Its id is the first 12 hex characters of the SHA-256 of the real path of its git
    common directory, so the main checkout, a subfolder and every linked worktree share one id and one job store."""
    cwd = os.path.abspath(cwd or os.getcwd())
    try:
        top, common = git(cwd, "rev-parse", "--show-toplevel", "--git-common-dir").splitlines()[:2]
    except (RepoState, ValueError):
        raise RepoState("not inside a git repository: %s" % cwd)
    rid = hashlib.sha256(os.path.realpath(os.path.join(cwd, common)).encode("utf-8")).hexdigest()[:12]
    return Repo(top, rid, os.path.join(st.home(), "jobs", rid))


def worktree_path(r, job_id):
    return os.path.join(st.home(), "worktrees", r.id, job_id)


def branch(job_id):
    return "agy/" + job_id


def check_job_id(job_id):
    """A task id follows the job id rule and makes a branch name git accepts (spec 4.3); otherwise ValueError (exit 2)."""
    if not isinstance(job_id, str) or not st.JOB_ID.match(job_id):
        raise ValueError("job id must match [A-Za-z0-9][A-Za-z0-9._-]{0,119}: %r" % (job_id,))
    if not _ok(None, "check-ref-format", "--branch", branch(job_id)):
        raise ValueError("job id %r does not make a valid branch name (%s)" % (job_id, branch(job_id)))


def changes(path):
    """The lines of `git status --porcelain` in a checkout or worktree: staged, unstaged and untracked-not-ignored."""
    return git(path, "status", "--porcelain").splitlines()


def current_branch(path):
    """The branch checked out in path, or "" when HEAD is detached."""
    return subprocess.run(["git", "symbolic-ref", "-q", "--short", "HEAD"], cwd=path, capture_output=True,
                          text=True).stdout.strip()


def lock(r):
    """The repository lock, held while a task is created, merged, cleaned up or discarded."""
    return st.flock(os.path.join(private(r.store), "repo.lock"))


def create(r, job_id):
    """Spec 4.3 steps 1 to 4, run under the repository lock: refuse a checkout with changes or a detached HEAD (26) and a
    branch or folder that is already there (24), then add the worktree on a new branch from HEAD.
    Returns (base_branch, base_commit)."""
    dirty = changes(r.top)
    if dirty:
        raise RepoState("the checkout %s has uncommitted or untracked files; commit, stash or ignore them first:\n%s"
                        % (r.top, "\n".join(dirty)))
    head = current_branch(r.top)
    if not head:
        raise RepoState("HEAD is detached in %s; check out the branch the task should start from" % r.top)
    base = git(r.top, "rev-parse", "--verify", "HEAD").strip()
    wt, br = worktree_path(r, job_id), branch(job_id)
    if _ok(r.top, "show-ref", "--verify", "--quiet", "refs/heads/" + br) or os.path.lexists(wt):
        raise Mismatch("branch %s or folder %s already exists and belongs to another job; use another job id, or "
                       "remove them if they are left over" % (br, wt))
    private(os.path.dirname(wt))
    git(r.top, "worktree", "add", "-q", "-b", br, wt, base)
    return head, base
```

- [ ] **Step 5: Create `agycall/task.py`**

Create `agycall/task.py`:

```python
"""Coding tasks (spec 4): the grant file with its deny mirror, the wrapped prompt, and starting, continuing and driving a
task through the runner's task policy."""
import hashlib
import json
import os

from . import store as st

READ_ONLY = ("ls", "cat", "head", "tail", "wc", "grep", "rg", "git status", "git diff", "git log", "git show")
PROMPT = """You are working on a coding task in the git worktree {worktree}.
Make every change inside that folder; do not write files anywhere else.
The commands you may run are exactly these:
{commands}
Before running any command that is not on the list, stop and reply with one line `NEED: <command>` per command instead of trying it.
Do not commit, branch or push.
End with at most 15 lines: files changed, checks run and their results, anything not done; never include diffs or file contents.

The task:

{text}"""


def config_path():
    """agy's shared config; AGYCALL_AGY_CONFIG overrides the path (tests always set it)."""
    return os.path.expanduser(os.environ.get("AGYCALL_AGY_CONFIG") or "~/.gemini/config/config.json")


def shared_grants():
    """userSettings.globalPermissionGrants.allow of agy's shared config, which agy adds to every --settings run
    (spec 4.2). A missing file or key means none; a file of any other shape raises ValueError, because the deny mirror
    cannot be built from it."""
    path = config_path()
    bad = "cannot read agy's shared grants in %s; fix the file before starting a task" % path
    try:
        with open(path, encoding="utf-8") as fh:
            node = json.load(fh)
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as e:
        raise ValueError("%s (%s)" % (bad, e))
    for key in ("userSettings", "globalPermissionGrants", "allow"):
        if not isinstance(node, dict):
            raise ValueError(bad)
        node = node.get(key)
        if node is None:
            return []
    if not isinstance(node, list) or not all(isinstance(g, str) for g in node):
        raise ValueError(bad)
    return node


def check_allow(allow):
    for a in allow:
        if not isinstance(a, str) or not a.strip() or any(ch in a for ch in "\r\n()"):
            raise ValueError("an --allow entry must be a non-empty command without newlines or parentheses: %r" % (a,))


def commands(allow):
    """The commands a task may run: the read-only set, then the --allow entries, each once."""
    return list(dict.fromkeys(READ_ONLY + tuple(allow)))


def grant(worktree, allow):
    """The --settings content of a task (spec 4.2): writes inside the worktree and the allowed commands; every shared
    grant that is not exactly one of these goes in deny, which agy checks before allow."""
    own = ["write_file(%s)" % worktree] + ["command(%s)" % c for c in commands(allow)]
    return {"permissions": {"allow": own, "deny": list(dict.fromkeys(g for g in shared_grants() if g not in own))}}


def write_grant(path, worktree, allow):
    """Write the grant file with the deny mirror as the shared config is now; returns the file's SHA-256."""
    data = json.dumps(grant(worktree, allow), indent=1)
    st.write_atomic(path, data)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def prompt(worktree, allow, text):
    """The task text inside the fixed instructions of spec 4.4."""
    return PROMPT.format(worktree=worktree, commands="\n".join("- " + c for c in commands(allow)), text=text)
```

- [ ] **Step 6: Run the tests**

Run: `uvx pytest -q tests/test_worktree.py tests/test_task.py`
Expected: all pass.
Run: `for i in 1 2 3; do uvx pytest -q tests/ || break; done`
Expected: every run passes (191 tests).

- [ ] **Step 7: Commit**

```bash
git add agycall/errors.py agycall/__init__.py agycall/worktree.py agycall/task.py tests/agyturns.py tests/conftest.py tests/fake_agy.py tests/test_worktree.py tests/test_task.py
printf '%s\n' "Create task worktrees and build a task's grant and prompt" \
  "" \
  "worktree.py finds the repository and its store, and creates a task's" \
  "worktree on agy/<job>, refusing a checkout with changes, a detached HEAD," \
  "a branch or folder that exists, and a job id git refuses as a branch." \
  "task.py builds the grant with the deny mirror of agy's shared grants" \
  "and the wrapped prompt. Exit codes 26 and 28 are added." \
  "" \
  "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" "Claude-Session: https://claude.ai/code/session_013coVpN7if3z6kaMYLTrz3c" > "$TMPDIR/agycall-msg.txt"
git commit -F "$TMPDIR/agycall-msg.txt"
```

---

### Task 2: Task start and the runner's task policy

**Files:**
- Modify: `agycall/runner.py`, `agycall/checks.py`, `agycall/task.py`, `agycall/worktree.py`, `agycall/control.py`, `agycall/cli.py`
- Test: `tests/test_task.py`

**Interfaces:**
- Consumes: everything Task 1 produces; step 2's `runner._prepare`, `runner._drive`, `runner._settle`, `runner._check`, `runner._now`, `control.result`.
- Produces:
  - `runner._prepare(..., settings=None, effort=None, policy="run", segment=None, end=None)`; the returned state gains `policy`, `settings` (grant file path), `effort`, `segment` (int or `None`), `end` (aware `datetime` or `None`), `fix` (`FIX` or `FIX_TASK`) and `fatal` (`"fatal"` or `"setup-error"`).
  - `runner._lock(job, stack) -> int` (the lock descriptor; `Running` when held; runs `recover`), `runner._own(c, stack)` (the controller record), `runner._atts(c) -> list[str]` (the current segment's attempts for a task, all attempts otherwise), `runner.FIX_TASK`, `runner._next(prompt, last, launch, fix=FIX)`.
  - A task's `launch.json` gains `segment`, `grant` and `grantSha`.
  - `checks.TAIL = 4000`; `checks.run_cmd(..., combined=False)`.
  - `task.NO_CHANGE`, `task.segments(job) -> list[int]`, `task.start(job_id, text, *, model, check_cmd, allow=(), effort=None, check_timeout=600, timeout="60m", max_turns=3, deadline="2h", cwd=None, new_version=False) -> store.Job`, `task.drive(job) -> runner.Result`.
  - A task's `request.json`: `identity`, `kind: "task"`, `prompt`, `key`, `cwd`, `created`, `session`, and `task: {repo, checkout, worktree, branch, baseBranch, baseCommit, model, effort, allow, check, checkTimeout, timeout}`; `segments/NNN.json`: `{segment, text, prompt, allow, grant, grantSha, maxTurns, deadline, started, end}`.
  - `worktree.handback(job) -> list[str]`; `control.result` appends it for a task and exits with the recorded code for `setup-error`.
  - CLI: `agy-call task --job ID --model M [--effort E] [--allow CMD]... --check-cmd CMD [--check-timeout S] [--timeout T] [--max-turns N] [--deadline T] [--new-version] --prompt-file F` prints the hand-back and exits with the `result` code.

- [ ] **Step 1: Write the failing tests**

In `tests/test_task.py`, replace:

```python
import json

import pytest

from agycall import task
```

with:

```python
import json
import os
import stat
import subprocess
import sys

import pytest

from agycall import control, quota, store, task, worktree
from agycall.errors import Fatal, GaveUp, Mismatch, Quota, RepoState
from agyturns import error_reply, git, model_error, quota_error, reply

HERE = os.path.dirname(os.path.abspath(__file__))
AGYCALL = os.path.join(os.path.dirname(HERE), "bin", "agy-call")


def run_cli(*args, **kw):
    return subprocess.run([sys.executable, AGYCALL, *args], capture_output=True, text=True, timeout=60, **kw)
```

Append to `tests/test_task.py`, after two blank lines:

```python
def _start(job_id="t1", text="Add b.txt.", **kw):
    args = dict(model="m", check_cmd="test -f b.txt")
    args.update(kw)
    return task.start(job_id, text, **args)


def _wt(job_id="t1"):
    return worktree.worktree_path(worktree.repo(), job_id)


def test_a_task_runs_in_its_worktree_with_its_grant_and_ends_done(repo, fake, tmp_path):
    set_turns, launches = fake
    _config(tmp_path, ["command(gcloud run)", "command(npm test)"])
    set_turns(reply("Created b.txt.", write={"b.txt": "b\n"}))
    job = _start(allow=["npm test"], effort="high", check_cmd="grep -q Created && test -f b.txt")
    r = task.drive(job)
    wt = _wt()
    assert r.answer == "Created b.txt." and job.status()["state"] == "done"
    first = launches()[0]
    assert os.path.realpath(first["cwd"]) == os.path.realpath(wt)
    assert first["argv"][first["argv"].index("--effort") + 1] == "high"
    assert first["argv"][first["argv"].index("--settings") + 1] == job.path("grant-1.json")
    perms = first["settings"]["permissions"]
    assert perms["allow"][0] == "write_file(%s)" % wt and "command(npm test)" in perms["allow"]
    assert perms["deny"] == ["command(gcloud run)"]
    assert first["prompt"] == task.prompt(wt, ["npm test"], "Add b.txt.")
    assert not (repo / "b.txt").exists() and open(os.path.join(wt, "b.txt")).read() == "b\n"
    launch = store.read_json(os.path.join(job.launched()[0], "launch.json"))
    assert launch["segment"] == 1 and launch["grant"] == job.path("grant-1.json") and len(launch["grantSha"]) == 64
    assert stat.S_IMODE(os.stat(job.path("grant-1.json")).st_mode) == 0o600
    req = store.read_json(job.path("request.json"))
    assert req["kind"] == "task" and req["task"]["baseBranch"] == "main" and req["task"]["branch"] == "agy/t1"


def test_a_failing_check_sends_the_end_of_its_output_back_in_the_same_conversation(repo, fake):
    set_turns, launches = fake
    set_turns(reply("Wrote b.", write={"b.txt": "wrong\n"}), reply("Fixed b.", write={"b.txt": "b\n"}))
    check = "printf '%09000d' 0 | tr 0 x; echo END; grep -q '^b$' b.txt || { echo 'b.txt is wrong' >&2; exit 3; }"
    r = task.drive(_start(check_cmd=check))
    assert r.answer == "Fixed b." and len(launches()) == 2
    second = launches()[1]
    assert second["argv"][second["argv"].index("--conversation") + 1] == "c1"
    assert "exited 3" in second["prompt"] and second["prompt"].endswith("END\nb.txt is wrong\n")
    assert "x" * 3900 in second["prompt"] and "x" * 4001 not in second["prompt"]  # only the last 4000 characters


def test_a_reply_that_changed_nothing_fails_the_check(repo, fake):
    set_turns, launches = fake
    set_turns(reply("Nothing to do."), reply("Done now.", write={"b.txt": "b\n"}))
    r = task.drive(_start())
    assert r.answer == "Done now." and task.NO_CHANGE in launches()[1]["prompt"]


def test_every_launch_of_a_segment_counts_against_max_turns(repo, fake):
    set_turns, launches = fake
    set_turns(reply("Wrote b.", write={"b.txt": "wrong\n"}))
    job = _start(check_cmd="grep -q '^b$' b.txt", max_turns=2)
    with pytest.raises(GaveUp):
        task.drive(job)
    assert len(launches()) == 2 and job.status()["state"] == "failed"
    set_turns(quota_error())
    job2 = _start("t2", model="m2", max_turns=1)
    with pytest.raises(Quota):
        task.drive(job2)
    with pytest.raises(GaveUp):  # the launch that met the quota used the segment's only turn
        task.drive(job2)
    assert len(launches()) == 3


def test_no_turn_starts_after_the_segment_deadline(repo, fake):
    set_turns, launches = fake
    set_turns(reply("Wrote b.", write={"b.txt": "wrong\n"}))
    job = _start(check_cmd="sleep 3; exit 1", deadline="2s")
    with pytest.raises(GaveUp, match="deadline passed"):
        task.drive(job)
    assert len(launches()) == 1 and job.status()["state"] == "failed"


def test_setup_errors_end_a_task_as_setup_error(repo, fake):
    set_turns, _ = fake
    set_turns(model_error())
    job = _start()
    with pytest.raises(Fatal) as e:
        task.drive(job)
    assert e.value.code == 14 and job.status()["state"] == "setup-error"
    assert control.result(job.store, "t1")[1] == 14


def test_a_rerun_returns_the_saved_task_and_a_changed_request_is_refused(repo, fake):
    set_turns, launches = fake
    set_turns(reply("Created b.txt.", write={"b.txt": "b\n"}))
    task.drive(_start())
    (repo / "c.txt").write_text("c\n")
    git(repo, "add", "c.txt")
    git(repo, "commit", "-q", "-m", "more")  # the base branch moved on; the rerun keeps the recorded base commit
    (repo / "d.txt").write_text("untracked")  # and the checkout does not need to be clean for a rerun
    assert task.drive(_start()).answer == "Created b.txt." and len(launches()) == 1
    with pytest.raises(Mismatch):
        _start(text="Add c.txt.")
    with pytest.raises(RepoState, match="--cleanup or --discard"):
        _start(new_version=True)


def test_a_grant_added_to_the_shared_config_is_denied_on_the_rerun(repo, fake, tmp_path):
    set_turns, launches = fake
    set_turns(quota_error(), reply("Created b.txt.", write={"b.txt": "b\n"}))
    job = _start()
    with pytest.raises(Quota):
        task.drive(job)
    assert launches()[0]["settings"]["permissions"]["deny"] == []
    _config(tmp_path, ["command(curl)"])  # the user approves something in the Antigravity app meanwhile
    quota.clear("m", quota.generation("m"))
    assert task.drive(_start()).answer == "Created b.txt."  # the same request: no exit 24
    assert launches()[1]["settings"]["permissions"]["deny"] == ["command(curl)"]


def test_a_reply_that_came_with_an_agy_error_is_judged_by_the_check_alone(repo, fake):
    set_turns, launches = fake
    set_turns(error_reply("Created b.txt."))  # nothing was written, so the check fails
    with pytest.raises(GaveUp, match="no follow-up was sent"):
        task.drive(_start())
    set_turns(dict(error_reply("Created b.txt."), write={"b.txt": "b\n"}))
    job = _start("t2")
    assert task.drive(job).answer == "Created b.txt." and len(launches()) == 2
    assert "agyError" in store.read_json(os.path.join(job.launched()[0], "result.json"))

def test_cli_task_prints_the_hand_back(repo, fake, tmp_path):
    set_turns, _ = fake
    set_turns(reply("Created b.txt.", write={"b.txt": "b\n"}))
    (tmp_path / "task.md").write_text("Add b.txt.")
    base = ("task", "--model", "m", "--prompt-file", str(tmp_path / "task.md"))
    r = run_cli(*base, "--job", "t1", "--check-cmd", "test -f b.txt", cwd=str(repo))
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith("Created b.txt.\n\nworktree: %s\nbranch: agy/t1\n" % _wt())
    assert "?? b.txt" in r.stdout
    r = run_cli(*base, "--job", "t2", cwd=str(repo))
    assert r.returncode == 2 and "--check-cmd" in r.stderr
    r = run_cli(*base, "--job", "a..b", "--check-cmd", "true", cwd=str(repo))
    assert r.returncode == 2 and "branch name" in r.stderr
    r = run_cli(*base, "--job", "t3", "--check-cmd", "true", cwd=str(tmp_path))
    assert r.returncode == 26 and "not inside a git repository" in r.stderr
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uvx pytest -q tests/test_task.py`
Expected: 10 failed, 8 passed: the eight Task 1 tests pass; the new tests fail with `AttributeError: module 'agycall.task' has no attribute 'start'`, and the CLI test with `invalid choice: 'task'` (exit 2).

- [ ] **Step 3: Give `agycall/runner.py` the task policy's main path**

Make these edits to the step 2 file, in this order.

In `agycall/runner.py`, replace:

```python
CANCELLED = "cancelled by request"
```

with:

```python
CANCELLED = "cancelled by request"
FIX_TASK = ("The check of your work failed. Fix the files in the worktree so that it passes, keep the work you already "
            "did, and end with the same short summary. What the check reported:\n\n{findings}")
```

In `agycall/runner.py`, replace:

```python
def _next(prompt, last, launch):
    if last is None:
```

with:

```python
def _next(prompt, last, launch, fix=FIX):
    if last is None:
```

In `agycall/runner.py`, replace:

```python
    msg = FIX.format(findings="; ".join(last.get("findings") or ["the reply was not usable"]))
```

with:

```python
    msg = fix.format(findings="; ".join(last.get("findings") or ["the reply was not usable"]))
```

In `agycall/runner.py`, replace:

```python
def _launch(c, att, message, kind, generation):
    cmd = agy.argv(agy.path(), message, model=c.model, timeout=c.timeout, mode=c.mode, dirs=c.dirs, conversation=c.conv)
    launch = {"pid": None, "pgid": None, "kind": kind, "message": message, "conversation": c.conv,
              "generation": generation, "started": _now(), "argv": agy.redact(cmd)}
    st.write_atomic(os.path.join(att, "launch.json"), launch)

    def started(pid, when):
        launch.update(pid=pid, pgid=pid, pstart=when)
        st.write_atomic(os.path.join(att, "launch.json"), launch)

    r = agy.run(att, cmd, cwd=c.cwd, deadline=agy.seconds(c.timeout) + agy.GRACE, fds=c.fds, on_start=started,
                should_stop=c.should_stop)
```

with:

```python
def _atts(c):
    """The attempts the job loop looks at: all of them for run and batch, the current segment's for a task."""
    atts = c.job.launched()
    if not c.segment:
        return atts
    return [a for a in atts if (st.read_json(os.path.join(a, "launch.json"), {}) or {}).get("segment", 1) == c.segment]


def _launch(c, att, message, kind, generation):
    cmd = agy.argv(agy.path(), message, model=c.model, timeout=c.timeout, mode=c.mode, dirs=c.dirs, conversation=c.conv,
                   settings=c.settings, effort=c.effort)
    launch = {"pid": None, "pgid": None, "kind": kind, "message": message, "conversation": c.conv,
              "generation": generation, "started": _now(), "argv": agy.redact(cmd)}
    deadline = agy.seconds(c.timeout) + agy.GRACE
    if c.segment:  # which segment and grant this launch used; the segment's deadline also bounds the turn
        with open(c.settings, "rb") as fh:
            launch.update(segment=c.segment, grant=c.settings, grantSha=hashlib.sha256(fh.read()).hexdigest())
        deadline = min(deadline, max(1, (c.end - dt.datetime.now(dt.timezone.utc)).total_seconds()))
    st.write_atomic(os.path.join(att, "launch.json"), launch)

    def started(pid, when):
        launch.update(pid=pid, pgid=pid, pstart=when)
        st.write_atomic(os.path.join(att, "launch.json"), launch)

    r = agy.run(att, cmd, cwd=c.cwd, deadline=deadline, fds=c.fds, on_start=started, should_stop=c.should_stop)
```

In `agycall/runner.py`, replace:

```python
        code = {"auth": 11, "model": 14}.get(name, 22)
        finish("fatal", outcome="fatal", findings=[reason])
```

with:

```python
        code = {"auth": 11, "model": 14}.get(name, 22)
        finish(c.fatal, outcome="fatal", findings=[reason])
```

In `agycall/runner.py`, replace:

```python
def _prepare(job_id, prompt, *, store=None, namespace=None, model=None, timeout="10m", cwd=None, dirs=(), mode=None,
             conversation=None, expect=None, require=None, schema=None, check_cmd=None, check_timeout=120, check=None,
             check_id=None):
    """Check a request before anything is written, and return the job's working state."""
```

with:

```python
def _prepare(job_id, prompt, *, store=None, namespace=None, model=None, timeout="10m", cwd=None, dirs=(), mode=None,
             conversation=None, expect=None, require=None, schema=None, check_cmd=None, check_timeout=120, check=None,
             check_id=None, settings=None, effort=None, policy="run", segment=None, end=None):
    """Check a request before anything is written, and return the job's working state. A task (policy "task") also
    passes its grant file (settings), its effort, its segment number and the segment's end (an aware datetime)."""
```

In `agycall/runner.py`, replace:

```python
    c.should_stop = lambda: "cancel" if _cancel_requested(job) else None
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
```

with:

```python
    c.should_stop = lambda: "cancel" if _cancel_requested(job) else None
    c.policy, c.settings, c.effort, c.segment, c.end = policy, settings, effort, segment, end
    c.fix, c.fatal = (FIX_TASK, "setup-error") if policy == "task" else (FIX, "fatal")
    return c


def _lock(job, stack):
    """Take the job lock without blocking, held until stack closes (Running when another process holds it), then
    recover what a dead controller left. Returns the lock's descriptor."""
    try:
        fd = stack.enter_context(st.flock(job.lock_path, blocking=False)).fileno()
    except st.Busy:
        raise Running("job %s is running in another process" % job.id)
    recover(job)  # before any archive: a live child must never lose its folder
    return fd


def _own(c, stack):
    """Record this process as the job's controller until stack closes. cancel reads it to know whether a live process
    will act on cancel.request; the record is cleared before the job lock is released, because the stack unwinds in
    reverse order."""
    c.job.set_status(controller={"pid": os.getpid(), "pstart": agy.start_time(os.getpid())})
    stack.callback(c.job.set_status, controller=None)


def _admit(c, stack, new_version):
    """Take the job lock (held until stack closes), refuse or archive a changed request, and write request.json for a
    new one."""
    job = c.job
    c.stack = stack
    c.fds.append(_lock(job, stack))
    req = st.read_json(job.path("request.json"))
```

In `agycall/runner.py`, replace:

```python
    done = job.launched()
    last_res = (st.read_json(os.path.join(done[-1], "result.json"), {}) or {}) if done else {}
```

with:

```python
    done = _atts(c)
    last_res = (st.read_json(os.path.join(done[-1], "result.json"), {}) or {}) if done else {}
```

In `agycall/runner.py`, replace:

```python
        atts = job.launched()
        launches = [st.read_json(os.path.join(a, "launch.json"), {}) or {} for a in atts]
        results = [st.read_json(os.path.join(a, "result.json"), {}) or {} for a in atts]
        message, kind = _next(prompt, results[-1] if results else None, launches[-1] if launches else None)
```

with:

```python
        atts = _atts(c)
        launches = [st.read_json(os.path.join(a, "launch.json"), {}) or {} for a in atts]
        results = [st.read_json(os.path.join(a, "result.json"), {}) or {} for a in atts]
        message, kind = _next(prompt, results[-1] if results else None, launches[-1] if launches else None, c.fix)
```

In `agycall/runner.py`, replace:

```python
        turns = sum(1 for l in launches if l.get("kind") not in ("retry", "retry-first"))
        if kind not in ("retry", "retry-first") and turns >= max_turns:
            findings = (results[-1].get("findings") if results else None) or ["turn limit reached"]
            job.set_status(state="failed", findings=findings)
            raise GaveUp(findings, c.conv, _usage(job), job.dir)
```

with:

```python
        if c.segment:  # spec 5.3: every launch of a task segment counts, retries and follow-ups included
            turns = len(launches)
        else:
            turns = sum(1 for l in launches if l.get("kind") not in ("retry", "retry-first"))
        if (c.segment or kind not in ("retry", "retry-first")) and turns >= max_turns:
            findings = (results[-1].get("findings") if results else None) or ["turn limit reached"]
            job.set_status(state="failed", findings=findings)
            raise GaveUp(findings, c.conv, _usage(job), job.dir)
        if c.end and dt.datetime.now(dt.timezone.utc) >= c.end:
            findings = ["the segment's deadline passed at %s; continue with task --continue" % c.end.isoformat()]
            job.set_status(state="failed", findings=findings)
            raise GaveUp(findings, c.conv, _usage(job), job.dir)
```

In `agycall/runner.py`, replace:

```python
            job.set_status(state="fatal", findings=[e.reason], code=e.code)
```

with:

```python
            job.set_status(state=c.fatal, findings=[e.reason], code=e.code)
```

In `agycall/runner.py`, replace:

```python
    with contextlib.ExitStack() as stack:
        _admit(c, stack, new_version)
        # cancel reads this to know whether a live process will act on cancel.request; the callback runs before the
        # job lock is released, because the stack unwinds in reverse order
        c.job.set_status(controller={"pid": os.getpid(), "pstart": agy.start_time(os.getpid())})
        stack.callback(c.job.set_status, controller=None)
        return _drive(c, max_turns, ignore_quota, fresh)
```

with:

```python
    with contextlib.ExitStack() as stack:
        _admit(c, stack, new_version)
        _own(c, stack)
        return _drive(c, max_turns, ignore_quota, fresh)
```

`call()` and `enqueue()` behave as before: `_admit` is the old lock code moved into `_lock`, and `_own` is the old controller record moved out of `call()`.

- [ ] **Step 4: Let the check command return its combined output**

In `agycall/checks.py`, replace:

```python
TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}
```

with:

```python
TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}
TAIL = 4000  # characters of a task check's output sent back as the finding
```

In `agycall/checks.py`, replace:

```python
def run_cmd(cmd, text, timeout, cwd=None, fds=(), on_start=None, should_stop=None):
    """Run a check command in its own process group, with text on stdin and the descriptors fds inherited.
    on_start(pid, start time) is called once it runs; should_stop() is polled every agy.POLL seconds, and "cancel" stops
    the group and raises Cancelled. Exit 0 passes ([]), exit 1 rejects (its stdout is the finding), and any other exit,
    a timeout or a failure to start is a CheckError."""
```

with:

```python
def run_cmd(cmd, text, timeout, cwd=None, fds=(), on_start=None, should_stop=None, combined=False):
    """Run a check command in its own process group, with text on stdin and the descriptors fds inherited.
    on_start(pid, start time) is called once it runs; should_stop() is polled every agy.POLL seconds, and "cancel" stops
    the group and raises Cancelled. Exit 0 passes ([]), exit 1 rejects (its stdout is the finding), and any other exit,
    a timeout or a failure to start is a CheckError. With combined (a task's check, spec 4.5) stderr joins stdout, and
    every exit but 0 rejects with the last TAIL characters of that output as the finding."""
```

In `agycall/checks.py`, replace:

```python
            p = subprocess.Popen(cmd, shell=True, stdin=inp, stdout=out, stderr=err, cwd=cwd, start_new_session=True,
                                 pass_fds=tuple(fds))
```

with:

```python
            p = subprocess.Popen(cmd, shell=True, stdin=inp, stdout=out, stderr=subprocess.STDOUT if combined else err,
                                 cwd=cwd, start_new_session=True, pass_fds=tuple(fds))
```

In `agycall/checks.py`, replace:

```python
    if p.returncode == 0:
        return []
    if p.returncode == 1:
```

with:

```python
    if p.returncode == 0:
        return []
    if combined:
        return ["the check command exited %s; the end of its output:\n%s" % (p.returncode, stdout[-TAIL:])]
    if p.returncode == 1:
```

- [ ] **Step 5: Add `start`, `drive` and the task check to `agycall/task.py`**

In `agycall/task.py`, replace:

```python
import hashlib
import json
import os

from . import store as st
```

with:

```python
import contextlib
import datetime as dt
import hashlib
import json
import os
import re

from . import agy, checks, runner, worktree
from . import store as st
from .errors import CheckError, Mismatch, RepoState
```

In `agycall/task.py`, replace:

```python

The task:

{text}"""
```

with:

```python

The task:

{text}"""
NO_CHANGE = "no file in the worktree changed"
```

Append to `agycall/task.py`, after two blank lines:

```python
def _identity(text, model, effort, check_cmd, repo_id, base_commit, own):
    """A task's identity (spec 7.2): the request, the repository, the base commit and the SHA-256 of the grant's allow
    list. The deny mirror is left out: it follows the user's shared grants, and a rerun must not exit 24 because those
    changed."""
    payload = {"text": text, "model": model, "effort": effort, "check": check_cmd, "repo": repo_id, "base": base_commit,
               "grant": hashlib.sha256(json.dumps(own).encode("utf-8")).hexdigest()}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def segments(job):
    """The numbers of a task's segments, oldest first."""
    d = job.path("segments")
    names = os.listdir(d) if os.path.isdir(d) else []
    return sorted(int(n[:-5]) for n in names if re.fullmatch(r"\d{3,}\.json", n))


def _segment_path(job, n):
    return job.path("segments", "%03d.json" % n)


def _write_segment(job, n, text, wt, allow, grant_n, max_turns, deadline):
    """Write segments/NNN.json (spec 4.7): the wrapped prompt, the grant file and its digest, and the segment's own
    budget and end time."""
    started = dt.datetime.now(dt.timezone.utc)
    seg = {"segment": n, "text": text, "prompt": prompt(wt, allow, text), "allow": list(allow),
           "grant": job.path("grant-%d.json" % grant_n), "maxTurns": max_turns, "deadline": deadline,
           "started": started.isoformat(), "end": (started + dt.timedelta(seconds=agy.seconds(deadline))).isoformat()}
    seg["grantSha"] = write_grant(seg["grant"], wt, allow)
    st.write_atomic(_segment_path(job, n), seg)
    return seg


def _validate(text, wt, allow, timeout, deadline, max_turns):
    """Refuse a bad request before anything is written (exit 2)."""
    check_allow(allow)
    agy.seconds(timeout)
    agy.seconds(deadline)
    if max_turns < 1:
        raise ValueError("--max-turns must be at least 1")
    size = len(prompt(wt, allow, text).encode("utf-8"))
    if size > agy.MAX_PROMPT:
        raise ValueError("the wrapped task prompt is %d bytes, over this system's single-argument limit of %d bytes: "
                         "put the material in a file in the repository and refer to it" % (size, agy.MAX_PROMPT))


def _checker(check_cmd, check_timeout, wt):
    """A task's check (spec 4.5): a worktree with no change fails with NO_CHANGE; otherwise the check command runs in
    the worktree with the reply on stdin, and any exit but 0 is a finding made of the end of its output."""
    def check(text, **run):
        try:
            changed = worktree.changes(wt)
        except RepoState as e:
            raise CheckError(str(e))
        if not changed:
            return checks.Verdict(False, None, [NO_CHANGE], False)
        findings = checks.run_cmd(check_cmd, text, check_timeout, combined=True, **run)
        return checks.Verdict(not findings, None if findings else text, findings, False)
    return check


def start(job_id, text, *, model, check_cmd, allow=(), effort=None, check_timeout=600, timeout="60m", max_turns=3,
          deadline="2h", cwd=None, new_version=False):
    """`agy-call task` (spec 4.3): check the request; then, under the job lock and the repository lock, find the same
    request already recorded, or create the worktree, segment 1 with its grant, and request.json. Returns the job;
    drive() runs it."""
    worktree.check_job_id(job_id)
    if not model:
        raise ValueError("model is required: pass the exact agy model name, e.g. gemini-4-argon-high")
    if not check_cmd:
        raise ValueError("task needs --check-cmd; pass --check-cmd true when there is nothing to build or test")
    allow = list(dict.fromkeys(allow))
    r = worktree.repo(cwd)
    job, wt = st.Job(r.store, job_id), worktree.worktree_path(r, job_id)
    _validate(text, wt, allow, timeout, deadline, max_turns)
    own = grant(wt, allow)["permissions"]["allow"]  # also reads the shared config, so a broken one refuses here
    with contextlib.ExitStack() as stack:
        runner._lock(job, stack)
        stack.enter_context(worktree.lock(r))
        req = st.read_json(job.path("request.json"))
        base = ((req or {}).get("task") or {}).get("baseCommit")
        if req and (new_version or req.get("identity") != _identity(text, model, effort, check_cmd, r.id, base, own)):
            if not new_version:
                raise Mismatch("task %s exists with a different request; continue it with --continue, or remove its "
                               "worktree with --cleanup or --discard and pass --new-version (%s)" % (job_id, job.dir))
            if not os.path.exists(job.path("cleanup.json")):
                raise RepoState("task %s still has its worktree %s; run task --cleanup or --discard first" % (job_id, wt))
            job.archive()
            req = None
        if not req:
            base_branch, base_commit = worktree.create(r, job_id)
            _write_segment(job, 1, text, wt, allow, 1, max_turns, deadline)
            st.write_atomic(job.path("request.json"), {
                "identity": _identity(text, model, effort, check_cmd, r.id, base_commit, own), "kind": "task",
                "prompt": text, "key": model, "cwd": wt, "created": runner._now(),
                "session": os.environ.get("AGYCALL_SESSION") or None,
                "task": {"repo": r.id, "checkout": r.top, "worktree": wt, "branch": worktree.branch(job_id),
                         "baseBranch": base_branch, "baseCommit": base_commit, "model": model, "effort": effort,
                         "allow": allow, "check": check_cmd, "checkTimeout": check_timeout, "timeout": timeout}})
    return job


def drive(job):
    """Run the latest segment of a task: in the foreground, on a rerun or in the background worker. A saved answer is
    returned without a launch; otherwise the worktree must still exist, and the grant file is written again first, so
    its deny mirror follows the shared config as it is now."""
    t = (st.read_json(job.path("request.json"), {}) or {}).get("task")
    if not t:
        raise ValueError("no task %s in %s" % (job.id, job.store))
    n = segments(job)[-1]
    seg = st.read_json(_segment_path(job, n))
    c = runner._prepare(job.id, seg["prompt"], store=job.store, model=t["model"], timeout=t["timeout"],
                        cwd=t["worktree"], check_cmd=t["check"], check_timeout=t["checkTimeout"], settings=seg["grant"],
                        effort=t["effort"], policy="task", segment=n, end=dt.datetime.fromisoformat(seg["end"]))
    c.checker = _checker(t["check"], t["checkTimeout"], t["worktree"])
    with contextlib.ExitStack() as stack:
        c.stack = stack
        c.fds.append(runner._lock(job, stack))
        if not os.path.exists(job.path("answer.txt")):
            if not os.path.isdir(t["worktree"]):
                raise RepoState("the worktree %s of task %s is missing; start the task again with --new-version"
                                % (t["worktree"], job.id))
            write_grant(seg["grant"], t["worktree"], seg["allow"])
        runner._own(c, stack)
        return runner._drive(c, seg["maxTurns"], False, False)
```

- [ ] **Step 6: Add the hand-back**

Append to `agycall/worktree.py`, after two blank lines:

```python
def handback(job):
    """The lines `result` adds for a task (spec 4.8): the worktree, its branch and base, and what changed there."""
    t = (st.read_json(job.path("request.json"), {}) or {}).get("task") or {}
    lines = ["", "worktree: %s" % t.get("worktree"), "branch: %s" % t.get("branch"),
             "base: %s at %s" % (t.get("baseBranch"), t.get("baseCommit"))]
    gone = st.read_json(job.path("cleanup.json"))
    if gone:
        return lines + ["the worktree was removed by task --%s" % gone["action"]]
    if not os.path.isdir(t.get("worktree") or ""):
        return lines + ["the worktree is missing"]
    merged = st.read_json(job.path("merge.json"))
    if merged:
        lines.append("merged into %s as %s" % (t["baseBranch"], merged["mergeCommit"]))
    stat = git(t["worktree"], "diff", "--stat", t["baseCommit"]).splitlines()
    return lines + ["git status --porcelain:"] + (changes(t["worktree"]) or ["(nothing uncommitted)"]) + \
        ["git diff --stat %s:" % t["baseCommit"]] + (stat or ["(no tracked file changed)"])
```

In `agycall/control.py`, replace:

```python
from . import agy, runner
```

with:

```python
from . import agy, runner, worktree
```

In `agycall/control.py`, replace:

```python
    if state == "done" and os.path.exists(job.path("answer.txt")):
        return runner._read(job.path("answer.txt")), 0
    lines = ["state: %s" % _state(job)] + ["finding: %s" % f for f in s.get("findings") or []]
    for att in reversed(job.launched()):
        reply = runner._saved_reply(att)
        if reply.strip():
            lines += ["last reply (attempt %s):" % att, reply]
            break
    return "\n".join(lines), (s.get("code", 22) if state == "fatal" else CODES.get(state, 1))
```

with:

```python
    if state == "done" and os.path.exists(job.path("answer.txt")):
        text, code = runner._read(job.path("answer.txt")), 0
    else:
        lines = ["state: %s" % _state(job)] + ["finding: %s" % f for f in s.get("findings") or []]
        for att in reversed(job.launched()):
            reply = runner._saved_reply(att)
            if reply.strip():
                lines += ["last reply (attempt %s):" % att, reply]
                break
        text, code = "\n".join(lines), (s.get("code", 22) if state in ("fatal", "setup-error") else CODES.get(state, 1))
    if _request(job).get("kind") == "task":  # the hand-back of spec 4.8
        text = "\n".join([text.rstrip("\n")] + worktree.handback(job))
    return text, code
```

- [ ] **Step 7: Add `agy-call task` to `agycall/cli.py`**

In `agycall/cli.py`, replace:

```python
from . import control
from . import store as st
```

with:

```python
from . import control, task
from . import store as st
```

In `agycall/cli.py`, replace:

```python
def main(argv):
```

with:

```python
def _task(a):
    """agy-call task: start or rerun a coding task in its own worktree (spec 4.3); prints the hand-back."""
    if not a.job or not a.prompt_file:
        raise ValueError("task needs --job and --prompt-file")
    with open(a.prompt_file, encoding="utf-8") as fh:
        text = fh.read()
    job = task.start(a.job, text, model=a.model, check_cmd=a.check_cmd, allow=a.allow, effort=a.effort,
                     check_timeout=a.check_timeout, timeout=a.timeout, max_turns=a.max_turns, deadline=a.deadline,
                     new_version=a.new_version)
    task.drive(job)
    return control.result(job.store, job.id)


def main(argv):
```

In `agycall/cli.py`, replace:

```python
    w = sub.add_parser("worker")  # started by run --background; not for direct use
```

with:

```python
    t = sub.add_parser("task")
    t.add_argument("--job")
    t.add_argument("--prompt-file")
    t.add_argument("--model")
    t.add_argument("--effort")
    t.add_argument("--allow", action="append", default=[])
    t.add_argument("--check-cmd")
    t.add_argument("--check-timeout", type=float, default=600)
    t.add_argument("--timeout", default="60m")
    t.add_argument("--max-turns", type=int, default=3)
    t.add_argument("--deadline", default="2h")
    t.add_argument("--new-version", action="store_true")
    w = sub.add_parser("worker")  # started by run --background; not for direct use
```

In `agycall/cli.py`, replace:

```python
        if a.cmd == "run":
            return _run(a)
        text, code = getattr(control, a.cmd)(st.store_dir(a.store, a.namespace), a.job)
```

with:

```python
        if a.cmd == "run":
            return _run(a)
        if a.cmd == "task":
            text, code = _task(a)
        else:
            text, code = getattr(control, a.cmd)(st.store_dir(a.store, a.namespace), a.job)
```

In `agycall/cli.py`, replace:

```python
"""agy-call run [--background] | batch | status | result | cancel. Prints the accepted answer or the report on stdout; errors on
stderr; exit codes in SKILL.md."""
```

with:

```python
"""agy-call run [--background] | batch | task | status | result | cancel. Prints the accepted answer or the report on
stdout; errors on stderr; exit codes in SKILL.md."""
```

- [ ] **Step 8: Run the tests**

Run: `uvx pytest -q tests/test_task.py`
Expected: all pass.
Run: `for i in 1 2 3; do uvx pytest -q tests/ || break; done`
Expected: every run passes (201 tests).

- [ ] **Step 9: Commit**

```bash
git add agycall/runner.py agycall/checks.py agycall/task.py agycall/worktree.py agycall/control.py agycall/cli.py tests/test_task.py
printf '%s\n' "Run a coding task in its worktree through the runner" \
  "" \
  "agy-call task creates the worktree and the first segment, then runs the" \
  "job loop in the worktree with the grant passed as --settings. The task" \
  "check fails a turn that changed nothing, sends any non-zero exit back as" \
  "the end of its output, counts every launch against --max-turns and stops" \
  "at the segment deadline. result prints the hand-back." \
  "" \
  "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" "Claude-Session: https://claude.ai/code/session_013coVpN7if3z6kaMYLTrz3c" > "$TMPDIR/agycall-msg.txt"
git commit -F "$TMPDIR/agycall-msg.txt"
```

---

### Task 3: Blocked, summary, timeout and interrupted rows

**Files:**
- Modify: `agycall/runner.py`, `agycall/control.py`
- Test: `tests/test_task.py`

**Interfaces:**
- Consumes: Task 2's `c.policy`, `_atts`, `_next(..., fix)`, `task.start`, `task.drive`.
- Produces:
  - `runner.NEED_ONLY`, `runner.SUMMARY_ONLY`, `runner.NEED` (a line starting with `NEED:` after optional spaces), `runner.TASK_STOPS`, `runner.INTERRUPTED`, `runner._interrupt(job, att)`.
  - Attempt outcomes `need` (with `denied`), `summary` and `blocked` (with `denied` and `need`); launch kinds `need` and `summary`; `status.json` gains `denied` and `need` for a blocked task.
  - `control.CODES["blocked"] = 28`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_task.py`, replace:

```python
import sys

import pytest

from agycall import control, quota, store, task, worktree
from agycall.errors import Fatal, GaveUp, Mismatch, Quota, RepoState
from agyturns import error_reply, git, model_error, quota_error, reply
```

with:

```python
import sys
import time

import pytest

from agycall import agy, control, quota, runner, store, task, worktree
from agycall.errors import Blocked, Fatal, GaveUp, Mismatch, Quota, RepoState
from agyturns import denied, empty, error_reply, git, model_error, partial, quota_error, reply
```

Append to `tests/test_task.py`, after two blank lines:

```python
def test_need_lines_block_the_task_even_when_the_check_passes(repo, fake):
    set_turns, launches = fake
    set_turns(reply("Created b.txt.\nNEED: npm install left-pad\n  NEED: curl example.com", write={"b.txt": "b\n"}))
    job = _start()
    with pytest.raises(Blocked) as e:
        task.drive(job)
    assert e.value.code == 28 and "NEED: npm install left-pad" in str(e.value) and len(launches()) == 1
    s = job.status()
    assert s["state"] == "blocked" and s["need"] == ["NEED: npm install left-pad", "NEED: curl example.com"]
    assert s["denied"] == [] and not os.path.exists(job.path("answer.txt")) and os.path.exists(os.path.join(_wt(), "b.txt"))
    with pytest.raises(Blocked):  # a rerun stays blocked and launches nothing
        task.drive(_start())
    assert len(launches()) == 1 and control.result(job.store, "t1")[1] == 28


def test_a_refused_command_gets_one_turn_that_asks_for_the_need_lines(repo, fake):
    set_turns, launches = fake
    set_turns(denied(), reply("NEED: npm test"))
    job = _start()
    with pytest.raises(Blocked):
        task.drive(job)
    second = launches()[1]
    assert len(launches()) == 2 and second["prompt"] == runner.NEED_ONLY
    assert second["argv"][second["argv"].index("--conversation") + 1] == "c1"
    s = job.status()
    assert s["state"] == "blocked" and s["need"] == ["NEED: npm test"] and s["denied"] == ["command"]


def test_a_refused_write_blocks_at_once_and_a_command_is_asked_about_only_once(repo, fake):
    set_turns, launches = fake
    set_turns(denied("write_file"))
    job = _start()
    with pytest.raises(Blocked, match="write_file"):
        task.drive(job)
    assert len(launches()) == 1 and job.status()["denied"] == ["write_file"]
    set_turns(denied())
    with pytest.raises(Blocked, match="named no command"):
        task.drive(_start("t2"))
    assert len(launches()) == 3  # the refused launch and one NEED: follow-up


def test_an_empty_reply_gets_one_summary_turn_and_is_never_resent_blind(repo, fake):
    set_turns, launches = fake
    set_turns(empty(conv="c1"), reply("Created b.txt.", write={"b.txt": "b\n"}))
    assert task.drive(_start()).answer == "Created b.txt."
    assert launches()[1]["prompt"] == runner.SUMMARY_ONLY and "--conversation" in launches()[1]["argv"]
    set_turns(empty(conv="c2"))
    with pytest.raises(GaveUp, match="never resent blind"):  # a second empty reply after the summary turn
        task.drive(_start("t2"))
    set_turns(empty())
    with pytest.raises(GaveUp, match="never resent blind"):  # no conversation to ask in
        task.drive(_start("t3"))
    assert len(launches()) == 5


def test_a_timeout_fails_the_task_and_keeps_the_partial_reply(repo, fake, monkeypatch):
    set_turns, launches = fake
    set_turns(partial("half done"))
    job = _start()
    for _ in range(2):  # a rerun keeps the stop
        with pytest.raises(GaveUp, match="partial reply"):
            task.drive(job)
    assert len(launches()) == 1 and open(os.path.join(job.launched()[0], "reply.txt")).read() == "half done"
    monkeypatch.setattr(agy, "POLL", 0.1)
    set_turns(reply("late", write={"b.txt": "b\n"}, sleep=30))
    t0 = time.monotonic()
    with pytest.raises(GaveUp, match="deadline"):  # the segment's deadline stops a running turn
        task.drive(_start("t2", deadline="2s"))
    assert time.monotonic() - t0 < 15 and len(launches()) == 2


def test_a_task_whose_controller_died_sends_no_automatic_turn(repo, fake):
    set_turns, launches = fake
    set_turns(reply("x"))
    job = _start()
    att = job.new_attempt()
    dead = subprocess.Popen(["true"])
    dead.wait()
    store.write_atomic(os.path.join(att, "launch.json"), {"pid": dead.pid, "pgid": dead.pid, "kind": "first", "message": "q",
                                                         "conversation": None, "generation": 0, "segment": 1,
                                                         "started": runner._now()})
    with open(os.path.join(att, "agy.out"), "wb") as fh:
        fh.write(b'{"status": "SUCC')
    for _ in range(2):
        with pytest.raises(GaveUp, match="nothing was resent"):
            task.drive(job)
    assert launches() == [] and store.read_json(os.path.join(att, "result.json"))["outcome"] == "interrupted"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uvx pytest -q tests/test_task.py`
Expected: 6 failed, 18 passed: the six new tests fail, because the runner still treats these outcomes as it does for `run` (no `Blocked`, a `DENIED` follow-up, a restart turn or a plain resend).

- [ ] **Step 3: Add the task rows of spec 5.2 to `agycall/runner.py`**

In `agycall/runner.py`, replace:

```python
import os
import time
import types

from . import agy, checks, classify, quota
from . import store as st
from .errors import Cancelled, CheckError, Fatal, GaveUp, Mismatch, NoConversation, Quota, Running
```

with:

```python
import os
import re
import time
import types

from . import agy, checks, classify, quota
from . import store as st
from .errors import Blocked, Cancelled, CheckError, Fatal, GaveUp, Mismatch, NoConversation, Quota, Running
```

In `agycall/runner.py`, replace:

```python
FIX_TASK = ("The check of your work failed. Fix the files in the worktree so that it passes, keep the work you already "
            "did, and end with the same short summary. What the check reported:\n\n{findings}")
```

with:

```python
FIX_TASK = ("The check of your work failed. Fix the files in the worktree so that it passes, keep the work you already "
            "did, and end with the same short summary. What the check reported:\n\n{findings}")
NEED_ONLY = ("Some of the commands you tried were refused. Reply only with one line `NEED: <command>` for each command "
             "you tried that was refused; do not run anything or change any file.")
SUMMARY_ONLY = ("Your last turn ended without a reply. Do not change any more files; reply only with the final summary "
                "of at most 15 lines: files changed, checks run and their results, anything not done.")
NEED = re.compile(r"^[ \t]*NEED:.*$", re.MULTILINE)
TASK_STOPS = ("failed", "blocked", "interrupted")  # a task segment that ended this way sends nothing more on a rerun
INTERRUPTED = ("the process running this task stopped before agy's output was complete, and nothing was resent; "
               "agy-call result shows the worktree's changes; continue with task --continue")
```

In `agycall/runner.py`, replace:

```python
    if outcome == "denied":
        return DENIED, "denied"
```

with:

```python
    if outcome == "denied":
        return DENIED, "denied"
    if outcome == "need":
        return NEED_ONLY, "need"
    if outcome == "summary":
        return SUMMARY_ONLY, "summary"
```

In `agycall/runner.py`, replace:

```python
def _launch(c, att, message, kind, generation):
```

with:

```python
def _interrupt(job, att):
    """Record an attempt that its saved output did not settle as interrupted, keeping the conversation id it saw."""
    got = _saved_conversation(att)
    st.write_atomic(os.path.join(att, "result.json"), {"exit": None, "outcome": "interrupted", "conversation": got, "ended": _now()})
    job.set_status(state="interrupted")
    if got and not job.status().get("conversation"):
        job.set_status(conversation=got)


def _launch(c, att, message, kind, generation):
```

In `agycall/runner.py`, replace:

```python
    name = oc.name
    if name == "reply" or (name in AGY_ERRORS and oc.response.strip()):
```

with:

```python
    name = oc.name
    if c.policy == "task" and name not in ("quota", "auth", "model", "malformed"):  # the task column of spec 5.2
        need = [l.strip() for l in NEED.findall(oc.response)]
        asked = launch.get("kind") == "need"
        if name == "denied" and "command" in oc.denied and c.conv and not need and not asked:
            finish(outcome="need", denied=oc.denied, findings=["agy refused a command; asking which commands it needs"])
            return None  # spec 4.6: one turn that asks only for the NEED: lines
        if need or asked or name == "denied":
            earlier = [st.read_json(os.path.join(a, "result.json"), {}) or {} for a in _atts(c) if a != att]
            kinds = sorted(set(oc.denied).union(*(r.get("denied") or [] for r in earlier)))
            findings = need or ["agy refused a %s action and named no command it needs" % "/".join(kinds or ["tool"])]
            finish("blocked", outcome="blocked", findings=findings, denied=kinds, need=need)
            job.set_status(denied=kinds, need=need)
            raise Blocked(findings, job.dir)
        if name == "timeout":
            give_up(["%s; the partial reply and the worktree are kept; continue with task --continue" % oc.reason])
        if name == "empty":
            if c.conv and launch.get("kind") != "summary":
                finish(outcome="summary", findings=[oc.reason])  # one turn that asks only for the final summary
                return None
            give_up(["%s; a coding task is never resent blind; continue with task --continue" % oc.reason])
    if name == "reply" or (name in AGY_ERRORS and oc.response.strip()):
```

In `agycall/runner.py`, replace:

```python
    if att and oc is None:  # the saved output does not settle the attempt; run and batch restart the turn
        got = _saved_conversation(att)
        st.write_atomic(os.path.join(att, "result.json"), {"exit": None, "outcome": "interrupted", "conversation": got, "ended": _now()})
        job.set_status(state="interrupted")
        if got and not job.status().get("conversation"):
            job.set_status(conversation=got)
```

with:

```python
    if att and oc is None:  # the saved output does not settle the attempt; run and batch restart the turn
        _interrupt(job, att)
        if c.policy == "task":  # a task sends no automatic turn (spec 7.4)
            job.set_status(state="failed", findings=[INTERRUPTED])
            raise GaveUp([INTERRUPTED], c.conv, _usage(job), job.dir)
```

In `agycall/runner.py`, replace:

```python
    elif last_res.get("outcome") == "failed" and (last_res.get("classified") in AGY_ERRORS or last_res.get("conversationConflict")):
```

with:

```python
    elif c.policy == "task" and last_res.get("outcome") in TASK_STOPS:
        findings = job.status().get("findings") or last_res.get("findings") or ["the task stopped"]
        if last_res["outcome"] == "blocked":
            job.set_status(state="blocked")
            raise Blocked(findings, job.dir)
        job.set_status(state="failed")
        raise GaveUp(findings, c.conv, _usage(job), job.dir)
    elif last_res.get("outcome") == "failed" and (last_res.get("classified") in AGY_ERRORS or last_res.get("conversationConflict")):
```

In `agycall/control.py`, replace:

```python
CODES = {"done": 0, "quota": 10, "failed": 20, "check-error": 21, "no-conversation": 23,
         "queued": 25, "running": 25, "checking": 25, "cancelled": 27}
```

with:

```python
CODES = {"done": 0, "quota": 10, "failed": 20, "check-error": 21, "no-conversation": 23,
         "queued": 25, "running": 25, "checking": 25, "cancelled": 27, "blocked": 28}
```

- [ ] **Step 4: Run the tests**

Run: `uvx pytest -q tests/test_task.py`
Expected: all pass.
Run: `for i in 1 2 3; do uvx pytest -q tests/ || break; done`
Expected: every run passes (207 tests).

Then run `grep -n 'policy == "task"' agycall/runner.py`; expected: four lines (the outcome rows in `_settle`, the `INTERRUPTED` stop and the rerun guard in `_drive`, and `_prepare`).

- [ ] **Step 5: Commit**

```bash
git add agycall/runner.py agycall/control.py tests/test_task.py
printf '%s\n' "Block a task on NEED lines and refusals; fail it without resending" \
  "" \
  "NEED: lines or a refused action end a task blocked (exit 28); a refused" \
  "command with no NEED: line first gets one turn asking for them. An empty" \
  "reply gets one summary-only turn, a timeout fails the task with the partial" \
  "reply kept, and a dead controller's unsettled turn is not resent. A rerun" \
  "of a stopped segment launches nothing." \
  "" \
  "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" "Claude-Session: https://claude.ai/code/session_013coVpN7if3z6kaMYLTrz3c" > "$TMPDIR/agycall-msg.txt"
git commit -F "$TMPDIR/agycall-msg.txt"
```

---

### Task 4: `--continue` segments, background tasks and the repository store

**Files:**
- Modify: `agycall/task.py`, `agycall/control.py`, `agycall/cli.py`
- Test: `tests/test_task.py`

**Interfaces:**
- Consumes: Task 2's `task.start`, `task.drive`, `_write_segment`, `_validate`, `segments`; Task 3's `runner._interrupt`; step 2's `control.start`, `control.work`.
- Produces:
  - `task.continue_(job_id, text, *, allow=(), max_turns=3, deadline="2h", cwd=None, background=False) -> store.Job`; `task.start(..., background=False)`; `task._queue(job)` writes `background.json` as `{"kind": "task"}` and state `queued`.
  - `control.spawn(job) -> job id` (the detached worker; `control.start` now calls it); `control.work` drives a task when `background.json` has `kind: "task"`.
  - `control.status_all() -> (text, 0)`; each `status` row is `id  state  kind  age  finding`.
  - CLI: `task --continue`, `task --background` (prints the job id), `status --all`; without `--store` or `--namespace`, `status`, `result` and `cancel` use the repository store inside a git repository.

- [ ] **Step 1: Write the failing tests**

In `tests/test_task.py`, replace:

```python
from agycall.errors import Blocked, Fatal, GaveUp, Mismatch, Quota, RepoState
from agyturns import denied, empty, error_reply, git, model_error, partial, quota_error, reply
```

with:

```python
from agycall.errors import Blocked, Cancelled, Fatal, GaveUp, Mismatch, NoConversation, Quota, RepoState, Running
from agyturns import denied, empty, error_reply, git, model_error, partial, quota_error, reply, wait_for
```

Append to `tests/test_task.py`, after two blank lines:

```python
def test_continue_appends_a_segment_in_the_same_worktree_and_conversation(repo, fake):
    set_turns, launches = fake
    set_turns(reply("Created b.txt.", write={"b.txt": "b\n"}), reply("Created c.txt.", write={"c.txt": "c\n"}))
    job = _start()
    task.drive(job)
    store.write_atomic(job.path("cancel.request"), "{}")  # an old request must not stop the new segment
    job = task.continue_("t1", "Add c.txt too.", allow=["make"], max_turns=2, deadline="1h")
    assert task.drive(job).answer == "Created c.txt."
    second = launches()[1]
    assert second["argv"][second["argv"].index("--conversation") + 1] == "c1"
    assert os.path.realpath(second["cwd"]) == os.path.realpath(_wt())
    assert second["prompt"] == task.prompt(_wt(), ["make"], "Add c.txt too.")
    assert second["argv"][second["argv"].index("--settings") + 1] == job.path("grant-2.json")
    assert "command(make)" in second["settings"]["permissions"]["allow"]
    assert "command(make)" not in launches()[0]["settings"]["permissions"]["allow"]
    assert open(job.path("segments", "001-answer.txt")).read() == "Created b.txt."
    assert open(job.path("answer.txt")).read() == "Created c.txt."
    assert os.path.exists(job.path("cancel.request.002.done")) and not os.path.exists(job.path("cancel.request"))
    seg = store.read_json(job.path("segments", "002.json"))
    assert seg["maxTurns"] == 2 and seg["deadline"] == "1h" and seg["grant"] == job.path("grant-2.json")
    assert store.read_json(os.path.join(job.launched()[1], "launch.json"))["segment"] == 2


def test_continue_after_blocked_and_after_cancelled_never_exits_24(repo, fake):
    set_turns, launches = fake
    set_turns(reply("NEED: make"), reply("Done.", write={"b.txt": "b\n"}))
    job = _start()
    with pytest.raises(Blocked):
        task.drive(job)
    assert task.drive(task.continue_("t1", "make is allowed now.", allow=["make"])).answer == "Done."
    set_turns(reply("More.", write={"c.txt": "c\n"}))
    job = task.continue_("t1", "One more thing.")
    store.write_atomic(job.path("cancel.request"), "{}")
    with pytest.raises(Cancelled):
        task.drive(job)
    assert job.status()["state"] == "cancelled" and len(launches()) == 2
    assert task.drive(task.continue_("t1", "One more thing.")).answer == "More."
    assert [l["argv"][l["argv"].index("--conversation") + 1] for l in launches()[1:]] == ["c1", "c1"]
    assert store.read_json(job.path("segments", "004.json"))["grant"] == job.path("grant-2.json")  # no new --allow


def test_continue_refuses_an_unknown_task_a_running_one_one_without_a_conversation_and_a_missing_worktree(repo, fake):
    set_turns, _ = fake
    with pytest.raises(ValueError, match="no task"):
        task.continue_("nope", "x")
    set_turns(empty())
    job = _start()
    with pytest.raises(GaveUp):
        task.drive(job)
    with store.flock(job.lock_path):
        with pytest.raises(Running):
            task.continue_("t1", "x")
    with pytest.raises(NoConversation):
        task.continue_("t1", "x")
    git(repo, "worktree", "remove", "--force", _wt())
    with pytest.raises(RepoState, match="missing"):
        task.continue_("t1", "x")


def test_continue_after_the_controller_died_settles_the_old_turn_first(repo, fake):
    set_turns, launches = fake
    set_turns(reply("Created b.txt.", write={"b.txt": "b\n"}), reply("Added d.", write={"d.txt": "d\n"}))
    job = _start()
    task.drive(job)
    task.continue_("t1", "Add c.txt.")
    att = job.new_attempt()  # segment 2's first turn: its controller died after agy wrote a complete reply
    dead = subprocess.Popen(["true"])
    dead.wait()
    store.write_atomic(os.path.join(att, "launch.json"), {"pid": dead.pid, "pgid": dead.pid, "kind": "first", "message": "m",
                                                         "conversation": "c1", "generation": 0, "segment": 2,
                                                         "started": runner._now()})
    with open(os.path.join(att, "agy.out"), "w") as fh:
        fh.write(json.dumps({"status": "SUCCESS", "response": "the old reply", "conversation_id": "c1"}))
    job = task.continue_("t1", "Add d.txt.")
    assert store.read_json(os.path.join(att, "result.json"))["outcome"] == "interrupted"
    assert task.drive(job).answer == "Added d."  # not the old reply
    assert launches()[1]["prompt"] == task.prompt(_wt(), [], "Add d.txt.")


def test_a_background_task_runs_in_a_detached_worker_and_is_found_from_its_worktree(repo, fake, tmp_path):
    set_turns, launches = fake
    set_turns(reply("Created b.txt.", write={"b.txt": "b\n"}, sleep=2), reply("Created c.txt.", write={"c.txt": "c\n"}))
    (tmp_path / "one.md").write_text("Add b.txt.")
    (tmp_path / "two.md").write_text("Add c.txt.")
    env = dict(os.environ, PATH="/usr/bin:/bin")  # no agy-call on the PATH: the worker must use this checkout
    r = run_cli("task", "--background", "--job", "t1", "--model", "m", "--check-cmd", "test -f b.txt",
                "--prompt-file", str(tmp_path / "one.md"), cwd=str(repo), env=env)
    assert r.returncode == 0 and r.stdout == "t1\n", r.stderr
    job = store.Job(worktree.repo().store, "t1")
    assert job.status()["state"] in ("queued", "running")  # the command returned while the turn still runs
    wait_for(lambda: job.status().get("state") == "done")
    r = run_cli("status", cwd=_wt(), env=env)  # from inside the task's worktree: the repository store
    assert r.returncode == 0 and r.stdout.startswith("t1  done  task  "), r.stdout
    r = run_cli("result", "t1", cwd=_wt(), env=env)
    assert r.returncode == 0 and r.stdout.startswith("Created b.txt.\n") and "branch: agy/t1" in r.stdout
    r = run_cli("task", "--continue", "--background", "--job", "t1", "--prompt-file", str(tmp_path / "two.md"),
                cwd=str(repo), env=env)
    assert r.returncode == 0
    wait_for(lambda: job.status().get("state") == "done" and os.path.exists(job.path("answer.txt")))
    assert open(job.path("answer.txt")).read() == "Created c.txt." and len(launches()) == 2
    r = run_cli("status", "--all", cwd=str(tmp_path), env=env)
    assert "%s (repository %s)\n  t1  done  task  " % (job.store, worktree.repo().top) in r.stdout
    assert open(job.path("worker.log")).read().count("agy-call worker: done") == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uvx pytest -q tests/test_task.py`
Expected: 5 failed, 24 passed: the new tests fail with `AttributeError: module 'agycall.task' has no attribute 'continue_'`, and the background test with `unrecognized arguments: --background` (exit 2).

- [ ] **Step 3: Add `continue_` and the background queue to `agycall/task.py`**

In `agycall/task.py`, replace:

```python
from .errors import CheckError, Mismatch, RepoState
```

with:

```python
from .errors import CheckError, Mismatch, NoConversation, RepoState
```

In `agycall/task.py`, replace:

```python
def start(job_id, text, *, model, check_cmd, allow=(), effort=None, check_timeout=600, timeout="60m", max_turns=3,
          deadline="2h", cwd=None, new_version=False):
    """`agy-call task` (spec 4.3): check the request; then, under the job lock and the repository lock, find the same
    request already recorded, or create the worktree, segment 1 with its grant, and request.json. Returns the job;
    drive() runs it."""
```

with:

```python
def _queue(job):
    """Mark a task for the background worker: control.work reads the kind from background.json."""
    st.write_atomic(job.path("background.json"), {"kind": "task"})
    job.set_status(state="queued")


def start(job_id, text, *, model, check_cmd, allow=(), effort=None, check_timeout=600, timeout="60m", max_turns=3,
          deadline="2h", cwd=None, new_version=False, background=False):
    """`agy-call task` (spec 4.3): check the request; then, under the job lock and the repository lock, find the same
    request already recorded, or create the worktree, segment 1 with its grant, and request.json. Returns the job;
    drive() runs it, or with background the job is queued for control.spawn."""
```

In `agycall/task.py`, replace:

```python
                         "allow": allow, "check": check_cmd, "checkTimeout": check_timeout, "timeout": timeout}})
    return job
```

with:

```python
                         "allow": allow, "check": check_cmd, "checkTimeout": check_timeout, "timeout": timeout}})
        if background:
            _queue(job)
    return job


def continue_(job_id, text, *, allow=(), max_turns=3, deadline="2h", cwd=None, background=False):
    """`agy-call task --continue` (spec 4.7): under the job lock, append a segment to a task. A turn its controller left
    unsettled is recorded as interrupted first, so its reply is never taken as the new segment's answer. The segment has
    its own prompt, budget and deadline, and a new grant file when --allow adds commands; the previous answer moves to
    segments/NNN-answer.txt and a pending cancel request is renamed, so neither ends the new segment."""
    r = worktree.repo(cwd)
    job = st.Job(r.store, job_id)
    t = (st.read_json(job.path("request.json"), {}) or {}).get("task")
    if not t or t.get("repo") != r.id:
        raise ValueError("no task %s in %s" % (job_id, r.store))
    with contextlib.ExitStack() as stack:
        runner._lock(job, stack)
        if not os.path.isdir(t["worktree"]):
            raise RepoState("the worktree %s of task %s is missing; start the task again with --new-version"
                            % (t["worktree"], job_id))
        att = runner.pending(job)
        if att:
            runner._interrupt(job, att)
        if not job.status().get("conversation"):
            raise NoConversation("task %s has no conversation to continue; its replies are in %s"
                                 % (job_id, job.path("attempts")))
        n = segments(job)[-1]
        prev = st.read_json(_segment_path(job, n))
        added = [a for a in dict.fromkeys(allow) if a not in prev["allow"]]
        _validate(text, t["worktree"], prev["allow"] + added, t["timeout"], deadline, max_turns)
        grant_n = int(re.search(r"grant-(\d+)\.json$", prev["grant"]).group(1)) + (1 if added else 0)
        if os.path.exists(job.path("answer.txt")):
            os.replace(job.path("answer.txt"), job.path("segments", "%03d-answer.txt" % n))
        if os.path.exists(job.path("cancel.request")):
            os.replace(job.path("cancel.request"), job.path("cancel.request.%03d.done" % (n + 1)))
        _write_segment(job, n + 1, text, t["worktree"], prev["allow"] + added, grant_n, max_turns, deadline)
        job.set_status(findings=[], need=[], denied=[])
        if background:
            _queue(job)
    return job
```

- [ ] **Step 4: Spawn task workers and list every store in `agycall/control.py`**

In `agycall/control.py`, replace:

```python
from . import agy, runner, worktree
```

with:

```python
from . import agy, runner, task, worktree
```

In `agycall/control.py`, replace:

```python
        rows.append(("%s  %s  %s  %s" % (job.id, _state(job), _age(job), finding)).rstrip())
    return "\n".join(rows) or "no jobs in %s" % store, 0
```

with:

```python
        kind = _request(job).get("kind") or "run"
        rows.append(("%s  %s  %s  %s  %s" % (job.id, _state(job), kind, _age(job), finding)).rstrip())
    return "\n".join(rows) or "no jobs in %s" % store, 0


def status_all():
    """(text, exit code) for `agy-call status --all`: every store under ~/.agy-call/jobs, repository stores and
    namespace stores alike, one block per store that has jobs."""
    root = os.path.join(st.home(), "jobs")
    blocks = []
    for name in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        store = os.path.join(root, name)
        found = jobs(store)
        if not found:
            continue
        top = next(((_request(j).get("task") or {}).get("checkout") for j in found if _request(j).get("task")), None)
        head = "%s (repository %s)" % (store, top) if top else store
        blocks.append("\n".join([head] + ["  " + line for line in status(store)[0].splitlines()]))
    return "\n\n".join(blocks) or "no jobs in %s" % root, 0
```

In `agycall/control.py`, replace:

```python
def start(job_id, prompt, **kw):
    """`run --background`: record the run as queued and start a detached worker for it (spec 7.1); returns the job id."""
    job = runner.enqueue(job_id, prompt, **kw)
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(p for p in (ROOT, os.environ.get("PYTHONPATH")) if p))
```

with:

```python
def start(job_id, prompt, **kw):
    """`run --background`: record the run as queued and start a detached worker for it (spec 7.1); returns the job id."""
    return spawn(runner.enqueue(job_id, prompt, **kw))


def spawn(job):
    """Start the detached worker of a queued job, run or task; returns the job id."""
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(p for p in (ROOT, os.environ.get("PYTHONPATH")) if p))
```

In `agycall/control.py`, replace:

```python
    job = st.Job(store, job_id)
    try:
        runner.call(**st.read_json(job.path("background.json")))
```

with:

```python
    job = st.Job(store, job_id)
    args = st.read_json(job.path("background.json"))
    try:
        if args.get("kind") == "task":
            task.drive(job)
        else:
            runner.call(**args)
```

`control.py` now imports `task`, which imports `runner` and `worktree`; neither imports `control`, so there is no import cycle.

- [ ] **Step 5: Wire `--continue`, `--background`, `status --all` and the repository store into `agycall/cli.py`**

In `agycall/cli.py`, replace:

```python
from . import control, task
from . import store as st
from .batch import batch
from .errors import AgyCallError, Quota
```

with:

```python
from . import control, task, worktree
from . import store as st
from .batch import batch
from .errors import AgyCallError, Quota, RepoState
```

In `agycall/cli.py`, replace:

```python
    job = task.start(a.job, text, model=a.model, check_cmd=a.check_cmd, allow=a.allow, effort=a.effort,
                     check_timeout=a.check_timeout, timeout=a.timeout, max_turns=a.max_turns, deadline=a.deadline,
                     new_version=a.new_version)
    task.drive(job)
    return control.result(job.store, job.id)
```

with:

```python
    if a.cont:
        job = task.continue_(a.job, text, allow=a.allow, max_turns=a.max_turns, deadline=a.deadline,
                             background=a.background)
    else:
        job = task.start(a.job, text, model=a.model, check_cmd=a.check_cmd, allow=a.allow, effort=a.effort,
                         check_timeout=a.check_timeout, timeout=a.timeout, max_turns=a.max_turns, deadline=a.deadline,
                         new_version=a.new_version, background=a.background)
    if a.background:
        return control.spawn(job), 0
    task.drive(job)
    return control.result(job.store, job.id)


def _store(a):
    """--store or --namespace when given; else the repository store when the current directory is inside a git
    repository; else the default store."""
    if a.store or a.namespace:
        return st.store_dir(a.store, a.namespace)
    try:
        return worktree.repo().store
    except RepoState:
        return st.store_dir()
```

In `agycall/cli.py`, replace:

```python
    t.add_argument("--new-version", action="store_true")
```

with:

```python
    t.add_argument("--new-version", action="store_true")
    t.add_argument("--continue", action="store_true", dest="cont")
    t.add_argument("--background", action="store_true")
```

In `agycall/cli.py`, replace:

```python
        p.add_argument("--namespace")
```

with:

```python
        p.add_argument("--namespace")
        if name == "status":
            p.add_argument("--all", action="store_true")
```

In `agycall/cli.py`, replace:

```python
        if a.cmd == "task":
            text, code = _task(a)
        else:
            text, code = getattr(control, a.cmd)(st.store_dir(a.store, a.namespace), a.job)
```

with:

```python
        if a.cmd == "task":
            text, code = _task(a)
        elif a.cmd == "status" and a.all:
            text, code = control.status_all()
        else:
            text, code = getattr(control, a.cmd)(_store(a), a.job)
```

- [ ] **Step 6: Run the tests**

Run: `uvx pytest -q tests/test_task.py`
Expected: all pass.
Run: `for i in 1 2 3; do uvx pytest -q tests/ || break; done`
Expected: every run passes (212 tests).

Then run `ps -ax -o pid=,command= | grep "agycall.cli worker" | grep -v grep`; expected: no output (no worker outlived the suite).

- [ ] **Step 7: Commit**

```bash
git add agycall/task.py agycall/control.py agycall/cli.py tests/test_task.py
printf '%s\n' "Continue a task in new segments and run tasks in the background" \
  "" \
  "task --continue appends a segment with its own prompt, budget, deadline" \
  "and grant in the task's conversation and worktree, after settling a turn" \
  "a dead controller left. task --background queues the job for the detached" \
  "worker. status, result and cancel default to the repository's store, and" \
  "status --all lists every store." \
  "" \
  "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" "Claude-Session: https://claude.ai/code/session_013coVpN7if3z6kaMYLTrz3c" > "$TMPDIR/agycall-msg.txt"
git commit -F "$TMPDIR/agycall-msg.txt"
```

---

### Task 5: Merge, cleanup and discard

**Files:**
- Modify: `agycall/worktree.py`, `agycall/cli.py`
- Test: `tests/test_worktree.py`

**Interfaces:**
- Consumes: Task 1's `repo`, `git`, `changes`, `current_branch`, `lock`; Task 2's `checks.run_cmd(..., combined=True)` and the task record; `runner._now`.
- Produces:
  - `worktree.merge(job_id, cwd=None) -> (text, code)`: 0 merged, 20 the check failed; `RepoState` for every refusal; `Running` when the job lock is held; writes `merge.json` `{taskCommit, mergeCommit, baseTip, check, checkExit, time}` or `merge-failed.json` `{exit, output, conflicts, aborted, time}`.
  - `worktree.cleanup(job_id, cwd=None) -> (text, 0)` and `worktree.discard(job_id, cwd=None) -> (text, 0)`; both write `cleanup.json` `{action, branchTip, time}`.
  - CLI: `agy-call task --merge JOB`, `--cleanup JOB`, `--discard JOB` (mutually exclusive).

- [ ] **Step 1: Write the failing tests**

In `tests/test_worktree.py`, replace:

```python
import os
import stat

import pytest

from agycall import store, worktree
from agycall.errors import Mismatch, RepoState
from agyturns import git
```

with:

```python
import os
import stat
import subprocess
import sys
import threading
import time

import pytest

from agycall import control, store, task, worktree
from agycall.errors import Mismatch, RepoState, Running
from agyturns import git, reply

HERE = os.path.dirname(os.path.abspath(__file__))
AGYCALL = os.path.join(os.path.dirname(HERE), "bin", "agy-call")
```

Append to `tests/test_worktree.py`, after two blank lines:

```python
def _committed_task(fake, job_id="t1", check="test -f b.txt", files=None):
    """A task that ended done, with its work committed on agy/<job> the way the skill tells Claude to."""
    set_turns, _ = fake
    set_turns(reply("Created b.txt.", write=files or {"b.txt": "b\n"}))
    job = task.start(job_id, "Add b.txt.", model="m", check_cmd=check)
    task.drive(job)
    wt = worktree.worktree_path(worktree.repo(), job_id)
    git(wt, "add", "-A")
    git(wt, "commit", "-q", "-m", "agy task %s" % job_id)
    return job, wt


def test_merge_makes_the_merge_commit_and_cleanup_removes_the_worktree_and_branch(repo, fake):
    job, wt = _committed_task(fake, check="mkdir -p build && touch build/out.o && test -f b.txt")
    base_tip, task_commit = git(repo, "rev-parse", "main").strip(), git(wt, "rev-parse", "HEAD").strip()
    text, code = worktree.merge("t1")  # the check leaves ignored build output in the worktree: still clean
    head = git(repo, "rev-parse", "main").strip()
    assert code == 0 and head in text and (repo / "b.txt").exists()
    assert git(repo, "rev-list", "--parents", "-n", "1", head).split() == [head, base_tip, task_commit]
    assert git(repo, "log", "-1", "--format=%s").strip() == "Merge agy task t1"
    m = store.read_json(job.path("merge.json"))
    assert (m["taskCommit"], m["mergeCommit"], m["baseTip"], m["checkExit"]) == (task_commit, head, base_tip, 0)
    assert "merged into main as %s" % head in control.result(job.store, "t1")[0]
    assert os.path.exists(os.path.join(wt, "build", "out.o"))
    assert worktree.cleanup("t1")[1] == 0  # ignored build output does not stop the removal
    assert not os.path.exists(wt) and git(repo, "branch", "--list", "agy/t1") == ""
    assert store.read_json(job.path("cleanup.json"))["action"] == "cleanup"
    assert "removed by task --cleanup" in control.result(job.store, "t1")[0]
    assert worktree.cleanup("t1") == ("task t1 was already removed by --cleanup", 0)
    task.start("t1", "Add c.txt.", model="m", check_cmd="true", new_version=True)  # allowed once cleaned up
    assert os.path.isdir(wt) and os.path.isdir(os.path.join(job.store, ".versions", "t1", "v1"))


def test_merge_refuses_until_the_task_and_the_main_checkout_are_ready(repo, fake):
    job, wt = _committed_task(fake)
    with store.flock(job.lock_path):
        with pytest.raises(Running):
            worktree.merge("t1")
    open(os.path.join(wt, "x.txt"), "w").close()
    with pytest.raises(RepoState, match="uncommitted changes; commit them on agy/t1"):
        worktree.merge("t1")
    os.remove(os.path.join(wt, "x.txt"))
    git(repo, "checkout", "-q", "-b", "other")
    with pytest.raises(RepoState, match="not on main"):
        worktree.merge("t1")
    git(repo, "checkout", "-q", "main")
    (repo / "y.txt").write_text("y")
    with pytest.raises(RepoState, match="main checkout .* has uncommitted"):
        worktree.merge("t1")
    git(repo, "add", "y.txt")
    git(repo, "commit", "-q", "-m", "the base moves on")
    with pytest.raises(RepoState, match="base moved: merge main into agy/t1, test, commit, and run --merge again"):
        worktree.merge("t1")
    assert not os.path.exists(job.path("merge.json"))
    git(wt, "reset", "-q", "--hard", git(wt, "rev-parse", "HEAD~1").strip())  # the task commit undone
    git(wt, "merge", "-q", "main")
    with pytest.raises(RepoState, match="no commit that main does not have"):
        worktree.merge("t1")


def test_a_failing_check_or_one_that_edits_tracked_files_refuses_the_merge(repo, fake):
    job, wt = _committed_task(fake, check="test -f b.txt && test ! -f stop")
    open(os.path.join(wt, "stop"), "w").close()
    git(wt, "add", "stop")
    git(wt, "commit", "-q", "-m", "stop")
    before = git(repo, "rev-parse", "main").strip()
    text, code = worktree.merge("t1")
    assert code == 20 and "nothing was merged" in text and "exited 1" in text
    job2, wt2 = _committed_task(fake, "t2", check="test -f b.txt && { test ! -f edit || echo more >> b.txt; }")
    open(os.path.join(wt2, "edit"), "w").close()
    git(wt2, "add", "edit")
    git(wt2, "commit", "-q", "-m", "edit")
    with pytest.raises(RepoState, match="the check changed the worktree"):
        worktree.merge("t2")
    assert git(repo, "rev-parse", "main").strip() == before and "more" in open(os.path.join(wt2, "b.txt")).read()


def test_a_failing_merge_hook_is_aborted_and_recorded(repo, fake):
    job, wt = _committed_task(fake)
    hook = repo / ".git" / "hooks" / "pre-merge-commit"
    hook.write_text("#!/bin/sh\necho hook says no >&2\nexit 1\n")
    hook.chmod(0o755)
    before = git(repo, "rev-parse", "main").strip()
    with pytest.raises(RepoState, match="hook says no"):
        worktree.merge("t1")
    assert git(repo, "rev-parse", "main").strip() == before and worktree.changes(str(repo)) == []
    assert subprocess.run(["git", "rev-parse", "-q", "--verify", "MERGE_HEAD"], cwd=str(repo)).returncode != 0
    failed = store.read_json(job.path("merge-failed.json"))
    assert failed["aborted"] is True and "hook says no" in failed["output"] and not os.path.exists(job.path("merge.json"))


def test_a_merge_waits_for_the_repository_lock(repo, fake):
    job, wt = _committed_task(fake)
    out = []
    with worktree.lock(worktree.repo()):
        th = threading.Thread(target=lambda: out.append(worktree.merge("t1")))
        th.start()
        time.sleep(1)
        assert th.is_alive() and not os.path.exists(job.path("merge.json"))
    th.join(30)
    assert out[0][1] == 0


def test_discard_drops_an_unmerged_task_only_without_uncommitted_changes(repo, fake):
    set_turns, _ = fake
    set_turns(reply("Created b.txt.", write={"b.txt": "b\n"}))
    job = task.start("t1", "Add b.txt.", model="m", check_cmd="true")
    task.drive(job)
    wt = worktree.worktree_path(worktree.repo(), "t1")
    with pytest.raises(RepoState, match="uncommitted changes"):
        worktree.discard("t1")
    git(wt, "add", "-A")
    git(wt, "commit", "-q", "-m", "work")
    with pytest.raises(RepoState, match="not merged into main"):
        worktree.cleanup("t1")
    with store.flock(job.lock_path):
        with pytest.raises(Running):
            worktree.discard("t1")
    assert worktree.discard("t1")[1] == 0
    assert not os.path.exists(wt) and git(repo, "branch", "--list", "agy/t1") == ""
    assert store.read_json(job.path("cleanup.json"))["action"] == "discard"


def test_cli_merge_and_cleanup(repo, fake):
    _committed_task(fake)
    r = subprocess.run([sys.executable, AGYCALL, "task", "--merge", "t1"], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and r.stdout.startswith("merged agy/t1 into main as "), r.stderr
    r = subprocess.run([sys.executable, AGYCALL, "task", "--cleanup", "t1"], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and r.stdout.startswith("removed the worktree ")
    r = subprocess.run([sys.executable, AGYCALL, "task", "--merge", "nope"], capture_output=True, text=True, timeout=60)
    assert r.returncode == 2 and "no task nope" in r.stderr
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uvx pytest -q tests/test_worktree.py`
Expected: 7 failed, 5 passed: the Task 1 tests pass; the new tests fail with `AttributeError: module 'agycall.worktree' has no attribute 'merge'` (or `cleanup`, `discard`), and the CLI test because `task --merge` is not an option yet.

- [ ] **Step 3: Add merge, cleanup and discard to `agycall/worktree.py`**

In `agycall/worktree.py`, replace:

```python
import collections
import hashlib
import os
import subprocess

from . import store as st
from .errors import Mismatch, RepoState
```

with:

```python
import collections
import contextlib
import hashlib
import os
import subprocess

from . import checks, runner
from . import store as st
from .errors import Mismatch, RepoState, Running
```

Append to `agycall/worktree.py`, after two blank lines:

```python
def _rev(cwd, ref):
    return git(cwd, "rev-parse", "--verify", ref).strip()


@contextlib.contextmanager
def _held(job_id, cwd):
    """A task's repository, job and task record, with the job lock taken without blocking (Running, exit 25, while the
    job runs or another command holds it) and then the repository lock, both held until the block ends."""
    r = repo(cwd)
    job = st.Job(r.store, job_id)
    t = (st.read_json(job.path("request.json"), {}) or {}).get("task")
    if not t:
        raise ValueError("no task %s in %s" % (job_id, r.store))
    with contextlib.ExitStack() as stack:
        try:
            stack.enter_context(st.flock(job.lock_path, blocking=False))
        except st.Busy:
            raise Running("task %s is running or another agy-call command holds it" % job_id)
        stack.enter_context(lock(r))
        yield r, job, t


def merge(job_id, cwd=None):
    """`agy-call task --merge JOB` (spec 4.8): check the task branch tip in its worktree and merge it into the base
    branch of the main checkout with a merge commit. Returns (text, exit code): 0 merged, 20 the check failed; every
    refusal raises RepoState (26) and leaves the main checkout as it was."""
    with _held(job_id, cwd) as (r, job, t):
        wt, br, base, top = t["worktree"], t["branch"], t["baseBranch"], t["checkout"]
        if not os.path.isdir(wt):
            raise RepoState("the worktree %s of task %s is missing" % (wt, job_id))
        dirty = changes(wt)
        if dirty:
            raise RepoState("the task worktree %s has uncommitted changes; commit them on %s first:\n%s"
                            % (wt, br, "\n".join(dirty)))
        if current_branch(top) != base:
            raise RepoState("the main checkout %s is not on %s; check out %s first" % (top, base, base))
        dirty = changes(top)
        if dirty:
            raise RepoState("the main checkout %s has uncommitted or untracked files:\n%s" % (top, "\n".join(dirty)))
        task_commit, base_tip = _rev(top, "refs/heads/" + br), _rev(top, "refs/heads/" + base)
        if _ok(top, "merge-base", "--is-ancestor", task_commit, base_tip):
            raise RepoState("%s has no commit that %s does not have; commit the task's work on %s first" % (br, base, br))
        if not _ok(top, "merge-base", "--is-ancestor", base_tip, task_commit):
            raise RepoState("base moved: merge %s into %s, test, commit, and run --merge again" % (base, br))
        if _rev(wt, "HEAD") != task_commit:
            raise RepoState("the worktree %s is not on the tip of %s" % (wt, br))
        try:
            with open(job.path("answer.txt"), encoding="utf-8") as fh:
                answer = fh.read()
        except OSError:
            answer = ""
        findings = checks.run_cmd(t["check"], answer, t["checkTimeout"], cwd=wt, combined=True)
        if findings:
            return "the check failed on %s, so nothing was merged:\n%s" % (task_commit, findings[0]), 20
        if changes(wt) or _rev(wt, "HEAD") != task_commit or _rev(top, "refs/heads/" + br) != task_commit:
            raise RepoState("the check changed the worktree or the branch %s, so nothing was merged; its changes stay in "
                            "%s" % (br, wt))
        p = subprocess.run(["git", "merge", "--no-ff", "--no-edit", "-m", "Merge agy task %s" % job_id, br], cwd=top,
                           capture_output=True, text=True, env=dict(os.environ, GIT_EDITOR="true"))
        if p.returncode:
            output = (p.stdout + p.stderr).strip()
            conflicts = git(top, "diff", "--name-only", "--diff-filter=U").split()
            aborted = _ok(top, "merge", "--abort") if _ok(top, "rev-parse", "-q", "--verify", "MERGE_HEAD") else None
            st.write_atomic(job.path("merge-failed.json"), {"exit": p.returncode, "output": output[-4000:],
                                                            "conflicts": conflicts, "aborted": aborted,
                                                            "time": runner._now()})
            if aborted is False:
                raise RepoState("git merge failed, and git merge --abort failed too: the main checkout %s has an "
                                "unfinished merge; finish or abort it yourself. git said: %s" % (top, output[-1000:]))
            raise RepoState("git merge failed and nothing was merged%s. git said: %s"
                            % (" (conflicts: %s)" % ", ".join(conflicts) if conflicts else "", output[-1000:]))
        merged = _rev(top, "refs/heads/" + base)
        parents = git(top, "rev-list", "--parents", "-n", "1", merged).split()
        if parents != [merged, base_tip, task_commit]:
            raise RepoState("git merge did not make the expected merge commit: %s has parents %s; check the main "
                            "checkout %s" % (merged, " ".join(parents[1:]), top))
        st.write_atomic(job.path("merge.json"), {"taskCommit": task_commit, "mergeCommit": merged, "baseTip": base_tip,
                                                 "check": t["check"], "checkExit": 0, "time": runner._now()})
        return "merged %s into %s as %s" % (br, base, merged), 0


def _remove(r, job, t, action):
    """Remove a task's worktree and then its branch, never with --force: git refuses a worktree with uncommitted or
    untracked files (ignored files do not count), and the branch is deleted only while it still has the tip read here.
    Records the removal in cleanup.json; the job record stays."""
    wt, br = t["worktree"], t["branch"]
    tip = _rev(r.top, "refs/heads/" + br)
    if os.path.isdir(wt):
        git(r.top, "worktree", "remove", wt)
    else:
        git(r.top, "worktree", "prune")
    git(r.top, "update-ref", "-d", "refs/heads/" + br, tip)
    st.write_atomic(job.path("cleanup.json"), {"action": action, "branchTip": tip, "time": runner._now()})
    return "removed the worktree %s and the branch %s (its tip was %s)" % (wt, br, tip), 0


def cleanup(job_id, cwd=None):
    """`agy-call task --cleanup JOB` (spec 4.9): remove a task's worktree and branch once the branch is merged into its
    base branch."""
    with _held(job_id, cwd) as (r, job, t):
        gone = st.read_json(job.path("cleanup.json"))
        if gone:
            return "task %s was already removed by --%s" % (job_id, gone["action"]), 0
        if not _ok(r.top, "merge-base", "--is-ancestor", "refs/heads/" + t["branch"], "refs/heads/" + t["baseBranch"]):
            raise RepoState("%s is not merged into %s; merge it with task --merge, or drop it with task --discard"
                            % (t["branch"], t["baseBranch"]))
        return _remove(r, job, t, "cleanup")


def discard(job_id, cwd=None):
    """`agy-call task --discard JOB` (spec 4.9): drop a task, merged or not, when its worktree has no uncommitted
    change."""
    with _held(job_id, cwd) as (r, job, t):
        gone = st.read_json(job.path("cleanup.json"))
        if gone:
            return "task %s was already removed by --%s" % (job_id, gone["action"]), 0
        dirty = changes(t["worktree"]) if os.path.isdir(t["worktree"]) else []
        if dirty:
            raise RepoState("the task worktree %s has uncommitted changes; commit or remove them first:\n%s"
                            % (t["worktree"], "\n".join(dirty)))
        return _remove(r, job, t, "discard")
```

- [ ] **Step 4: Add `--merge`, `--cleanup` and `--discard` to `agycall/cli.py`**

In `agycall/cli.py`, replace:

```python
    if not a.job or not a.prompt_file:
        raise ValueError("task needs --job and --prompt-file")
```

with:

```python
    for action in ("merge", "cleanup", "discard"):
        if getattr(a, action):
            return getattr(worktree, action)(getattr(a, action))
    if not a.job or not a.prompt_file:
        raise ValueError("task needs --job and --prompt-file")
```

In `agycall/cli.py`, replace:

```python
    t.add_argument("--background", action="store_true")
```

with:

```python
    t.add_argument("--background", action="store_true")
    actions = t.add_mutually_exclusive_group()
    for action in ("merge", "cleanup", "discard"):
        actions.add_argument("--" + action, metavar="JOB")
```

In `agycall/cli.py`, replace:

```python
    """agy-call task: start or rerun a coding task in its own worktree (spec 4.3); prints the hand-back."""
```

with:

```python
    """agy-call task: start, rerun or continue a coding task in its own worktree and print the hand-back (spec 4.3,
    4.7), or merge, clean up or discard one (4.8, 4.9)."""
```

- [ ] **Step 5: Run the tests**

Run: `uvx pytest -q tests/test_worktree.py`
Expected: all pass.
Run: `for i in 1 2 3; do uvx pytest -q tests/ || break; done`
Expected: every run passes (219 tests).

- [ ] **Step 6: Commit**

```bash
git add agycall/worktree.py agycall/cli.py tests/test_worktree.py
printf '%s\n' "Merge, clean up and discard task worktrees" \
  "" \
  "task --merge checks the task branch tip in its worktree and merges it into" \
  "the base branch with a merge commit, refusing a dirty or moved state and" \
  "aborting a failed merge. --cleanup removes a merged task's worktree and" \
  "branch, --discard an unmerged one without uncommitted changes; neither" \
  "forces git." \
  "" \
  "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" "Claude-Session: https://claude.ai/code/session_013coVpN7if3z6kaMYLTrz3c" > "$TMPDIR/agycall-msg.txt"
git commit -F "$TMPDIR/agycall-msg.txt"
```

---

### Task 6: Docs

**Files:**
- Modify: `skills/agy-call/SKILL.md`, `README.md`

**Interfaces:**
- Consumes: Tasks 1 to 5, and the step 2 text of `skills/agy-call/SKILL.md`.
- Produces: user-facing docs that match the code.

This task is mechanical: it has no test of its own and gets no task reviewer; the final review covers it.

- [ ] **Step 1: Update `skills/agy-call/SKILL.md`**

Make these replacements; every new line is one sentence.

In `skills/agy-call/SKILL.md`, replace:

```markdown
`CheckError` (21), `NoConversation` (23), `Cancelled` (27).
```

with:

```markdown
`CheckError` (21), `NoConversation` (23), `RepoState` (26), `Cancelled` (27), `Blocked` (28, with `.findings`).
```

In `skills/agy-call/SKILL.md`, replace:

```markdown
`agy-call status [JOB] [--store DIR] [--namespace NAME]` lists the jobs of a store, one line each: the id, the state, the age and the last finding.
```

with:

```markdown
`agy-call status [JOB] [--all] [--store DIR] [--namespace NAME]` lists the jobs of a store, one line each: the id, the state, the kind (`run` or `task`), the age and the last finding.
Without `--store` or `--namespace`, `status`, `result` and `cancel` use the task store of the current git repository when they run inside one (a task's worktree counts), and the default store otherwise; pass `--namespace default` for `run` jobs of the default store from inside a repository.
`--all` lists every store under `~/.agy-call/jobs`, repository stores and namespace stores, one block per store.
```

In `skills/agy-call/SKILL.md`, replace:

```markdown
For any other state it prints the state, the findings and the last saved reply, and exits with that state's code: 10 quota, 20 failed, 21 check error, 23 no conversation, 25 queued, running or checking, 27 cancelled, and the setup codes 11, 13, 14 and 22.
```

with:

```markdown
For any other state it prints the state, the findings and the last saved reply, and exits with that state's code: 10 quota, 20 failed, 21 check error, 23 no conversation, 25 queued, running or checking, 27 cancelled, 28 blocked, and the setup codes 11, 13, 14 and 22.
For a task it then prints the hand-back (see Coding tasks).
```

In `skills/agy-call/SKILL.md`, replace:

```markdown
| `fatal` | agy is missing or not signed in, the model is unknown, or agy's output was not a JSON envelope |
```

with:

```markdown
| `blocked` | a task's reply had `NEED:` lines or agy refused an action (exit 28); waiting for a wider grant |
| `fatal` | agy is missing or not signed in, the model is unknown, or agy's output was not a JSON envelope |
| `setup-error` | the same as `fatal`, for a task |
```

In `skills/agy-call/SKILL.md`, replace:

```markdown
| 25 | the job is running in another process; from `result`, the job is still queued, running or checking; from `cancel`, it is still stopping |
| 27 | the job was cancelled |
```

with:

```markdown
| 25 | the job is running in another process; from `result`, the job is still queued, running or checking; from `cancel`, it is still stopping |
| 26 | repository state: not inside a git repository, uncommitted or untracked files, a detached HEAD, a missing worktree, or a merge, cleanup or discard that the repository's state refuses |
| 27 | the job was cancelled |
| 28 | a task is blocked on permission: its reply had `NEED:` lines or agy refused an action |
```

In `skills/agy-call/SKILL.md`, replace:

```markdown
Output that is not a complete reply restarts the turn.
```

with:

```markdown
Output that is not a complete reply restarts the turn.
A task's record is `~/.agy-call/jobs/<repo id>/<job>/`; beside the files above it holds `grant-<n>.json` (the grant passed with `--settings`), `segments/NNN.json` (each segment's prompt, grant and budget), `segments/NNN-answer.txt` (an earlier segment's answer), and `merge.json`, `merge-failed.json` and `cleanup.json` once those commands ran.
```

In `skills/agy-call/SKILL.md`, replace:

```markdown
- 27: the job was cancelled and stays cancelled; its replies are kept under `attempts/`; to do the work anyway, run it under a new job id.
```

with:

```markdown
- 26: the repository is not in the state the command needs; the message says what to change (commit, stash or ignore files, check out a branch, merge the base into the task branch, or clean up a task before `--new-version`).
- 27: the job was cancelled and stays cancelled; its replies are kept under `attempts/`; to do the work anyway, run it under a new job id.
- 28: read the `NEED:` lines and the refused action kinds (`agy-call status JOB`), then follow "After `blocked`" under Coding tasks.
```

In `skills/agy-call/SKILL.md`, replace everything from the line `## Coding tasks` up to the line `## Multi-turn work` (that line stays) with:

````markdown
## Coding tasks

`agy-call task` hands a coding task to Gemini in its own git worktree on a new branch `agy/<job>`, with a permission grant made for that task only; use it instead of `run` for any work that edits files.
Run it from inside the repository:

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
- `--background` prints the job id and runs the task in a detached worker; follow it with `agy-call status` and `agy-call result JOB`.

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

After `blocked`: you may continue with `--allow <command>` for build, test or read commands confined to the repository.
Ask the user first for anything that reaches outside it: network access, deletion outside the worktree, package installs, `git push`, or credentials.

### Hand-back and merge

1. Read the diff in the worktree and run the build and tests there yourself.
2. Commit on `agy/<job>` in the worktree with the user's git identity and a message naming the job.
3. Run `agy-call task --merge JOB` from the repository.
4. Never push.

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
In shell scripts pass each option and its value as separate words: `--conversation "$ID"`, not one string holding both.

````

- [ ] **Step 2: Update `README.md`**

In `README.md`, replace:

```markdown
- A call can run in the background; `agy-call status`, `result` and `cancel` show how it is going, print its answer and stop it.
```

with:

```markdown
- A call can run in the background; `agy-call status`, `result` and `cancel` show how it is going, print its answer and stop it.
- It can hand a coding task to Gemini in its own git worktree and branch, with a permission grant made for that task only, and merge the checked result locally; agy-call cannot limit what a granted command does, because a test command runs the repository's own code.
```

- [ ] **Step 3: Check and commit**

Run: `grep -c "$(printf '\342\200\224')" README.md skills/agy-call/SKILL.md`; expected: `0` for each.
Read the new lines of `skills/agy-call/SKILL.md` once for one sentence per line.
Run: `uvx pytest -q tests/`; expected: all pass (219 tests).

```bash
git add README.md skills/agy-call/SKILL.md
printf '%s\n' "Document coding tasks, their states and exit codes 26 and 28" \
  "" \
  "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" "Claude-Session: https://claude.ai/code/session_013coVpN7if3z6kaMYLTrz3c" > "$TMPDIR/agycall-msg.txt"
git commit -F "$TMPDIR/agycall-msg.txt"
```

---

## Self-review

- Spec coverage: 3.3 repository store, worktree folder and modes (Task 1); 4.1 the guarantee stated in the skill and README (Task 6); 4.2 grant, read-only set, `--allow` rule and deny mirror (Task 1), passed with `--settings` and recorded per launch (Task 2), rebuilt on every drive (Task 2); 4.3 refusals 26 and 24, `check-ref-format`, repository lock, `--check-cmd` required (Tasks 1 and 2); 4.4 wrapped prompt (Task 1); 4.5 check in the worktree, combined-output finding, no-change failure, output-limit and agy-error replies (Task 2), `NEED:` and refusals (Task 3); 4.6 blocked, the one `NEED:` follow-up, nothing widened (Task 3); 4.7 segments, answer moved aside, cancel request consumed, new grant, exits 25 and 26, no agy `--continue`, `--new-version` only after cleanup (Tasks 4 and 5); 4.8 hand-back (Task 2) and merge with both locks, every refusal, the check, abort and confirmation (Task 5); 4.9 cleanup and discard (Task 5); 5.2 task column (Tasks 2 and 3); 5.3 budgets and the segment deadline (Task 2); 5.4 `blocked`, `setup-error` (Tasks 2 and 3); 7.2 task identity (Task 2); 7.3 `status` with kind and `--all`, `result` hand-back (Tasks 2 and 4); 7.4 a task sends no automatic turn after an unsettled attempt (Task 3); 13 exits 26 and 28 (Tasks 1, 3, 6).
  Spec 10.2 task cases: dirty, untracked and staged files, detached HEAD, branch clash, refused branch name (Task 1); grant written and passed, check in the worktree with a non-zero exit sent back (Task 2); `NEED:` and denial paths, no-change failure (Tasks 2 and 3); cleanup refusing an unmerged branch, discard refusing uncommitted changes (Task 5).
  Continuation cases: after `done`, `blocked` and `cancelled`, new grant and budget, old answer moved, old cancel consumed, no exit 24 (Task 4).
  Merge cases: running job, dirty worktree, dirty or wrong main checkout, moved base, failing check, a check that edits tracked files, a failing merge hook aborted with no `MERGE_HEAD`, `merge.json` only after the confirmed merge commit, merges serialised by the repository lock (Task 5); the conflict case is replaced as explained under Spec clarifications.
  Not in this step by design: reviews and snapshots (step 4), the plugin rename, slash commands, `setup` and the SessionStart hook (step 5), and the live release check with real agy, which the controller runs after this step.
- Placeholders: none; every code step is a whole file, an exact old/new pair taken from the file as the earlier steps leave it, or a block to append.
- Names used across tasks: `worktree.repo/create/changes/current_branch/lock/git/handback/merge/cleanup/discard`, `task.grant/write_grant/prompt/start/drive/continue_/segments/_write_segment/_validate/_queue`, `runner._prepare(..., settings, effort, policy, segment, end)`, `runner._lock(job, stack)`, `runner._own(c, stack)`, `runner._atts(c)`, `runner._interrupt(job, att)`, `control.spawn(job)`, `control.status_all()`, `checks.run_cmd(..., combined=True)`.
- Review Focus: each of the five lines has its test in the named task.

## After this plan

- The controller runs one live release check with real agy: a tiny task in a temporary repository with the deny mirror in place (spec 10.4); this plan does not include it.
- Step 4 (reviews) reuses `worktree.repo`, the repository store, `worktree.lock` and the read-only grant of `task.READ_ONLY`.
- Nothing in this plan pushes; pushing the branch is the user's call.
