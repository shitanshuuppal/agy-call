import os
import stat

import pytest

from agycall import store, worktree
from agycall.errors import Mismatch, RepoState
from agyturns import bounded, git


def _repo(*a):
    return bounded(lambda: worktree.repo(*a))


def _create(r, job):
    return bounded(lambda: worktree.create(r, job))


def test_repo_id_is_shared_by_a_subfolder_and_a_linked_worktree(repo, tmp_path):
    r = _repo()
    (repo / "sub").mkdir()
    assert _repo(str(repo / "sub")) == r
    git(repo, "worktree", "add", "-q", "-b", "other", str(tmp_path / "linked"))
    linked = _repo(str(tmp_path / "linked"))
    assert linked.id == r.id and linked.store == r.store and len(r.id) == 12
    assert r.store == os.path.join(store.home(), "jobs", r.id)
    with pytest.raises(RepoState, match="not inside a git repository"):
        _repo(str(tmp_path))


def test_create_adds_a_private_worktree_on_a_new_branch(repo):
    r = _repo()
    assert _create(r, "j1") == ("main", git(repo, "rev-parse", "HEAD").strip())
    wt = worktree.worktree_path(r, "j1")
    assert git(wt, "rev-parse", "--abbrev-ref", "HEAD").strip() == "agy/j1" and os.path.exists(os.path.join(wt, "a.txt"))
    assert stat.S_IMODE(os.stat(os.path.dirname(wt)).st_mode) == 0o700
    with worktree.lock(r):
        assert stat.S_IMODE(os.stat(r.store).st_mode) == 0o700
    with pytest.raises(Mismatch, match="agy/j1"):  # the branch and the folder belong to a job already
        _create(r, "j1")


def test_create_refuses_a_checkout_with_changes(repo):
    r = _repo()
    (repo / "new.txt").write_text("x")
    with pytest.raises(RepoState, match="new.txt"):  # untracked
        _create(r, "j1")
    (repo / "new.txt").unlink()
    (repo / "a.txt").write_text("changed\n")
    with pytest.raises(RepoState, match="a.txt"):  # unstaged
        _create(r, "j1")
    git(repo, "add", "a.txt")
    with pytest.raises(RepoState, match="a.txt"):  # staged
        _create(r, "j1")
    assert not os.path.exists(worktree.worktree_path(r, "j1"))
    git(repo, "checkout", "-q", "HEAD", "--", "a.txt")
    (repo / "build").mkdir()
    (repo / "build" / "out.o").write_text("ignored")  # ignored files do not count
    _create(r, "j1")


def test_create_refuses_a_detached_head_and_a_branch_or_folder_that_exists(repo):
    r = _repo()
    git(repo, "checkout", "-q", "--detach")
    with pytest.raises(RepoState, match="detached"):
        _create(r, "j1")
    git(repo, "checkout", "-q", "main")
    os.makedirs(worktree.worktree_path(r, "j2"))
    with pytest.raises(Mismatch):
        _create(r, "j2")
    git(repo, "branch", "agy/j3")
    with pytest.raises(Mismatch):
        _create(r, "j3")


def test_task_ids_must_make_a_branch_name_git_accepts():
    worktree.check_job_id("fix-1.2_x")
    for bad in ("a..b", "a.lock", "a.", "-x", "a/b", ""):
        with pytest.raises(ValueError):
            worktree.check_job_id(bad)
