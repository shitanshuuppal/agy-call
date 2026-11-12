"""Job control (spec 7.3): status, result and cancel for the jobs of a store."""
import datetime as dt
import json
import os
import time

from . import agy, runner
from . import store as st

ACTIVE = ("queued", "running", "checking")
CODES = {"done": 0, "quota": 10, "failed": 20, "check-error": 21, "no-conversation": 23,
         "queued": 25, "running": 25, "checking": 25, "cancelled": 27}
CANCEL_WAIT = 20  # seconds cancel waits for the job to stop


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
        check = st.read_json(os.path.join(att, "check.json"))
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
        rows.append(("%s  %s  %s  %s" % (job.id, _state(job), _age(job), finding)).rstrip())
    return "\n".join(rows) or "no jobs in %s" % store, 0


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
        return runner._read(job.path("answer.txt")), 0
    lines = ["state: %s" % _state(job)] + ["finding: %s" % f for f in s.get("findings") or []]
    for att in reversed(job.launched()):
        reply = runner._saved_reply(att)
        if reply.strip():
            lines += ["last reply (attempt %s):" % att, reply]
            break
    return "\n".join(lines), (s.get("code", 22) if state == "fatal" else CODES.get(state, 1))


def _only_running(store):
    running = [j for j in _mine(store) if j.status().get("state") in ACTIVE]
    if not running:
        raise ValueError("no job of this session is running in %s" % store)
    if len(running) > 1:
        raise ValueError("several jobs of this session are running; name one: %s" % ", ".join(sorted(j.id for j in running)))
    return running[0]


def _stop_orphans(job):
    """No controller is alive, so nothing will act on cancel.request: stop the newest attempt's check and agy process
    groups while they are still the recorded processes, record the attempt and the job as cancelled, and return the pid
    of a group that outlived SIGKILL (else None)."""
    att = runner.pending(job)
    if att:
        for name in ("check.json", "launch.json"):
            rec = st.read_json(os.path.join(att, name))
            if agy.owns(rec) and not agy.stop_group(rec["pgid"]):
                return rec["pid"]
        if not os.path.exists(os.path.join(att, "result.json")):  # a rerun that started meanwhile may have settled it
            st.write_atomic(os.path.join(att, "result.json"), {"exit": None, "outcome": "cancelled", "classified": "cancelled",
                                                               "findings": [runner.CANCELLED], "ended": runner._now()})
    job.set_status(state="cancelled", findings=[runner.CANCELLED])
    return None


def cancel(store, job_id=None, wait=CANCEL_WAIT):
    """(text, exit code) for `agy-call cancel [JOB]`: write cancel.request without taking the job lock, then wait up to
    `wait` seconds for the job to stop. A job that is not running is left alone."""
    job = _job(store, job_id) if job_id else _only_running(store)
    s = job.status()
    if s.get("state") == "cancelled" or not (s.get("state") in ACTIVE or agy.owns(s.get("controller"))):
        return s.get("state") or "unknown", 0
    st.write_atomic(job.path("cancel.request"), {"requested": runner._now()})
    if not agy.owns(job.status().get("controller")):
        stuck = _stop_orphans(job)
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
