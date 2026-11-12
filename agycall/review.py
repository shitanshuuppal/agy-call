"""Reviews (spec 8): the target and its snapshot, the review's identity and default job id, the read-only grant, the
prompt, the findings check, what `result` prints, and cleanup."""
import collections
import contextlib
import datetime as dt
import hashlib
import json
import os
import shutil
import tempfile

from . import agy, checks, runner, task, worktree
from . import store as st
from .errors import Mismatch, RepoState

SCOPES = ("auto", "working-tree", "branch")
SEVERITIES = ("MUST-FIX", "SHOULD-FIX", "NIT")
DEADLINE = "2h"  # spec 5.3: bounds one drive of a review across its turns
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
        # GIT_OPTIONAL_LOCKS=0: status must not refresh the stat cache in the user's own index
        scope = "working-tree" if worktree.git(top, "status", "--porcelain",
                                               env=dict(os.environ, GIT_OPTIONAL_LOCKS="0")).strip() else "branch"
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


def snapshot_path(r, job_id):
    """The snapshot worktree of a review: in .snap, which no job id can equal, so no task worktree shares it."""
    return os.path.join(os.path.dirname(worktree.worktree_path(r, job_id)), ".snap", job_id)


def _check_existing(top, rv):
    """An existing snapshot folder is reused only when it is a clean, detached worktree of this repository at the
    recorded snapshot commit; anything else is refused (RepoState, exit 26) and left as it is."""
    wt = rv["worktree"]
    if not os.path.lexists(wt):
        return False
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
    try:
        mine = os.path.realpath(os.path.join(top, worktree.git(top, "rev-parse", "--git-common-dir", env=env).strip()))
        theirs = os.path.realpath(os.path.join(wt, worktree.git(wt, "rev-parse", "--git-common-dir", env=env).strip()))
        ok = (os.path.realpath(worktree.git(wt, "rev-parse", "--show-toplevel", env=env).strip()) == os.path.realpath(wt)
              and mine == theirs and not worktree.current_branch(wt)
              and worktree.git(wt, "rev-parse", "HEAD", env=env).strip() == rv["snapshot"]
              and not worktree.git(wt, "status", "--porcelain", env=env).strip())
    except RepoState:
        ok = False
    if not ok:
        raise RepoState("%s exists but is not the clean snapshot worktree of review at %s; move it away or pass "
                        "another --job" % (wt, rv["snapshot"]))
    return True


def _snapshot(top, rv):
    """Make sure the snapshot ref and the detached snapshot worktree exist (spec 8.3), on the first run and on a rerun
    after --cleanup removed them."""
    present = _check_existing(top, rv)
    if not worktree._ok(top, "show-ref", "--verify", "--quiet", rv["ref"]):
        worktree.git(top, "update-ref", rv["ref"], rv["snapshot"])
    if not present:
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
                  "ref": REF + job.id, "worktree": snapshot_path(r, job.id), "files": t.files,
                  "adversarial": bool(adversarial), "focus": focus or "", "model": model, "effort": effort,
                  "timeout": timeout, "maxTurns": max_turns, "grant": job.path("grant-1.json"),
                  "schema": job.path("schema.json")}
            if not os.path.exists(job.path("answer.txt")):
                _check_existing(r.top, rv)  # refuse a conflicting folder before the first write
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
    launch; otherwise the snapshot worktree must exist, and the runner writes the grant file again before every launch,
    so its deny mirror follows the shared config as it is then. The deadline of spec 5.3 runs from the start of each
    drive."""
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
        if not os.path.exists(job.path("answer.txt")) and not os.path.isdir(rv["worktree"]):
            raise RepoState("the snapshot worktree %s of review %s is missing; run the same review command again "
                            "to make it" % (rv["worktree"], job.id))
        c.refresh = lambda: task.write_grant(rv["grant"], None, ())
        runner._own(c, stack)
        return runner._drive(c, rv["maxTurns"], False, False)
