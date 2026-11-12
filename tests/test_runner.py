import datetime as dt
import json
import os
import shlex
import subprocess
import sys
import threading
import time

import pytest

from agycall import agy, quota, runner, store
from agycall.errors import Cancelled, CheckError, Fatal, GaveUp, Mismatch, NoConversation, Quota, Running
from agyturns import auth_error, empty, denied, error_reply, malformed, model_error, partial, quota_error, reply, wait_for

HERE = os.path.dirname(os.path.abspath(__file__))


def S(tmp_path):
    return str(tmp_path / "store")


GOOD = reply('```json\n{"answer": "B", "confidence": 97}\n```')
BAD = reply("I would choose B.")


def test_success_then_rerun_does_not_launch(tmp_path, fake):
    set_turns, launches = fake
    set_turns(GOOD)
    r = runner.call("j1", "question?", store=S(tmp_path), model="m", expect="json", require=["answer"])
    assert r.answer == {"answer": "B", "confidence": 97} and r.conversation == "c1" and r.attempts == 1
    assert r.usage == {"output": 10, "input": 5}
    a = launches()[0]["argv"]
    assert a[:2] == ["--model", "m"] and a[a.index("--output-format") + 1] == "json" and launches()[0]["prompt"] == "question?"
    again = runner.call("j1", "question?", store=S(tmp_path), model="m", expect="json", require=["answer"])
    assert again.answer == r.answer and len(launches()) == 1


def test_unreadable_then_good_resumes_same_conversation(tmp_path, fake):
    set_turns, launches = fake
    set_turns(BAD, GOOD)
    r = runner.call("j1", "q", store=S(tmp_path), model="m", expect="json", require=["answer"])
    assert r.answer["answer"] == "B" and r.attempts == 2
    second = launches()[1]
    assert second["argv"][second["argv"].index("--conversation") + 1] == "c1"
    assert "could not be used" in second["prompt"] and "no JSON value" in second["prompt"]
    job = store.Job(S(tmp_path), "j1")
    assert open(job.path("attempts", "001", "reply.txt")).read() == "I would choose B."  # nothing deleted


def test_timeout_then_complete_answer(tmp_path, fake):
    set_turns, launches = fake
    set_turns(partial("half an ans"), reply("the complete answer"))
    r = runner.call("j1", "q", store=S(tmp_path), model="m")
    assert r.answer == "the complete answer"
    assert "from the beginning" in launches()[1]["prompt"]


def test_denied_once_then_success_and_denied_twice_fails(tmp_path, fake):
    set_turns, launches = fake
    set_turns(denied(), reply("ok"))
    assert runner.call("j1", "q", store=S(tmp_path), model="m").answer == "ok"
    assert "denied by the permission policy" in launches()[1]["prompt"]
    set_turns(denied(conv="c2"))
    with pytest.raises(GaveUp):
        runner.call("j2", "q", store=S(tmp_path), model="m")
    assert store.Job(S(tmp_path), "j2").status()["state"] == "failed"


def test_empty_is_retried_once_with_the_same_message(tmp_path, fake):
    set_turns, launches = fake
    set_turns(empty(), reply("fine"))
    assert runner.call("j1", "q", store=S(tmp_path), model="m").answer == "fine"
    assert launches()[1]["prompt"] == "q" and "--conversation" not in launches()[1]["argv"]


def test_quota_sets_hold_and_blocks_other_jobs(tmp_path, fake):
    set_turns, launches = fake
    set_turns(quota_error("Resets in 1h12m27s"))
    with pytest.raises(Quota) as e:
        runner.call("j1", "q", store=S(tmp_path), model="m")
    left = (e.value.until - __import__("datetime").datetime.now(e.value.until.tzinfo)).total_seconds()
    assert 4300 < left <= 4347
    with pytest.raises(Quota):
        runner.call("j2", "q", store=S(tmp_path), model="m")
    assert len(launches()) == 1  # j2 never launched
    assert store.Job(S(tmp_path), "j1").status()["state"] == "quota"


def test_older_generation_success_does_not_clear_hold(tmp_path, fake):
    set_turns, _ = fake
    set_turns(reply("ok", sleep=1.5))
    p = subprocess.Popen([sys.executable, "-c",
                          "import sys; sys.path.insert(0, %r); from agycall import runner; runner.call('slow', 'q', store=%r, model='m')" % (os.path.dirname(HERE), S(tmp_path))],
                         env=dict(os.environ))
    time.sleep(0.7)  # the slow job has passed admission at generation 0
    until = quota.hold("m", "r", 600)
    assert p.wait(timeout=30) == 0
    assert quota.held("m") == until


def test_error_envelope_gets_lost_context_message(tmp_path, fake):
    set_turns, launches = fake
    set_turns(reply('{"error": "missing_initial_prompt"}'),
              reply('{"action": "revise"}'))
    r = runner.call("j1", "ORIGINAL PROMPT TEXT", store=S(tmp_path), model="m", expect="json", require=["action"])
    assert r.answer == {"action": "revise"}
    assert "The original request was:" in launches()[1]["prompt"] and "ORIGINAL PROMPT TEXT" in launches()[1]["prompt"]


def test_max_turns_then_higher_limit_continues(tmp_path, fake):
    set_turns, launches = fake
    set_turns(BAD)
    with pytest.raises(GaveUp) as e:
        runner.call("j1", "q", store=S(tmp_path), model="m", expect="json", max_turns=2)
    assert e.value.conversation == "c1" and len(launches()) == 2
    set_turns(GOOD)
    r = runner.call("j1", "q", store=S(tmp_path), model="m", expect="json", max_turns=3)
    assert r.answer["answer"] == "B" and len(launches()) == 3
    assert "--conversation" in launches()[2]["argv"]


def test_same_job_in_two_processes(tmp_path, fake):
    job = store.Job(S(tmp_path), "j1")
    holder = subprocess.Popen([sys.executable, "-c",
                               "import fcntl,os,sys,time; os.makedirs(os.path.dirname(sys.argv[1]), exist_ok=True); f=open(sys.argv[1],'a'); fcntl.flock(f, fcntl.LOCK_EX); print('held', flush=True); time.sleep(5)", job.lock_path],
                              stdout=subprocess.PIPE, text=True)
    assert holder.stdout.readline().strip() == "held"
    try:
        with pytest.raises(Running):
            runner.call("j1", "q", store=S(tmp_path), model="m")
    finally:
        holder.kill()
        holder.wait()


def _fake_attempt(tmp_path, pgid):
    job = store.Job(S(tmp_path), "j1")
    os.makedirs(job.dir, exist_ok=True)
    att = job.new_attempt()
    store.write_atomic(os.path.join(att, "launch.json"), {"pid": pgid, "pgid": pgid, "kind": "first", "message": "q", "generation": 0})
    open(os.path.join(att, "delegate.err"), "w").write("AGY_USAGE " + json.dumps({"conversation_id": "c9"}) + "\n")
    job.set_status(state="running", conversation="c9")
    return job, att


def test_live_child_blocks_a_new_launch(tmp_path, fake):
    set_turns, launches = fake
    set_turns(GOOD)
    child = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        job = store.Job(S(tmp_path), "j1")
        os.makedirs(job.dir, exist_ok=True)
        store.write_atomic(job.path("request.json"), {"identity": store.identity("q", "m", os.path.abspath(os.getcwd()), [], None,
                           {"json": False, "require": [], "schema": None, "cmd": None, "check_id": None}, None)})
        _fake_attempt(tmp_path, child.pid)
        with pytest.raises(Running):
            runner.call("j1", "q", store=S(tmp_path), model="m")
    finally:
        child.kill()
        child.wait()


def test_dead_child_is_marked_interrupted_and_restarted(tmp_path, fake):
    set_turns, launches = fake
    set_turns(reply("full answer", "c9"))
    dead = subprocess.Popen(["true"], start_new_session=True)
    dead.wait()
    job = store.Job(S(tmp_path), "j1")
    os.makedirs(job.dir, exist_ok=True)
    store.write_atomic(job.path("request.json"), {"identity": store.identity("q", "m", os.path.abspath(os.getcwd()), [], None,
                       {"json": False, "require": [], "schema": None, "cmd": None, "check_id": None}, None)})
    _, att = _fake_attempt(tmp_path, dead.pid)
    r = runner.call("j1", "q", store=S(tmp_path), model="m")
    assert r.answer == "full answer"
    assert store.read_json(os.path.join(att, "result.json"))["outcome"] == "interrupted"
    assert "from the beginning" in launches()[0]["prompt"]
    assert launches()[0]["argv"][launches()[0]["argv"].index("--conversation") + 1] == "c9"


def test_unlaunched_attempt_is_renamed(tmp_path, fake):
    set_turns, _ = fake
    set_turns(GOOD)
    job = store.Job(S(tmp_path), "j1")
    os.makedirs(job.dir, exist_ok=True)
    job.new_attempt()  # never got a launch.json
    runner.call("j1", "q", store=S(tmp_path), model="m")
    names = sorted(os.listdir(job.path("attempts")))
    assert names == ["001.unlaunched", "002"]


def test_crash_between_answer_and_status(tmp_path, fake):
    set_turns, launches = fake
    set_turns(GOOD)
    runner.call("j1", "q", store=S(tmp_path), model="m")
    store.Job(S(tmp_path), "j1").set_status(state="running")
    r = runner.call("j1", "q", store=S(tmp_path), model="m")
    assert r.text.startswith("```json") and len(launches()) == 1
    assert store.Job(S(tmp_path), "j1").status()["state"] == "done"


def test_changed_request_is_refused_then_versioned(tmp_path, fake):
    set_turns, _ = fake
    set_turns(BAD)
    with pytest.raises(GaveUp):
        runner.call("j1", "q", store=S(tmp_path), model="m", expect="json", max_turns=1)
    with pytest.raises(Mismatch):
        runner.call("j1", "a different question", store=S(tmp_path), model="m", expect="json", max_turns=1)
    set_turns(GOOD)
    runner.call("j1", "a different question", store=S(tmp_path), model="m", new_version=True)
    assert os.path.isdir(os.path.join(S(tmp_path), ".versions", "j1", "v1", "attempts", "001"))


def test_conversation_lock_makes_second_job_wait(tmp_path, fake):
    set_turns, _ = fake
    set_turns(reply("ok", "shared"))
    lock = runner.conv_lock("shared")
    holder = subprocess.Popen([sys.executable, "-c",
                               "import fcntl,os,sys,time; os.makedirs(os.path.dirname(sys.argv[1]), exist_ok=True); f=open(sys.argv[1],'a'); fcntl.flock(f, fcntl.LOCK_EX); print('held', flush=True); time.sleep(1.5)", lock],
                              stdout=subprocess.PIPE, text=True)
    assert holder.stdout.readline().strip() == "held"
    t0 = time.monotonic()
    runner.call("turn2", "q2", store=S(tmp_path), model="m", conversation="shared")
    assert time.monotonic() - t0 >= 1.0
    holder.wait()


def test_hanging_check_is_an_operational_failure(tmp_path, fake):
    set_turns, launches = fake
    set_turns(reply("x"))
    with pytest.raises(CheckError):
        runner.call("j1", "q", store=S(tmp_path), model="m", check_cmd="sleep 30", check_timeout=1)
    assert len(launches()) == 1 and store.Job(S(tmp_path), "j1").status()["state"] == "check-error"


def test_check_error_rerun_rechecks_the_saved_reply(tmp_path, fake, monkeypatch):
    set_turns, launches = fake
    set_turns(reply("the reply"))
    flag = tmp_path / "flag"
    monkeypatch.setenv("FLAGFILE", str(flag))
    cmd = 'test -f "$FLAGFILE" || exit 3'
    with pytest.raises(CheckError):
        runner.call("j1", "q", store=S(tmp_path), model="m", check_cmd=cmd)
    flag.write_text("")
    r = runner.call("j1", "q", store=S(tmp_path), model="m", check_cmd=cmd)
    assert r.answer == "the reply" and len(launches()) == 1


def test_missing_conversation_id_stops_without_fresh(tmp_path, fake):
    set_turns, launches = fake
    set_turns(reply("not json", None), reply('{"answer": 1}', None))
    with pytest.raises(NoConversation):
        runner.call("j1", "q", store=S(tmp_path), model="m", expect="json")
    assert len(launches()) == 1
    r = runner.call("j1", "q", store=S(tmp_path), model="m", expect="json", fresh=True)
    assert r.answer == {"answer": 1}
    assert launches()[1]["prompt"].startswith("q\n\n") and "--conversation" not in launches()[1]["argv"]


def test_fatal_codes(tmp_path, fake):
    set_turns, _ = fake
    set_turns(model_error())
    with pytest.raises(Fatal) as e:
        runner.call("j1", "q", store=S(tmp_path), model="m")
    assert e.value.code == 14 and 'invalid --model "m"' in e.value.reason
    set_turns(auth_error())
    with pytest.raises(Fatal) as e:
        runner.call("j2", "q", store=S(tmp_path), model="m")
    assert e.value.code == 11
    set_turns(malformed())
    with pytest.raises(Fatal) as e:
        runner.call("j3", "q", store=S(tmp_path), model="m")
    assert e.value.code == 22 and "not a JSON envelope" in e.value.reason


def test_missing_agy_is_fatal_13(tmp_path, monkeypatch):
    monkeypatch.setenv("AGYCALL_AGY", str(tmp_path / "nope"))
    with pytest.raises(Fatal) as e:
        runner.call("j1", "q", store=S(tmp_path), model="m")
    assert e.value.code == 13
    job = store.Job(S(tmp_path), "j1")
    assert job.status()["state"] == "fatal"
    assert store.read_json(os.path.join(job.launched()[-1], "result.json"))["outcome"] == "fatal"


def test_noisy_stdout_is_malformed_and_kept(tmp_path, fake):
    set_turns, _ = fake
    raw = b'Warning: update available\n{"status": "SUCCESS", "response": "paid text"}'
    set_turns(malformed(raw=raw, stderr="", exit=0))
    with pytest.raises(Fatal) as e:
        runner.call("j1", "q", store=S(tmp_path), model="m")
    att = store.Job(S(tmp_path), "j1").launched()[0]
    assert e.value.code == 22 and os.path.join(att, "agy.out") in e.value.reason
    assert open(os.path.join(att, "agy.out"), "rb").read() == raw


def test_large_prompt_and_non_utf8_output(tmp_path, fake):
    set_turns, launches = fake
    big = "x" * min(300_000, agy.MAX_PROMPT)
    set_turns({"raw_hex": b'{"status": "SUCCESS", "conversation_id": "c1", "response": "ok \xff end"}'.hex()})
    r = runner.call("j1", big, store=S(tmp_path), model="m")
    assert launches()[0]["prompt"] == big
    assert r.answer.startswith("ok ") and r.answer.endswith(" end")


def test_cwd_and_dirs_are_passed(tmp_path, fake):
    set_turns, launches = fake
    set_turns(GOOD)
    work = tmp_path / "work"
    work.mkdir()
    runner.call("j1", "q", store=S(tmp_path), model="pro", cwd=str(work), dirs=[str(work)], timeout="15m", mode="plan")
    l = launches()[0]
    assert os.path.realpath(l["cwd"]) == os.path.realpath(str(work))
    a = l["argv"]
    assert a[:4] == ["--model", "pro", "--print-timeout", "900s"] and a[a.index("--add-dir") + 1] == str(work)
    assert a[a.index("--mode") + 1] == "plan" and a[-2:] == ["-p", "q"]


def test_locks_outlive_a_killed_caller(tmp_path, fake):
    import signal
    set_turns, launches = fake
    set_turns(reply("late", "cX", sleep=4), reply("ok", "cX"))
    p = subprocess.Popen([sys.executable, "-c",
                          "import sys; sys.path.insert(0, %r); from agycall import runner; runner.call('j1', 'q', store=%r, model='m', conversation='cX')" % (os.path.dirname(HERE), S(tmp_path))],
                         env=dict(os.environ))
    deadline = time.monotonic() + 15
    while not launches() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert launches()
    time.sleep(0.5)
    os.kill(p.pid, signal.SIGKILL)
    p.wait(timeout=10)
    t0 = time.monotonic()
    with pytest.raises(Running):
        runner.call("j1", "q", store=S(tmp_path), model="m", conversation="cX")
    with pytest.raises(Running):
        runner.call("j1", "q", store=S(tmp_path), model="m", conversation="cX", new_version=True)
    import threading
    th = threading.Thread(target=lambda: runner.call("j2", "q2", store=S(tmp_path), model="m", conversation="cX"), daemon=True)
    th.start()
    th.join(timeout=30)
    assert not th.is_alive()  # a regression fails here instead of hanging the suite
    assert time.monotonic() - t0 >= 2.0  # waited for the orphaned delegate's conversation lock
    with pytest.raises(GaveUp) as e:  # the orphaned agy's reply is recovered, but its exit status was lost
        runner.call("j1", "q", store=S(tmp_path), model="m", conversation="cX")
    att = store.Job(S(tmp_path), "j1").launched()[0]
    res = store.read_json(os.path.join(att, "result.json"))
    assert res["classified"] == "reply-with-error" and res["recovered"] is True
    assert "only by a check" in str(e.value) and len(launches()) == 2  # nothing was resent


def test_answer_is_saved_before_the_accepted_result(tmp_path, fake, monkeypatch):
    set_turns, launches = fake
    set_turns(GOOD)
    real = store.write_atomic

    def crash(path, data):
        if path.endswith("result.json") and "'accepted'" in str(data):
            raise KeyboardInterrupt
        return real(path, data)

    monkeypatch.setattr(store, "write_atomic", crash)
    with pytest.raises(KeyboardInterrupt):
        runner.call("j1", "q", store=S(tmp_path), model="m", expect="json")
    monkeypatch.setattr(store, "write_atomic", real)
    assert os.path.exists(store.Job(S(tmp_path), "j1").path("answer.txt"))
    assert runner.call("j1", "q", store=S(tmp_path), model="m", expect="json").answer["answer"] == "B" and len(launches()) == 1


def test_retry_after_agy_output_does_not_start_a_new_conversation(tmp_path, fake):
    set_turns, launches = fake
    set_turns(GOOD)
    job = store.Job(S(tmp_path), "j1")
    os.makedirs(job.dir, exist_ok=True)
    store.write_atomic(job.path("request.json"), {"identity": store.identity("q", "m", os.path.abspath(os.getcwd()), [], None,
                       {"json": False, "require": [], "schema": None, "cmd": None, "check_id": None}, None)})
    att = job.new_attempt()
    store.write_atomic(os.path.join(att, "launch.json"), {"pid": 1, "pgid": None, "kind": "first", "message": "q", "generation": 0})
    store.write_atomic(os.path.join(att, "result.json"), {"rc": 2, "outcome": "retry", "conversation": None})
    open(os.path.join(att, "agy-1.args"), "w").write('["-p", "x"]')
    open(os.path.join(att, "agy-1.out"), "w").write("partial paid text")
    with pytest.raises(NoConversation):
        runner.call("j1", "q", store=S(tmp_path), model="m")
    assert launches() == []


def test_conversation_id_stranded_in_result_json(tmp_path, fake):
    set_turns, launches = fake
    set_turns(reply(GOOD["response"], "c5"))
    job = store.Job(S(tmp_path), "j1")
    os.makedirs(job.dir, exist_ok=True)
    store.write_atomic(job.path("request.json"), {"identity": store.identity("q", "m", os.path.abspath(os.getcwd()), [], None,
                       {"json": False, "require": [], "schema": None, "cmd": None, "check_id": None}, None)})
    att = job.new_attempt()
    store.write_atomic(os.path.join(att, "launch.json"), {"pid": 1, "pgid": None, "kind": "first", "message": "q", "generation": 0})
    store.write_atomic(os.path.join(att, "result.json"), {"rc": 0, "outcome": "rejected", "conversation": "c5", "findings": ["bad"]})
    runner.call("j1", "q", store=S(tmp_path), model="m")
    argv = launches()[0]["argv"]
    assert argv[argv.index("--conversation") + 1] == "c5"


def test_probe_call_clears_its_hold(tmp_path, fake):
    set_turns, _ = fake
    set_turns(GOOD)
    quota.hold("m", "r", 600)
    runner.call("j1", "q", store=S(tmp_path), model="m", ignore_quota=True)
    assert quota.held("m") is None


def test_quota_first_attempt_with_output_resends_the_prompt(tmp_path, fake):
    set_turns, launches = fake
    set_turns(GOOD)
    job = store.Job(S(tmp_path), "j1")
    os.makedirs(job.dir, exist_ok=True)
    store.write_atomic(job.path("request.json"), {"identity": store.identity("q", "m", os.path.abspath(os.getcwd()), [], None,
                       {"json": False, "require": [], "schema": None, "cmd": None, "check_id": None}, None)})
    att = job.new_attempt()
    store.write_atomic(os.path.join(att, "launch.json"), {"pid": 1, "pgid": None, "kind": "first", "message": "q", "generation": 0})
    store.write_atomic(os.path.join(att, "result.json"), {"rc": 10, "outcome": "quota", "conversation": None})
    open(os.path.join(att, "agy-1.args"), "w").write('["-p", "x"]')
    open(os.path.join(att, "agy-1.out"), "w").write("quota error text")
    r = runner.call("j1", "q", store=S(tmp_path), model="m")
    assert r.answer and launches()[0]["prompt"] == "q" and "--conversation" not in launches()[0]["argv"]


def _retry_attempt(tmp_path, args):
    job = store.Job(S(tmp_path), "j1")
    os.makedirs(job.dir, exist_ok=True)
    store.write_atomic(job.path("request.json"), {"identity": store.identity("q", "m", os.path.abspath(os.getcwd()), [], None,
                       {"json": False, "require": [], "schema": None, "cmd": None, "check_id": None}, None)})
    att = job.new_attempt()
    store.write_atomic(os.path.join(att, "launch.json"), {"pid": 1, "pgid": None, "kind": "first", "message": "q", "generation": 0})
    store.write_atomic(os.path.join(att, "result.json"), {"rc": 2, "outcome": "retry", "conversation": None})
    open(os.path.join(att, "agy-1.args"), "w").write(json.dumps(args))
    open(os.path.join(att, "agy-1.out"), "w").write("partial paid text")


def test_prompt_containing_help_still_counts_as_paid_output(tmp_path, fake):
    _retry_attempt(tmp_path, ["-p", "Implement a --help option"])
    with pytest.raises(NoConversation):
        runner.call("j1", "q", store=S(tmp_path), model="m")


def test_help_probe_output_does_not_count(tmp_path, fake):
    set_turns, launches = fake
    set_turns(GOOD)
    _retry_attempt(tmp_path, ["--help"])
    runner.call("j1", "q", store=S(tmp_path), model="m")
    assert launches()[0]["prompt"] == "q"


def test_model_is_required(tmp_path, fake):
    set_turns, launches = fake
    with pytest.raises(ValueError, match="model is required"):
        runner.call("j1", "q", store=S(tmp_path))
    with pytest.raises(TypeError):
        runner.call("j1", "q", store=S(tmp_path), model="m", tier="pro")
    assert launches() == []


def test_invalid_schema_launches_nothing(tmp_path, fake):
    set_turns, launches = fake
    schema = tmp_path / "s.json"
    schema.write_text(json.dumps({"type": "int"}))
    with pytest.raises(ValueError):
        runner.call("j1", "q", store=S(tmp_path), model="m", schema=str(schema))
    assert launches() == []


def test_output_limit_error_reply_is_checked_and_accepted(tmp_path, fake):
    set_turns, launches = fake
    set_turns(error_reply(GOOD["response"]))
    r = runner.call("j1", "q", store=S(tmp_path), model="m", expect="json", require=["answer"])
    assert r.answer["answer"] == "B" and len(launches()) == 1
    att = store.Job(S(tmp_path), "j1").launched()[0]
    assert "output token limit" in store.read_json(os.path.join(att, "result.json"))["agyError"]


def test_output_limit_error_reply_that_fails_checks_stops(tmp_path, fake):
    set_turns, launches = fake
    set_turns(error_reply(BAD["response"]), GOOD)
    with pytest.raises(GaveUp) as e:
        runner.call("j1", "q", store=S(tmp_path), model="m", expect="json", require=["answer"])
    assert len(launches()) == 1 and "no follow-up was sent" in str(e.value)


def test_output_limit_error_without_a_reply_stops_at_once(tmp_path, fake):
    set_turns, launches = fake
    set_turns(error_reply(""))
    with pytest.raises(GaveUp) as e:
        runner.call("j1", "q", store=S(tmp_path), model="m", max_turns=6)
    assert len(launches()) == 1 and "output token limit" in str(e.value)


def test_reply_after_an_agy_error_needs_a_check(tmp_path, fake):
    set_turns, launches = fake
    set_turns(error_reply("Implemented the change.", error="stream closed unexpectedly"))
    with pytest.raises(GaveUp) as e:
        runner.call("j1", "q", store=S(tmp_path), model="m")
    att = store.Job(S(tmp_path), "j1").launched()[0]
    assert len(launches()) == 1 and "accepted after an agy error only by a check" in str(e.value)
    assert open(os.path.join(att, "reply.txt")).read() == "Implemented the change."
    assert store.Job(S(tmp_path), "j1").status()["state"] == "failed"


def test_attempt_files_of_the_new_layout(tmp_path, fake):
    set_turns, _ = fake
    set_turns(GOOD)
    runner.call("j1", "secret question", store=S(tmp_path), model="m")
    att = store.Job(S(tmp_path), "j1").launched()[0]
    assert sorted(os.listdir(att)) == ["agy.args", "agy.err", "agy.out", "launch.json", "reply.txt", "result.json"]
    assert "secret question" not in open(os.path.join(att, "agy.args")).read()
    res = store.read_json(os.path.join(att, "result.json"))
    assert res["exit"] == 0 and res["agyStatus"] == "SUCCESS" and res["classified"] == "reply" and res["outcome"] == "accepted"
    assert "pstart" in store.read_json(os.path.join(att, "launch.json"))  # the value itself is tested in test_agy.py


def test_oversized_prompt_and_bad_timeout_launch_nothing(tmp_path, fake):
    set_turns, launches = fake
    set_turns(GOOD)
    with pytest.raises(ValueError, match="limit is"):
        runner.call("j1", "é" * (agy.MAX_PROMPT // 2 + 1), store=S(tmp_path), model="m")  # 2 bytes each in UTF-8
    with pytest.raises(ValueError, match="timeout"):
        runner.call("j2", "q", store=S(tmp_path), model="m", timeout="ten minutes")
    assert launches() == [] and not os.path.exists(os.path.join(S(tmp_path), "j1"))


def test_check_id_without_a_check_does_not_accept_an_error_reply(tmp_path, fake):
    set_turns, launches = fake
    set_turns(error_reply("Implemented the change.", error="stream closed unexpectedly"))
    with pytest.raises(GaveUp):
        runner.call("j1", "q", store=S(tmp_path), model="m", check_id="label-only")
    assert len(launches()) == 1


def test_check_error_rerun_after_an_agy_error_sends_no_follow_up(tmp_path, fake, monkeypatch):
    set_turns, launches = fake
    set_turns(error_reply("Implemented the change.", error="stream closed unexpectedly"), GOOD)
    flag = tmp_path / "flag"
    monkeypatch.setenv("FLAGFILE", str(flag))
    cmd = 'test -f "$FLAGFILE" || exit 3; exit 1'
    with pytest.raises(CheckError):
        runner.call("j1", "q", store=S(tmp_path), model="m", check_cmd=cmd)
    flag.write_text("")
    with pytest.raises(GaveUp) as e:
        runner.call("j1", "q", store=S(tmp_path), model="m", check_cmd=cmd)
    assert len(launches()) == 1 and "no follow-up was sent" in str(e.value)
    assert store.Job(S(tmp_path), "j1").status()["state"] == "failed"


def test_rerun_of_a_failed_error_reply_sends_no_follow_up(tmp_path, fake):
    set_turns, launches = fake
    set_turns(error_reply(BAD["response"]), GOOD)
    for max_turns in (4, 6):
        with pytest.raises(GaveUp) as e:
            runner.call("j1", "q", store=S(tmp_path), model="m", expect="json", max_turns=max_turns)
        assert "no follow-up was sent" in str(e.value)
    assert len(launches()) == 1


def test_third_call_after_a_guarded_recheck_sends_no_follow_up(tmp_path, fake, monkeypatch):
    set_turns, launches = fake
    set_turns(error_reply("Implemented the change.", error="stream closed unexpectedly"), GOOD)
    flag = tmp_path / "flag"
    monkeypatch.setenv("FLAGFILE", str(flag))
    cmd = 'test -f "$FLAGFILE" || exit 3; exit 1'
    with pytest.raises(CheckError):
        runner.call("j1", "q", store=S(tmp_path), model="m", check_cmd=cmd)
    flag.write_text("")
    for _ in range(2):
        with pytest.raises(GaveUp) as e:
            runner.call("j1", "q", store=S(tmp_path), model="m", check_cmd=cmd)
        assert "no follow-up was sent" in str(e.value)
    assert len(launches()) == 1


def test_generated_follow_up_over_the_prompt_limit_is_refused(tmp_path, fake, monkeypatch):
    set_turns, launches = fake
    monkeypatch.setattr(agy, "MAX_PROMPT", 2000)
    set_turns(reply('{"error": "missing_initial_prompt"}'), reply('{"action": "revise"}'))
    with pytest.raises(GaveUp) as e:
        runner.call("j1", "x" * 1900, store=S(tmp_path), model="m", expect="json", require=["action"])
    assert "single-argument limit" in str(e.value)
    assert len(launches()) == 1 and len(store.Job(S(tmp_path), "j1").launched()) == 1


def test_saved_answer_is_returned_even_if_the_prompt_limit_shrinks(tmp_path, fake, monkeypatch):
    set_turns, launches = fake
    monkeypatch.setattr(agy, "MAX_PROMPT", 2000)
    set_turns(reply("ok"))
    runner.call("j1", "x" * 1500, store=S(tmp_path), model="m")
    monkeypatch.setattr(agy, "MAX_PROMPT", 1000)
    assert runner.call("j1", "x" * 1500, store=S(tmp_path), model="m").answer == "ok"
    assert len(launches()) == 1


def _cident(cmd=None):
    return {"json": False, "require": [], "schema": None, "cmd": cmd, "check_id": None}


def _pending(tmp_path, out, check_cmd=None, started=None, conversation=None):
    """A job whose controller died after agy exited: launch.json and agy.out, but no result.json."""
    job = store.Job(S(tmp_path), "j1")
    os.makedirs(job.dir, exist_ok=True)
    store.write_atomic(job.path("request.json"), {"identity": store.identity(
        "q", "m", os.path.abspath(os.getcwd()), [], conversation, _cident(check_cmd), None)})
    dead = subprocess.Popen(["true"], start_new_session=True)
    dead.wait()
    att = job.new_attempt()
    store.write_atomic(os.path.join(att, "launch.json"), {"pid": dead.pid, "pgid": dead.pid, "kind": "first", "message": "q",
                                                         "conversation": conversation, "generation": 0,
                                                         "started": started or runner._now()})
    with open(os.path.join(att, "agy.out"), "wb") as fh:
        fh.write(out)
    open(os.path.join(att, "agy.err"), "w").close()
    return job, att


def _env(response, conv="c7", status="SUCCESS"):
    return json.dumps({"status": status, "response": response, "conversation_id": conv}).encode("utf-8")


def test_recovered_reply_is_accepted_by_a_passing_check(tmp_path, fake):
    set_turns, launches = fake
    set_turns(GOOD)
    _, att = _pending(tmp_path, _env("the reply"), check_cmd="grep -q reply")
    r = runner.call("j1", "q", store=S(tmp_path), model="m", check_cmd="grep -q reply")
    assert r.answer == "the reply" and r.conversation == "c7" and launches() == []
    res = store.read_json(os.path.join(att, "result.json"))
    assert res["classified"] == "reply-with-error" and res["recovered"] is True and res["outcome"] == "accepted"
    assert "exit status" in res["agyError"]
    assert open(os.path.join(att, "reply.txt")).read() == "the reply"


def test_recovered_reply_failing_its_check_sends_no_follow_up(tmp_path, fake):
    set_turns, launches = fake
    set_turns(GOOD)
    _pending(tmp_path, _env("the reply"), check_cmd="exit 1")
    for _ in range(2):  # a rerun keeps the stop
        with pytest.raises(GaveUp) as e:
            runner.call("j1", "q", store=S(tmp_path), model="m", check_cmd="exit 1")
        assert "no follow-up was sent" in str(e.value)
    assert launches() == []


def test_truncated_output_is_interrupted_and_the_turn_restarts(tmp_path, fake):
    set_turns, launches = fake
    set_turns(reply("full answer", "c7"))
    job, att = _pending(tmp_path, b'{"status": "SUCC', conversation="c7")
    r = runner.call("j1", "q", store=S(tmp_path), model="m", conversation="c7")
    assert r.answer == "full answer" and store.read_json(os.path.join(att, "result.json"))["outcome"] == "interrupted"
    assert "from the beginning" in launches()[0]["prompt"]
    assert launches()[0]["argv"][launches()[0]["argv"].index("--conversation") + 1] == "c7"


def test_incomplete_output_after_the_deadline_is_a_timeout(tmp_path, fake):
    set_turns, launches = fake
    set_turns(reply("full answer", "c7"))
    old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)).isoformat()
    _, att = _pending(tmp_path, b"", started=old, conversation="c7")
    r = runner.call("j1", "q", store=S(tmp_path), model="m", conversation="c7")
    res = store.read_json(os.path.join(att, "result.json"))
    assert res["outcome"] == "timeout" and res["recovered"] is True and r.answer == "full answer"
    assert "from the beginning" in launches()[0]["prompt"]


def test_reply_in_another_conversation_stops_the_job(tmp_path, fake):
    set_turns, launches = fake
    set_turns(reply("ok", "c2"))
    with pytest.raises(GaveUp) as e:
        runner.call("j1", "q", store=S(tmp_path), model="m", conversation="c1")
    assert "agy answered in conversation c2 instead of c1" in str(e.value)
    job = store.Job(S(tmp_path), "j1")
    assert job.status()["state"] == "failed" and not os.path.exists(job.path("answer.txt"))
    assert store.read_json(os.path.join(job.launched()[0], "result.json"))["conversationConflict"] == "c2"
    with pytest.raises(GaveUp):
        runner.call("j1", "q", store=S(tmp_path), model="m", conversation="c1", max_turns=8)
    assert len(launches()) == 1


def test_check_runs_in_the_job_cwd_while_the_job_shows_checking(tmp_path, fake):
    set_turns, _ = fake
    set_turns(reply("x"))
    work = tmp_path / "work"
    work.mkdir()
    (work / "marker").write_text("")
    status = os.path.join(S(tmp_path), "j1", "status.json")
    cmd = "test -f marker && grep -q '\"state\": \"checking\"' " + shlex.quote(status)
    r = runner.call("j1", "q", store=S(tmp_path), model="m", cwd=str(work), check_cmd=cmd)
    rec = store.read_json(os.path.join(store.Job(S(tmp_path), "j1").launched()[0], "check.json"))
    assert r.answer == "x" and rec["pid"] == rec["pgid"] and rec["pstart"]


def test_check_left_running_by_a_killed_controller_blocks_then_runs_again(tmp_path, fake, monkeypatch):
    import signal
    set_turns, launches = fake
    set_turns(reply("the reply", "c1"))
    flag = tmp_path / "flag"
    monkeypatch.setenv("FLAGFILE", str(flag))
    cmd = 'test -f "$FLAGFILE" || sleep 30; grep -q reply'
    code = ("import sys; sys.path.insert(0, %r); from agycall import runner; "
            "runner.call('j1', 'q', store=%r, model='m', cwd=%r, check_cmd=%r)" % (os.path.dirname(HERE), S(tmp_path), str(tmp_path), cmd))
    p = subprocess.Popen([sys.executable, "-c", code], env=dict(os.environ))
    job = store.Job(S(tmp_path), "j1")

    def check_rec():
        atts = job.launched()
        return store.read_json(os.path.join(atts[0], "check.json")) if atts else None

    rec = wait_for(check_rec)
    os.kill(p.pid, signal.SIGKILL)
    p.wait(timeout=10)
    try:
        with pytest.raises(Running):  # the orphaned check still holds the job lock
            runner.call("j1", "q", store=S(tmp_path), model="m", cwd=str(tmp_path), check_cmd=cmd)
    finally:
        assert agy.stop_group(rec["pgid"])
    flag.write_text("")
    r = runner.call("j1", "q", store=S(tmp_path), model="m", cwd=str(tmp_path), check_cmd=cmd)
    assert r.answer == "the reply" and len(launches()) == 1
    res = store.read_json(os.path.join(job.launched()[0], "result.json"))
    assert res["recovered"] is True and res["outcome"] == "accepted"


def _request_cancel(job):
    store.write_atomic(job.path("cancel.request"), "{}")
    return True


def test_cancel_request_stops_a_running_turn(tmp_path, fake, monkeypatch):
    set_turns, launches = fake
    set_turns(reply("late", sleep=30))
    monkeypatch.setattr(agy, "POLL", 0.1)
    job = store.Job(S(tmp_path), "j1")
    threading.Thread(target=lambda: wait_for(launches) and _request_cancel(job), daemon=True).start()
    t0 = time.monotonic()
    with pytest.raises(Cancelled) as e:
        runner.call("j1", "q", store=S(tmp_path), model="m")
    assert e.value.code == 27 and time.monotonic() - t0 < 20
    att = job.launched()[0]
    res = store.read_json(os.path.join(att, "result.json"))
    assert res["classified"] == "cancelled" and res["outcome"] == "cancelled" and job.status()["state"] == "cancelled"
    assert not agy.alive(store.read_json(os.path.join(att, "launch.json"))["pgid"])


def test_cancelled_job_stays_cancelled_and_launches_nothing(tmp_path, fake):
    set_turns, launches = fake
    set_turns(GOOD)
    job = store.Job(S(tmp_path), "j1")
    _request_cancel(job)
    for _ in range(2):
        with pytest.raises(Cancelled):
            runner.call("j1", "q", store=S(tmp_path), model="m")
    assert launches() == [] and job.status()["state"] == "cancelled"


def test_cancel_during_the_check_stops_it(tmp_path, fake, monkeypatch):
    set_turns, _ = fake
    set_turns(reply("x"))
    monkeypatch.setattr(agy, "POLL", 0.1)
    job = store.Job(S(tmp_path), "j1")
    checking = lambda: job.launched() and os.path.exists(os.path.join(job.launched()[0], "check.json"))
    threading.Thread(target=lambda: wait_for(checking) and _request_cancel(job), daemon=True).start()
    with pytest.raises(Cancelled):
        runner.call("j1", "q", store=S(tmp_path), model="m", check_cmd="sleep 30")
    att = job.launched()[0]
    assert store.read_json(os.path.join(att, "result.json"))["outcome"] == "cancelled"
    assert not agy.alive(store.read_json(os.path.join(att, "check.json"))["pgid"])
    assert not os.path.exists(job.path("answer.txt")) and job.status()["state"] == "cancelled"


def test_cancel_after_the_check_passes_saves_no_answer(tmp_path, fake):
    set_turns, _ = fake
    set_turns(reply("x"))
    req = os.path.join(S(tmp_path), "j1", "cancel.request")
    with pytest.raises(Cancelled):
        runner.call("j1", "q", store=S(tmp_path), model="m", check_cmd="touch " + shlex.quote(req))
    job = store.Job(S(tmp_path), "j1")
    assert not os.path.exists(job.path("answer.txt")) and job.status()["state"] == "cancelled"
    assert open(os.path.join(job.launched()[0], "reply.txt")).read() == "x"  # the reply is kept


def test_cancel_request_found_during_recovery(tmp_path, fake):
    set_turns, launches = fake
    set_turns(GOOD)
    job, att = _pending(tmp_path, _env("paid reply"))
    _request_cancel(job)
    with pytest.raises(Cancelled):
        runner.call("j1", "q", store=S(tmp_path), model="m")
    res = store.read_json(os.path.join(att, "result.json"))
    assert res["classified"] == "cancelled" and res["recovered"] is True and launches() == []
    assert open(os.path.join(att, "reply.txt")).read() == "paid reply"
