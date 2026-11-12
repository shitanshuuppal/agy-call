"""Coding tasks (spec 4): the grant file with its deny mirror, the wrapped prompt, and starting, continuing and driving a
task through the runner's task policy."""
import contextlib
import datetime as dt
import hashlib
import json
import os
import re

from . import agy, checks, runner, worktree
from . import store as st
from .errors import CheckError, Mismatch, NoConversation, RepoState, Running

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
NO_CHANGE = "no file in the worktree changed"


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
        if key not in node:
            return []
        node = node[key]
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
    grant that is not exactly one of these goes in deny, which agy checks before allow. A review passes no worktree and
    no --allow entries, so its grant is the read-only commands alone."""
    own = (["write_file(%s)" % worktree] if worktree else []) + ["command(%s)" % c for c in commands(allow)]
    return {"permissions": {"allow": own, "deny": list(dict.fromkeys(g for g in shared_grants() if g not in own))}}


def write_grant(path, worktree, allow):
    """Write the grant file with the deny mirror as the shared config is now; returns the file's SHA-256."""
    data = json.dumps(grant(worktree, allow), indent=1)
    st.write_atomic(path, data)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def prompt(worktree, allow, text):
    """The task text inside the fixed instructions of spec 4.4."""
    return PROMPT.format(worktree=worktree, commands="\n".join("- " + c for c in commands(allow)), text=text)


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


def _checker(check_cmd, check_timeout, wt, end):
    """A task's check (spec 4.5): a worktree with no change fails with NO_CHANGE; otherwise the check command runs in
    the worktree with the reply on stdin, and any exit but 0 is a finding made of the end of its output. The command
    runs for at most the time left in the segment; a segment whose deadline has passed before or by the check is
    rejected, and the job loop then ends it as failed."""
    def left():
        return (end - dt.datetime.now(dt.timezone.utc)).total_seconds()

    def late():
        return checks.Verdict(False, None, ["the segment's deadline passed at %s while the check ran" % end.isoformat()], False)

    def check(text, **run):
        if left() <= 0:
            return late()
        try:
            changed = worktree.changes(wt)
        except RepoState as e:
            raise CheckError(str(e))
        if not changed:
            return checks.Verdict(False, None, [NO_CHANGE], False)
        try:
            findings = checks.run_cmd(check_cmd, text, min(check_timeout, left()), combined=True, **run)
        except CheckError:
            if left() <= 0:
                return late()
            raise
        if left() <= 0:
            return late()
        return checks.Verdict(not findings, None if findings else text, findings, False)
    return check


def _queue(job):
    """Mark a task for the background worker: control.work reads the kind from background.json."""
    st.write_atomic(job.path("background.json"), {"kind": "task"})
    job.set_status(state="queued")


def start(job_id, text, *, model, check_cmd, allow=(), effort=None, check_timeout=600, timeout="60m", max_turns=3,
          deadline="2h", cwd=None, new_version=False, background=False):
    """`agy-call task` (spec 4.3): check the request; then, under the job lock and the repository lock, find the same
    request already recorded, or create the worktree, segment 1 with its grant, and request.json. Returns the job;
    drive() runs it, or with background the job is queued for control.spawn."""
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
        if job.status().get("state") == "queued":  # reserved, and drive has not taken it: it would run only the newest
            raise Running("a continuation is already queued for task %s; wait for it to start (or run the task "
                          "again to recover a controller that never did)" % job_id)
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
        grant(t["worktree"], prev["allow"] + added)  # fails on an unreadable shared config before anything moves
        moved = []
        for name, dest in (("answer.txt", job.path("segments", "%03d-answer.txt" % n)),
                           ("cancel.request", job.path("cancel.request.%03d.done" % (n + 1)))):
            if os.path.exists(job.path(name)):
                os.replace(job.path(name), dest)
                moved.append((dest, job.path(name)))
        try:
            _write_segment(job, n + 1, text, t["worktree"], prev["allow"] + added, grant_n, max_turns, deadline)
        except BaseException:
            for dest, src in moved:
                os.replace(dest, src)
            raise
        job.set_status(findings=[], need=[], denied=[], state="queued")  # reserved until drive takes the segment
        if background:
            st.write_atomic(job.path("background.json"), {"kind": "task"})
    return job


def drive(job):
    """Run the latest segment of a task: in the foreground, on a rerun or in the background worker. A saved answer is
    returned without a launch; otherwise the worktree must still exist, and the runner writes the grant file again before
    every launch, so its deny mirror follows the shared config as it is then."""
    t = (st.read_json(job.path("request.json"), {}) or {}).get("task")
    if not t:
        raise ValueError("no task %s in %s" % (job.id, job.store))
    with contextlib.ExitStack() as stack:
        fd = runner._lock(job, stack)
        n = segments(job)[-1]  # chosen under the lock, so a segment added meanwhile is the one that runs
        seg = st.read_json(_segment_path(job, n))
        if job.status().get("state") == "queued":  # the reserved segment is taken: another --continue may follow
            job.set_status(state="running")
        c = runner._prepare(job.id, seg["prompt"], store=job.store, model=t["model"], timeout=t["timeout"],
                            cwd=t["worktree"], check_cmd=t["check"], check_timeout=t["checkTimeout"],
                            settings=seg["grant"], effort=t["effort"], policy="task", segment=n,
                            end=dt.datetime.fromisoformat(seg["end"]))
        c.checker = _checker(t["check"], t["checkTimeout"], t["worktree"], c.end)
        c.stack = stack
        c.fds.append(fd)
        if not os.path.exists(job.path("answer.txt")) and not os.path.isdir(t["worktree"]):
            raise RepoState("the worktree %s of task %s is missing; start the task again with --new-version"
                            % (t["worktree"], job.id))
        c.refresh = lambda: write_grant(seg["grant"], t["worktree"], seg["allow"])
        runner._own(c, stack)
        return runner._drive(c, seg["maxTurns"], False, False)
