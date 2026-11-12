"""agy-call run | batch | status | result | cancel. Prints the accepted answer or the report on stdout; errors on
stderr; exit codes in SKILL.md."""
import argparse
import collections
import json
import sys

from . import control
from . import store as st
from .batch import batch
from .errors import AgyCallError, Quota
from .runner import call


def _common(p):
    p.add_argument("--store")
    p.add_argument("--namespace")
    p.add_argument("--model")
    p.add_argument("--timeout", default="10m")
    p.add_argument("--cwd")
    p.add_argument("--dir", action="append", default=[], dest="dirs")
    p.add_argument("--mode")
    p.add_argument("--expect", choices=["json"])
    p.add_argument("--require", type=lambda s: [k for k in s.split(",") if k])
    p.add_argument("--schema")
    p.add_argument("--check-cmd")
    p.add_argument("--check-timeout", type=float, default=120)
    p.add_argument("--max-turns", type=int, default=4)
    p.add_argument("--ignore-quota", action="store_true")
    p.add_argument("--fresh", action="store_true")


def _opts(a):
    keys = ["store", "namespace", "model", "timeout", "cwd", "dirs", "mode", "expect", "require", "schema",
            "check_cmd", "check_timeout", "max_turns", "ignore_quota", "fresh"]
    return {k: getattr(a, k) for k in keys}


def _run(a):
    if a.prompt is not None:
        prompt = a.prompt
    elif a.prompt_file:
        with open(a.prompt_file, encoding="utf-8") as fh:
            prompt = fh.read()
    else:
        prompt = sys.stdin.read()
    res = call(a.job, prompt, conversation=a.conversation, new_version=a.new_version, **_opts(a))
    sys.stdout.write(res.text if res.text.endswith("\n") else res.text + "\n")
    return 0


def _batch(a):
    with open(a.jobs_file, encoding="utf-8") as fh:
        jobs = [json.loads(l) for l in fh if l.strip()]
    try:
        res = batch(jobs, workers=a.workers, **_opts(a))
    except ValueError as e:  # duplicate job ids
        print("agy-call: %s" % e, file=sys.stderr)
        return 2
    states = collections.Counter("done" if not isinstance(v, Exception) else type(v).__name__ for v in res.values())
    for s, n in sorted(states.items()):
        print("%s %d" % (s, n))
    for job_id, v in res.items():
        if isinstance(v, Exception):
            print("  %s: %s" % (job_id, v))
    if all(not isinstance(v, Exception) for v in res.values()):
        return 0
    return 10 if any(isinstance(v, Quota) for v in res.values()) else 20


def main(argv):
    ap = argparse.ArgumentParser(prog="agy-call", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--job", required=True)
    src = r.add_mutually_exclusive_group()
    src.add_argument("--prompt-file")
    src.add_argument("--prompt")
    r.add_argument("--conversation")
    r.add_argument("--new-version", action="store_true")
    _common(r)
    b = sub.add_parser("batch")
    b.add_argument("jobs_file")
    b.add_argument("--workers", type=int, default=4)
    _common(b)
    for name in ("status", "result", "cancel"):
        p = sub.add_parser(name)
        p.add_argument("job", nargs="?")
        p.add_argument("--store")
        p.add_argument("--namespace")
    a = ap.parse_args(argv)
    if a.cmd == "batch":
        return _batch(a)
    try:
        if a.cmd == "run":
            return _run(a)
        text, code = getattr(control, a.cmd)(st.store_dir(a.store, a.namespace), a.job)
    except AgyCallError as e:
        print("agy-call: %s" % e, file=sys.stderr)
        return e.code
    except ValueError as e:  # a bad argument, job id, timeout, prompt size, or an unknown job
        print("agy-call: %s" % e, file=sys.stderr)
        return 2
    sys.stdout.write(text if text.endswith("\n") else text + "\n")
    return code
