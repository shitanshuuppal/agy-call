"""Launch one agy run (spec section 6): agy's stdout and stderr go straight into the attempt folder, the prompt is one
argument with stdin closed, and a deadline or a cancel request stops the whole process group, SIGTERM first and SIGKILL
after KILL_AFTER."""
import dataclasses
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time

from .errors import Fatal

MAX_PROMPT = 131071 if sys.platform.startswith("linux") else 512 * 1024  # Linux caps one argument at 32 pages including its NUL; macOS caps only the total
GRACE = 60               # seconds past agy's own --print-timeout before agy-call stops the run
KILL_AFTER = 10          # seconds between SIGTERM and SIGKILL
POLL = 1                 # seconds between checks while agy or a check command runs


@dataclasses.dataclass
class Run:
    exit: object     # agy's exit status, negative for a signal; None when agy-call stopped it
    stopped: object  # "deadline" or "cancel" when agy-call stopped it, else None
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


def owns(rec):
    """True while the process an ownership file records (its pid and start time) is still that same process."""
    return bool(rec and rec.get("pid") and rec.get("pstart")) and start_time(rec["pid"]) == rec["pstart"]


def stop_group(pgid, reap=None):
    """Stop a process group: SIGTERM, then SIGKILL after KILL_AFTER seconds. reap is called while waiting, so an exited
    child of ours does not keep its group looking alive. True once no member is left; False if one outlived SIGKILL by
    5 seconds."""
    for sig, patience in ((signal.SIGTERM, KILL_AFTER), (signal.SIGKILL, 5)):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return True
        end = time.monotonic() + patience
        while time.monotonic() < end:
            if reap:
                reap()
            if not alive(pgid):
                return True
            time.sleep(0.1)
    return False


def run(att, cmd, *, cwd, deadline, fds=(), on_start=None, should_stop=None):
    """Run agy until it exits, the deadline passes, or should_stop() returns "cancel" (polled every POLL seconds)."""
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
        try:
            on_start(p.pid, start_time(p.pid))
        except BaseException:  # without a recorded pid nothing can find this agy later
            stop_group(p.pid, p.poll)
            p.wait()
            raise
    end = time.monotonic() + deadline
    while True:
        try:
            rc, stopped = p.wait(timeout=POLL), None
            break
        except subprocess.TimeoutExpired:
            try:
                asked = should_stop() if should_stop else None
            except BaseException:  # a failing cancel check must not leave the group running
                stop_group(p.pid, p.poll)
                p.wait()
                raise
            if asked == "cancel":
                stopped = "cancel"
            elif time.monotonic() >= end:
                stopped = "deadline"
            else:
                continue
            stop_group(p.pid, p.poll)
            p.wait()
            rc = None
            break
    with open(out_path, "rb") as fh:
        data = fh.read()
    with open(err_path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    return Run(rc, stopped, data, text)
