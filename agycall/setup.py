"""agy-call setup (spec 9): check that this machine can run agy-call, without sending a prompt or spending quota.
It prints one line per check, "ok" or "FAIL", and exits 0 only when every check passes.
Tests: uvx pytest tests/test_setup.py"""
import platform
import re
import subprocess
import sys

from . import agy
from .errors import Fatal

MIN_AGY = (1, 5, 6)
FLAGS = ("--settings", "--json-schema", "--effort", "--output-format")
MODELS = ("gemini-4-argon-high", "gemini-3.8-flash-low")  # the model names the skill and the slash commands use
TIMEOUT = 30  # seconds for each agy probe; `agy models` needs the network


def _probe(exe, *args):
    """(exit status, stdout, stderr) of one agy probe; the exit status is None, and stderr says why, when agy could
    not be started or gave no answer within TIMEOUT."""
    try:
        r = subprocess.run([exe, *args], stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8",
                           errors="replace", timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return None, "", "no answer within %ds" % TIMEOUT
    except OSError as e:
        return None, "", "could not start it: %s" % e
    return r.returncode, r.stdout, r.stderr


def _said(code, out, err):
    """The last line agy printed, or its exit status, for a FAIL line."""
    text = (err.strip() or out.strip()).splitlines()
    return text[-1][:200] if text else "exit status %s" % code


def check():
    """(report, exit code) for `agy-call setup`."""
    lines = []

    def line(ok, text):
        lines.append(("ok   " if ok else "FAIL ") + text)

    line(sys.version_info >= (3, 9), "Python %s (3.9 or newer)" % platform.python_version())
    line(sys.platform == "darwin" or sys.platform.startswith("linux"), "operating system %s (macOS or Linux)" % sys.platform)
    try:
        exe = agy.path()
    except Fatal as e:
        line(False, e.reason)
        return "\n".join(lines), 1
    line(True, "agy found at %s" % exe)

    code, out, err = _probe(exe, "--version")
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", out) if code == 0 else None
    if m:
        version = tuple(int(x) for x in m.groups())  # compared as numbers, so 1.10.0 is newer than 1.5.6
        line(version >= MIN_AGY, "agy version %d.%d.%d (1.5.6 or newer)" % version)
    else:
        line(False, "agy --version: %s" % _said(code, out, err))

    code, out, err = _probe(exe, "--help")  # agy prints its help on stderr
    missing = [f for f in FLAGS if not re.search(r"(?<![\w-])%s(?![\w-])" % re.escape(f), out + err)]
    if code is None:
        line(False, "agy --help: %s" % err)
    else:
        line(not missing, "agy --help lists %s" % ", ".join(FLAGS) if not missing
             else "agy --help does not list %s; update agy" % ", ".join(missing))

    code, out, err = _probe(exe, "models")
    if code != 0:
        line(False, "agy models (needs a signed-in agy): %s" % _said(code, out, err))
    else:
        line(True, "agy models (agy is signed in)")
        listed = {row.split()[0] for row in out.splitlines() if row.split()}
        for name in MODELS:
            line(name in listed, "model %s %s" % (name, "listed" if name in listed else "not listed by agy models"))
    return "\n".join(lines), 0 if all(row.startswith("ok") for row in lines) else 1
