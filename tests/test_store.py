import json
import os
import subprocess
import sys

import pytest

from agycall import store


def test_home_and_default_store(agy_home):
    assert store.home() == str(agy_home)
    assert store.store_dir() == os.path.join(str(agy_home), "jobs", "default")
    assert store.store_dir(namespace="flaws") == os.path.join(str(agy_home), "jobs", "flaws")
    assert store.store_dir("/x/y") == "/x/y"


def test_write_atomic_and_read_json(tmp_path):
    p = tmp_path / "a" / "b.json"
    store.write_atomic(str(p), {"x": 1})
    assert store.read_json(str(p)) == {"x": 1}
    store.write_atomic(str(p), "plain")
    assert p.read_text() == "plain"
    assert store.read_json(str(tmp_path / "missing.json"), {"d": 1}) == {"d": 1}
    assert not [n for n in os.listdir(p.parent) if n.startswith(".tmp-")]


def test_job_id_is_validated(tmp_path):
    with pytest.raises(ValueError):
        store.Job(str(tmp_path), "../escape")
    with pytest.raises(ValueError):
        store.Job(str(tmp_path), "x" * 121)
    store.Job(str(tmp_path), "k-a7a75c02.8643a5f3.turn1")


def test_lock_lives_outside_the_job_folder(tmp_path):
    job = store.Job(str(tmp_path), "j1")
    assert job.lock_path == os.path.join(str(tmp_path), ".locks", "j1.lock")
    assert not job.lock_path.startswith(job.dir + os.sep)


def test_attempt_numbers_come_from_the_directory(tmp_path):
    job = store.Job(str(tmp_path), "j1")
    a1 = job.new_attempt()
    a2 = job.new_attempt()
    assert os.path.basename(a1) == "001" and os.path.basename(a2) == "002"
    os.rename(a2, a2 + ".unlaunched")
    assert os.path.basename(job.new_attempt()) == "003"  # an unlaunched number is never reused
    assert [os.path.basename(a) for a in job.launched()] == ["001", "003"]


def test_status_merges(tmp_path):
    job = store.Job(str(tmp_path), "j1")
    job.set_status(state="running", attempts=1)
    job.set_status(state="done")
    assert job.status() == {"state": "done", "attempts": 1}


def test_archive_moves_to_versions(tmp_path):
    job = store.Job(str(tmp_path), "j1")
    job.new_attempt()
    v1 = job.archive()
    assert v1 == os.path.join(str(tmp_path), ".versions", "j1", "v1")
    assert not os.path.exists(job.dir)
    job.new_attempt()
    assert job.archive().endswith("v2")


def test_identity_ignores_nothing_it_should_not(tmp_path):
    base = dict(prompt="p", key="m", cwd="/a", dirs=["/d"], seed=None, check_identity={"json": True}, mode=None)
    one = store.identity(**base)
    assert one == store.identity(**base)
    for k, v in [("prompt", "q"), ("key", "n"), ("cwd", "/b"), ("dirs", ["/e"]), ("seed", "c1"), ("check_identity", {"json": False}), ("mode", "plan")]:
        assert store.identity(**dict(base, **{k: v})) != one, k


def test_flock_non_blocking_raises_busy(tmp_path):
    p = str(tmp_path / "l.lock")
    holder = subprocess.Popen([sys.executable, "-c",
                               "import fcntl,time,sys; f=open(sys.argv[1],'a'); fcntl.flock(f, fcntl.LOCK_EX); print('held', flush=True); time.sleep(5)", p],
                              stdout=subprocess.PIPE, text=True)
    assert holder.stdout.readline().strip() == "held"
    try:
        with pytest.raises(store.Busy):
            with store.flock(p, blocking=False):
                pass
    finally:
        holder.kill()
        holder.wait()
    with store.flock(p, blocking=False):
        pass


def test_attempt_1000(tmp_path):
    job = store.Job(str(tmp_path), "j1")
    os.makedirs(job.path("attempts", "999"))
    assert os.path.basename(job.new_attempt()) == "1000"
    assert os.path.basename(job.new_attempt()) == "1001"
    assert [os.path.basename(a) for a in job.launched()] == ["999", "1000", "1001"]


@pytest.mark.parametrize("bad", [".", "..", ".locks", ".versions", ".hidden", "-x", "_x"])
def test_job_id_first_char_must_be_alphanumeric(tmp_path, bad):
    with pytest.raises(ValueError):
        store.Job(str(tmp_path), bad)
