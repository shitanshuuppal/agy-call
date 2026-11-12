import json
import os
import subprocess
import sys

import pytest

import agycall.batch  # noqa: F401
from agycall import quota, runner
from agycall.errors import Quota
from agyturns import quota_error, reply

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
batchmod = sys.modules["agycall.batch"]  # agycall.batch the attribute is the function, re-exported by __init__


def test_batch_stops_launching_after_quota(tmp_path, fake):
    set_turns, launches = fake
    set_turns(reply("a"), reply("b", "c2"), quota_error())
    jobs = [{"job": "j%d" % i, "prompt": "p%d" % i} for i in range(6)]
    res = batchmod.batch(jobs, workers=1, store=str(tmp_path / "s"), model="m")
    assert res["j0"].answer == "a" and res["j1"].answer == "b"
    assert isinstance(res["j2"], Quota)
    assert all(isinstance(res["j%d" % i], Quota) for i in range(3, 6))
    assert len(launches()) == 3  # j3..j5 never launched


def test_batch_returns_done_jobs_even_during_a_hold(tmp_path, fake):
    set_turns, launches = fake
    set_turns(reply("a"))
    batchmod.batch([{"job": "j0", "prompt": "p"}], store=str(tmp_path / "s"), model="m")
    quota.hold("m", "r", 600)
    res = batchmod.batch([{"job": "j0", "prompt": "p"}], store=str(tmp_path / "s"), model="m")
    assert res["j0"].answer == "a" and len(launches()) == 1


def run_cli(*args, input=None):
    return subprocess.run([sys.executable, os.path.join(ROOT, "bin", "agy-call"), *args], input=input, capture_output=True, text=True)


def test_cli_run_prints_answer_and_exit_codes(tmp_path, fake):
    set_turns, _ = fake
    set_turns(reply('{"answer": "B"}'))
    pf = tmp_path / "p.txt"
    pf.write_text("question")
    r = run_cli("run", "--job", "j1", "--store", str(tmp_path / "s"), "--model", "m", "--prompt-file", str(pf), "--expect", "json", "--require", "answer")
    assert r.returncode == 0 and json.loads(r.stdout) == {"answer": "B"}
    set_turns(quota_error("Resets in 5m"))
    r = run_cli("run", "--job", "j2", "--store", str(tmp_path / "s"), "--model", "m", input="q")
    assert r.returncode == 10 and "quota for m until" in r.stderr


def test_cli_batch_summary(tmp_path, fake):
    set_turns, _ = fake
    set_turns(reply("ok"))
    jf = tmp_path / "jobs.jsonl"
    jf.write_text("\n".join(json.dumps({"job": "j%d" % i, "prompt": "p"}) for i in range(3)))
    r = run_cli("batch", str(jf), "--store", str(tmp_path / "s"), "--model", "m", "--workers", "2")
    assert r.returncode == 0 and "done 3" in r.stdout


def test_batch_ignore_quota_launches_during_hold(tmp_path, fake):
    set_turns, launches = fake
    set_turns(reply("a"))
    quota.hold("m", "r", 600)
    res = batchmod.batch([{"job": "j0", "prompt": "p"}], store=str(tmp_path / "s"), model="m", ignore_quota=True)
    assert res["j0"].answer == "a" and len(launches()) == 1


def test_batch_bad_row_does_not_abort(tmp_path, fake):
    set_turns, launches = fake
    set_turns(reply("a"))
    res = batchmod.batch([{"job": "bad/id", "prompt": "p"}, {"job": "j1", "prompt": "p"}], workers=1, store=str(tmp_path / "s"), model="m")
    assert isinstance(res["bad/id"], ValueError) and res["j1"].answer == "a"


def test_batch_duplicate_ids_rejected(tmp_path, fake):
    set_turns, launches = fake
    with pytest.raises(ValueError, match="duplicate job ids in batch"):
        batchmod.batch([{"job": "j1", "prompt": "p"}, {"job": "j1", "prompt": "q"}], store=str(tmp_path / "s"), model="m")
    assert launches() == []


def test_cli_value_errors_exit_2(tmp_path, fake):
    r = run_cli("run", "--job", "j1", "--store", str(tmp_path / "s"), "--prompt", "q")
    assert r.returncode == 2 and "model is required" in r.stderr
    r = run_cli("run", "--job", "bad/id", "--store", str(tmp_path / "s"), "--model", "m", "--prompt", "q")
    assert r.returncode == 2 and "job id" in r.stderr


def test_cli_batch_exits_27_when_a_job_was_cancelled(tmp_path, fake):
    set_turns, launches = fake
    set_turns(reply("ok"))
    cancelled = tmp_path / "s" / "j0"
    cancelled.mkdir(parents=True)
    (cancelled / "cancel.request").write_text("{}")  # the runner stops before its first launch
    jf = tmp_path / "jobs.jsonl"
    jf.write_text(json.dumps({"job": "j0", "prompt": "p"}) + "\n")
    r = run_cli("batch", str(jf), "--store", str(tmp_path / "s"), "--model", "m")
    assert r.returncode == 27 and "Cancelled" in r.stdout and launches() == []
