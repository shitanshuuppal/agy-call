import json
import os
import subprocess
import sys

import pytest

from agycall import agy, control, runner, store
from agyturns import reply, wait_for

HERE = os.path.dirname(os.path.abspath(__file__))
AGYCALL = os.path.join(os.path.dirname(HERE), "bin", "agy-call")


def S(tmp_path):
    return str(tmp_path / "store")


def run_cli(*args, **kw):
    return subprocess.run([sys.executable, AGYCALL, *args], capture_output=True, text=True, timeout=60, **kw)


def test_background_run_finishes_in_a_detached_worker(tmp_path, fake):
    set_turns, launches = fake
    set_turns(reply('{"answer": "B"}', sleep=3))
    (tmp_path / "schema.json").write_text(json.dumps({"type": "object", "required": ["answer"]}))
    env = dict(os.environ, PATH="/usr/bin:/bin")  # no agy-call on the PATH: the worker must use this checkout
    r = run_cli("run", "--background", "--job", "j1", "--store", "s", "--cwd", str(tmp_path), "--schema", "schema.json",
                "--model", "m", "--prompt", "q", cwd=str(tmp_path), env=env)
    assert r.returncode == 0 and r.stdout == "j1\n"
    job = store.Job(str(tmp_path / "s"), "j1")
    try:
        assert job.status()["state"] in ("queued", "running")  # the command returned while the turn still runs
        wait_for(lambda: job.status().get("state") == "done")
    finally:
        run_cli("cancel", "j1", "--store", job.store)
    text, code = control.result(job.store, "j1")
    assert code == 0 and json.loads(text) == {"answer": "B"}
    bg = store.read_json(job.path("background.json"))
    assert os.path.realpath(bg["store"]) == os.path.realpath(job.store) and bg["cwd"] == str(tmp_path)
    assert os.path.realpath(bg["schema"]) == os.path.realpath(str(tmp_path / "schema.json"))
    wait_for(lambda: "agy-call worker: done" in open(job.path("worker.log")).read())
    again = runner.call("j1", "q", store=job.store, model="m", cwd=str(tmp_path), schema=str(tmp_path / "schema.json"))
    assert again.answer == {"answer": "B"} and len(launches()) == 1


def test_background_refuses_a_bad_request_before_writing_anything(tmp_path, fake):
    set_turns, launches = fake
    r = run_cli("run", "--background", "--job", "j1", "--store", S(tmp_path), "--prompt", "q")
    assert r.returncode == 2 and "model is required" in r.stderr
    (tmp_path / "bad.json").write_text(json.dumps({"type": "int"}))
    r = run_cli("run", "--background", "--job", "j1", "--store", S(tmp_path), "--model", "m", "--prompt", "q",
                "--schema", str(tmp_path / "bad.json"))
    assert r.returncode == 2 and "invalid schema" in r.stderr
    assert not os.path.exists(os.path.join(S(tmp_path), "j1")) and launches() == []
    with pytest.raises(ValueError, match="background"):
        runner.enqueue("j2", "q", store=S(tmp_path), model="m", check=lambda v: [], check_id="c:1")


def test_background_job_is_cancelled_through_the_cli(tmp_path, fake):
    set_turns, launches = fake
    set_turns(reply("late", sleep=30))
    assert run_cli("run", "--background", "--job", "j1", "--store", S(tmp_path), "--model", "m", "--prompt", "q").returncode == 0
    job = store.Job(S(tmp_path), "j1")
    try:
        wait_for(lambda: job.status().get("state") == "running" and launches())
        r = run_cli("cancel", "j1", "--store", S(tmp_path))
        assert r.returncode == 0 and r.stdout == "cancelled\n"
        wait_for(lambda: not agy.owns(job.status().get("controller")))  # the worker has exited
        assert run_cli("result", "j1", "--store", S(tmp_path)).returncode == 27
        wait_for(lambda: "was cancelled" in open(job.path("worker.log")).read())
    finally:
        run_cli("cancel", "j1", "--store", S(tmp_path))


def test_background_start_refuses_a_job_that_is_running(tmp_path, fake):
    set_turns, launches = fake
    set_turns(reply("late", sleep=30))
    args = ("run", "--background", "--job", "j1", "--store", S(tmp_path), "--model", "m", "--prompt", "q")
    assert run_cli(*args).returncode == 0
    try:
        wait_for(launches)
        r = run_cli(*args)
        assert r.returncode == 25 and "running" in r.stderr
    finally:
        run_cli("cancel", "j1", "--store", S(tmp_path))


def test_enqueue_normalises_require_and_refuses_unserialisable_arguments(tmp_path, fake):
    set_turns, launches = fake
    set_turns(reply('{"answer": "B"}'))
    job = runner.enqueue("j1", "q", store=S(tmp_path), model="m", cwd=str(tmp_path), require={"answer"})
    assert store.read_json(job.path("background.json"))["require"] == ["answer"]
    assert control.work(job.store, "j1") == 0 and job.status()["state"] == "done"
    with pytest.raises(ValueError, match="serial"):
        runner.enqueue("j2", "q", store=S(tmp_path), model="m", cwd=str(tmp_path), max_turns=object())
    assert not os.path.exists(os.path.join(S(tmp_path), "j2"))


def test_enqueue_stores_the_prepared_require_list_for_an_iterator(tmp_path, fake):
    set_turns, launches = fake
    set_turns(reply('{"answer": "B"}'))
    job = runner.enqueue("j1", "q", store=S(tmp_path), model="m", cwd=str(tmp_path), require=iter(["answer"]))
    assert store.read_json(job.path("background.json"))["require"] == ["answer"]
    assert control.work(job.store, "j1") == 0 and job.status()["state"] == "done"


def test_worker_records_a_setup_failure_instead_of_leaving_the_job_queued(tmp_path, fake):
    set_turns, launches = fake
    schema = tmp_path / "schema.json"
    schema.write_text(json.dumps({"type": "object"}))
    job = runner.enqueue("j1", "q", store=S(tmp_path), model="m", cwd=str(tmp_path), schema=str(schema))
    schema.unlink()  # preparation in the worker now fails before the runner records anything
    assert control.work(job.store, "j1") == 2
    s = job.status()
    assert s["state"] == "fatal" and s["code"] == 2 and "schema.json" in s["findings"][0]
    assert control.result(job.store, "j1")[1] == 2 and launches() == []
