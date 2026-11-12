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
