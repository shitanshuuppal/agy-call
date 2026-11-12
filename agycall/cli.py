"""agy-call run [--background] | batch | task | status | result | cancel. Prints the accepted answer or the report on
stdout; errors on stderr; exit codes in SKILL.md."""
import argparse
import collections
import json
import sys

from . import control, task
from . import store as st
from .batch import batch
from .errors import AgyCallError, Cancelled, Quota, RepoState, Running
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
    kw = dict(conversation=a.conversation, new_version=a.new_version, **_opts(a))
    if a.background:
        print(control.start(a.job, prompt, **kw))
        return 0
    res = call(a.job, prompt, **kw)
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
    if any(isinstance(v, Quota) for v in res.values()):
        return 10
    return 27 if any(isinstance(v, Cancelled) for v in res.values()) else 20


def _task(a):
    """agy-call task: start or rerun a coding task in its own worktree (spec 4.3); prints the hand-back."""
    if not a.job or not a.prompt_file:
        raise ValueError("task needs --job and --prompt-file")
    with open(a.prompt_file, encoding="utf-8") as fh:
        text = fh.read()
    job = task.start(a.job, text, model=a.model, check_cmd=a.check_cmd, allow=a.allow, effort=a.effort,
                     check_timeout=a.check_timeout, timeout=a.timeout, max_turns=a.max_turns, deadline=a.deadline,
                     new_version=a.new_version)
    try:
        task.drive(job)
    except (Running, RepoState):  # nothing ran: the job is held elsewhere, or its worktree is gone
        raise
    except AgyCallError as e:  # a recorded failure: hand back the worktree, its branch and the last reply as well
        print("agy-call: %s" % e, file=sys.stderr)
    return control.result(job.store, job.id)


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
    r.add_argument("--background", action="store_true")
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
    t = sub.add_parser("task")
    t.add_argument("--job")
    t.add_argument("--prompt-file")
    t.add_argument("--model")
    t.add_argument("--effort")
    t.add_argument("--allow", action="append", default=[])
    t.add_argument("--check-cmd")
    t.add_argument("--check-timeout", type=float, default=600)
    t.add_argument("--timeout", default="60m")
    t.add_argument("--max-turns", type=int, default=3)
    t.add_argument("--deadline", default="2h")
    t.add_argument("--new-version", action="store_true")
    w = sub.add_parser("worker")  # started by run --background; not for direct use
    w.add_argument("--store", required=True)
    w.add_argument("--job", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "batch":
        return _batch(a)
    try:
        if a.cmd == "worker":
            return control.work(a.store, a.job)
        if a.cmd == "run":
            return _run(a)
        if a.cmd == "task":
            text, code = _task(a)
        else:
            text, code = getattr(control, a.cmd)(st.store_dir(a.store, a.namespace), a.job)
    except AgyCallError as e:
        print("agy-call: %s" % e, file=sys.stderr)
        return e.code
    except ValueError as e:  # a bad argument, job id, timeout, prompt size, or an unknown job
        print("agy-call: %s" % e, file=sys.stderr)
        return 2
    sys.stdout.write(text if text.endswith("\n") else text + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
