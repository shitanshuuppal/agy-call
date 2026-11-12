"""Launch one agy run (spec section 6): agy's stdout and stderr go straight into the attempt folder, the prompt is one
argument with stdin closed, and a deadline stops the whole process group, SIGTERM first and SIGKILL after KILL_AFTER."""
import dataclasses
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import time

from .errors import Fatal

MAX_PROMPT = 512 * 1024  # bytes of UTF-8
GRACE = 60               # seconds past agy's own --print-timeout before agy-call stops the run
KILL_AFTER = 10          # seconds between SIGTERM and SIGKILL
POLL = 1                 # seconds between checks while agy runs


@dataclasses.dataclass
class Run:
    exit: object     # agy's exit status, negative for a signal; None when agy-call stopped it
    stopped: object  # "deadline", or None
    out: bytes
    err: str


def path():
    p = os.environ.get("AGYCALL_AGY") or shutil.which("agy")
    if not p or not os.path.exists(p):
        raise Fatal(13, "agy not found (set AGYCALL_AGY or put agy on PATH)")
    return p


def seconds(t):
    """Seconds in a duration such as "90", "90s", "10m" or "1h30m"."""
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s?)?", str(t).strip())
    total = sum(int(x) * f for x, f in zip(m.groups(), (3600, 60, 1)) if x) if m else 0
    if not total:
        raise ValueError("timeout must look like 90s, 10m or 1h30m and be above zero: %r" % (t,))
    return total


def argv(agy, prompt, *, model, timeout, mode=None, dirs=(), conversation=None, settings=None, effort=None, json_schema=None):
    cmd = [agy, "--model", model, "--print-timeout", "%ds" % seconds(timeout), "--output-format", "json"]
    for flag, value in (("--settings", settings), ("--effort", effort), ("--json-schema", json_schema), ("--mode", mode)):
        if value:
            cmd += [flag, value]
    for d in dirs:
        cmd += ["--add-dir", d]
    if conversation:
        cmd += ["--conversation", conversation]
    return cmd + ["-p", prompt]


def redact(cmd):
    """The argument list with the prompt replaced by its size and hash; the prompt itself is kept in launch.json."""
    prompt = cmd[-1].encode("utf-8")
    return cmd[:-1] + ["<prompt: %d bytes, sha256 %s>" % (len(prompt), hashlib.sha256(prompt).hexdigest())]


def alive(pgid):
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def start_time(pid):
    try:
        return subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, timeout=5).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _stop(p):
    try:
        os.killpg(p.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    end = time.monotonic() + KILL_AFTER
    while time.monotonic() < end:
        p.poll()  # reap agy, or its exited leader keeps the group looking alive
        if not alive(p.pid):
            return
        time.sleep(0.1)
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def run(att, cmd, *, cwd, deadline, fds=(), on_start=None):
    with open(os.path.join(att, "agy.args"), "w", encoding="utf-8") as fh:
        json.dump(redact(cmd), fh)
    out_path, err_path = os.path.join(att, "agy.out"), os.path.join(att, "agy.err")
    with open(out_path, "wb") as out, open(err_path, "wb") as err:
        try:  # agy inherits the lock descriptors, so the job stays locked while agy lives
            p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=out, stderr=err, cwd=cwd,
                                 start_new_session=True, pass_fds=tuple(fds))
        except OSError as e:
            raise Fatal(13, "agy could not start: %s" % e)
    if on_start:
        on_start(p.pid, start_time(p.pid))
    end = time.monotonic() + deadline
    while True:
        try:
            rc, stopped = p.wait(timeout=POLL), None
            break
        except subprocess.TimeoutExpired:
            if time.monotonic() >= end:
                _stop(p)
                p.wait()
                rc, stopped = None, "deadline"
                break
    with open(out_path, "rb") as fh:
        data = fh.read()
    with open(err_path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    return Run(rc, stopped, data, text)
