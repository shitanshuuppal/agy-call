import glob
import os
import stat
import subprocess
import sys

import pytest

from agycall import shim

HERE = os.path.dirname(os.path.abspath(__file__))
DELEGATES = sorted(glob.glob(os.path.expanduser("~/.claude/plugins/cache/antigravity-for-claude-code/antigravity/*/scripts/agy-delegate.sh")))


def fake_agy(tmp_path):
    p = tmp_path / "realbin" / "agy"
    p.parent.mkdir()
    p.write_text("#!%s\n" % sys.executable + open(os.path.join(HERE, "fake_agy.py")).read())
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


def test_shim_tees_streams_and_keeps_exit_code(tmp_path):
    real = fake_agy(tmp_path)
    bin_dir = shim.install(str(tmp_path / "shimbin"))
    attempt = tmp_path / "att"
    attempt.mkdir()
    env = shim.env(dict(os.environ, FAKE_AGY_MODE="fail"), bin_dir, str(attempt), real)
    r = subprocess.run(["agy", "-p", "hello"], env=env, capture_output=True, text=True)
    assert r.returncode == 1
    assert "PAID REPLY TEXT 12345" in r.stdout  # passed through unchanged
    assert (attempt / "agy-1.out").read_text() == "PAID REPLY TEXT 12345\n"
    assert "fake agy stderr line" in (attempt / "agy-1.err").read_text()
    assert '"-p"' in (attempt / "agy-1.args").read_text()
    subprocess.run(["agy", "--version"], env=env, capture_output=True)
    assert (attempt / "agy-2.out").read_text().strip() == "1.5.3"  # each invocation gets its own set


def test_shim_without_attempt_folder_still_runs(tmp_path):
    real = fake_agy(tmp_path)
    bin_dir = shim.install(str(tmp_path / "shimbin"))
    env = shim.env(dict(os.environ), bin_dir, str(tmp_path / "does-not-exist"), real)
    r = subprocess.run(["agy", "-p", "x"], env=env, capture_output=True, text=True)
    assert r.returncode == 0 and "PAID REPLY TEXT" in r.stdout
    assert "cannot save agy streams" in r.stderr


@pytest.mark.skipif(not DELEGATES, reason="agy-delegate plugin not installed")
def test_real_delegate_discards_reply_but_shim_keeps_it(tmp_path):
    real = fake_agy(tmp_path)
    bin_dir = shim.install(str(tmp_path / "shimbin"))
    attempt = tmp_path / "att"
    attempt.mkdir()
    env = shim.env(dict(os.environ, FAKE_AGY_MODE="fail", CLAUDE_PLUGIN_OPTION_STRUCTURED_OUTPUT="on"), bin_dir, str(attempt), real)
    r = subprocess.run(["bash", DELEGATES[-1], "--timeout", "1m", "-"], input="task", env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode != 0
    assert "PAID REPLY TEXT 12345" not in r.stdout  # the delegate threw the reply away
    kept = "".join(open(p).read() for p in glob.glob(str(attempt / "agy-*.out")))
    assert "PAID REPLY TEXT 12345" in kept  # the shim saved it


def test_killed_shim_loses_no_bytes(tmp_path):
    import signal
    import time
    real = fake_agy(tmp_path)
    bin_dir = shim.install(str(tmp_path / "shimbin"))
    attempt = tmp_path / "att"
    attempt.mkdir()
    env = shim.env(dict(os.environ, FAKE_AGY_MODE="hang"), bin_dir, str(attempt), real)
    p = subprocess.Popen(["agy", "-p", "x"], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    time.sleep(1)
    os.killpg(p.pid, signal.SIGTERM)
    p.wait(timeout=10)
    assert (attempt / "agy-1.out").read_text() == "PAID REPLY TEXT 12345\n"


def test_shim_falls_back_when_a_stream_file_cannot_open(tmp_path):
    real = fake_agy(tmp_path)
    bin_dir = shim.install(str(tmp_path / "shimbin"))
    attempt = tmp_path / "att"
    attempt.mkdir()
    (attempt / "agy-1.out").mkdir()  # the args file opens, the .out file cannot
    env = shim.env(dict(os.environ, FAKE_AGY_MODE="fail"), bin_dir, str(attempt), real)
    r = subprocess.run(["agy", "-p", "x"], env=env, capture_output=True, text=True)
    assert r.returncode == 1 and "PAID REPLY TEXT 12345" in r.stdout
    assert "cannot save agy streams" in r.stderr
