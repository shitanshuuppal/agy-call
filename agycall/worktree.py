"""Task worktrees (spec 3.3, 4.3, 4.8, 4.9): the repository store, a task's worktree and branch, the hand-back, and
merge, cleanup and discard."""
import collections
import contextlib
import hashlib
import os
import subprocess

from . import checks, runner
from . import store as st
from .errors import Mismatch, RepoState, Running

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
