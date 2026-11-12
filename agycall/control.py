"""Job control (spec 7): status, result and cancel for the jobs of a store, and the detached background worker."""
import contextlib
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import time

from . import agy, review, runner, task, worktree
from . import store as st
from .errors import AgyCallError

ACTIVE = ("queued", "running", "checking")
CODES = {"done": 0, "quota": 10, "failed": 20, "check-error": 21, "no-conversation": 23,
         "queued": 25, "running": 25, "checking": 25, "cancelled": 27, "blocked": 28}
CANCEL_WAIT = 20  # seconds cancel waits for the job to stop
ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))  # this checkout; the worker never uses PATH


def _job(store, job_id):
    job = st.Job(store, job_id)
    if not os.path.exists(job.path("request.json")):
        raise ValueError("no job %s in %s" % (job_id, store))
    return job


def _request(job):
    return st.read_json(job.path("request.json"), {}) or {}


def _session():
    return os.environ.get("AGYCALL_SESSION") or None


def jobs(store):
    """The store's jobs: those recorded with the current session (AGYCALL_SESSION) first, each group newest first."""
    names = os.listdir(store) if os.path.isdir(store) else []
    found = [st.Job(store, n) for n in names if st.JOB_ID.match(n) and os.path.exists(os.path.join(store, n, "request.json"))]
    found.sort(key=lambda j: _request(j).get("created") or "", reverse=True)
    return [j for j in found if _request(j).get("session") == _session()] + \
        [j for j in found if _request(j).get("session") != _session()]


def _mine(store):
    return [j for j in jobs(store) if _request(j).get("session") == _session()]


def live(job):
    """(what, pid) for each of the job's processes still running: its controller, then the newest attempt's agy and
    check command; pid is None when agy started but its pid was never recorded (spec 7.4)."""
    procs = []
    ctl = job.status().get("controller")
    if agy.owns(ctl):
        procs.append(("controller", ctl["pid"]))
    att = runner.pending(job)
    if att:
        launch = st.read_json(os.path.join(att, "launch.json"), {}) or {}
        if not launch.get("pid"):
            procs.append(("agy", None))
        elif agy.owns(launch):
            procs.append(("agy", launch["pid"]))
    atts = job.launched()
    check = st.read_json(os.path.join(atts[-1], "check.json")) if atts else None  # a check-error recheck has a result
    if agy.owns(check):
        procs.append(("check", check["pid"]))
    return procs


def _procs(procs):
    return ", ".join("%s pid %s" % (what, pid or "unknown") for what, pid in procs)


def _state(job):
    """The recorded state; for a queued, running or checking job also what still runs for it."""
    state = job.status().get("state") or "unknown"
    if state not in ACTIVE:
        return state
    return "%s (%s)" % (state, _procs(live(job)) or "no live process; rerun the job to recover it")


def _age(job):
    created = _request(job).get("created")
    if not created:
        return "?"
    secs = max(0, int((dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(created)).total_seconds()))
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if secs >= size:
            return "%d%s" % (secs // size, unit)
    return "%ds" % secs


def status(store, job_id=None):
    """(text, exit code) for `agy-call status [JOB]`."""
    if job_id:
        job = _job(store, job_id)
        lines = ["job: %s" % job.id, "state: %s" % _state(job)]
        lines += ["%s: %s" % (k, v if isinstance(v, str) else json.dumps(v))
                  for k, v in sorted(job.status().items()) if k != "state"]
        lines.append("processes: %s" % (_procs(live(job)) or "none"))
        return "\n".join(lines), 0
    rows = []
    for job in jobs(store):
        finding = ((job.status().get("findings") or [""])[-1] or "").replace("\n", " ")[:100]
        kind = _request(job).get("kind") or "run"
        rows.append(("%s  %s  %s  %s  %s" % (job.id, _state(job), kind, _age(job), finding)).rstrip())
    return "\n".join(rows) or "no jobs in %s" % store, 0


def status_all():
    """(text, exit code) for `agy-call status --all`: every store under ~/.agy-call/jobs, repository stores and
    namespace stores alike, one block per store that has jobs."""
    root = os.path.join(st.home(), "jobs")
    blocks = []
    for name in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        store = os.path.join(root, name)
        found = jobs(store)
        if not found:
            continue
        top = next(((_request(j).get("task") or _request(j).get("review") or {}).get("checkout") for j in found
                    if _request(j).get("kind") in ("task", "review")), None)
        head = "%s (repository %s)" % (store, top) if top else store
        blocks.append("\n".join([head] + ["  " + line for line in status(store)[0].splitlines()]))
    return "\n\n".join(blocks) or "no jobs in %s" % root, 0


def _newest_finished(store):
    done = [j for j in _mine(store) if os.path.exists(j.path("status.json")) and j.status().get("state") not in ACTIVE]
    if not done:
        raise ValueError("no finished job of this session in %s" % store)
    return max(done, key=lambda j: os.path.getmtime(j.path("status.json")))


def result(store, job_id=None):
    """(text, exit code) for `agy-call result [JOB]`; without JOB, the newest finished job of the current session."""
    job = _job(store, job_id) if job_id else _newest_finished(store)
    s = job.status()
    state = s.get("state")
    if state == "done" and os.path.exists(job.path("answer.txt")):
        text = review.render(job) if _request(job).get("kind") == "review" else runner._read(job.path("answer.txt"))
        code = 0
    else:
        lines = ["state: %s" % _state(job)] + ["finding: %s" % f for f in s.get("findings") or []]
        for att in reversed(job.launched()):
            reply = runner._saved_reply(att)
            if reply.strip():
                lines += ["last reply (attempt %s):" % att, reply]
                break
        text, code = "\n".join(lines), (s.get("code", 22) if state in ("fatal", "setup-error") else CODES.get(state, 1))
    if _request(job).get("kind") == "task":  # the hand-back of spec 4.8
        text = "\n".join([text.rstrip("\n")] + worktree.handback(job))
    return text, code


def _only_running(store):
    running = [j for j in _mine(store) if j.status().get("state") in ACTIVE or agy.owns(j.status().get("controller"))]
    if not running:
        raise ValueError("no job of this session is running in %s" % store)
    if len(running) > 1:
        raise ValueError("several jobs of this session are running; name one: %s" % ", ".join(sorted(j.id for j in running)))
    return running[0]


def _stop_orphans(job, before):
    """No controller is alive, so nothing will act on cancel.request: stop the newest attempt's check and agy process
    groups while they are still the recorded processes, record the attempt and the job as cancelled, and return the pid
    of a group that outlived SIGKILL, or "unknown" when the job lock is still held after every recorded process was
    stopped (a launch whose pid was never recorded), else None. In both cases the job's state is left as it was.
    `before` is the state cancel read first: a job that has an answer or has left that state for a non-active one
    finished meanwhile, and is returned as ("finished", state) with nothing changed. The check is made under the job
    lock, which stays held through the record updates."""
    if _finished(job, before):
        return "finished", job.status().get("state")
    atts = job.launched()
    att = runner.pending(job)
    recs = []
    if atts:  # the newest attempt's check, even when it has a result: a check-error recheck runs against that result
        recs.append(st.read_json(os.path.join(atts[-1], "check.json")))
    if att:
        recs.append(st.read_json(os.path.join(att, "launch.json")))
    for rec in recs:
        if agy.owns(rec) and not agy.stop_group(rec["pgid"]):
            return rec["pid"]
    try:
        with st.flock(job.lock_path, blocking=False):
            if _finished(job, before):
                return "finished", job.status().get("state")
            if att and not os.path.exists(os.path.join(att, "result.json")):  # a rerun that started meanwhile may have settled it
                st.write_atomic(os.path.join(att, "result.json"), {"exit": None, "outcome": "cancelled", "classified": "cancelled",
                                                                   "findings": [runner.CANCELLED], "ended": runner._now()})
            job.set_status(state="cancelled", findings=[runner.CANCELLED])
    except st.Busy:
        return "unknown"
    return None


def _finished(job, before):
    state = job.status().get("state")
    return os.path.exists(job.path("answer.txt")) or (state != before and state not in ACTIVE)


def cancel(store, job_id=None, wait=CANCEL_WAIT):
    """(text, exit code) for `agy-call cancel [JOB]`: write cancel.request without taking the job lock, then wait up to
    `wait` seconds for the job to stop. A job that is not running is left alone."""
    job = _job(store, job_id) if job_id else _only_running(store)
    s = job.status()
    if s.get("state") == "cancelled" or not (s.get("state") in ACTIVE or agy.owns(s.get("controller"))):
        return s.get("state") or "unknown", 0
    st.write_atomic(job.path("cancel.request"), {"requested": runner._now()})
    if not agy.owns(job.status().get("controller")):
        stuck = _stop_orphans(job, s.get("state"))
        if isinstance(stuck, tuple):
            with contextlib.suppress(FileNotFoundError):  # a concurrent cancel may have removed it
                os.unlink(job.path("cancel.request"))  # it must not cancel a later rerun of a finished job
            return "finished before cancel (%s)" % stuck[1], 0
        if stuck:
            return "still stopping (pid %s)" % stuck, 25
    end = time.monotonic() + wait
    while True:
        s = job.status()
        if s.get("state") == "cancelled":
            return "cancelled", 0
        if s.get("state") not in ACTIVE and not agy.owns(s.get("controller")):
            return "finished before cancel (%s)" % s.get("state"), 0
        if time.monotonic() >= end:
            procs = live(job)
            return "still stopping (pid %s)" % ((procs[-1][1] if procs else None) or "unknown"), 25
        time.sleep(0.2)


def start(job_id, prompt, **kw):
    """`run --background`: record the run as queued and start a detached worker for it (spec 7.1); returns the job id."""
    return spawn(runner.enqueue(job_id, prompt, **kw))


def spawn(job):
    """Start the detached worker of a queued job, run, task or review; returns the job id."""
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(p for p in (ROOT, os.environ.get("PYTHONPATH")) if p))
    exe = os.environ.get("AGYCALL_AGY") or shutil.which("agy")  # the worker starts in the job folder: make it absolute here
    if exe:
        env["AGYCALL_AGY"] = os.path.abspath(exe)
    with open(job.path("worker.log"), "ab") as log:
        proc = subprocess.Popen([sys.executable, "-m", "agycall.cli", "worker", "--store", job.store, "--job", job.id],
                                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, cwd=job.dir, env=env,
                                start_new_session=True)
    st.write_atomic(job.path("worker.json"), {"pid": proc.pid, "pstart": agy.start_time(proc.pid)})  # task --continue reads it
    return job.id


def _record_setup_failure(job, identity, finding, code):
    """Mark the job fatal, but only while it is still the queued request this worker was started for and nothing else
    holds it; a job another process is running or a newer version of the job is left alone."""
    try:
        with st.flock(job.lock_path, blocking=False):
            if _request(job).get("identity") == identity and job.status().get("state") == "queued" \
                    and not agy.owns(job.status().get("controller")):
                job.set_status(state="fatal", findings=[finding], code=code)
    except st.Busy:
        pass


def work(store, job_id):
    """The worker: run the saved request; the outcome is in status.json, and one line goes to worker.log."""
    job = st.Job(store, job_id)
    identity = _request(job).get("identity")
    args = st.read_json(job.path("background.json"))
    try:
        if args.get("kind") == "task":
            task.drive(job)
        elif args.get("kind") == "review":
            review.drive(job)
        else:
            runner.call(**args)
    except Exception as e:  # a start failure leaves the job queued: a schema file that has gone or changed
        code = e.code if isinstance(e, AgyCallError) else 2
        finding = str(e) if isinstance(e, AgyCallError) else "%s: %s" % (type(e).__name__, e)
        print("agy-call worker: %s" % finding, flush=True)
        _record_setup_failure(job, identity, finding, code)  # does nothing once the runner has taken the job on
        return code
    print("agy-call worker: done", flush=True)
    return 0
