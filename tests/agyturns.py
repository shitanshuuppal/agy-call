"""Builders for tests/fake_agy.py turns, one per shape of agy run the classifier knows, and a bounded wait for tests
that drive real processes, and git for tests that use a repository."""
import os
import signal
import subprocess
import threading
import time

CUT_OFF = ("Your previous response was cut off because it exceeded the output token limit\n"
           "Please continue from where you left off, keeping your response shorter\nRetries remaining: 3")
DENIED_NOTICE = ('jetski: no output produced, a tool required the "command" permission that headless mode cannot '
                 "prompt for, so it was auto-denied.\n")


def reply(text, conv="c1", **extra):
    return {"response": text, "conversation": conv, **extra}


def error_reply(text, error=CUT_OFF, conv="c1"):
    return {"exit": 1, "status": "ERROR", "response": text, "error": error, "conversation": conv}


def empty(conv=None):
    return {"response": "", "conversation": conv}


def denied(kind="command", conv="c1"):
    return {"response": "", "conversation": conv, "stderr": DENIED_NOTICE,
            "denied_actions": [{"action": kind, "display_name": "RunCommand"}]}


def partial(text, conv="c1"):
    return {"response": text, "conversation": conv, "stderr": "print timeout after 10m0s, returning partial output\n"}


def quota_error(reset="Resets in 10m"):
    return {"exit": 1, "status": "ERROR", "response": "", "error": "RESOURCE_EXHAUSTED: quota exceeded for this model. " + reset}


def auth_error():
    return {"exit": 1, "raw_hex": "", "stderr": "Error: unauthenticated; run agy once to sign in\n"}


def model_error(name="m"):
    return {"exit": 1, "raw_hex": "", "stderr": 'Error: invalid --model "%s"\n' % name}


def malformed(raw=b"", stderr="flag provided but not defined: -zzz\n", exit=2):
    return {"exit": exit, "raw_hex": raw.hex(), "stderr": stderr}


def wait_for(predicate, timeout=30):
    """Poll predicate until it returns something truthy and return that; fail after timeout seconds."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("condition not met within %s seconds" % timeout)


def _descendants():
    """The pids of every descendant of this process, from `ps`; children in their own sessions are included."""
    out = subprocess.run(["ps", "-axo", "pid=,ppid="], capture_output=True, text=True, timeout=30).stdout
    kids = {}
    for line in out.splitlines():
        pid, ppid = (int(x) for x in line.split())
        kids.setdefault(ppid, []).append(pid)
    found, todo = set(), [os.getpid()]
    while todo:
        for pid in kids.get(todo.pop(), []):
            if pid not in found:
                found.add(pid)
                todo.append(pid)
    return found


def _kill(pid, group):
    try:
        (os.killpg if group else os.kill)(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def bounded(fn, pids=(), timeout=30):
    """Run fn in a daemon thread and return its result (or re-raise its exception). When it has not returned after
    timeout seconds, kill every process group in pids (the list the test fills from on_start) and every descendant of
    the test process that did not exist before the call (its process group and itself), and fail the test. When fn
    raises, the same processes are killed before the exception is re-raised."""
    box = {}
    before = _descendants()

    def target():
        try:
            box["value"] = fn()
        except BaseException as e:
            box["error"] = e

    th = threading.Thread(target=target, daemon=True)
    th.start()
    th.join(timeout)

    def cleanup():
        for pid in list(pids):
            _kill(pid, True)
        for pid in _descendants() - before:
            _kill(pid, True)
            _kill(pid, False)

    if th.is_alive():
        cleanup()
        raise AssertionError("the launcher call did not return within %s seconds; its process groups were killed" % timeout)
    if "error" in box:
        cleanup()
        raise box["error"]
    return box.get("value")


def git(cwd, *args):
    """Run git in cwd for a test and return its stdout; a failure fails the test."""
    return subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True,
                          timeout=30).stdout
