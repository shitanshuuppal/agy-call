# agy plugin, build step 4: `agy-call review`, snapshots and the schema validator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `agy-call review` has Gemini review the repository's change in a read-only snapshot and returns a checked list of findings with a severity each, tied to the exact code reviewed, in the foreground or the background, with `result`, `status`, `cancel` and `--cleanup`.

**Architecture:** `agycall/review.py` owns reviews: it finds the target (working tree or branch), computes the review's identity and default job id before anything is written, records the snapshot commit under `refs/agy-call/<job>` with a detached worktree, and drives the review through `runner._drive` with `policy="review"`.
The runner's step 3 task switches become `policy != "run"`, so reviews share the task column of spec 5.2 (blocked, summary turn, timeout, budgets, deadline) and differ only in their messages and the absence of a worktree-changed check; a review launch adds `--json-schema`.
The schema validator in `checks.py` enforces the keywords the findings schema uses and refuses every other keyword before a launch.

**Tech Stack:** Python 3.9+ standard library only; git 2.32 or newer in the tests; tests with pytest run as `uvx pytest -q tests/`.

**Spec:** `docs/superpowers/specs/2026-11-12-agy-plugin-design.md`, sections 8.1 to 8.6, 7.2 (review identity), the review column of 5.2, 5.3 (review budgets), 3.3 (snapshot worktree path) and the review cases of 10.2.
This is build step 4 of section 12, and it applies on top of build step 2 (`docs/superpowers/plans/2026-11-12-agy-plugin-step2-job-control.md`, with its review fixes) and build step 3 (`docs/superpowers/plans/2026-11-12-agy-plugin-step3-tasks.md`), which must both be merged first.
The controller's decisions in `docs/superpowers/plans/notes-step4.md` bind this step and win where they differ from the spec: the command line of `review`, the schema validator's `ValueError` (exit 2) instead of the spec's `CheckError`, and the review policy as the task policy without the worktree-changed check.
The plugin rename, the slash commands and their default `--model gemini-4-argon-high --effort high` are step 5.

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
- `task` behaviour stays unchanged: its messages, states, exit codes and record files are word for word and byte for byte what step 3 makes, and `tests/test_task.py` and `tests/test_worktree.py` pass without edits.
- The `repo` fixture also sets `GIT_CONFIG_GLOBAL=/dev/null` and `GIT_CONFIG_NOSYSTEM=1`, so a developer's own git settings (commit signing, hooks paths) cannot change a test.
- The background worker still gets only `--store` and `--job` on its command line; a task or review worker reads everything else from the job record.
- Every test that uses real processes waits with a bounded loop (`wait_for`, 30 seconds at most), and the suite is run three times at the end of each code task.

## Review Focus

- A changed file whose name has a space, in a subfolder, with the review started from that subfolder: the changed-file list holds the exact name relative to the top of the checkout, and a reply that lists it is not marked `incomplete` (Task 2, `test_a_review_from_a_subfolder_covers_the_whole_checkout_with_exact_file_names`; Task 3, `test_a_file_name_with_a_space_counts_as_reviewed`).
- A finding whose `file` is written `./a.txt` instead of `a.txt`: one format-repair turn in the same conversation names the changed files, and the corrected reply is accepted (Task 3, `test_a_finding_on_a_file_outside_the_change_gets_a_format_repair_turn`).
- A repository whose base branch is neither `main` nor `master` and that has no `origin/HEAD`: the branch scope exits 26 asking for `--base`, and `--base trunk` works (Task 2, `test_a_branch_review_without_a_base_branch_asks_for_base`).
- Files edited right after `review --background` returns: they are not in the review, because the snapshot exists before the worker starts, and `result` warns that the code changed (Task 4, `test_a_background_review_takes_its_snapshot_before_it_returns`).
- A checkout with no git identity that refuses to guess one (`user.useConfigOnly`): the snapshot commit is still made, with agy-call's own author and committer (Task 2, `test_a_checkout_without_a_git_identity_can_be_reviewed`).

## Spec clarifications made by this plan

- The identity's `scope` field is the scope actually used, so `--scope auto` and the scope it resolves to give the same identity and the same default job id.
- The snapshot commit is made with agy-call's own author and committer (`SNAP_ENV`) and `--no-gpg-sign`, so it needs no git identity and never asks for a signature; the commit is private to the ref `refs/agy-call/<job>`.
- For the branch scope the snapshot commit is `HEAD` itself (spec 8.3), and the base is `merge-base(BASE, HEAD)`.
- `--base` applies to the branch scope; `--scope working-tree --base REF` exits 2, and with `--scope auto` on a checkout with changes it is not used.
- A review job id given with `--job` follows the task rule (`git check-ref-format`), because it names the ref `refs/agy-call/<job>`.
- Reviews have no `--deadline` and no `--new-version` (the command line of notes-step4.md); the 2-hour deadline of spec 5.3 runs from the start of each drive (first run, rerun or worker), so a rerun after a quota stop is not stopped by the time the hold took.
- A review that ended `failed` or `blocked` keeps that stop on a rerun, as a task segment does; because the default id follows the code and the settings, the way to review again is another `--job`, which the stop's finding says.
- The accepted answer of a review (`answer.txt`) is the reply's JSON; when `files_reviewed` misses changed files, agy-call sets `verdict` to `incomplete` and adds `missing_files`, a key that is not in the schema Gemini answers to.
- The stale-code warning of `result` compares tree ids and prints the first 12 hex characters of each; for the working-tree scope the current tree is written through a copy of the index, as the snapshot was.
- `result` renders a review only in state `done`; other states print the generic state, findings and last reply, as for every job.
- The schema validator refuses every keyword outside the nine of spec 8.4, annotation keywords such as `description` and `$schema` included, and accepts `additionalProperties` only as `false`; a search for callers that pass `--schema` or `schema=` to agy-call found none, so no known caller breaks.
- A rerun of an unfinished review after `--cleanup` makes the ref and the snapshot worktree again from the recorded snapshot commit; a finished one returns the saved findings without them.
- `review --cleanup` removes the snapshot worktree without `--force`, so a snapshot worktree that someone edited is refused (26) with git's message.

## File map

| File | Change | Responsibility |
|---|---|---|
| `agycall/checks.py` | modify | the keywords of spec 8.4 enforced, every other keyword refused (exit 2) |
| `agycall/review.py` | create | target, snapshot, identity, default id, prompt, record, findings check, drive, rendering, cleanup |
| `agycall/runner.py` | modify | `policy="review"`, `POLICIES` messages, `--json-schema` |
| `agycall/task.py` | modify | `grant(None, ())`: the read-only grant of a review |
| `agycall/control.py` | modify | review rendering in `result`, review workers, `status --all` heading |
| `agycall/cli.py` | modify | `agy-call review` and `review --cleanup` |
| `tests/test_checks.py` | modify | validator keywords and refusals |
| `tests/test_review.py` | create | snapshots, identity, policy, findings check, CLI, background, cleanup |
| `skills/agy-call/SKILL.md`, `README.md` | modify | reviews, states and exit codes |

---

### Task 1: Schema validator keywords

**Files:**
- Modify: `agycall/checks.py`
- Test: `tests/test_checks.py`

**Interfaces:**
- Consumes: step 1's `checks.validate(value, schema, where="$") -> list[str]`, `checks._check_schema(s, where="$")` and `checks.make_check(..., schema=PATH, ...)`.
- Produces:
  - `checks.KEYWORDS`: the set of the nine keywords spec 8.4 lists.
  - `checks.validate` enforces type lists (`"type": ["integer", "null"]`), `minLength`, `minimum`, `maximum` and `additionalProperties: false`, with these findings: `$.x should be integer or null`, `$.x should have length N or more`, `$.x should be at least N`, `$.x should be at most N`, `$ has a key the schema does not allow: K`.
  - `checks.make_check(schema=...)` raises `ValueError("invalid schema: ...")` for any other keyword, for `additionalProperties` other than `false`, for a non-number bound, and for an empty or unknown type list; the CLI turns that into exit 2 before anything is written or launched.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_checks.py`, after two blank lines:

```python
def test_schema_type_lists_lengths_bounds_and_closed_objects(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"type": "object", "additionalProperties": False, "required": ["summary", "line"],
                             "properties": {"summary": {"type": "string", "minLength": 1},
                                            "line": {"type": ["integer", "null"], "minimum": 1, "maximum": 9}}}))
    check, _ = checks.make_check(schema=str(p))
    assert check('{"summary": "s", "line": 3}').ok and check('{"summary": "s", "line": null}').ok
    v = check('{"summary": "", "line": -4, "extra": 1}')
    assert not v.ok and sorted(v.findings) == ["$ has a key the schema does not allow: extra",
                                               "$.line should be at least 1", "$.summary should have length 1 or more"]
    assert check('{"summary": "s", "line": 10}').findings == ["$.line should be at most 9"]
    assert check('{"summary": "s", "line": "3"}').findings == ["$.line should be integer or null"]
    assert check('{"summary": "s", "line": true}').findings == ["$.line should be integer or null"]


@pytest.mark.parametrize("schema", [{"type": "string", "pattern": "^a"}, {"$schema": "x"}, {"description": "d"},
                                    {"properties": {"a": {"oneOf": []}}}, {"items": {"format": "date"}},
                                    {"additionalProperties": True}, {"additionalProperties": {"type": "string"}},
                                    {"minLength": "1"}, {"maximum": True}, {"type": []}, {"type": ["string", "int"]}])
def test_a_schema_the_validator_cannot_enforce_is_refused(tmp_path, schema):
    p = tmp_path / "s.json"
    p.write_text(json.dumps(schema))
    with pytest.raises(ValueError, match="invalid schema"):
        checks.make_check(schema=str(p))


def test_run_refuses_an_unenforced_schema_keyword_before_any_launch(tmp_path, fake, capsys):
    from agycall import cli
    set_turns, launches = fake
    set_turns({"response": '{"a": "x"}', "conversation": "c1"})
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"type": "object", "properties": {"a": {"type": "string", "pattern": "^x"}}}))
    code = cli.main(["run", "--job", "j", "--model", "m", "--prompt", "p", "--schema", str(p),
                     "--store", str(tmp_path / "s")])
    assert code == 2 and "pattern" in capsys.readouterr().err and launches() == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uvx pytest -q tests/test_checks.py`
Expected: 11 failed, 24 passed: the closed-object test (an extra key and the bounds are not reported), 9 of the 11 refusal cases (`{"type": []}` and `{"type": ["string", "int"]}` already raise), and the CLI test (exit 0 with one launch).

- [ ] **Step 3: Enforce the keywords and refuse every other one**

In `agycall/checks.py`, replace:

```python
TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}
```

with:

```python
TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}
KEYWORDS = {"type", "enum", "required", "properties", "items", "additionalProperties", "minLength", "minimum",
            "maximum"}  # spec 8.4: every keyword the validator enforces; any other one is refused
```

In `agycall/checks.py`, replace:

```python
def _type_ok(value, t):
    if t == "number":
```

with:

```python
def _type_ok(value, t):
    if isinstance(t, list):
        return any(_type_ok(value, x) for x in t)
    if t == "number":
```

In `agycall/checks.py`, replace:

```python
def validate(value, schema, where="$"):
    t = schema.get("type")
    if t and not _type_ok(value, t):
        return ["%s should be %s" % (where, t)]
    out = []
    if "enum" in schema and value not in schema["enum"]:
        out.append("%s should be one of %s" % (where, schema["enum"]))
    if isinstance(value, dict):
        out += ["%s is missing %s" % (where, k) for k in schema.get("required", []) if k not in value]
        for k, sub in schema.get("properties", {}).items():
            if k in value:
                out += validate(value[k], sub, "%s.%s" % (where, k))
```

with:

```python
def validate(value, schema, where="$"):
    t = schema.get("type")
    if t is not None and not _type_ok(value, t):
        return ["%s should be %s" % (where, t if isinstance(t, str) else " or ".join(t))]
    out = []
    if "enum" in schema and value not in schema["enum"]:
        out.append("%s should be one of %s" % (where, schema["enum"]))
    if isinstance(value, str) and len(value) < schema.get("minLength", 0):
        out.append("%s should have length %s or more" % (where, schema["minLength"]))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            out.append("%s should be at least %s" % (where, schema["minimum"]))
        if "maximum" in schema and value > schema["maximum"]:
            out.append("%s should be at most %s" % (where, schema["maximum"]))
    if isinstance(value, dict):
        out += ["%s is missing %s" % (where, k) for k in schema.get("required", []) if k not in value]
        props = schema.get("properties", {})
        for k, sub in props.items():
            if k in value:
                out += validate(value[k], sub, "%s.%s" % (where, k))
        if schema.get("additionalProperties") is False:
            out += ["%s has a key the schema does not allow: %s" % (where, k) for k in value if k not in props]
```

In `agycall/checks.py`, replace:

```python
def _check_schema(s, where="$"):
    if not isinstance(s, dict):
        raise ValueError("invalid schema: %s is not an object" % where)
    t = s.get("type")
    if t is not None and (not isinstance(t, str) or t not in _NAMES):
        raise ValueError("invalid schema: unknown type %r at %s" % (t, where))
```

with:

```python
def _check_schema(s, where="$"):
    if not isinstance(s, dict):
        raise ValueError("invalid schema: %s is not an object" % where)
    unknown = sorted(set(s) - KEYWORDS)
    if unknown:  # spec 8.4: a keyword the validator does not enforce is refused before any launch
        raise ValueError("invalid schema: %s uses %s, which agy-call does not enforce; it supports %s"
                         % (where, ", ".join(unknown), ", ".join(sorted(KEYWORDS))))
    t = s.get("type")
    names = t if isinstance(t, list) else [t]
    if t is not None and not (names and all(isinstance(n, str) and n in _NAMES for n in names)):
        raise ValueError("invalid schema: unknown type %r at %s" % (t, where))
    if s.get("additionalProperties", False) is not False:
        raise ValueError("invalid schema: additionalProperties at %s must be false" % where)
    for k in ("minLength", "minimum", "maximum"):
        if k in s and (isinstance(s[k], bool) or not isinstance(s[k], (int, float))):
            raise ValueError("invalid schema: %s at %s must be a number" % (k, where))
```

- [ ] **Step 4: Run the tests**

Run: `uvx pytest -q tests/test_checks.py`
Expected: all pass.

Run: `for i in 1 2 3; do uvx pytest -q tests/ || break; done`
Expected: every run passes (242 tests).

- [ ] **Step 5: Commit**

```bash
git add agycall/checks.py tests/test_checks.py
printf '%s\n' "Enforce the schema keywords reviews need and refuse the others" \
  "" \
  "The validator now checks type lists, minLength, minimum, maximum and" \
  "additionalProperties false, and a schema that uses any other keyword" \
  "exits 2 before anything is launched (spec 8.4)." \
  "" \
  "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" "Claude-Session: https://claude.ai/code/session_013coVpN7if3z6kaMYLTrz3c" > "$TMPDIR/agycall-msg.txt"
git commit -F "$TMPDIR/agycall-msg.txt"
```

---

### Task 2: Review target, identity and snapshot

**Files:**
- Create: `agycall/review.py`
- Test: `tests/test_review.py`

**Interfaces:**
- Consumes: step 3's `worktree.repo(cwd=None) -> Repo(top, id, store)`, `worktree.git(cwd, *args, env=None) -> str`, `worktree._ok(cwd, *args) -> bool`, `worktree.private(path)`, `worktree.worktree_path(r, name)`, `worktree.changes(path)`, `worktree.check_job_id(job_id)`, `worktree.lock(r)`, `runner._lock(job, stack)`, `runner._now()`, `task.READ_ONLY`, `task.shared_grants()`, `store.Job`, `store.write_atomic`, `store.read_json`.
- Produces:
  - `review.SCOPES`, `review.SEVERITIES`, `review.SCHEMA` (the dict of spec 8.4), `review.SCHEMA_TEXT` (its JSON text, written to the record and passed to agy), `review.SCHEMA_SHA`, `review.PROMPT`, `review.ADVERSARIAL`, `review.FOCUS`, `review.REF` (`"refs/agy-call/"`), `review.SNAP_ENV`.
  - `review.Target(scope, base, tree, head, files)`; `review.tree(top) -> tree id`; `review.target(top, scope="auto", base=None) -> Target` (`RepoState` "nothing to review" or a missing base).
  - `review.identity(repo_id, target, adversarial, focus, model, effort) -> sha256 hex`; `review.default_id(ident, adversarial) -> "review-<standard|adversarial>-<16 hex>"`; `review.prompt(rv) -> str`.
  - `review.start(*, model, effort=None, scope="auto", base=None, adversarial=False, focus=None, job_id=None, timeout="20m", max_turns=3, cwd=None) -> store.Job`.
  - A review's `request.json`: `identity`, `kind: "review"`, `prompt`, `key`, `cwd` (the snapshot worktree), `created`, `session`, and `review: {repo, checkout, scope, base, tree, snapshot, ref, worktree, files, adversarial, focus, model, effort, timeout, maxTurns, grant, schema}`; `schema.json` beside it.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_review.py`:

```python
import os
import re
import subprocess
import time

import pytest

from agycall import review, store, worktree
from agycall.errors import Mismatch, RepoState
from agyturns import git


def _rv(job):
    return store.read_json(job.path("request.json"))["review"]


def _refs(repo):
    return git(repo, "for-each-ref", "--format=%(refname)", "refs/agy-call/").split()


def test_a_snapshot_holds_staged_unstaged_and_untracked_files_and_leaves_the_checkout_alone(repo):
    (repo / "a.txt").write_text("staged\n")
    git(repo, "add", "a.txt")
    (repo / "a.txt").write_text("staged\nunstaged\n")
    (repo / "c.txt").write_text("new\n")
    (repo / "build").mkdir()
    (repo / "build" / "out.o").write_text("ignored\n")
    index = (repo / ".git" / "index").read_bytes()
    status = git(repo, "status", "--porcelain")
    job = review.start(model="m")
    assert (repo / ".git" / "index").read_bytes() == index and git(repo, "status", "--porcelain") == status
    assert git(repo, "symbolic-ref", "--short", "HEAD").strip() == "main"
    rv = _rv(job)
    head = git(repo, "rev-parse", "HEAD").strip()
    assert rv["scope"] == "working-tree" and rv["base"] == head and rv["files"] == ["a.txt", "c.txt"]
    assert re.fullmatch(r"review-standard-[0-9a-f]{16}", job.id)
    assert git(repo, "show", rv["snapshot"] + ":a.txt") == "staged\nunstaged\n"
    assert git(repo, "rev-parse", rv["snapshot"] + "^").strip() == head
    assert git(repo, "rev-parse", rv["snapshot"] + "^{tree}").strip() == rv["tree"]
    assert git(repo, "ls-tree", "-r", "--name-only", rv["snapshot"]).split() == [".gitignore", "a.txt", "c.txt"]
    assert _refs(repo) == ["refs/agy-call/" + job.id]
    assert git(repo, "rev-parse", "refs/agy-call/" + job.id).strip() == rv["snapshot"]
    snap = rv["worktree"]
    assert snap == worktree.worktree_path(worktree.repo(), job.id + "-snap")
    assert git(snap, "rev-parse", "HEAD").strip() == rv["snapshot"] and open(os.path.join(snap, "c.txt")).read() == "new\n"
    assert subprocess.run(["git", "symbolic-ref", "-q", "HEAD"], cwd=snap).returncode == 1  # detached
    assert open(rv["schema"]).read() == review.SCHEMA_TEXT
    req = store.read_json(job.path("request.json"))
    assert req["kind"] == "review" and req["cwd"] == snap
    assert "git diff %s %s" % (head, rv["snapshot"]) in req["prompt"] and "\n- a.txt\n- c.txt\n" in req["prompt"]


def test_nothing_to_review_exits_26_and_writes_nothing(repo):
    with pytest.raises(RepoState, match="nothing to review"):
        review.start(model="m")  # auto on a clean checkout is the branch scope, and main has nothing past main
    with pytest.raises(RepoState, match="nothing to review"):
        review.start(model="m", scope="working-tree")
    assert _refs(repo) == [] and not os.path.exists(worktree.repo().store)


def test_the_branch_scope_reviews_from_the_merge_base_to_head(repo):
    git(repo, "checkout", "-q", "-b", "feature")
    (repo / "b.txt").write_text("b\n")
    git(repo, "add", "b.txt")
    git(repo, "commit", "-q", "-m", "b")
    fork = git(repo, "merge-base", "main", "HEAD").strip()
    git(repo, "checkout", "-q", "main")
    (repo / "m.txt").write_text("m\n")
    git(repo, "add", "m.txt")
    git(repo, "commit", "-q", "-m", "m")
    git(repo, "checkout", "-q", "feature")
    rv = _rv(review.start(model="m"))  # a clean checkout: auto is the branch scope
    assert rv["scope"] == "branch" and rv["base"] == fork and rv["files"] == ["b.txt"]
    assert rv["snapshot"] == git(repo, "rev-parse", "HEAD").strip()
    git(repo, "update-ref", "refs/remotes/origin/main", git(repo, "rev-parse", "main").strip())
    git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    git(repo, "branch", "-q", "-m", "main", "old")  # origin/HEAD is found first
    assert _rv(review.start(model="m", scope="branch", job_id="r2"))["base"] == fork


def test_a_branch_review_without_a_base_branch_asks_for_base(repo):
    git(repo, "branch", "-q", "-m", "main", "trunk")
    git(repo, "checkout", "-q", "-b", "feature")
    (repo / "b.txt").write_text("b\n")
    git(repo, "add", "b.txt")
    git(repo, "commit", "-q", "-m", "b")
    with pytest.raises(RepoState, match="pass --base REF"):
        review.start(model="m")
    with pytest.raises(RepoState, match="unknown base"):
        review.start(model="m", base="nope")
    assert _rv(review.start(model="m", base="trunk"))["files"] == ["b.txt"]
    with pytest.raises(ValueError, match="branch scope only"):
        review.start(model="m", scope="working-tree", base="trunk")


def test_a_review_from_a_subfolder_covers_the_whole_checkout_with_exact_file_names(repo, monkeypatch):
    (repo / "docs").mkdir()
    (repo / "docs" / "my file.txt").write_text("x\n")
    (repo / "a.txt").write_text("changed\n")
    monkeypatch.chdir(repo / "docs")
    assert _rv(review.start(model="m"))["files"] == ["a.txt", "docs/my file.txt"]


def test_a_checkout_without_a_git_identity_can_be_reviewed(repo):
    git(repo, "config", "--unset", "user.name")
    git(repo, "config", "--unset", "user.email")
    git(repo, "config", "user.useConfigOnly", "true")  # git refuses to guess one from the account and host names
    (repo / "a.txt").write_text("changed\n")
    rv = _rv(review.start(model="m"))
    assert git(repo, "show", rv["snapshot"] + ":a.txt") == "changed\n"


def test_the_identity_follows_the_content_and_the_settings_not_the_time(repo):
    (repo / "a.txt").write_text("changed\n")
    job = review.start(model="m")
    time.sleep(1.1)  # a later commit time
    (repo / "a.txt").write_text("changed\n")  # the same content written again
    again = review.start(model="m")
    assert again.id == job.id and _rv(again)["snapshot"] == _rv(job)["snapshot"]  # the record is reused
    r = worktree.repo()
    t = review.target(r.top)
    ident = review.identity(r.id, t, False, None, "m", None)
    assert review.default_id(ident, False) == job.id
    others = {review.identity(r.id, t, True, None, "m", None), review.identity(r.id, t, False, "races", "m", None),
              review.identity(r.id, t, False, None, "m2", None), review.identity(r.id, t, False, None, "m", "high")}
    assert ident not in others and len(others) == 4
    assert review.default_id(review.identity(r.id, t, True, None, "m", None), True).startswith("review-adversarial-")
    (repo / "a.txt").write_text("changed again\n")
    assert review.start(model="m").id != job.id


def test_a_job_id_given_twice_for_different_reviews_exits_24(repo):
    (repo / "a.txt").write_text("changed\n")
    review.start(model="m", job_id="r1")
    (repo / "a.txt").write_text("changed again\n")
    with pytest.raises(Mismatch, match="another --job"):
        review.start(model="m", job_id="r1")
    with pytest.raises(ValueError, match="branch name"):
        review.start(model="m", job_id="a..b")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uvx pytest -q tests/test_review.py`
Expected: FAIL at collection with `ImportError: cannot import name 'review' from 'agycall'`.

- [ ] **Step 3: Create `agycall/review.py`**

Create `agycall/review.py`:

```python
"""Reviews (spec 8): the target and its snapshot, the review's identity and default job id, the read-only grant, the
prompt, the findings check, what `result` prints, and cleanup."""
import collections
import contextlib
import hashlib
import json
import os
import shutil
import tempfile

from . import agy, runner, task, worktree
from . import store as st
from .errors import Mismatch, RepoState

SCOPES = ("auto", "working-tree", "branch")
SEVERITIES = ("MUST-FIX", "SHOULD-FIX", "NIT")
SCHEMA = {"type": "object", "additionalProperties": False,
          "required": ["verdict", "summary", "files_reviewed", "findings"],
          "properties": {
              "verdict": {"enum": ["approve", "changes-needed", "incomplete"]},
              "summary": {"type": "string", "minLength": 1},
              "files_reviewed": {"type": "array", "items": {"type": "string"}},
              "findings": {"type": "array", "items": {
                  "type": "object", "additionalProperties": False,
                  "required": ["severity", "title", "file", "line_start", "line_end", "how_hit", "fix"],
                  "properties": {
                      "severity": {"enum": list(SEVERITIES)},
                      "title": {"type": "string", "minLength": 1},
                      "file": {"type": ["string", "null"]},
                      "line_start": {"type": ["integer", "null"], "minimum": 1},
                      "line_end": {"type": ["integer", "null"], "minimum": 1},
                      "how_hit": {"type": "string", "minLength": 1},
                      "fix": {"type": "string", "minLength": 1}}}}}}
SCHEMA_TEXT = json.dumps(SCHEMA, indent=1)
SCHEMA_SHA = hashlib.sha256(SCHEMA_TEXT.encode("utf-8")).hexdigest()
PROMPT = """You are reviewing a code change in the git worktree {worktree}, a read-only snapshot of the change.
Base commit: {base}
Snapshot commit: {snapshot}
Run `git diff {base} {snapshot}` to see the change; the snapshot is checked out in that folder.
The changed files are:
{files}
The commands you may run are exactly these:
{commands}
Before running any command that is not on the list, stop and reply with one line `NEED: <command>` per command instead of trying it.
Do not create, change or delete any file.
Report every MUST-FIX and SHOULD-FIX problem with how a user or author reaches it (how_hit) and the fix, and list smaller points as NIT findings.
{extra}Reply with one JSON object and nothing else, matching this JSON schema:
{schema}
files_reviewed lists every changed file you read.
file is one of the changed files exactly as listed above, or null for a finding about the change as a whole; line_start and line_end are line numbers in the snapshot, or null.
verdict is approve only when there is no MUST-FIX or SHOULD-FIX finding, changes-needed when there is one, and incomplete when you could not review every changed file."""
ADVERSARIAL = ("Assume the change has defects: look for the ways it breaks, and challenge the approach and the design "
               "choices as well as the code; a finding about the design may have file null.\n")
FOCUS = "Give particular attention to: {focus}\n"
REF = "refs/agy-call/"
# the snapshot commit's own author and committer, so a checkout without a git identity can still be reviewed
SNAP_ENV = {"GIT_AUTHOR_NAME": "agy-call", "GIT_AUTHOR_EMAIL": "agy-call@localhost",
            "GIT_COMMITTER_NAME": "agy-call", "GIT_COMMITTER_EMAIL": "agy-call@localhost"}
Target = collections.namedtuple("Target", "scope base tree head files")  # head: the snapshot commit for branch scope


def _commit(top, ref):
    try:
        return worktree.git(top, "rev-parse", "--verify", "--quiet", ref + "^{commit}").strip()
    except RepoState:
        return None


def tree(top):
    """The tree of the checkout's files as `git add -A` would stage them, written through a copy of its index, so the
    user's index, files and branches stay as they are (spec 8.3)."""
    index = os.path.join(top, worktree.git(top, "rev-parse", "--git-path", "index").strip())
    with tempfile.TemporaryDirectory() as d:
        env = dict(os.environ, GIT_INDEX_FILE=os.path.join(d, "index"))
        if os.path.exists(index):
            shutil.copyfile(index, env["GIT_INDEX_FILE"])
        worktree.git(top, "add", "-A", env=env)
        return worktree.git(top, "write-tree", env=env).strip()


def target(top, scope="auto", base=None):
    """Spec 8.2: the scope used, the base commit, the tree under review, the snapshot commit for branch scope, and the
    changed files; RepoState (26) when there is nothing to review."""
    if scope == "auto":
        scope = "working-tree" if worktree.changes(top) else "branch"
    head = None
    if scope == "working-tree":
        base_commit = worktree.git(top, "rev-parse", "--verify", "HEAD").strip()
        t = tree(top)
    else:
        names = [base] if base else ["origin/HEAD", "main", "master"]
        tip = next((c for c in (_commit(top, n) for n in names) if c), None)
        if not tip:
            raise RepoState("unknown base %r in %s" % (base, top) if base else
                            "no base branch in %s: none of origin/HEAD, main or master exists; pass --base REF" % top)
        head = worktree.git(top, "rev-parse", "--verify", "HEAD").strip()
        base_commit = worktree.git(top, "merge-base", tip, head).strip()
        t = worktree.git(top, "rev-parse", head + "^{tree}").strip()
    files = [f for f in worktree.git(top, "diff", "--name-only", "--no-renames", "-z", base_commit, t).split("\0") if f]
    if not files:
        raise RepoState("nothing to review: the %s scope has no change against %s" % (scope, base_commit))
    return Target(scope, base_commit, t, head, files)


def identity(repo_id, t, adversarial, focus, model, effort):
    """A review's identity (spec 7.2): the SHA-256 of exactly these fields. The job id, the snapshot commit and every
    path are left out, so the same content snapshotted at another time has the same identity."""
    payload = {"repo": repo_id, "base": t.base, "tree": t.tree, "scope": t.scope, "adversarial": bool(adversarial),
               "focus": focus or "", "model": model, "effort": effort, "schema": SCHEMA_SHA}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def default_id(ident, adversarial):
    """Spec 8.6."""
    return "review-%s-%s" % ("adversarial" if adversarial else "standard", ident[:16])


def prompt(rv):
    """The review prompt of spec 8.3 for a recorded review."""
    extra = (ADVERSARIAL if rv["adversarial"] else "") + (FOCUS.format(focus=rv["focus"]) if rv["focus"] else "")
    return PROMPT.format(worktree=rv["worktree"], base=rv["base"], snapshot=rv["snapshot"],
                         files="\n".join("- " + f for f in rv["files"]),
                         commands="\n".join("- " + c for c in task.READ_ONLY), extra=extra, schema=SCHEMA_TEXT)


def _snapshot(top, rv):
    """Make sure the snapshot ref and the detached snapshot worktree exist (spec 8.3), on the first run and on a rerun
    after --cleanup removed them."""
    if not worktree._ok(top, "show-ref", "--verify", "--quiet", rv["ref"]):
        worktree.git(top, "update-ref", rv["ref"], rv["snapshot"])
    if not os.path.isdir(rv["worktree"]):
        worktree.private(os.path.dirname(rv["worktree"]))
        worktree.git(top, "worktree", "add", "-q", "--detach", rv["worktree"], rv["snapshot"])


def start(*, model, effort=None, scope="auto", base=None, adversarial=False, focus=None, job_id=None, timeout="20m",
          max_turns=3, cwd=None):
    """`agy-call review` (spec 8.1 to 8.3, 8.6): the target and the identity come first, then the job id; under the job
    lock and the repository lock, the same review already recorded is reused, or the snapshot commit, the schema file
    and request.json are written; then the snapshot ref and worktree are made unless the review is done. Returns the
    job; drive() runs it."""
    if not model:
        raise ValueError("model is required: pass the exact agy model name, e.g. gemini-4-argon-high")
    if scope not in SCOPES:
        raise ValueError("--scope must be one of %s" % ", ".join(SCOPES))
    if base and scope == "working-tree":
        raise ValueError("--base applies to the branch scope only")
    agy.seconds(timeout)
    if max_turns < 1:
        raise ValueError("--max-turns must be at least 1")
    if job_id:
        worktree.check_job_id(job_id)
    task.shared_grants()  # a broken shared config refuses here, before anything is written
    r = worktree.repo(cwd)
    t = target(r.top, scope, base)
    ident = identity(r.id, t, adversarial, focus, model, effort)
    job = st.Job(r.store, job_id or default_id(ident, adversarial))
    with contextlib.ExitStack() as stack:
        runner._lock(job, stack)
        stack.enter_context(worktree.lock(r))
        req = st.read_json(job.path("request.json"))
        if req and req.get("identity") != ident:
            raise Mismatch("job %s exists with a different review; pass another --job (%s)" % (job.id, job.dir))
        if not req:
            snap = t.head or worktree.git(r.top, "commit-tree", "--no-gpg-sign", "-p", t.base, "-m",
                                          "agy-call review snapshot " + job.id, t.tree,
                                          env=dict(os.environ, **SNAP_ENV)).strip()
            rv = {"repo": r.id, "checkout": r.top, "scope": t.scope, "base": t.base, "tree": t.tree, "snapshot": snap,
                  "ref": REF + job.id, "worktree": worktree.worktree_path(r, job.id + "-snap"), "files": t.files,
                  "adversarial": bool(adversarial), "focus": focus or "", "model": model, "effort": effort,
                  "timeout": timeout, "maxTurns": max_turns, "grant": job.path("grant-1.json"),
                  "schema": job.path("schema.json")}
            text = prompt(rv)
            if len(text.encode("utf-8")) > agy.MAX_PROMPT:
                raise ValueError("the review prompt is %d bytes, over this system's single-argument limit of %d bytes; "
                                 "review fewer files at a time" % (len(text.encode("utf-8")), agy.MAX_PROMPT))
            st.write_atomic(rv["schema"], SCHEMA_TEXT)
            req = {"identity": ident, "kind": "review", "prompt": text, "key": model, "cwd": rv["worktree"],
                   "created": runner._now(), "session": os.environ.get("AGYCALL_SESSION") or None, "review": rv}
            st.write_atomic(job.path("request.json"), req)
        if not os.path.exists(job.path("answer.txt")):
            _snapshot(r.top, req["review"])
    return job
```

- [ ] **Step 4: Run the tests**

Run: `uvx pytest -q tests/test_review.py`
Expected: all pass.

Run: `for i in 1 2 3; do uvx pytest -q tests/ || break; done`
Expected: every run passes (250 tests).

- [ ] **Step 5: Commit**

```bash
git add agycall/review.py tests/test_review.py
printf '%s\n' "Snapshot the change a review looks at" \
  "" \
  "review.py finds the target of a review (working tree or branch), its" \
  "identity and default job id, and records the review: a snapshot commit" \
  "kept under refs/agy-call/<job>, a detached snapshot worktree, the" \
  "findings schema and the prompt. The user's index, files and branches" \
  "are not touched, and nothing is written when there is nothing to review." \
  "" \
  "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" "Claude-Session: https://claude.ai/code/session_013coVpN7if3z6kaMYLTrz3c" > "$TMPDIR/agycall-msg.txt"
git commit -F "$TMPDIR/agycall-msg.txt"
```

---

### Task 3: The review policy, the findings check and `review.drive`

**Files:**
- Modify: `agycall/runner.py`, `agycall/task.py`, `agycall/review.py`
- Test: `tests/test_review.py`

**Interfaces:**
- Consumes: Task 2's `review.start`, `review.SCHEMA`, the review record; step 3's `runner._prepare(..., settings, effort, policy, segment, end)`, `runner._drive`, `runner._own`, `runner.NEED_ONLY`, `runner.TASK_STOPS`, `task.write_grant(path, worktree, allow)`; step 1's `agy.argv(..., json_schema=None)`; Task 1's `checks.validate`.
- Produces:
  - `runner._prepare(..., json_schema=None)` and `policy="review"`; `runner.POLICIES` (per policy: `fix`, `summary`, `fatal`, and for `task` and `review` also `what`, `again`, `interrupted`); the state gains `words` and `json_schema`; `runner._next(prompt, last, launch, fix=FIX, summary=SUMMARY_ONLY)`; `runner.FIX_REVIEW`, `runner.SUMMARY_REVIEW`, `runner.INTERRUPTED_REVIEW`.
  - A review launch passes `--json-schema <record>/schema.json`; `run`, `batch` and `task` launches pass no `--json-schema`, and every task message stays word for word.
  - `task.grant(None, ())` is the read-only grant: the `command(...)` entries of `task.READ_ONLY` and the deny mirror, no `write_file`.
  - `review.DEADLINE = "2h"`; `review.checker(files) -> check(text, **run) -> checks.Verdict`; an accepted reply's answer is the reply's JSON, with `verdict: "incomplete"` and `missing_files` added when `files_reviewed` misses changed files.
  - `review.drive(job) -> runner.Result`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_review.py`, replace:

```python
import os
import re
import subprocess
import time

import pytest

from agycall import review, store, worktree
from agycall.errors import Mismatch, RepoState
from agyturns import git
```

with:

```python
import json
import os
import re
import subprocess
import time

import pytest

from agycall import quota, review, runner, store, task, worktree
from agycall.errors import Blocked, Fatal, GaveUp, Mismatch, Quota, RepoState
from agyturns import denied, empty, error_reply, git, model_error, partial, quota_error, reply
```

Append to `tests/test_review.py`, after two blank lines:

```python
def _finding(file, severity="MUST-FIX", start=1, end=2, **extra):
    f = {"severity": severity, "title": "Crash on empty input", "file": file, "line_start": start, "line_end": end,
         "how_hit": "Run it on an empty file.", "fix": "Return early on empty input."}
    f.update(extra)
    return f


def _review_json(files, verdict="changes-needed", findings=None, **extra):
    data = {"verdict": verdict, "summary": "One problem.", "files_reviewed": list(files),
            "findings": [_finding(files[0])] if findings is None else findings}
    data.update(extra)
    return json.dumps(data)


def _changed(repo, *names):
    for n in names:
        (repo / n).write_text("changed %s\n" % n)


def test_a_review_runs_in_its_snapshot_with_the_read_only_grant_and_the_schema(repo, fake, tmp_path):
    set_turns, launches = fake
    (tmp_path / "agy-config.json").write_text(json.dumps(
        {"userSettings": {"globalPermissionGrants": {"allow": ["command(gcloud run)", "command(git diff)"]}}}))
    _changed(repo, "a.txt")
    set_turns(reply(_review_json(["a.txt"])))
    job = review.start(model="m", effort="high")
    r = review.drive(job)
    rv = _rv(job)
    assert job.status()["state"] == "done" and json.loads(r.text)["verdict"] == "changes-needed"
    first = launches()[0]
    assert os.path.realpath(first["cwd"]) == os.path.realpath(rv["worktree"])
    assert first["settings"]["permissions"] == {"allow": ["command(%s)" % c for c in task.READ_ONLY],
                                                "deny": ["command(gcloud run)"]}
    argv = first["argv"]
    assert argv[argv.index("--json-schema") + 1] == rv["schema"] and argv[argv.index("--effort") + 1] == "high"
    assert argv[argv.index("--settings") + 1] == rv["grant"] and "--conversation" not in argv
    assert first["prompt"] == store.read_json(job.path("request.json"))["prompt"]
    assert "Assume the change has defects" not in first["prompt"]


def test_run_still_passes_no_json_schema_to_agy(tmp_path, fake):
    set_turns, launches = fake
    set_turns(reply('{"a": 1}'))
    (tmp_path / "s.json").write_text('{"type": "object"}')
    runner.call("j", "p", store=str(tmp_path / "s"), model="m", schema=str(tmp_path / "s.json"))
    assert "--json-schema" not in launches()[0]["argv"]


def test_the_adversarial_prompt_and_the_focus_text(repo):
    _changed(repo, "a.txt")
    p = store.read_json(review.start(model="m", adversarial=True, focus="error paths").path("request.json"))["prompt"]
    assert "Assume the change has defects" in p and "Give particular attention to: error paths\n" in p
    assert "Give particular attention" not in store.read_json(review.start(model="m").path("request.json"))["prompt"]


def test_the_findings_check_applies_the_schema_and_the_local_rules():
    check = review.checker(["a.txt", "b.txt"])
    v = check(_review_json(["a.txt", "b.txt"]))
    assert v.ok and json.loads(v.answer)["verdict"] == "changes-needed"
    v = check(_review_json(["a.txt", "b.txt"], findings=[_finding("a.txt", start=-4)], summary="", extra=1))
    assert not v.ok and sorted(v.findings) == ["$ has a key the schema does not allow: extra",
                                               "$.findings[0].line_start should be at least 1",
                                               "$.summary should have length 1 or more"]
    v = check(_review_json(["a.txt", "b.txt"], findings=[_finding("a.txt", start=5, end=2)]))
    assert v.findings == ["$.findings[0].line_start is greater than its line_end"]
    v = check(_review_json(["a.txt", "b.txt"], verdict="approve", findings=[_finding("a.txt", "SHOULD-FIX")]))
    assert v.findings == ["$.verdict is approve but there is a MUST-FIX or SHOULD-FIX finding"]
    v = check(_review_json(["a.txt", "b.txt"], verdict="approve",
                           findings=[_finding(None, "NIT", None, None), _finding("b.txt", "NIT", 3, None)]))
    assert v.ok
    assert "no JSON value" in check("Looks good to me.").findings[0]


def test_a_finding_on_a_file_outside_the_change_gets_a_format_repair_turn(repo, fake):
    set_turns, launches = fake
    _changed(repo, "a.txt")
    set_turns(reply(_review_json(["a.txt"], findings=[_finding("./a.txt")])), reply(_review_json(["a.txt"])))
    job = review.start(model="m")
    assert json.loads(review.drive(job).text)["findings"][0]["file"] == "a.txt"
    second = launches()[1]
    assert second["prompt"].startswith("Your review could not be used because its format is wrong")
    assert "'./a.txt' is not one of the changed files; use null or one of: a.txt" in second["prompt"]
    assert second["argv"][second["argv"].index("--conversation") + 1] == "c1"


def test_format_repairs_stop_at_the_turn_limit(repo, fake):
    set_turns, launches = fake
    _changed(repo, "a.txt")
    set_turns(reply("Looks good to me."))
    job = review.start(model="m")
    with pytest.raises(GaveUp, match="no JSON value"):
        review.drive(job)
    assert len(launches()) == 3 and job.status()["state"] == "failed"


def test_files_left_out_of_files_reviewed_end_done_as_incomplete_without_another_turn(repo, fake):
    set_turns, launches = fake
    _changed(repo, "a.txt", "b.txt")
    set_turns(reply(_review_json(["a.txt"], verdict="approve", findings=[])))
    job = review.start(model="m")
    data = json.loads(review.drive(job).text)
    assert data["verdict"] == "incomplete" and data["missing_files"] == ["b.txt"]
    assert len(launches()) == 1 and job.status()["state"] == "done"


def test_a_file_name_with_a_space_counts_as_reviewed(repo, fake):
    set_turns, _ = fake
    (repo / "docs").mkdir()
    (repo / "docs" / "my file.txt").write_text("x\n")
    set_turns(reply(_review_json(["docs/my file.txt"])))
    data = json.loads(review.drive(review.start(model="m")).text)
    assert data["verdict"] == "changes-needed" and "missing_files" not in data


def test_a_reply_that_came_with_an_agy_error_is_judged_by_the_check_alone(repo, fake):
    set_turns, launches = fake
    _changed(repo, "a.txt")
    set_turns(error_reply(_review_json(["a.txt"])))
    job = review.start(model="m")
    assert json.loads(review.drive(job).text)["verdict"] == "changes-needed"
    assert "agyError" in store.read_json(os.path.join(job.launched()[0], "result.json"))
    set_turns(error_reply("Looks good to me."))
    with pytest.raises(GaveUp, match="no follow-up was sent"):  # no format repair after an output-limit stop
        review.drive(review.start(model="m", job_id="r2"))
    assert len(launches()) == 2


def test_need_lines_and_refused_commands_block_a_review(repo, fake):
    set_turns, launches = fake
    _changed(repo, "a.txt")
    set_turns(reply("NEED: find . -name '*.py'"), denied(), reply("NEED: find ."))
    job = review.start(model="m")
    with pytest.raises(Blocked) as e:
        review.drive(job)
    assert e.value.code == 28 and job.status()["state"] == "blocked"
    assert job.status()["need"] == ["NEED: find . -name '*.py'"]
    with pytest.raises(Blocked):
        review.drive(job)  # a rerun keeps the stop and launches nothing
    assert len(launches()) == 1
    job = review.start(model="m", job_id="r2")
    with pytest.raises(Blocked):
        review.drive(job)
    assert launches()[2]["prompt"] == runner.NEED_ONLY and job.status()["denied"] == ["command"]


def test_an_empty_reply_gets_one_turn_that_asks_for_the_json_only(repo, fake):
    set_turns, launches = fake
    _changed(repo, "a.txt")
    set_turns(empty("c1"), reply(_review_json(["a.txt"])))
    assert review.drive(review.start(model="m")).text
    assert launches()[1]["prompt"] == runner.SUMMARY_REVIEW
    set_turns(empty("c2"))
    with pytest.raises(GaveUp, match="a review is never resent blind; review again under another --job"):
        review.drive(review.start(model="m", job_id="r2"))


def test_a_timeout_fails_the_review_and_keeps_the_partial_reply(repo, fake):
    set_turns, _ = fake
    _changed(repo, "a.txt")
    set_turns(partial('{"verdict": "app'))
    job = review.start(model="m")
    with pytest.raises(GaveUp, match="review again under another --job"):
        review.drive(job)
    assert open(os.path.join(job.launched()[0], "reply.txt")).read() == '{"verdict": "app'


def test_setup_errors_end_a_review_as_setup_error(repo, fake):
    set_turns, _ = fake
    _changed(repo, "a.txt")
    set_turns(model_error())
    job = review.start(model="m")
    with pytest.raises(Fatal):
        review.drive(job)
    assert job.status()["state"] == "setup-error" and job.status()["code"] == 14


def test_the_same_content_reviewed_later_returns_the_saved_review(repo, fake):
    set_turns, launches = fake
    _changed(repo, "a.txt")
    set_turns(reply(_review_json(["a.txt"])))
    first = review.drive(review.start(model="m")).text
    time.sleep(1.1)
    _changed(repo, "a.txt")  # the same content written again later
    assert review.drive(review.start(model="m")).text == first and len(launches()) == 1


def test_a_rerun_after_a_quota_stop_continues_the_conversation(repo, fake):
    set_turns, launches = fake
    _changed(repo, "a.txt")
    set_turns(dict(quota_error(), conversation="c1"), reply(_review_json(["a.txt"])))
    job = review.start(model="m")
    with pytest.raises(Quota):
        review.drive(job)
    quota.clear("m", quota.generation("m"))
    assert review.drive(review.start(model="m")).text and len(launches()) == 2
    assert job.status()["state"] == "done"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uvx pytest -q tests/test_review.py`
Expected: 14 failed, 9 passed: the eight Task 2 tests pass, and so does `test_run_still_passes_no_json_schema_to_agy`, which pins today's `run` behaviour; the new tests fail with `AttributeError: module 'agycall.review' has no attribute 'drive'` (or `'checker'`).

- [ ] **Step 3: Give `agycall/runner.py` the review policy**

Make these edits to the step 3 file, in this order.
Every `c.policy == "task"` switch becomes `c.policy != "run"`, and the task messages that name `task --continue` take their wording from `c.words`, so the task texts stay word for word and a review gets its own.

In `agycall/runner.py`, replace:

```python
INTERRUPTED = ("the process running this task stopped before agy's output was complete, and nothing was resent; "
               "agy-call result shows the worktree's changes; continue with task --continue")
```

with:

```python
INTERRUPTED = ("the process running this task stopped before agy's output was complete, and nothing was resent; "
               "agy-call result shows the worktree's changes; continue with task --continue")
FIX_REVIEW = ("Your review could not be used because its format is wrong: {findings}. Reply again with the corrected "
              "JSON object only, keeping the findings you made.")
SUMMARY_REVIEW = ("Your last turn ended without a reply. Do not run anything more; reply only with the JSON object of "
                  "your review.")
INTERRUPTED_REVIEW = ("the process running this review stopped before agy's output was complete, and nothing was "
                      "resent; review again under another --job")
POLICIES = {  # per policy: the fix and summary-only messages, the state of a setup failure, and for the task and review
    # columns of spec 5.2 what a stop calls the job, what it suggests, and the finding of an interrupted turn
    "run": {"fix": FIX, "summary": SUMMARY_ONLY, "fatal": "fatal"},
    "task": {"fix": FIX_TASK, "summary": SUMMARY_ONLY, "fatal": "setup-error", "what": "a coding task",
             "again": "continue with task --continue", "interrupted": INTERRUPTED},
    "review": {"fix": FIX_REVIEW, "summary": SUMMARY_REVIEW, "fatal": "setup-error", "what": "a review",
               "again": "review again under another --job", "interrupted": INTERRUPTED_REVIEW}}
```

In `agycall/runner.py`, replace:

```python
def _next(prompt, last, launch, fix=FIX):
```

with:

```python
def _next(prompt, last, launch, fix=FIX, summary=SUMMARY_ONLY):
```

In `agycall/runner.py`, replace:

```python
        return SUMMARY_ONLY, "summary"
```

with:

```python
        return summary, "summary"
```

In `agycall/runner.py`, replace:

```python
                   settings=c.settings, effort=c.effort)
```

with:

```python
                   settings=c.settings, effort=c.effort, json_schema=c.json_schema)
```

In `agycall/runner.py`, replace:

```python
    if c.policy == "task" and name not in ("quota", "auth", "model", "malformed"):  # the task column of spec 5.2
```

with:

```python
    if c.policy != "run" and name not in ("quota", "auth", "model", "malformed"):  # spec 5.2's task and review column
```

In `agycall/runner.py`, replace:

```python
            give_up(["%s; the partial reply and the worktree are kept; continue with task --continue" % oc.reason])
```

with:

```python
            give_up(["%s; the partial reply and the worktree are kept; %s" % (oc.reason, c.words["again"])])
```

In `agycall/runner.py`, replace:

```python
            give_up(["%s; a coding task is never resent blind; continue with task --continue" % oc.reason])
```

with:

```python
            give_up(["%s; %s is never resent blind; %s" % (oc.reason, c.words["what"], c.words["again"])])
```

In `agycall/runner.py`, replace:

```python
             check_id=None, settings=None, effort=None, policy="run", segment=None, end=None):
    """Check a request before anything is written, and return the job's working state. A task (policy "task") also
    passes its grant file (settings), its effort, its segment number and the segment's end (an aware datetime)."""
```

with:

```python
             check_id=None, settings=None, effort=None, policy="run", segment=None, end=None, json_schema=None):
    """Check a request before anything is written, and return the job's working state. A task (policy "task") or a
    review (policy "review") also passes its grant file (settings), its effort, its segment number and the segment's end
    (an aware datetime); a review also passes the schema file that agy gets with --json-schema."""
```

In `agycall/runner.py`, replace:

```python
    c.fix, c.fatal = (FIX_TASK, "setup-error") if policy == "task" else (FIX, "fatal")
```

with:

```python
    c.words, c.json_schema = POLICIES[policy], json_schema
    c.fix, c.fatal = c.words["fix"], c.words["fatal"]
```

In `agycall/runner.py`, replace:

```python
        if c.policy == "task":  # a task sends no automatic turn (spec 7.4)
            job.set_status(state="failed", findings=[INTERRUPTED])
            raise GaveUp([INTERRUPTED], c.conv, _usage(job), job.dir)
```

with:

```python
        if c.policy != "run":  # a task or a review sends no automatic turn (spec 7.4)
            job.set_status(state="failed", findings=[c.words["interrupted"]])
            raise GaveUp([c.words["interrupted"]], c.conv, _usage(job), job.dir)
```

In `agycall/runner.py`, replace:

```python
    elif c.policy == "task" and last_res.get("outcome") in TASK_STOPS:
```

with:

```python
    elif c.policy != "run" and last_res.get("outcome") in TASK_STOPS:
```

In `agycall/runner.py`, replace:

```python
        message, kind = _next(prompt, results[-1] if results else None, launches[-1] if launches else None, c.fix)
```

with:

```python
        message, kind = _next(prompt, results[-1] if results else None, launches[-1] if launches else None, c.fix,
                              c.words["summary"])
```

In `agycall/runner.py`, replace:

```python
            findings = ["the segment's deadline passed at %s; continue with task --continue" % c.end.isoformat()]
```

with:

```python
            findings = ["the segment's deadline passed at %s; %s" % (c.end.isoformat(), c.words["again"])]
```

Run: `grep -n 'policy == "task"' agycall/runner.py`
Expected: no output.

- [ ] **Step 4: Let `task.grant` build the read-only grant**

In `agycall/task.py`, replace:

```python
def grant(worktree, allow):
    """The --settings content of a task (spec 4.2): writes inside the worktree and the allowed commands; every shared
    grant that is not exactly one of these goes in deny, which agy checks before allow."""
    own = ["write_file(%s)" % worktree] + ["command(%s)" % c for c in commands(allow)]
```

with:

```python
def grant(worktree, allow):
    """The --settings content of a task (spec 4.2): writes inside the worktree and the allowed commands; every shared
    grant that is not exactly one of these goes in deny, which agy checks before allow. A review passes no worktree and
    no --allow entries, so its grant is the read-only commands alone."""
    own = (["write_file(%s)" % worktree] if worktree else []) + ["command(%s)" % c for c in commands(allow)]
```

- [ ] **Step 5: Add the findings check and `drive` to `agycall/review.py`**

In `agycall/review.py`, replace:

```python
import contextlib
import hashlib
```

with:

```python
import contextlib
import datetime as dt
import hashlib
```

In `agycall/review.py`, replace:

```python
from . import agy, runner, task, worktree
```

with:

```python
from . import agy, checks, runner, task, worktree
```

In `agycall/review.py`, replace:

```python
SEVERITIES = ("MUST-FIX", "SHOULD-FIX", "NIT")
```

with:

```python
SEVERITIES = ("MUST-FIX", "SHOULD-FIX", "NIT")
DEADLINE = "2h"  # spec 5.3: bounds one drive of a review across its turns
```

Append to `agycall/review.py`, after two blank lines:

```python
def checker(files):
    """Spec 8.4: the schema, then the file, line and verdict rules; a reply that breaks any of them gets a format-repair
    turn. A reply that keeps them but leaves changed files out of files_reviewed is accepted with its verdict forced to
    incomplete and the missing files listed, and no further turn is sent."""
    def check(text, **run):
        try:
            value = checks.extract_json(text)
        except ValueError as e:
            return checks.Verdict(False, None, ["%s; reply with the JSON object only" % e], False)
        findings = checks.validate(value, SCHEMA)
        if not findings:
            for i, f in enumerate(value["findings"]):
                if f["file"] is not None and f["file"] not in files:
                    findings.append("$.findings[%d].file %r is not one of the changed files; use null or one of: %s"
                                    % (i, f["file"], ", ".join(files)))
                if f["line_start"] is not None and f["line_end"] is not None and f["line_start"] > f["line_end"]:
                    findings.append("$.findings[%d].line_start is greater than its line_end" % i)
            if value["verdict"] == "approve" and any(f["severity"] != "NIT" for f in value["findings"]):
                findings.append("$.verdict is approve but there is a MUST-FIX or SHOULD-FIX finding")
        if findings:
            return checks.Verdict(False, None, findings, False)
        missing = [f for f in files if f not in value["files_reviewed"]]
        if missing:
            value = dict(value, verdict="incomplete", missing_files=missing)
        return checks.Verdict(True, json.dumps(value, ensure_ascii=False, indent=1), [], False)
    return check


def drive(job):
    """Run a review: in the foreground, on a rerun or in the background worker. A saved answer is returned without a
    launch; otherwise the snapshot worktree must exist, and the grant file is written again first, so its deny mirror
    follows the shared config as it is now. The deadline of spec 5.3 runs from the start of each drive."""
    req = st.read_json(job.path("request.json"), {}) or {}
    rv = req.get("review")
    if not rv:
        raise ValueError("no review %s in %s" % (job.id, job.store))
    end = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=agy.seconds(DEADLINE))
    c = runner._prepare(job.id, req["prompt"], store=job.store, model=rv["model"], timeout=rv["timeout"],
                        cwd=rv["worktree"], settings=rv["grant"], effort=rv["effort"], policy="review", segment=1,
                        end=end, json_schema=rv["schema"])
    c.checker, c.checked = checker(rv["files"]), True  # checked: a reply that came with an agy error may still pass
    with contextlib.ExitStack() as stack:
        c.stack = stack
        c.fds.append(runner._lock(job, stack))
        if not os.path.exists(job.path("answer.txt")):
            if not os.path.isdir(rv["worktree"]):
                raise RepoState("the snapshot worktree %s of review %s is missing; run the same review command again "
                                "to make it" % (rv["worktree"], job.id))
            task.write_grant(rv["grant"], None, ())
        runner._own(c, stack)
        return runner._drive(c, rv["maxTurns"], False, False)
```

- [ ] **Step 6: Run the tests**

Run: `uvx pytest -q tests/test_review.py tests/test_task.py`
Expected: all pass.

Run: `for i in 1 2 3; do uvx pytest -q tests/ || break; done`
Expected: every run passes (265 tests).

- [ ] **Step 7: Commit**

```bash
git add agycall/runner.py agycall/task.py agycall/review.py tests/test_review.py
printf '%s\n' "Run reviews with the read-only grant and check their findings" \
  "" \
  "The runner gains a review policy beside the task policy: the rows of" \
  "spec 5.2 are shared, and only the messages differ. A review launch" \
  "passes the findings schema with --json-schema; the reply is checked" \
  "again against the schema and the file, line and verdict rules, which" \
  "get format-repair turns, and missing coverage ends done as incomplete." \
  "" \
  "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" "Claude-Session: https://claude.ai/code/session_013coVpN7if3z6kaMYLTrz3c" > "$TMPDIR/agycall-msg.txt"
git commit -F "$TMPDIR/agycall-msg.txt"
```

---

### Task 4: `agy-call review`, the findings in `result`, background and cleanup

**Files:**
- Modify: `agycall/review.py`, `agycall/control.py`, `agycall/cli.py`
- Test: `tests/test_review.py`

**Interfaces:**
- Consumes: Tasks 2 and 3's `review.start`, `review.drive`, `review.tree`, the review record; step 3's `control.spawn(job)`, `control.work`, `control.result`, `control.status_all`, `worktree.repo`, `worktree.lock`, `runner._lock`.
- Produces:
  - `review.start(..., background=False)`; `review._queue(job)` writes `background.json` as `{"kind": "review"}` and state `queued`, after the snapshot exists.
  - `review.render(job) -> str` (spec 8.5); `control.result` prints it for a `done` review.
  - `review.cleanup(job_id, cwd=None) -> (text, 0)`; `Running` (25) while the job lock is held; `ValueError` (2) for an unknown review.
  - `control.work` drives a review when `background.json` has `kind: "review"`; `status --all` names the repository of a store that holds only reviews.
  - CLI: `agy-call review [--job ID] --model M [--effort E] [--scope auto|working-tree|branch] [--base REF] [--adversarial] [--focus TEXT] [--background] [--max-turns N] [--timeout T]` and `agy-call review --cleanup JOB`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_review.py`, replace:

```python
import json
import os
import re
import subprocess
import time

import pytest

from agycall import quota, review, runner, store, task, worktree
from agycall.errors import Blocked, Fatal, GaveUp, Mismatch, Quota, RepoState
from agyturns import denied, empty, error_reply, git, model_error, partial, quota_error, reply
```

with:

```python
import json
import os
import re
import subprocess
import sys
import time

import pytest

from agycall import quota, review, runner, store, task, worktree
from agycall.errors import Blocked, Fatal, GaveUp, Mismatch, Quota, RepoState
from agyturns import denied, empty, error_reply, git, model_error, partial, quota_error, reply, wait_for

HERE = os.path.dirname(os.path.abspath(__file__))
AGYCALL = os.path.join(os.path.dirname(HERE), "bin", "agy-call")


def run_cli(*args, **kw):
    return subprocess.run([sys.executable, AGYCALL, *args], capture_output=True, text=True, timeout=60, **kw)
```

Append to `tests/test_review.py`, after two blank lines:

```python
def test_cli_review_prints_the_findings_and_result_warns_when_the_code_changed(repo, fake):
    set_turns, _ = fake
    r = run_cli("review", "--model", "m", cwd=str(repo))
    assert r.returncode == 26 and "nothing to review" in r.stderr
    _changed(repo, "a.txt")
    set_turns(reply(_review_json(["a.txt"], findings=[_finding("a.txt"),
                                                     _finding(None, "NIT", None, None, title="Name the flag")])))
    r = run_cli("review", "--model", "m", cwd=str(repo))
    assert r.returncode == 0, r.stderr
    store_dir = worktree.repo().store
    job = store.Job(store_dir, next(n for n in os.listdir(store_dir) if n.startswith("review-standard-")))
    assert r.stdout == ("verdict: changes-needed\nfindings: 1 MUST-FIX, 0 SHOULD-FIX, 1 NIT\n\n"
                        "MUST-FIX: Crash on empty input\n  where: a.txt:1-2\n  how it is reached: Run it on an empty "
                        "file.\n  fix: Return early on empty input.\n\nNIT: Name the flag\n  where: (the change as a "
                        "whole)\n  how it is reached: Run it on an empty file.\n  fix: Return early on empty input.\n\n"
                        "full JSON: %s\n" % job.path("answer.txt"))
    r = run_cli("status", cwd=str(repo))
    assert r.stdout.startswith("%s  done  review  " % job.id), r.stdout
    (repo / "a.txt").write_text("edited after the review\n")
    r = run_cli("result", job.id, cwd=str(repo))
    assert r.returncode == 0 and "\ncode changed since this review (snapshot %s, now " % _rv(job)["tree"][:12] in r.stdout


def test_a_background_review_takes_its_snapshot_before_it_returns(repo, fake, tmp_path):
    set_turns, _ = fake
    _changed(repo, "a.txt")
    set_turns(reply(_review_json(["a.txt"]), sleep=2))
    env = dict(os.environ, PATH="/usr/bin:/bin")  # no agy-call on the PATH: the worker must use this checkout
    r = run_cli("review", "--model", "m", "--background", cwd=str(repo), env=env)
    assert r.returncode == 0, r.stderr
    job = store.Job(worktree.repo().store, r.stdout.strip())
    (repo / "a.txt").write_text("edited while the review runs\n")
    (repo / "z.txt").write_text("new\n")
    assert job.status()["state"] in ("queued", "running")  # the command returned while the turn still runs
    wait_for(lambda: job.status().get("state") == "done")
    rv = _rv(job)
    assert rv["files"] == ["a.txt"] and open(os.path.join(rv["worktree"], "a.txt")).read() == "changed a.txt\n"
    r = run_cli("result", job.id, cwd=str(repo), env=env)
    assert r.stdout.startswith("verdict: changes-needed\n") and "code changed since this review" in r.stdout
    assert "agy-call worker: done" in open(job.path("worker.log")).read()
    r = run_cli("status", "--all", cwd=str(tmp_path), env=env)
    assert "%s (repository %s)\n  %s  done  review  " % (job.store, worktree.repo().top, job.id) in r.stdout


def test_cleanup_removes_the_snapshot_and_keeps_the_record_and_the_saved_review(repo, fake):
    set_turns, launches = fake
    _changed(repo, "a.txt")
    set_turns(reply(_review_json(["a.txt"])))
    job = review.start(model="m")
    review.drive(job)
    rv = _rv(job)
    with store.flock(job.lock_path):  # held as a running review holds it
        assert run_cli("review", "--cleanup", job.id, cwd=str(repo)).returncode == 25
    r = run_cli("review", "--cleanup", job.id, cwd=str(repo))
    assert r.returncode == 0 and "the review record stays" in r.stdout, r.stderr
    assert not os.path.exists(rv["worktree"]) and _refs(repo) == [] and os.path.exists(job.path("answer.txt"))
    assert len(git(repo, "worktree", "list").splitlines()) == 1  # only the main checkout is left
    assert run_cli("review", "--cleanup", job.id, cwd=str(repo)).returncode == 0  # nothing left to remove
    r = run_cli("review", "--model", "m", cwd=str(repo))  # the same review again: the saved one, and no new snapshot
    assert r.returncode == 0 and r.stdout.startswith("verdict: changes-needed\n") and len(launches()) == 1
    assert _refs(repo) == [] and not os.path.exists(rv["worktree"])
    assert run_cli("review", "--cleanup", "nope", cwd=str(repo)).returncode == 2


def test_a_rerun_after_cleanup_of_an_unfinished_review_makes_the_snapshot_again(repo, fake):
    set_turns, launches = fake
    _changed(repo, "a.txt")
    set_turns(quota_error(), reply(_review_json(["a.txt"])))
    job = review.start(model="m")
    with pytest.raises(Quota):
        review.drive(job)
    rv = _rv(job)
    assert review.cleanup(job.id)[1] == 0 and not os.path.exists(rv["worktree"])
    with pytest.raises(RepoState, match="run the same review command again"):
        review.drive(job)
    quota.clear("m", quota.generation("m"))
    assert review.drive(review.start(model="m")).text and len(launches()) == 2
    assert os.path.isdir(rv["worktree"]) and _refs(repo) == ["refs/agy-call/" + job.id]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uvx pytest -q tests/test_review.py`
Expected: 4 failed, 23 passed: the CLI tests exit 2 with `invalid choice: 'review'`, and the last test fails with `AttributeError: module 'agycall.review' has no attribute 'cleanup'`.

- [ ] **Step 3: Add the background queue, the rendering and cleanup to `agycall/review.py`**

In `agycall/review.py`, replace:

```python
def start(*, model, effort=None, scope="auto", base=None, adversarial=False, focus=None, job_id=None, timeout="20m",
          max_turns=3, cwd=None):
```

with:

```python
def _queue(job):
    """Mark a review for the background worker: control.work reads the kind from background.json."""
    st.write_atomic(job.path("background.json"), {"kind": "review"})
    job.set_status(state="queued")


def start(*, model, effort=None, scope="auto", base=None, adversarial=False, focus=None, job_id=None, timeout="20m",
          max_turns=3, cwd=None, background=False):
```

In `agycall/review.py`, replace:

```python
    and request.json are written; then the snapshot ref and worktree are made unless the review is done. Returns the
    job; drive() runs it."""
```

with:

```python
    and request.json are written; then the snapshot ref and worktree are made unless the review is done. Returns the
    job; drive() runs it, or with background the job is queued for control.spawn, after the snapshot exists."""
```

In `agycall/review.py`, replace:

```python
            _snapshot(r.top, req["review"])
    return job
```

with:

```python
            _snapshot(r.top, req["review"])
        if background:
            _queue(job)
    return job
```

Append to `agycall/review.py`, after two blank lines:

```python
def render(job):
    """What `result` prints for a finished review (spec 8.5): the verdict, the count at each severity, one block per
    finding and the path of the full JSON; then a warning when the code of the same scope has changed since."""
    rv = (st.read_json(job.path("request.json"), {}) or {})["review"]
    data = json.loads(runner._read(job.path("answer.txt")))
    counts = collections.Counter(f["severity"] for f in data["findings"])
    lines = ["verdict: %s" % data["verdict"], "findings: " + ", ".join("%d %s" % (counts[s], s) for s in SEVERITIES)]
    if data.get("missing_files"):
        lines.append("not reviewed: " + ", ".join(data["missing_files"]))
    for f in data["findings"]:
        where = f["file"] or "(the change as a whole)"
        if f["file"] and f["line_start"]:
            where += ":%d" % f["line_start"]
            if f["line_end"] and f["line_end"] != f["line_start"]:
                where += "-%d" % f["line_end"]
        lines += ["", "%s: %s" % (f["severity"], f["title"]), "  where: " + where,
                  "  how it is reached: " + f["how_hit"], "  fix: " + f["fix"]]
    lines += ["", "full JSON: " + job.path("answer.txt")]
    try:
        top = rv["checkout"]
        now = tree(top) if rv["scope"] == "working-tree" else worktree.git(top, "rev-parse", "HEAD^{tree}").strip()
    except (RepoState, OSError) as e:
        lines.append("could not compare the code with this review: %s" % e)
    else:
        if now != rv["tree"]:
            lines.append("code changed since this review (snapshot %s, now %s)" % (rv["tree"][:12], now[:12]))
    return "\n".join(lines)


def cleanup(job_id, cwd=None):
    """`agy-call review --cleanup JOB` (spec 8.3): under the job lock and the repository lock, remove the snapshot
    worktree and the snapshot ref, whichever still exist; the job record stays. Returns (text, 0)."""
    r = worktree.repo(cwd)
    job = st.Job(r.store, job_id)
    rv = (st.read_json(job.path("request.json"), {}) or {}).get("review")
    if not rv:
        raise ValueError("no review %s in %s" % (job_id, r.store))
    with contextlib.ExitStack() as stack:
        runner._lock(job, stack)
        stack.enter_context(worktree.lock(r))
        if os.path.isdir(rv["worktree"]):
            worktree.git(r.top, "worktree", "remove", rv["worktree"])
        if worktree._ok(r.top, "show-ref", "--verify", "--quiet", rv["ref"]):
            worktree.git(r.top, "update-ref", "-d", rv["ref"])
    return "removed the snapshot worktree %s and %s; the review record stays in %s" % (rv["worktree"], rv["ref"],
                                                                                        job.dir), 0
```

- [ ] **Step 4: Print reviews and run review workers in `agycall/control.py`**

In `agycall/control.py`, replace:

```python
from . import agy, runner, task, worktree
```

with:

```python
from . import agy, review, runner, task, worktree
```

In `agycall/control.py`, replace:

```python
        top = next(((_request(j).get("task") or {}).get("checkout") for j in found if _request(j).get("task")), None)
```

with:

```python
        top = next(((_request(j).get("task") or _request(j).get("review") or {}).get("checkout") for j in found
                    if _request(j).get("kind") in ("task", "review")), None)
```

In `agycall/control.py`, replace:

```python
    if state == "done" and os.path.exists(job.path("answer.txt")):
        text, code = runner._read(job.path("answer.txt")), 0
```

with:

```python
    if state == "done" and os.path.exists(job.path("answer.txt")):
        text = review.render(job) if _request(job).get("kind") == "review" else runner._read(job.path("answer.txt"))
        code = 0
```

In `agycall/control.py`, replace:

```python
    """Start the detached worker of a queued job, run or task; returns the job id."""
```

with:

```python
    """Start the detached worker of a queued job, run, task or review; returns the job id."""
```

In `agycall/control.py`, replace:

```python
        if args.get("kind") == "task":
            task.drive(job)
```

with:

```python
        if args.get("kind") == "task":
            task.drive(job)
        elif args.get("kind") == "review":
            review.drive(job)
```

`control.py` now imports `review`, which imports `runner`, `task` and `worktree`; none of them imports `control`, so there is no import cycle.

- [ ] **Step 5: Add `agy-call review` to `agycall/cli.py`**

In `agycall/cli.py`, replace:

```python
"""agy-call run [--background] | batch | task | status | result | cancel. Prints the accepted answer or the report on
stdout; errors on stderr; exit codes in SKILL.md."""
```

with:

```python
"""agy-call run [--background] | batch | task | review | status | result | cancel. Prints the accepted answer or the
report on stdout; errors on stderr; exit codes in SKILL.md."""
```

In `agycall/cli.py`, replace:

```python
from . import control, task, worktree
```

with:

```python
from . import control, review, task, worktree
```

In `agycall/cli.py`, replace:

```python
def _store(a):
```

with:

```python
def _review(a):
    """agy-call review: review the repository's change in a snapshot and print the findings (spec 8), or remove a
    review's snapshot (--cleanup)."""
    if a.cleanup:
        return review.cleanup(a.cleanup)
    job = review.start(model=a.model, effort=a.effort, scope=a.scope, base=a.base, adversarial=a.adversarial,
                       focus=a.focus, job_id=a.job, timeout=a.timeout, max_turns=a.max_turns, background=a.background)
    if a.background:
        return control.spawn(job), 0
    review.drive(job)
    return control.result(job.store, job.id)


def _store(a):
```

In `agycall/cli.py`, replace:

```python
    w = sub.add_parser("worker")
```

with:

```python
    v = sub.add_parser("review")
    v.add_argument("--job")
    v.add_argument("--model")
    v.add_argument("--effort")
    v.add_argument("--scope", choices=review.SCOPES, default="auto")
    v.add_argument("--base")
    v.add_argument("--adversarial", action="store_true")
    v.add_argument("--focus")
    v.add_argument("--background", action="store_true")
    v.add_argument("--max-turns", type=int, default=3)
    v.add_argument("--timeout", default="20m")
    v.add_argument("--cleanup", metavar="JOB")
    w = sub.add_parser("worker")
```

In `agycall/cli.py`, replace:

```python
        if a.cmd == "task":
            text, code = _task(a)
```

with:

```python
        if a.cmd == "task":
            text, code = _task(a)
        elif a.cmd == "review":
            text, code = _review(a)
```

- [ ] **Step 6: Run the tests**

Run: `uvx pytest -q tests/test_review.py`
Expected: all pass.

Run: `for i in 1 2 3; do uvx pytest -q tests/ || break; done`
Expected: every run passes (269 tests).

- [ ] **Step 7: Commit**

```bash
git add agycall/review.py agycall/control.py agycall/cli.py tests/test_review.py
printf '%s\n' "Add agy-call review with findings, background runs and cleanup" \
  "" \
  "agy-call review snapshots the change, runs the review in the" \
  "foreground or a detached worker, and result prints the verdict, the" \
  "count per severity and one block per finding, with a warning when the" \
  "code changed since the snapshot. review --cleanup removes the snapshot" \
  "worktree and ref and keeps the record." \
  "" \
  "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" "Claude-Session: https://claude.ai/code/session_013coVpN7if3z6kaMYLTrz3c" > "$TMPDIR/agycall-msg.txt"
git commit -F "$TMPDIR/agycall-msg.txt"
```

---

### Task 5: Docs

**Files:**
- Modify: `skills/agy-call/SKILL.md`, `README.md`

**Interfaces:**
- Consumes: Tasks 1 to 4, and `skills/agy-call/SKILL.md` and `README.md` as step 3's docs task leaves them.
- Produces: user-facing docs that match the code.

This task is mechanical: it has no test of its own and gets no task reviewer; the final review covers it.
Step 3's docs task may have adapted some of its lines to step 2's final wording, so each edit below names the line it changes by how the line starts, and gives the whole new text; every new line is one sentence.

- [ ] **Step 1: Update `skills/agy-call/SKILL.md`**

In the line that starts with `` `agy-call status [JOB] [--all] ``, replace `` the kind (`run` or `task`) `` with `` the kind (`run`, `task` or `review`) ``.

After the line `For a task it then prints the hand-back (see Coding tasks).`, add this line:

```markdown
For a `done` review it prints the findings instead of the JSON (see Reviews).
```

In the Job states table, replace the row that starts with `` | `blocked` | `` and the row that starts with `` | `setup-error` | `` with:

```markdown
| `blocked` | a task's or a review's reply had `NEED:` lines or agy refused an action (exit 28); waiting for a wider grant |
```

```markdown
| `setup-error` | the same as `fatal`, for a task or a review |
```

In the Exit codes table, replace the row that starts with `| 26 |` and the row that starts with `| 28 |` with:

```markdown
| 26 | repository state: not inside a git repository, uncommitted or untracked files, a detached HEAD, a missing worktree, nothing to review, or a merge, cleanup or discard that the repository's state refuses |
```

```markdown
| 28 | a task or a review is blocked on permission: its reply had `NEED:` lines or agy refused an action |
```

After the line that starts with `A task's record is`, add this line:

```markdown
A review's record is in the same repository store; beside the attempt files it holds `schema.json` (the findings schema passed with `--json-schema`), `grant-1.json` (its read-only grant) and, once it is done, `answer.txt` with the accepted findings as JSON.
```

Insert this section immediately before the line `## Multi-turn work` (that line stays):

````markdown
## Reviews

`agy-call review` has Gemini review the current change of the repository and returns a list of findings with a severity each, tied to the exact code it reviewed.
Run it from inside the repository:

```
agy-call review --model M [--effort E] [--scope auto|working-tree|branch] [--base REF] [--adversarial] [--focus TEXT] [--job ID] [--max-turns N] [--timeout T] [--background]
agy-call review --cleanup JOB
```

- `--scope working-tree` reviews the staged, unstaged and untracked files that are not ignored, against `HEAD`.
- `--scope branch` reviews `merge-base(BASE, HEAD)..HEAD`; `--base` names BASE and defaults to `origin/HEAD`, else `main`, else `master`; `--base` applies to the branch scope only.
- `--scope auto` is `working-tree` when `git status --porcelain` shows anything and `branch` otherwise [`auto`].
- When the target has no change, it exits 26 with "nothing to review".
- agy-call keeps a snapshot of the target as a commit under `refs/agy-call/<job>` and checks it out in a detached worktree, `~/.agy-call/worktrees/<repo id>/<job>-snap/`; your index, files and branches are not touched, and edits made while the review runs are not in it.
- Gemini reads the snapshot with a grant of the read-only commands only (`ls`, `cat`, `head`, `tail`, `wc`, `grep`, `rg`, `git status`, `git diff`, `git log`, `git show`), with no file writes and no `--allow` entries, and with the same deny list of shared grants as a task.
- `--adversarial` tells Gemini to assume the change has defects and to challenge the approach and the design choices as well as the code; such a finding may have no file.
- `--focus TEXT` tells Gemini what to give particular attention to.
- Without `--job` the id is `review-standard-<16 hex>` or `review-adversarial-<16 hex>`, taken from the review's identity: the repository, the base commit, the snapshot's tree, the scope, the adversarial flag, the focus text, the model, the effort and the schema.
  The same code under the same settings returns the saved review without a launch, at any later time, and any change to the code or the settings starts a new review.
- `--max-turns` counts every launch [3]; a review may run 2 hours in all across its turns; `--timeout` limits one turn [`20m`].
- `--background` takes the snapshot, prints the job id and runs the review in a detached worker; follow it with `agy-call status` and `agy-call result JOB`.

Gemini replies with one JSON object: a verdict (`approve`, `changes-needed` or `incomplete`), a summary, `files_reviewed`, and findings with a severity (`MUST-FIX`, `SHOULD-FIX` or `NIT`), a title, the file and lines, how the problem is reached and the fix.
agy passes the schema to Gemini with `--json-schema`, and agy-call checks the reply again.
A reply that breaks the schema, names a file outside the change, has `line_start` after `line_end`, or approves with a MUST-FIX or SHOULD-FIX finding gets a turn in the same conversation that asks only for the format to be fixed.
A reply that leaves changed files out of `files_reviewed` is kept with its verdict set to `incomplete` and the missing files listed, and no further turn is sent, because covering them is a new review that you have to ask for.
A reply that came with an agy error is accepted only when it passes these checks, and gets no format-repair turn.
`NEED:` lines or a refused command end the review `blocked` (exit 28), as for a task.
A review that ended `failed` or `blocked` keeps that stop on a rerun; to review again, pass another `--job`.

`agy-call result JOB` for a done review prints the verdict, the number of findings at each severity, one block per finding (severity and title, where, how it is reached, the fix) and the path of the full JSON.
When the code of the same scope has changed since the snapshot, it adds `code changed since this review (snapshot X, now Y)` with the two tree ids.
agy-call never starts a fix or a second review by itself; follow the user's rules for review loops.
`agy-call review --cleanup JOB` removes the snapshot worktree and the ref, and keeps the job record and its findings; it exits 25 while the review runs.

````

- [ ] **Step 2: Update `README.md`**

After the line that starts with `- It can hand a coding task to Gemini`, add this line:

```markdown
- It can have Gemini review the current change in a read-only snapshot and return a checked list of findings, each with a severity, tied to the exact code reviewed.
```

- [ ] **Step 3: Check and commit**

Run: `grep -c "$(printf '\342\200\224')" README.md skills/agy-call/SKILL.md`; expected: `0` for each.
Read the new lines of `skills/agy-call/SKILL.md` once for one sentence per line.
Run: `uvx pytest -q tests/`; expected: all pass (269 tests).

```bash
git add README.md skills/agy-call/SKILL.md
printf '%s\n' "Document reviews, their snapshot, findings and cleanup" \
  "" \
  "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" "Claude-Session: https://claude.ai/code/session_013coVpN7if3z6kaMYLTrz3c" > "$TMPDIR/agycall-msg.txt"
git commit -F "$TMPDIR/agycall-msg.txt"
```

---

## Self-review

- Spec coverage: 8.1 command line, as notes-step4.md gives it (Task 4); 8.2 the three scopes, the base fallbacks and "nothing to review" (Task 2); 8.3 the snapshot through a copy of the index, `HEAD` for the branch scope, `refs/agy-call/<job>`, the detached `<job>-snap` worktree, the prompt with both commits, `git diff`, the changed files and the severity bar, the adversarial prompt with the focus text (Task 2), and `--cleanup` (Task 4); 8.4 the schema passed with `--json-schema` and checked again, the file, line and verdict rules, missing coverage as `incomplete` with no further turn, format-only repair turns and the output-limit override (Tasks 1 and 3); 8.5 the rendering and the stale-code warning (Task 4); 8.6 the default id (Task 2); 7.2 the review identity computed before the snapshot ref, the worktree and the prompt exist (Task 2); 5.2 the review column through the shared task policy: `reply`, agy-error replies, `empty` with one summary-only turn, `denied` and `NEED:` to `blocked`, `quota`, setup errors as `setup-error`, `timeout` with the partial reply kept, `cancelled` (Task 3; cancel itself is step 2's and works on any job); 5.3 every launch counts against `--max-turns`, the 2-hour deadline, `--timeout` 20m (Tasks 3 and 4); 4.2 the review grant without `write_file` or `--allow` entries, with the deny mirror (Task 3); 7.1 `--background` (Task 4); 13 exit 26 for nothing to review (Tasks 2 and 4).
  Spec 10.2 review cases: the snapshot holds staged, unstaged and untracked files and leaves the index alone (Task 2); an empty target (Tasks 2 and 4); schema and local checks, with the format-repair turns tested apart (Task 3); missing coverage returns the findings as `incomplete`, lists the files and launches nothing more (Task 3); the stale-result warning (Task 4); identical content snapshotted at two times gives the same identity and reuses the saved review (Tasks 2 and 3).
  Schema validator cases: an unsupported keyword is refused before a launch (Task 1, as `ValueError` and exit 2 per notes-step4.md), and an object with an empty summary, `line_start: -4` and an extra field is rejected (Tasks 1 and 3).
  Not in this step by design: the slash commands `/agy:review` and `/agy:adversarial-review` with their default model and effort (step 5), and the live review in `tests/live/smoke.sh` (step 6).
- Placeholders: none; every code step is a whole file, an exact old/new pair taken from the file as the earlier steps leave it, or a block to append; the docs task names each line it changes and gives the whole new text.
- Names used across tasks: `review.SCOPES/SEVERITIES/SCHEMA/SCHEMA_TEXT/SCHEMA_SHA/PROMPT/ADVERSARIAL/FOCUS/REF/SNAP_ENV/DEADLINE`, `review.Target`, `review.tree/target/identity/default_id/prompt/start/checker/drive/render/cleanup/_snapshot/_queue`, `runner._prepare(..., json_schema)`, `runner.POLICIES`, `runner.FIX_REVIEW/SUMMARY_REVIEW/INTERRUPTED_REVIEW`, `runner._next(..., fix, summary)`, `c.words`, `c.json_schema`, `task.grant(None, ())`, `checks.KEYWORDS`.
- Review Focus: each of the five lines has its test in the named task.

## After this plan

- Step 5 adds `/agy:review` and `/agy:adversarial-review`, which call `agy-call review` with `--model gemini-4-argon-high --effort high` unless the user names others.
- The live checks of step 6 run one review with real agy in a temporary repository.
- Nothing in this plan pushes; pushing is the user's call.
