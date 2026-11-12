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
