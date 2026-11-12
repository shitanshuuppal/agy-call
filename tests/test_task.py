import hashlib
import json
import os
import stat
import subprocess
import sys
import time

import pytest

from agycall import agy, control, quota, runner, store, task, worktree
from agycall.errors import Blocked, Cancelled, Fatal, GaveUp, Mismatch, NoConversation, Quota, RepoState, Running
from agyturns import bounded, denied, empty, error_reply, git, model_error, partial, quota_error, reply, wait_for

HERE = os.path.dirname(os.path.abspath(__file__))
AGYCALL = os.path.join(os.path.dirname(HERE), "bin", "agy-call")


def run_cli(*args, **kw):
    return subprocess.run([sys.executable, AGYCALL, *args], capture_output=True, text=True, timeout=30, **kw)


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
    return bounded(lambda: task.start(job_id, text, **args))


def _wt(job_id="t1"):
    return worktree.worktree_path(worktree.repo(), job_id)


def test_a_task_runs_in_its_worktree_with_its_grant_and_ends_done(repo, fake, tmp_path):
    set_turns, launches = fake
    _config(tmp_path, ["command(gcloud run)", "command(npm test)"])
    set_turns(reply("Created b.txt.", write={"b.txt": "b\n"}))
    job = _start(allow=["npm test"], effort="high", check_cmd="grep -q Created && test -f b.txt")
    r = bounded(lambda: task.drive(job))
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
    r = bounded(lambda: task.drive(_start(check_cmd=check)))
    assert r.answer == "Fixed b." and len(launches()) == 2
    second = launches()[1]
    assert second["argv"][second["argv"].index("--conversation") + 1] == "c1"
    assert "exited 3" in second["prompt"] and second["prompt"].endswith("END\nb.txt is wrong\n")
    assert "x" * 3900 in second["prompt"] and "x" * 4001 not in second["prompt"]  # only the last 4000 characters


def test_a_reply_that_changed_nothing_fails_the_check(repo, fake):
    set_turns, launches = fake
    set_turns(reply("Nothing to do."), reply("Done now.", write={"b.txt": "b\n"}))
    r = bounded(lambda: task.drive(_start()))
    assert r.answer == "Done now." and task.NO_CHANGE in launches()[1]["prompt"]


def test_every_launch_of_a_segment_counts_against_max_turns(repo, fake):
    set_turns, launches = fake
    set_turns(reply("Wrote b.", write={"b.txt": "wrong\n"}))
    job = _start(check_cmd="grep -q '^b$' b.txt", max_turns=2)
    with pytest.raises(GaveUp):
        bounded(lambda: task.drive(job))
    assert len(launches()) == 2 and job.status()["state"] == "failed"
    set_turns(quota_error())
    job2 = _start("t2", model="m2", max_turns=1)
    with pytest.raises(Quota):
        bounded(lambda: task.drive(job2))
    with pytest.raises(GaveUp):  # the launch that met the quota used the segment's only turn
        bounded(lambda: task.drive(job2))
    assert len(launches()) == 3


def test_no_turn_starts_after_the_segment_deadline(repo, fake):
    set_turns, launches = fake
    set_turns(reply("Wrote b.", write={"b.txt": "wrong\n"}))
    job = _start(check_cmd="sleep 3; exit 1", deadline="2s")
    with pytest.raises(GaveUp, match="deadline passed"):
        bounded(lambda: task.drive(job))
    assert len(launches()) == 1 and job.status()["state"] == "failed"


def test_setup_errors_end_a_task_as_setup_error(repo, fake):
    set_turns, _ = fake
    set_turns(model_error())
    job = _start()
    with pytest.raises(Fatal) as e:
        bounded(lambda: task.drive(job))
    assert e.value.code == 14 and job.status()["state"] == "setup-error"
    assert control.result(job.store, "t1")[1] == 14


def test_a_rerun_returns_the_saved_task_and_a_changed_request_is_refused(repo, fake):
    set_turns, launches = fake
    set_turns(reply("Created b.txt.", write={"b.txt": "b\n"}))
    bounded(lambda: task.drive(_start()))
    (repo / "c.txt").write_text("c\n")
    git(repo, "add", "c.txt")
    git(repo, "commit", "-q", "-m", "more")  # the base branch moved on; the rerun keeps the recorded base commit
    (repo / "d.txt").write_text("untracked")  # and the checkout does not need to be clean for a rerun
    assert bounded(lambda: task.drive(_start())).answer == "Created b.txt." and len(launches()) == 1
    with pytest.raises(Mismatch):
        _start(text="Add c.txt.")
    with pytest.raises(RepoState, match="--cleanup or --discard"):
        _start(new_version=True)


def test_a_grant_added_to_the_shared_config_is_denied_on_the_rerun(repo, fake, tmp_path):
    set_turns, launches = fake
    set_turns(quota_error(), reply("Created b.txt.", write={"b.txt": "b\n"}))
    job = _start()
    with pytest.raises(Quota):
        bounded(lambda: task.drive(job))
    assert launches()[0]["settings"]["permissions"]["deny"] == []
    _config(tmp_path, ["command(curl)"])  # the user approves something in the Antigravity app meanwhile
    quota.clear("m", quota.generation("m"))
    assert bounded(lambda: task.drive(_start())).answer == "Created b.txt."  # the same request: no exit 24
    assert launches()[1]["settings"]["permissions"]["deny"] == ["command(curl)"]


def test_a_reply_that_came_with_an_agy_error_is_judged_by_the_check_alone(repo, fake):
    set_turns, launches = fake
    set_turns(error_reply("Created b.txt."))  # nothing was written, so the check fails
    with pytest.raises(GaveUp, match="no follow-up was sent"):
        bounded(lambda: task.drive(_start()))
    set_turns(dict(error_reply("Created b.txt."), write={"b.txt": "b\n"}))
    job = _start("t2")
    assert bounded(lambda: task.drive(job)).answer == "Created b.txt." and len(launches()) == 2
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


def test_the_attempt_counter_equals_the_attempt_folders(repo, fake):
    set_turns, _ = fake
    set_turns(reply("Wrote b.", write={"b.txt": "wrong\n"}), reply("Fixed b.", write={"b.txt": "b\n"}))
    job = _start(check_cmd="grep -q '^b$' b.txt")
    bounded(lambda: task.drive(job))
    assert job.status()["attempts"] == len(job.launched()) == 2
    job2 = _start("t2")  # the last turn repeats, and it writes b.txt
    bounded(lambda: task.drive(job2))
    assert job2.status()["attempts"] == len(job2.launched()) == 1


def test_cli_task_hands_back_a_failed_task(repo, fake, tmp_path):
    set_turns, _ = fake
    set_turns(reply("Wrote b.", write={"b.txt": "wrong\n"}))
    (tmp_path / "task.md").write_text("Add b.txt.")
    r = run_cli("task", "--model", "m", "--prompt-file", str(tmp_path / "task.md"), "--job", "t1", "--max-turns", "2",
                "--check-cmd", "grep -q '^b$' b.txt", cwd=str(repo))
    assert r.returncode == 20, r.stderr
    assert "worktree: %s\nbranch: agy/t1\n" % _wt() in r.stdout and "?? b.txt" in r.stdout
    assert "last reply" in r.stdout and "Wrote b." in r.stdout


def test_need_lines_block_the_task_even_when_the_check_passes(repo, fake):
    set_turns, launches = fake
    set_turns(reply("Created b.txt.\nNEED: npm install left-pad\n  NEED: curl example.com", write={"b.txt": "b\n"}))
    job = _start()
    with pytest.raises(Blocked) as e:
        bounded(lambda: task.drive(job))
    assert e.value.code == 28 and "NEED: npm install left-pad" in str(e.value) and len(launches()) == 1
    s = job.status()
    assert s["state"] == "blocked" and s["need"] == ["NEED: npm install left-pad", "NEED: curl example.com"]
    assert s["denied"] == [] and not os.path.exists(job.path("answer.txt")) and os.path.exists(os.path.join(_wt(), "b.txt"))
    with pytest.raises(Blocked):  # a rerun stays blocked and launches nothing
        bounded(lambda: task.drive(_start()))
    assert len(launches()) == 1 and control.result(job.store, "t1")[1] == 28


def test_a_refused_command_gets_one_turn_that_asks_for_the_need_lines(repo, fake):
    set_turns, launches = fake
    set_turns(denied(), reply("NEED: npm test"))
    job = _start()
    with pytest.raises(Blocked):
        bounded(lambda: task.drive(job))
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
        bounded(lambda: task.drive(job))
    assert len(launches()) == 1 and job.status()["denied"] == ["write_file"]
    set_turns(denied())
    with pytest.raises(Blocked, match="named no command"):
        bounded(lambda: task.drive(_start("t2")))
    assert len(launches()) == 3  # the refused launch and one NEED: follow-up


def test_an_empty_reply_gets_one_summary_turn_and_is_never_resent_blind(repo, fake):
    set_turns, launches = fake
    set_turns(empty(conv="c1"), reply("Created b.txt.", write={"b.txt": "b\n"}))
    assert bounded(lambda: task.drive(_start())).answer == "Created b.txt."
    assert launches()[1]["prompt"] == runner.SUMMARY_ONLY and "--conversation" in launches()[1]["argv"]
    set_turns(empty(conv="c2"))
    with pytest.raises(GaveUp, match="never resent blind"):  # a second empty reply after the summary turn
        bounded(lambda: task.drive(_start("t2")))
    set_turns(empty())
    with pytest.raises(GaveUp, match="never resent blind"):  # no conversation to ask in
        bounded(lambda: task.drive(_start("t3")))
    assert len(launches()) == 5


def test_a_timeout_fails_the_task_and_keeps_the_partial_reply(repo, fake, monkeypatch):
    set_turns, launches = fake
    set_turns(partial("half done"))
    job = _start()
    for _ in range(2):  # a rerun keeps the stop
        with pytest.raises(GaveUp, match="partial reply"):
            bounded(lambda: task.drive(job))
    assert len(launches()) == 1 and open(os.path.join(job.launched()[0], "reply.txt")).read() == "half done"
    monkeypatch.setattr(agy, "POLL", 0.1)
    set_turns(reply("late", write={"b.txt": "b\n"}, sleep=30))
    t0 = time.monotonic()
    with pytest.raises(GaveUp, match="deadline"):  # the segment's deadline stops a running turn
        bounded(lambda: task.drive(_start("t2", deadline="2s")))
    assert time.monotonic() - t0 < 15 and len(launches()) == 2


def test_a_task_whose_controller_died_sends_no_automatic_turn(repo, fake):
    set_turns, launches = fake
    set_turns(reply("x"))
    job = _start()
    att = job.new_attempt()
    dead = subprocess.Popen(["true"])
    bounded(lambda: dead.wait(timeout=30), [dead.pid])
    store.write_atomic(os.path.join(att, "launch.json"), {"pid": dead.pid, "pgid": dead.pid, "kind": "first", "message": "q",
                                                         "conversation": None, "generation": 0, "segment": 1,
                                                         "started": runner._now()})
    with open(os.path.join(att, "agy.out"), "wb") as fh:
        fh.write(b'{"status": "SUCC')
    for _ in range(2):
        with pytest.raises(GaveUp, match="nothing was resent"):
            bounded(lambda: task.drive(job))
    assert launches() == [] and store.read_json(os.path.join(att, "result.json"))["outcome"] == "interrupted"


def test_continue_appends_a_segment_in_the_same_worktree_and_conversation(repo, fake):
    set_turns, launches = fake
    set_turns(reply("Created b.txt.", write={"b.txt": "b\n"}), reply("Created c.txt.", write={"c.txt": "c\n"}))
    job = _start()
    bounded(lambda: task.drive(job))
    store.write_atomic(job.path("cancel.request"), "{}")  # an old request must not stop the new segment
    job = bounded(lambda: task.continue_("t1", "Add c.txt too.", allow=["make"], max_turns=2, deadline="1h"))
    assert bounded(lambda: task.drive(job)).answer == "Created c.txt."
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
        bounded(lambda: task.drive(job))
    assert bounded(lambda: task.drive(task.continue_("t1", "make is allowed now.", allow=["make"]))).answer == "Done."
    set_turns(reply("More.", write={"c.txt": "c\n"}))
    job = bounded(lambda: task.continue_("t1", "One more thing."))
    store.write_atomic(job.path("cancel.request"), "{}")
    with pytest.raises(Cancelled):
        bounded(lambda: task.drive(job))
    assert job.status()["state"] == "cancelled" and len(launches()) == 2
    assert bounded(lambda: task.drive(task.continue_("t1", "One more thing."))).answer == "More."
    assert [l["argv"][l["argv"].index("--conversation") + 1] for l in launches()[1:]] == ["c1", "c1"]
    assert store.read_json(job.path("segments", "004.json"))["grant"] == job.path("grant-2.json")  # no new --allow


def test_continue_refuses_an_unknown_task_a_running_one_one_without_a_conversation_and_a_missing_worktree(repo, fake):
    set_turns, _ = fake
    with pytest.raises(ValueError, match="no task"):
        bounded(lambda: task.continue_("nope", "x"))
    set_turns(empty())
    job = _start()
    with pytest.raises(GaveUp):
        bounded(lambda: task.drive(job))
    with store.flock(job.lock_path):
        with pytest.raises(Running):
            bounded(lambda: task.continue_("t1", "x"))
    with pytest.raises(NoConversation):
        bounded(lambda: task.continue_("t1", "x"))
    git(repo, "worktree", "remove", "--force", _wt())
    with pytest.raises(RepoState, match="missing"):
        bounded(lambda: task.continue_("t1", "x"))


def test_continue_after_the_controller_died_settles_the_old_turn_first(repo, fake):
    set_turns, launches = fake
    set_turns(reply("Created b.txt.", write={"b.txt": "b\n"}), reply("Added d.", write={"d.txt": "d\n"}))
    job = _start()
    bounded(lambda: task.drive(job))
    bounded(lambda: task.continue_("t1", "Add c.txt."))
    job.set_status(state="running")  # its drive took the segment, then the controller died
    att = job.new_attempt()  # segment 2's first turn: its controller died after agy wrote a complete reply
    dead = subprocess.Popen(["true"])
    bounded(lambda: dead.wait(timeout=30), [dead.pid])
    store.write_atomic(os.path.join(att, "launch.json"), {"pid": dead.pid, "pgid": dead.pid, "kind": "first", "message": "m",
                                                         "conversation": "c1", "generation": 0, "segment": 2,
                                                         "started": runner._now()})
    with open(os.path.join(att, "agy.out"), "w") as fh:
        fh.write(json.dumps({"status": "SUCCESS", "response": "the old reply", "conversation_id": "c1"}))
    job = bounded(lambda: task.continue_("t1", "Add d.txt."))
    assert store.read_json(os.path.join(att, "result.json"))["outcome"] == "interrupted"
    assert bounded(lambda: task.drive(job)).answer == "Added d."  # not the old reply
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


def test_a_need_follow_up_that_hit_quota_still_blocks_after_the_retry(repo, fake):
    set_turns, launches = fake
    set_turns(denied(), quota_error(), reply("I need nothing more.", write={"b.txt": "b\n"}))
    job = _start()
    with pytest.raises(Quota):
        bounded(lambda: task.drive(job))
    quota.clear("m", quota.generation("m"))
    with pytest.raises(Blocked):
        bounded(lambda: task.drive(_start()))
    assert launches()[2]["prompt"] == runner.NEED_ONLY and job.status()["state"] == "blocked"
    assert not os.path.exists(job.path("answer.txt"))


def test_a_refusal_that_includes_a_write_blocks_without_a_follow_up(repo, fake):
    set_turns, launches = fake
    resp = denied()
    resp["denied_actions"].append({"action": "write_file", "display_name": "WriteFile"})
    set_turns(resp)
    with pytest.raises(Blocked, match="write_file"):
        bounded(lambda: task.drive(_start()))
    assert len(launches()) == 1


def test_a_second_continuation_is_refused_while_one_is_queued(repo, fake):
    set_turns, launches = fake
    set_turns(reply("Created b.txt.", write={"b.txt": "b\n"}))
    job = _start(background=True)  # queued; no worker is started, so it has not taken the lock
    with pytest.raises(Running, match="already queued"):
        bounded(lambda: task.continue_("t1", "Something else."))
    assert task.segments(job) == [1]
    assert bounded(lambda: task.drive(job)).answer == "Created b.txt."  # the first segment's prompt is the one that runs
    assert launches()[0]["prompt"] == task.prompt(_wt(), [], "Add b.txt.")
    set_turns(reply("Created c.txt.", write={"c.txt": "c\n"}))
    queued = bounded(lambda: task.continue_("t1", "Add c.txt.", background=True))
    with pytest.raises(Running, match="already queued"):
        bounded(lambda: task.continue_("t1", "Another."))
    assert task.segments(queued) == [1, 2]
    assert bounded(lambda: task.drive(queued)).answer == "Created c.txt."
    assert launches()[1]["prompt"] == task.prompt(_wt(), [], "Add c.txt.")


def test_a_failed_continuation_keeps_the_answer_the_cancel_request_and_the_segments(repo, fake, tmp_path):
    set_turns, _ = fake
    set_turns(reply("Created b.txt.", write={"b.txt": "b\n"}))
    job = _start()
    bounded(lambda: task.drive(job))
    store.write_atomic(job.path("cancel.request"), "{}")
    (tmp_path / "agy-config.json").write_text("{not json")
    with pytest.raises(ValueError, match="shared grants"):
        bounded(lambda: task.continue_("t1", "Add c.txt.", allow=["make"]))
    assert open(job.path("answer.txt")).read() == "Created b.txt."
    assert os.path.exists(job.path("cancel.request")) and not os.path.exists(job.path("cancel.request.002.done"))
    assert task.segments(job) == [1] and not os.path.exists(job.path("segments", "001-answer.txt"))
    assert job.status()["state"] == "done"


def _config_writer(content):
    """A check command that fails the first time and then rewrites agy's shared config, as the Antigravity app does."""
    return ("grep -q '^b$' b.txt || { printf '%%s' '%s' > \"$AGYCALL_AGY_CONFIG\"; exit 1; }" % content)


def test_the_deny_mirror_is_rebuilt_before_every_launch_of_a_drive(repo, fake):
    set_turns, launches = fake
    set_turns(reply("Wrote b.", write={"b.txt": "wrong\n"}), reply("Fixed b.", write={"b.txt": "b\n"}))
    shared = json.dumps({"userSettings": {"globalPermissionGrants": {"allow": ["command(curl)"]}}})
    job = _start(check_cmd=_config_writer(shared))
    assert bounded(lambda: task.drive(job)).answer == "Fixed b."
    assert launches()[0]["settings"]["permissions"]["deny"] == []
    second = launches()[1]["settings"]
    assert second["permissions"]["deny"] == ["command(curl)"]
    rec = store.read_json(os.path.join(job.launched()[1], "launch.json"))
    assert rec["grantSha"] == hashlib.sha256(json.dumps(second, indent=1).encode("utf-8")).hexdigest()


def test_a_shared_config_that_became_malformed_stops_the_next_launch(repo, fake):
    set_turns, launches = fake
    set_turns(reply("Wrote b.", write={"b.txt": "wrong\n"}), reply("Fixed b.", write={"b.txt": "b\n"}))
    job = _start(check_cmd=_config_writer("{not json"))
    with pytest.raises(ValueError, match="shared grants"):
        bounded(lambda: task.drive(job))
    assert len(launches()) == 1 and job.status()["state"] == "setup-error"


def test_a_foreground_continuation_is_reserved_until_its_drive_takes_it(repo, fake):
    set_turns, launches = fake
    set_turns(reply("Created b.txt.", write={"b.txt": "b\n"}), reply("Created c.txt.", write={"c.txt": "c\n"}))
    job = _start()
    bounded(lambda: task.drive(job))
    job = bounded(lambda: task.continue_("t1", "Add c.txt."))  # reserved: its drive has not started
    assert job.status()["state"] == "queued"
    with pytest.raises(Running, match="already queued") as e:
        bounded(lambda: task.continue_("t1", "Another."))
    assert e.value.code == 25 and task.segments(job) == [1, 2]
    assert control.result(job.store, "t1")[1] == 25  # the old answer is not reported as done
    assert bounded(lambda: task.drive(job)).answer == "Created c.txt."
    assert launches()[1]["prompt"] == task.prompt(_wt(), [], "Add c.txt.")
    assert job.status()["state"] == "done"


def test_a_successful_check_cannot_outlast_the_segment_deadline(repo, fake):
    set_turns, launches = fake
    set_turns(reply("Created b.txt.", write={"b.txt": "b\n"}))
    job = _start(check_cmd="sleep 10", deadline="2s", max_turns=5)
    t0 = time.monotonic()
    with pytest.raises(GaveUp, match="deadline"):
        bounded(lambda: task.drive(job))
    assert time.monotonic() - t0 < 6 and len(launches()) == 1
    assert job.status()["state"] == "failed" and not os.path.exists(job.path("answer.txt"))


def test_the_summary_follow_up_stays_single_when_it_hits_quota_and_is_retried(repo, fake):
    set_turns, launches = fake
    set_turns(empty(conv="c1"), quota_error(), empty(conv="c1"))
    job = _start(max_turns=6)
    with pytest.raises(Quota):
        bounded(lambda: task.drive(job))
    quota.clear("m", quota.generation("m"))
    with pytest.raises(GaveUp, match="never resent blind"):
        bounded(lambda: task.drive(_start(max_turns=6)))
    assert [l["prompt"] == runner.SUMMARY_ONLY for l in launches()] == [False, True, True]
