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
    json.dumps({"userSettings": ["x"]}),
    json.dumps({"userSettings": None}),
    json.dumps({"userSettings": {"globalPermissionGrants": None}}),
    json.dumps({"userSettings": {"globalPermissionGrants": {"allow": None}}})])
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
