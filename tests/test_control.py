import os
import signal
import subprocess
import sys
import threading

import pytest

from agycall import agy, control, runner, store
from agycall.errors import Cancelled, Fatal, GaveUp
from agyturns import bounded, error_reply, model_error, reply, wait_for

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
AGYCALL = os.path.join(ROOT, "bin", "agy-call")


def S(tmp_path):
    return str(tmp_path / "store")


def run_cli(*args):
    return subprocess.run([sys.executable, AGYCALL, *args], capture_output=True, text=True, timeout=60)


def _fake_job(tmp_path, name="j1", session=None, **status):
    job = store.Job(S(tmp_path), name)
    store.write_atomic(job.path("request.json"), {"identity": "x", "created": runner._now(), "session": session})
    job.set_status(**status)
    return job


def _me():
    return {"pid": os.getpid(), "pstart": agy.start_time(os.getpid())}


def _launch_rec(job):
    atts = job.launched()
    rec = store.read_json(os.path.join(atts[0], "launch.json")) if atts else None
    return rec if rec and rec.get("pstart") else None


def test_status_lists_this_sessions_jobs_first(tmp_path, fake, monkeypatch):
    set_turns, _ = fake
    monkeypatch.setenv("AGYCALL_SESSION", "s1")
    set_turns(reply("a"))
    runner.call("mine", "q", store=S(tmp_path), model="m")
    monkeypatch.setenv("AGYCALL_SESSION", "s2")
    set_turns(error_reply("half", error="stream closed unexpectedly"))
    with pytest.raises(GaveUp):
        runner.call("other", "q", store=S(tmp_path), model="m")
    monkeypatch.setenv("AGYCALL_SESSION", "s1")
    text, code = control.status(S(tmp_path))
    lines = text.splitlines()
    assert code == 0 and len(lines) == 2
    assert lines[0].startswith("mine  done  ") and lines[1].startswith("other  failed  ")  # other is newer
    assert "stream closed unexpectedly" in lines[1]
    assert store.read_json(os.path.join(S(tmp_path), "mine", "request.json"))["session"] == "s1"
    assert control.status(str(tmp_path / "empty")) == ("no jobs in %s" % (tmp_path / "empty"), 0)


def test_status_of_one_job_shows_its_fields_and_processes(tmp_path, fake):
    set_turns, _ = fake
    set_turns(reply("a"))
    runner.call("j1", "q", store=S(tmp_path), model="m")
    text, code = control.status(S(tmp_path), "j1")
    assert code == 0 and "state: done" in text and "conversation: c1" in text and "processes: none" in text
    assert "controller: null" in text  # cleared when call() returned
    with pytest.raises(ValueError, match="no job nope"):
        control.status(S(tmp_path), "nope")


def test_result_prints_the_answer_or_the_state_findings_and_last_reply(tmp_path, fake, monkeypatch):
    set_turns, _ = fake
    monkeypatch.setenv("AGYCALL_SESSION", "s1")
    set_turns(reply("the answer"))
    runner.call("ok", "q", store=S(tmp_path), model="m")
    assert control.result(S(tmp_path), "ok") == ("the answer", 0)
    set_turns(error_reply("half a reply", error="stream closed unexpectedly"))
    with pytest.raises(GaveUp):
        runner.call("bad", "q", store=S(tmp_path), model="m")
    text, code = control.result(S(tmp_path), "bad")
    assert code == 20 and text.startswith("state: failed") and "stream closed" in text and text.endswith("half a reply")
    assert control.result(S(tmp_path))[1] == 20  # no id: the newest finished job of this session
    set_turns(model_error())
    with pytest.raises(Fatal):
        runner.call("setup", "q", store=S(tmp_path), model="m")
    assert control.result(S(tmp_path), "setup")[1] == 14


def test_cancel_of_a_finished_job_is_a_no_op(tmp_path, fake):
    set_turns, _ = fake
    set_turns(reply("a"))
    runner.call("j1", "q", store=S(tmp_path), model="m")
    assert control.cancel(S(tmp_path), "j1") == ("done", 0)
    assert not os.path.exists(os.path.join(S(tmp_path), "j1", "cancel.request"))


def test_cancel_leaves_the_stopping_to_a_live_controller(tmp_path, fake):
    set_turns, launches = fake
    set_turns(reply("late", sleep=30))
    p = subprocess.Popen([sys.executable, AGYCALL, "run", "--job", "j1", "--store", S(tmp_path), "--model", "m",
                          "--prompt", "q"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    pids = []
    try:
        job = store.Job(S(tmp_path), "j1")
        wait_for(lambda: launches() and _launch_rec(job) and agy.owns(job.status().get("controller")))
        pids.append(_launch_rec(job)["pgid"])
        text = control.status(S(tmp_path), "j1")[0]
        assert "controller pid %d" % p.pid in text and "agy pid %d" % _launch_rec(job)["pid"] in text
        assert bounded(lambda: control.cancel(S(tmp_path), "j1"), pids) == ("cancelled", 0)
        assert p.wait(timeout=30) == 27
        assert control.result(S(tmp_path), "j1")[1] == 27
        assert control.cancel(S(tmp_path), "j1") == ("cancelled", 0)  # repeated: a no-op
    finally:
        p.kill()
        p.wait()
        for pg in pids:
            agy.stop_group(pg)


def test_cancel_stops_agy_left_running_by_a_killed_controller(tmp_path, fake):
    set_turns, launches = fake
    set_turns(reply("late", sleep=30))
    code = "import sys; sys.path.insert(0, %r); from agycall import runner; runner.call('j1', 'q', store=%r, model='m')" % (ROOT, S(tmp_path))
    p = subprocess.Popen([sys.executable, "-c", code], env=dict(os.environ))
    job = store.Job(S(tmp_path), "j1")
    rec = wait_for(lambda: launches() and _launch_rec(job))
    os.kill(p.pid, signal.SIGKILL)
    p.wait(timeout=10)
    try:
        assert agy.owns(rec) and not agy.owns(job.status().get("controller"))
        assert bounded(lambda: control.cancel(S(tmp_path), "j1"), [rec["pgid"]]) == ("cancelled", 0)
        assert not agy.alive(rec["pgid"])
        assert store.read_json(os.path.join(job.launched()[0], "result.json"))["outcome"] == "cancelled"
        with pytest.raises(Cancelled):
            bounded(lambda: runner.call("j1", "q", store=S(tmp_path), model="m"), [rec["pgid"]])
        assert len(launches()) == 1
    finally:
        agy.stop_group(rec["pgid"])


def test_cancel_reports_a_job_that_finished_first(tmp_path):
    job = _fake_job(tmp_path, state="running", controller=_me())
    threading.Timer(0.5, lambda: job.set_status(state="done", controller=None)).start()
    assert bounded(lambda: control.cancel(S(tmp_path), "j1", wait=10)) == ("finished before cancel (done)", 0)


def test_cancel_that_runs_out_of_time_says_still_stopping(tmp_path):
    _fake_job(tmp_path, state="running", controller=_me())
    assert bounded(lambda: control.cancel(S(tmp_path), "j1", wait=1)) == ("still stopping (pid %d)" % os.getpid(), 25)


def test_cancel_without_an_id_needs_one_running_job_of_this_session(tmp_path, monkeypatch):
    monkeypatch.setenv("AGYCALL_SESSION", "s1")
    for name in ("a", "b"):
        _fake_job(tmp_path, name, session="s1", state="queued")
    _fake_job(tmp_path, "theirs", session="s2", state="running")
    with pytest.raises(ValueError, match="several jobs.*a, b$"):
        control.cancel(S(tmp_path))
    store.Job(S(tmp_path), "b").set_status(state="done")
    assert bounded(lambda: control.cancel(S(tmp_path))) == ("cancelled", 0)
    assert store.Job(S(tmp_path), "a").status()["state"] == "cancelled"
    assert store.Job(S(tmp_path), "theirs").status()["state"] == "running"


def test_cli_status_result_and_cancel(tmp_path, fake):
    set_turns, _ = fake
    set_turns(reply("the answer"))
    assert run_cli("run", "--job", "j1", "--store", S(tmp_path), "--model", "m", "--prompt", "q").returncode == 0
    r = run_cli("status", "--store", S(tmp_path))
    assert r.returncode == 0 and r.stdout.startswith("j1  done  ")
    r = run_cli("result", "j1", "--store", S(tmp_path))
    assert r.returncode == 0 and r.stdout == "the answer\n"
    r = run_cli("cancel", "j1", "--store", S(tmp_path))
    assert r.returncode == 0 and r.stdout == "done\n"
    r = run_cli("result", "nope", "--store", S(tmp_path))
    assert r.returncode == 2 and "no job nope" in r.stderr


def _sleeper():
    p = subprocess.Popen(["sleep", "30"], start_new_session=True)
    threading.Thread(target=p.wait, daemon=True).start()  # reap it, as init would an orphan; a zombie group rejects signals
    return p, {"pid": p.pid, "pgid": p.pid, "pstart": agy.start_time(p.pid)}


def test_cancel_stops_the_check_of_a_check_error_recheck(tmp_path):
    job = _fake_job(tmp_path, state="checking", controller=None)  # the controller died during the recheck
    att = job.new_attempt()
    store.write_atomic(os.path.join(att, "launch.json"), {"pid": 1, "pgid": 1, "pstart": "gone"})
    store.write_atomic(os.path.join(att, "result.json"), {"outcome": "check-error"})  # the recheck runs against this
    p, rec = _sleeper()
    store.write_atomic(os.path.join(att, "check.json"), rec)
    try:
        assert ("check", p.pid) in control.live(job)
        assert bounded(lambda: control.cancel(S(tmp_path), "j1"), [p.pid]) == ("cancelled", 0)
        assert p.wait(timeout=10) != 0
        assert job.status()["state"] == "cancelled"
    finally:
        p.kill()
        p.wait()


def test_cancel_does_not_claim_success_while_a_launch_has_no_recorded_pid(tmp_path):
    job = _fake_job(tmp_path, state="running", controller=None)
    att = job.new_attempt()
    store.write_atomic(os.path.join(att, "launch.json"), {"pid": None, "pgid": None})  # died before on_start
    with store.flock(job.lock_path):  # the unrecorded agy child still holds the job lock
        assert bounded(lambda: control.cancel(S(tmp_path), "j1")) == ("still stopping (pid unknown)", 25)
    assert job.status()["state"] == "running"
    assert not os.path.exists(os.path.join(att, "result.json"))


def test_cancel_without_an_id_counts_a_live_controller_whatever_the_recorded_state(tmp_path, monkeypatch):
    monkeypatch.setenv("AGYCALL_SESSION", "s1")
    _fake_job(tmp_path, "rerun", session="s1", state="check-error", controller=_me())
    _fake_job(tmp_path, "old", session="s1", state="failed")
    assert control._only_running(S(tmp_path)).id == "rerun"


def test_cancel_keeps_a_job_that_finishes_between_its_state_read_and_its_orphan_check(tmp_path, monkeypatch):
    job = _fake_job(tmp_path, state="running", controller=_me())
    real = agy.owns

    def owns(rec):
        if rec and rec.get("pid") == os.getpid():  # the orphan check: the job finishes just before it
            store.write_atomic(job.path("answer.txt"), "the answer")
            job.set_status(state="done", answer=True, controller=None)
            return False
        return real(rec)

    monkeypatch.setattr(control.agy, "owns", owns)
    assert bounded(lambda: control.cancel(S(tmp_path), "j1")) == ("finished before cancel (done)", 0)
    assert job.status()["state"] == "done" and open(job.path("answer.txt")).read() == "the answer"
    assert not os.path.exists(job.path("cancel.request"))
