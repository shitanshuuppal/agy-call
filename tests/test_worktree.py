import os
import signal
import stat
import subprocess
import sys
import threading
import time

import pytest

from agycall import control, store, task, worktree
from agycall.errors import Mismatch, RepoState, Running
from agyturns import bounded, git, reply, wait_for

HERE = os.path.dirname(os.path.abspath(__file__))
AGYCALL = os.path.join(os.path.dirname(HERE), "bin", "agy-call")


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
    bounded(lambda: worktree.check_job_id("fix-1.2_x"))
    for bad in ("a..b", "a.lock", "a.", "-x", "a/b", ""):
        with pytest.raises(ValueError):
            bounded(lambda bad=bad: worktree.check_job_id(bad))


def _committed_task(fake, job_id="t1", check="test -f b.txt", files=None):
    """A task that ended done, with its work committed on agy/<job> the way the skill tells Claude to."""
    set_turns, _ = fake
    set_turns(reply("Created b.txt.", write=files or {"b.txt": "b\n"}))
    job = bounded(lambda: task.start(job_id, "Add b.txt.", model="m", check_cmd=check))
    bounded(lambda: task.drive(job))
    wt = worktree.worktree_path(worktree.repo(), job_id)
    git(wt, "add", "-A")
    git(wt, "commit", "-q", "-m", "agy task %s" % job_id)
    return job, wt


def test_merge_makes_the_merge_commit_and_cleanup_removes_the_worktree_and_branch(repo, fake):
    job, wt = _committed_task(fake, check="mkdir -p build && touch build/out.o && test -f b.txt")
    base_tip, task_commit = git(repo, "rev-parse", "main").strip(), git(wt, "rev-parse", "HEAD").strip()
    text, code = bounded(lambda: worktree.merge("t1"))  # the check leaves ignored build output in the worktree: still clean
    head = git(repo, "rev-parse", "main").strip()
    assert code == 0 and head in text and (repo / "b.txt").exists()
    assert git(repo, "rev-list", "--parents", "-n", "1", head).split() == [head, base_tip, task_commit]
    assert git(repo, "log", "-1", "--format=%s").strip() == "Merge agy task t1"
    m = store.read_json(job.path("merge.json"))
    assert (m["taskCommit"], m["mergeCommit"], m["baseTip"], m["checkExit"]) == (task_commit, head, base_tip, 0)
    assert "merged into main as %s" % head in control.result(job.store, "t1")[0]
    assert os.path.exists(os.path.join(wt, "build", "out.o"))
    assert bounded(lambda: worktree.cleanup("t1"))[1] == 0  # ignored build output does not stop the removal
    assert not os.path.exists(wt) and git(repo, "branch", "--list", "agy/t1") == ""
    assert store.read_json(job.path("cleanup.json"))["action"] == "cleanup"
    assert "removed by task --cleanup" in control.result(job.store, "t1")[0]
    assert bounded(lambda: worktree.cleanup("t1")) == ("task t1 was already removed by --cleanup", 0)
    bounded(lambda: task.start("t1", "Add c.txt.", model="m", check_cmd="true", new_version=True))  # allowed once cleaned up
    assert os.path.isdir(wt) and os.path.isdir(os.path.join(job.store, ".versions", "t1", "v1"))


def test_merge_refuses_until_the_task_and_the_main_checkout_are_ready(repo, fake):
    job, wt = _committed_task(fake)
    with store.flock(job.lock_path):
        with pytest.raises(Running):
            bounded(lambda: worktree.merge("t1"))
    open(os.path.join(wt, "x.txt"), "w").close()
    with pytest.raises(RepoState, match="uncommitted changes; commit them on agy/t1"):
        bounded(lambda: worktree.merge("t1"))
    os.remove(os.path.join(wt, "x.txt"))
    git(repo, "checkout", "-q", "-b", "other")
    with pytest.raises(RepoState, match="not on main"):
        bounded(lambda: worktree.merge("t1"))
    git(repo, "checkout", "-q", "main")
    (repo / "y.txt").write_text("y")
    with pytest.raises(RepoState, match="main checkout .* has uncommitted"):
        bounded(lambda: worktree.merge("t1"))
    git(repo, "add", "y.txt")
    git(repo, "commit", "-q", "-m", "the base moves on")
    with pytest.raises(RepoState, match="base moved: merge main into agy/t1, test, commit, and run --merge again"):
        bounded(lambda: worktree.merge("t1"))
    assert not os.path.exists(job.path("merge.json"))
    git(wt, "reset", "-q", "--hard", git(wt, "rev-parse", "HEAD~1").strip())  # the task commit undone
    git(wt, "merge", "-q", "main")
    with pytest.raises(RepoState, match="no commit that main does not have"):
        bounded(lambda: worktree.merge("t1"))


def test_a_failing_check_or_one_that_edits_tracked_files_refuses_the_merge(repo, fake):
    job, wt = _committed_task(fake, check="test -f b.txt && test ! -f stop")
    open(os.path.join(wt, "stop"), "w").close()
    git(wt, "add", "stop")
    git(wt, "commit", "-q", "-m", "stop")
    before = git(repo, "rev-parse", "main").strip()
    text, code = bounded(lambda: worktree.merge("t1"))
    assert code == 20 and "nothing was merged" in text and "exited 1" in text
    job2, wt2 = _committed_task(fake, "t2", check="test -f b.txt && { test ! -f edit || echo more >> b.txt; }")
    open(os.path.join(wt2, "edit"), "w").close()
    git(wt2, "add", "edit")
    git(wt2, "commit", "-q", "-m", "edit")
    with pytest.raises(RepoState, match="the check changed the worktree"):
        bounded(lambda: worktree.merge("t2"))
    assert git(repo, "rev-parse", "main").strip() == before and "more" in open(os.path.join(wt2, "b.txt")).read()


def test_a_failing_merge_hook_is_aborted_and_recorded(repo, fake):
    job, wt = _committed_task(fake)
    hook = repo / ".git" / "hooks" / "pre-merge-commit"
    hook.write_text("#!/bin/sh\necho hook says no >&2\nexit 1\n")
    hook.chmod(0o755)
    before = git(repo, "rev-parse", "main").strip()
    with pytest.raises(RepoState, match="hook says no"):
        bounded(lambda: worktree.merge("t1"))
    assert git(repo, "rev-parse", "main").strip() == before and worktree.changes(str(repo)) == []
    assert subprocess.run(["git", "rev-parse", "-q", "--verify", "MERGE_HEAD"], cwd=str(repo), timeout=30).returncode != 0
    failed = store.read_json(job.path("merge-failed.json"))
    assert failed["aborted"] is True and "hook says no" in failed["output"] and not os.path.exists(job.path("merge.json"))


def test_a_merge_waits_for_the_repository_lock(repo, fake):
    job, wt = _committed_task(fake)
    out = []
    with worktree.lock(worktree.repo()):
        th = threading.Thread(target=lambda: out.append(worktree.merge("t1")), daemon=True)
        th.start()
        time.sleep(1)
        assert th.is_alive() and not os.path.exists(job.path("merge.json"))
    th.join(30)
    assert not th.is_alive() and out[0][1] == 0


def test_discard_drops_an_unmerged_task_only_without_uncommitted_changes(repo, fake):
    set_turns, _ = fake
    set_turns(reply("Created b.txt.", write={"b.txt": "b\n"}))
    job = bounded(lambda: task.start("t1", "Add b.txt.", model="m", check_cmd="true"))
    bounded(lambda: task.drive(job))
    wt = worktree.worktree_path(worktree.repo(), "t1")
    with pytest.raises(RepoState, match="uncommitted changes"):
        bounded(lambda: worktree.discard("t1"))
    git(wt, "add", "-A")
    git(wt, "commit", "-q", "-m", "work")
    with pytest.raises(RepoState, match="not merged into main"):
        bounded(lambda: worktree.cleanup("t1"))
    with store.flock(job.lock_path):
        with pytest.raises(Running):
            bounded(lambda: worktree.discard("t1"))
    assert bounded(lambda: worktree.discard("t1"))[1] == 0
    assert not os.path.exists(wt) and git(repo, "branch", "--list", "agy/t1") == ""
    assert store.read_json(job.path("cleanup.json"))["action"] == "discard"


def test_cli_merge_and_cleanup(repo, fake):
    _committed_task(fake)
    r = subprocess.run([sys.executable, AGYCALL, "task", "--merge", "t1"], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and r.stdout.startswith("merged agy/t1 into main as "), r.stderr
    r = subprocess.run([sys.executable, AGYCALL, "task", "--cleanup", "t1"], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and r.stdout.startswith("removed the worktree ")
    r = subprocess.run([sys.executable, AGYCALL, "task", "--merge", "nope"], capture_output=True, text=True, timeout=30)
    assert r.returncode == 2 and "no task nope" in r.stderr


def test_cleanup_and_discard_refuse_a_worktree_that_moved_off_the_task_branch(repo, fake):
    for name in ("cleanup", "discard"):
        job, wt = _committed_task(fake, "m" + name, check="test -f f%s.txt" % name, files={"f%s.txt" % name: "f\n"})
        assert bounded(lambda: worktree.merge("m" + name))[1] == 0
        git(wt, "checkout", "-q", "--detach")
        (open(os.path.join(wt, "extra.txt"), "w")).write("x")
        git(wt, "add", "-A")
        git(wt, "commit", "-q", "-m", "extra work")
        extra = git(wt, "rev-parse", "HEAD").strip()
        with pytest.raises(RepoState, match="not on the tip of agy/m%s" % name):
            bounded(lambda: getattr(worktree, name)("m" + name))
        assert os.path.isdir(wt) and git(wt, "rev-parse", "HEAD").strip() == extra
        assert git(repo, "branch", "--list", "agy/m" + name).strip() != ""


def test_merge_refuses_when_the_check_switches_the_main_checkout(repo, fake):
    job, wt = _committed_task(fake, check="test -f b.txt && { test ! -f go || git -C %s checkout -q -b elsewhere; }" % repo)
    open(os.path.join(wt, "go"), "w").close()
    git(wt, "add", "go")
    git(wt, "commit", "-q", "-m", "go")
    before = git(repo, "rev-parse", "main").strip()
    with pytest.raises(RepoState, match="main checkout"):
        bounded(lambda: worktree.merge("t1"))
    assert git(repo, "rev-parse", "main").strip() == before and git(repo, "rev-parse", "elsewhere").strip() == before
    assert not os.path.exists(job.path("merge.json"))


def test_merge_reads_the_task_record_after_taking_the_locks(repo, fake, monkeypatch):
    job, wt = _committed_task(fake)
    real = worktree.lock

    def replaced(r):  # a --new-version replaces the record between the first read and the lock
        req = store.read_json(job.path("request.json"))
        req["task"]["check"] = "false"
        store.write_atomic(job.path("request.json"), req)
        return real(r)

    monkeypatch.setattr(worktree, "lock", replaced)
    text, code = bounded(lambda: worktree.merge("t1"))
    assert code == 20 and not os.path.exists(job.path("merge.json"))


def _merge_controller(repo, tmp_path):
    """Start `agy-call task --merge t1` as its own process, and return it."""
    code = "import sys; from agycall import worktree; worktree.merge('t1')"
    env = dict(os.environ, PYTHONPATH=os.path.dirname(HERE))
    return subprocess.Popen([sys.executable, "-c", code], cwd=str(repo), env=env, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)


def _killed_controller_leaves_the_locks_held(repo, tmp_path, controller, child_pid):
    os.kill(controller.pid, signal.SIGKILL)
    bounded(lambda: controller.wait(timeout=30), [controller.pid])
    os.kill(child_pid, 0)  # the child outlived its controller
    for call in (lambda: worktree.cleanup("t1"), lambda: worktree.merge("t1"), lambda: worktree.discard("t1")):
        with pytest.raises(Running):
            bounded(call)


def test_a_killed_merge_controller_leaves_the_locks_held_while_its_check_runs(repo, fake, tmp_path):
    flag, pidfile = tmp_path / "flag", tmp_path / "pid"
    check = "test -f b.txt; if [ -f %s ]; then echo $$ > %s; sleep 60; fi" % (flag, pidfile)
    _committed_task(fake, check=check)
    flag.write_text("x")
    controller, child = _merge_controller(repo, tmp_path), []
    try:
        child.append(int(wait_for(lambda: pidfile.exists() and pidfile.read_text().strip())))
        _killed_controller_leaves_the_locks_held(repo, tmp_path, controller, child[0])
    finally:
        controller.kill()
        for pid in child:
            try:
                os.killpg(pid, signal.SIGKILL)
            except OSError:
                pass


def test_a_killed_merge_controller_leaves_the_locks_held_while_git_merge_runs(repo, fake, tmp_path):
    pidfile = tmp_path / "pid"
    _committed_task(fake)
    hook = repo / ".git" / "hooks" / "pre-merge-commit"
    hook.write_text("#!/bin/sh\necho $$ > %s\nexec sleep 60\n" % pidfile)
    hook.chmod(0o755)
    controller, child = _merge_controller(repo, tmp_path), []
    try:
        child.append(int(wait_for(lambda: pidfile.exists() and pidfile.read_text().strip())))
        _killed_controller_leaves_the_locks_held(repo, tmp_path, controller, child[0])
    finally:
        controller.kill()
        for pid in child:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
