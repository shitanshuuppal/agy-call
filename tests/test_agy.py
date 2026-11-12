import os
import sys
import time

import pytest

from agycall import agy
from agycall.errors import Fatal


def script(tmp_path, body):
    p = tmp_path / "agy"
    p.write_text("#!%s\nimport os, signal, subprocess, sys, time\n%s\n" % (sys.executable, body))
    p.chmod(0o755)
    return str(p)


def attempt(tmp_path):
    att = tmp_path / "att"
    att.mkdir()
    return att


def test_seconds():
    assert agy.seconds("90") == 90 and agy.seconds("90s") == 90 and agy.seconds("10m") == 600
    assert agy.seconds("1h30m") == 5400 and agy.seconds(" 2h ") == 7200
    for bad in ("", "0", "s", "ten minutes", "5d", "10 m"):
        with pytest.raises(ValueError):
            agy.seconds(bad)


def test_argv_order_and_redaction():
    cmd = agy.argv("/x/agy", "hello", model="m", timeout="10m", mode="plan", dirs=["/d"], conversation="c1",
                   settings="/g.json", effort="high", json_schema="/s.json")
    assert cmd == ["/x/agy", "--model", "m", "--print-timeout", "600s", "--output-format", "json", "--settings", "/g.json",
                   "--effort", "high", "--json-schema", "/s.json", "--mode", "plan", "--add-dir", "/d",
                   "--conversation", "c1", "-p", "hello"]
    assert agy.argv("/x/agy", "q", model="m", timeout="90") == [
        "/x/agy", "--model", "m", "--print-timeout", "90s", "--output-format", "json", "-p", "q"]
    red = agy.redact(cmd)
    assert red[:-1] == cmd[:-1] and red[-1].startswith("<prompt: 5 bytes, sha256 2cf24dba")


def test_run_saves_streams_closes_stdin_and_reports_the_start(tmp_path):
    exe = script(tmp_path, "time.sleep(0.5); sys.stdout.write(repr(sys.stdin.read())); sys.stderr.write('E'); sys.exit(3)")
    att = attempt(tmp_path)
    seen = []
    r = agy.run(str(att), [exe, "-p", "secret"], cwd=str(tmp_path), deadline=30, on_start=lambda pid, when: seen.append((pid, when)))
    assert r.exit == 3 and r.stopped is None and r.out == b"''" and r.err == "E"
    assert (att / "agy.out").read_bytes() == b"''" and (att / "agy.err").read_text() == "E"
    assert "secret" not in (att / "agy.args").read_text()
    assert seen[0][0] > 0 and seen[0][1]


def test_deadline_stops_the_whole_process_group(tmp_path, monkeypatch):
    monkeypatch.setattr(agy, "POLL", 0.1)
    exe = script(tmp_path, "subprocess.Popen(['sleep', '30']); print('started', flush=True); time.sleep(30)")
    att = attempt(tmp_path)
    pids = []
    t0 = time.monotonic()
    r = agy.run(str(att), [exe, "-p", "x"], cwd=str(tmp_path), deadline=1, on_start=lambda pid, when: pids.append(pid))
    assert r.stopped == "deadline" and r.exit is None and time.monotonic() - t0 < 10
    assert r.out == b"started\n" and not agy.alive(pids[0])


def test_sigterm_ignored_gets_sigkill(tmp_path, monkeypatch):
    monkeypatch.setattr(agy, "POLL", 0.1)
    monkeypatch.setattr(agy, "KILL_AFTER", 1)
    exe = script(tmp_path, "signal.signal(signal.SIGTERM, signal.SIG_IGN); print('up', flush=True); time.sleep(30)")
    t0 = time.monotonic()
    r = agy.run(str(attempt(tmp_path)), [exe, "-p", "x"], cwd=str(tmp_path), deadline=1)
    assert r.stopped == "deadline" and time.monotonic() - t0 < 8


def test_missing_agy_is_fatal_13(tmp_path, monkeypatch):
    monkeypatch.setenv("AGYCALL_AGY", str(tmp_path / "nope"))
    with pytest.raises(Fatal) as e:
        agy.path()
    assert e.value.code == 13


def test_unstartable_file_is_fatal_13(tmp_path):
    bad = tmp_path / "agy"
    bad.write_text("not executable")
    with pytest.raises(Fatal) as e:
        agy.run(str(attempt(tmp_path)), [str(bad), "-p", "x"], cwd=str(tmp_path), deadline=5)
    assert e.value.code == 13


def test_failing_start_callback_stops_agy(tmp_path, monkeypatch):
    monkeypatch.setattr(agy, "POLL", 0.1)
    monkeypatch.setattr(agy, "KILL_AFTER", 1)
    exe = script(tmp_path, "time.sleep(30)")
    pids = []

    def boom(pid, when):
        pids.append(pid)
        raise OSError("disk full")

    t0 = time.monotonic()
    with pytest.raises(OSError):
        agy.run(str(attempt(tmp_path)), [exe, "-x", "x"], cwd=str(tmp_path), deadline=30, on_start=boom)
    assert time.monotonic() - t0 < 10 and not agy.alive(pids[0])
