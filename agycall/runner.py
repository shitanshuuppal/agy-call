"""One agy-call job: launch agy, check the reply, follow up in the same conversation."""
import contextlib
import dataclasses
import datetime as dt
import glob
import hashlib
import json
import os
import time
import types

from . import agy, checks, classify, quota
from . import store as st
from .errors import Cancelled, CheckError, Fatal, GaveUp, Mismatch, NoConversation, Quota, Running

RETRY_WAIT = 30
FIX = ("Your last reply could not be used. Problems: {findings}. "
       "Reply again with the complete answer in the required format; keep the work you already did.")
RESTART = ("Your last turn was cut off before it finished. Write your complete answer again from the beginning, "
           "in full, in the required format; do not continue mid-sentence.")
DENIED = ("Your last tool call was denied by the permission policy. Do not repeat that call; "
          "continue the task with the tools you are allowed, and give the complete answer.")
LOST = "\n\nThe original request was:\n\n{prompt}"
FIRST_LIKE = ("first", "retry-first", "fresh")
AGY_ERRORS = ("reply-with-error", "output-limit")  # a reply that came with an agy error: only a check accepts it, no follow-up
CANCELLED = "cancelled by request"


@dataclasses.dataclass
class Result:
    job: str
    answer: object
    text: str
    conversation: str
    attempts: int
    usage: dict
    dir: str


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def conv_lock(conversation):
    return os.path.join(st.home(), "locks", "conv-%s.lock" % hashlib.sha256(conversation.encode()).hexdigest()[:32])


def parse_usage(err_text):
    for line in reversed(err_text.splitlines()):
        if line.startswith("AGY_USAGE "):
            try:
                return json.loads(line[len("AGY_USAGE "):])
            except ValueError:
                return {}
    return {}


def _cancel_requested(job):
    return os.path.exists(job.path("cancel.request"))


def _cancel(c, att=None, res=None):
    """Stop the job for a cancel request: record it as the attempt's outcome when there is one, set state cancelled,
    raise Cancelled. Saved replies stay where they are."""
    if att is not None:
        st.write_atomic(os.path.join(att, "result.json"), dict(res or {}, outcome="cancelled", findings=[CANCELLED]))
    c.job.set_status(state="cancelled", findings=[CANCELLED])
    raise Cancelled("job %s was cancelled; its attempts are kept in %s" % (c.job.id, c.job.path("attempts")))


def pending(job):
    """The newest attempt folder when it has a launch.json and no result.json (its controller stopped before settling
    it), else None."""
    atts = job.launched()
    if atts and os.path.exists(os.path.join(atts[-1], "launch.json")) and not os.path.exists(os.path.join(atts[-1], "result.json")):
        return atts[-1]
    return None


def recover(job):
    """First thing under the job lock: set aside an attempt that never launched, and raise Running while a recorded agy
    or check process of the newest attempt is alive. Settling that attempt waits until the request is known (_drive)."""
    atts = job.launched()
    if atts and not os.path.exists(os.path.join(atts[-1], "launch.json")):
        os.rename(atts[-1], atts[-1] + ".unlaunched")
        return
    att = pending(job)
    if not att:
        return
    for name, doing in (("launch.json", "running"), ("check.json", "checking")):
        rec = st.read_json(os.path.join(att, name), {}) or {}
        if rec.get("pgid") and agy.alive(rec["pgid"]):
            raise Running("job %s is %s (pid %s)" % (job.id, doing, rec.get("pid")))


def _usage(job):
    total = {}
    for a in job.launched():
        for k, v in ((st.read_json(os.path.join(a, "result.json"), {}) or {}).get("usage") or {}).items():
            if isinstance(v, (int, float)):
                total[k] = total.get(k, 0) + v
    return total


def _result(job, want_json):
    text = _read(job.path("answer.txt"))
    return Result(job=job.id, answer=json.loads(text) if want_json else text, text=text,
                  conversation=job.status().get("conversation"), attempts=len(job.launched()), usage=_usage(job), dir=job.dir)


def _next(prompt, last, launch):
    if last is None:
        return prompt, "first"
    outcome = last.get("outcome")
    if outcome in ("retry", "quota", "fatal"):  # agy never gave a usable turn: send the same message again
        return launch["message"], "retry-first" if launch.get("kind") in FIRST_LIKE else "retry"
    if outcome in ("timeout", "interrupted"):
        return RESTART, "restart"
    if outcome == "denied":
        return DENIED, "denied"
    msg = FIX.format(findings="; ".join(last.get("findings") or ["the reply was not usable"]))
    if last.get("lostContext"):
        msg += LOST.format(prompt=prompt)
    return msg, "fix"


def _agy_runs(att):
    """The stdout of each captured agy invocation of this attempt, in order, skipping help probes."""
    for path in sorted(glob.glob(os.path.join(att, "agy-*.out")), key=lambda p: int(p.rsplit("-", 1)[1][:-4])):
        try:
            args = json.loads(_read(path[:-4] + ".args"))
        except ValueError:
            args = []
        if not (any(a in ("--help", "-h") for a in args) and not any(a in ("-p", "--print", "--prompt") for a in args)):
            yield _read(path)


def _envelope(att):
    try:
        with open(os.path.join(att, "agy.out"), "rb") as fh:
            return classify.envelope(fh.read())
    except OSError:
        return None


def _saved_conversation(att):
    """The conversation id an attempt saw; delegate.err holds it for attempts made before agy-call launched agy itself."""
    return (_envelope(att) or {}).get("conversation_id") or \
        parse_usage(_read(os.path.join(att, "delegate.err"))).get("conversation_id") or None


def _saved_reply(att):
    """The reply text an attempt received, in the new layout (reply.txt) or the old one (delegate.out, agy-N.out)."""
    return _read(os.path.join(att, "reply.txt")) or _read(os.path.join(att, "delegate.out")) or _agy_reply(att) or ""


def _agy_output(att):
    """True when an agy run of this attempt produced output that may belong to a conversation we did not capture."""
    if _read(os.path.join(att, "agy.out")).strip():
        return _envelope(att) is None  # a parsed envelope already told us its conversation id
    return any(out.strip() for out in _agy_runs(att))


def _agy_reply(att):
    """The reply in agy's JSON envelope; agy-delegate drops it when agy exits non-zero after writing one."""
    for out in reversed(list(_agy_runs(att))):
        try:
            envelope = json.loads(out)
        except ValueError:
            continue
        if isinstance(envelope, dict) and isinstance(envelope.get("response"), str) and envelope["response"].strip():
            return envelope["response"]
    return None


def _recovered(job, att, timeout):
    """The outcome of an attempt whose controller stopped before settling it, from what the attempt saved (spec 7.4),
    or None when the saved output does not settle it. agy has exited: it held the job lock, which we now hold."""
    try:
        with open(os.path.join(att, "agy.out"), "rb") as fh:
            out = fh.read()
    except OSError:  # the old attempt layout, or a crash before agy started: nothing to classify
        return None
    if _cancel_requested(job):
        stopped = "cancel"
    elif classify.envelope(out) is not None:
        stopped = None
    else:
        started = (st.read_json(os.path.join(att, "launch.json"), {}) or {}).get("started")
        end = dt.datetime.fromisoformat(started) + dt.timedelta(seconds=agy.seconds(timeout) + agy.GRACE) if started else None
        if not end or end > dt.datetime.now(dt.timezone.utc):
            return None
        stopped = "deadline"
    oc = classify.classify(None, out, _read(os.path.join(att, "agy.err")), stopped)
    if stopped == "deadline":
        return dataclasses.replace(oc, reason="agy-call stopped before agy finished, and the turn's deadline has passed")
    if oc.name == "reply-with-error" and oc.status == "SUCCESS":
        return dataclasses.replace(oc, reason="agy-call stopped before it recorded agy's exit status")
    return oc


def _launch(c, att, message, kind, generation):
    cmd = agy.argv(agy.path(), message, model=c.model, timeout=c.timeout, mode=c.mode, dirs=c.dirs, conversation=c.conv)
    launch = {"pid": None, "pgid": None, "kind": kind, "message": message, "conversation": c.conv,
              "generation": generation, "started": _now(), "argv": agy.redact(cmd)}
    st.write_atomic(os.path.join(att, "launch.json"), launch)

    def started(pid, when):
        launch.update(pid=pid, pgid=pid, pstart=when)
        st.write_atomic(os.path.join(att, "launch.json"), launch)

    r = agy.run(att, cmd, cwd=c.cwd, deadline=agy.seconds(c.timeout) + agy.GRACE, fds=c.fds, on_start=started,
                should_stop=c.should_stop)
    return classify.classify(r.exit, r.out, r.err, r.stopped)


def _hold(c, conv):
    """Make conv the job's conversation and hold its lock until the job ends, so two turns never share a conversation."""
    c.conv = conv
    c.fds.append(c.stack.enter_context(st.flock(conv_lock(conv))).fileno())
    c.job.set_status(conversation=conv)


def _check(c, att, text, res):
    """Run the caller's check on a reply in the job's cwd; a check command records its process in check.json, the job
    shows `checking` while it runs, and a cancel request stops it and is recorded in res."""
    def started(pid, when):
        st.write_atomic(os.path.join(att, "check.json"), {"pid": pid, "pgid": pid, "pstart": when, "started": _now()})

    if c.cident["cmd"]:
        c.job.set_status(state="checking")  # before the command starts: it may read the status at once
    try:
        return c.checker(text, cwd=c.cwd, fds=c.fds, on_start=started, should_stop=c.should_stop)
    except Cancelled:
        _cancel(c, att, res)
    except CheckError:
        if _cancel_requested(c.job):  # a pending cancel request wins over a check error
            _cancel(c, att, res)
        raise


def _settle(c, att, oc, recovered=False):
    """Act on one attempt's outcome, whether its run just ended or a rerun recovered it from the saved output: return a
    Result when the job is done, None when the loop should send the next turn, and raise for every stop."""
    job = c.job
    launch = st.read_json(os.path.join(att, "launch.json"), {}) or {}
    if oc.response:
        st.write_atomic(os.path.join(att, "reply.txt"), oc.response)
    got = oc.conversation
    res = {"exit": oc.exit, "agyStatus": oc.status, "classified": oc.name, "conversation": got,
           "usage": oc.usage, "ended": _now()}
    if recovered:
        res["recovered"] = True

    def finish(state=None, **extra):
        res.update(extra)
        st.write_atomic(os.path.join(att, "result.json"), res)
        if state:
            job.set_status(state=state, findings=res.get("findings", []))

    def give_up(findings):
        finish("failed", outcome="failed", findings=findings)
        raise GaveUp(findings, c.conv, _usage(job), job.dir)

    if oc.name == "cancelled":
        _cancel(c, att, res)
    resumed = launch.get("conversation")
    if got and resumed and got != resumed:  # spec 7.2: a turn answered in another conversation stops the job
        res["conversationConflict"] = got
        give_up(["agy answered in conversation %s instead of %s" % (got, resumed)])
    if got and not c.conv:
        _hold(c, got)
    name = oc.name
    if name == "reply" or (name in AGY_ERRORS and oc.response.strip()):
        if name != "reply":
            res["agyError"] = oc.reason
            if not c.checked:
                give_up(["%s; the reply is saved in %s and is accepted after an agy error only by a check you ask for"
                         % (oc.reason, os.path.join(att, "reply.txt"))])
        try:
            verdict = _check(c, att, oc.response, res)
        except CheckError as e:
            finish("check-error", outcome="check-error", findings=[str(e)])
            raise
        if verdict.ok:
            if _cancel_requested(job):  # the last cancel point: nothing is saved as done after a request
                _cancel(c, att, res)
            st.write_atomic(job.path("answer.txt"), verdict.answer)  # the answer first: its presence means done
            finish(outcome="accepted")
            job.set_status(state="done", answer=True, conversation=c.conv, findings=[])
            if launch.get("generation") is not None:
                quota.clear(c.key, launch["generation"])
            return _result(job, c.cident["json"])
        if name != "reply":  # later turns in such a conversation failed the same way: send nothing more
            give_up(verdict.findings + ["no follow-up was sent because agy reported an error: %s" % oc.reason])
        finish(outcome="rejected", findings=verdict.findings, lostContext=verdict.lost_context)
    elif name == "output-limit":
        give_up(["agy stopped at its output token limit without a reply; its output is saved in %s. "
                 "Ask for the work as files on disk and a short reply" % att])
    elif name == "timeout":
        finish(outcome="timeout", partial=bool(oc.response.strip()))
    elif name == "denied":
        earlier = [st.read_json(os.path.join(a, "launch.json"), {}) or {} for a in job.launched() if a != att]
        if any(l.get("kind") == "denied" for l in earlier):
            give_up(["a tool call was denied twice"])
        finish(outcome="denied")
    elif name == "empty":
        if launch.get("kind") in ("retry", "retry-first"):
            finish(outcome="rejected", findings=[oc.reason])
        else:
            finish(outcome="retry", findings=[oc.reason])
            time.sleep(RETRY_WAIT)
    elif name == "quota":
        streams = [_read(os.path.join(att, "agy.err")), _read(os.path.join(att, "agy.out"))]
        until = quota.hold(c.key, oc.reason, quota.parse_reset(streams) or quota.DEFAULT_HOLD)
        finish("quota", outcome="quota", until=until.isoformat())
        raise Quota(c.key, until)
    else:  # auth, model, malformed
        reason = oc.reason
        if name == "malformed":
            reason += "; the raw output is in %s" % os.path.join(att, "agy.out")
        code = {"auth": 11, "model": 14}.get(name, 22)
        finish("fatal", outcome="fatal", findings=[reason])
        job.set_status(code=code)
        raise Fatal(code, reason)
    return None


def _prepare(job_id, prompt, *, store=None, namespace=None, model=None, timeout="10m", cwd=None, dirs=(), mode=None,
             conversation=None, expect=None, require=None, schema=None, check_cmd=None, check_timeout=120, check=None,
             check_id=None):
    """Check a request before anything is written, and return the job's working state."""
    if not model:
        raise ValueError("model is required: pass the exact agy model name, e.g. gemini-4-argon-high")
    size = len(prompt.encode("utf-8"))
    job = st.Job(st.store_dir(store, namespace), job_id)
    if size > agy.MAX_PROMPT and not os.path.exists(job.path("answer.txt")):  # a saved answer is still returned
        raise ValueError("the prompt is %d bytes; agy takes it as one argument, and this system's limit is %d bytes: "
                         "put the material in a file under the working directory and refer to it" % (size, agy.MAX_PROMPT))
    agy.seconds(timeout)  # a bad timeout fails here, before any attempt folder exists
    checker, cident = checks.make_check(expect, require, schema, check_cmd, check_timeout, check, check_id)
    cwd = os.path.abspath(cwd or os.getcwd())
    dirs = [os.path.abspath(d) for d in dirs]
    c = types.SimpleNamespace(
        job=job, prompt=prompt, model=model, key=model, timeout=timeout, mode=mode, cwd=cwd, dirs=dirs,
        seed=conversation, checker=checker, cident=cident,
        checked=any(cident[k] for k in ("json", "require", "schema", "cmd")) or check is not None,
        ident=st.identity(prompt, model, cwd, dirs, conversation, cident, mode), conv=None, fds=[], stack=None)
    c.should_stop = lambda: "cancel" if _cancel_requested(job) else None
    return c


def _admit(c, stack, new_version):
    """Take the job lock (held until stack closes), refuse or archive a changed request, and write request.json for a
    new one."""
    job = c.job
    c.stack = stack
    try:
        c.fds.append(stack.enter_context(st.flock(job.lock_path, blocking=False)).fileno())
    except st.Busy:
        raise Running("job %s is running in another process" % job.id)
    recover(job)  # before any archive: a live child must never lose its folder
    req = st.read_json(job.path("request.json"))
    if req and req.get("identity") != c.ident:
        if not new_version:
            raise Mismatch("job %s exists with a different request; pass new_version to replace it (%s)" % (job.id, job.dir))
        job.archive()
        req = None
    if not req:
        os.makedirs(job.dir, exist_ok=True)
        st.write_atomic(job.path("request.json"), {"identity": c.ident, "prompt": c.prompt, "key": c.key, "cwd": c.cwd,
                                                   "dirs": c.dirs, "seed": c.seed, "check": c.cident, "mode": c.mode,
                                                   "created": _now(), "session": os.environ.get("AGYCALL_SESSION") or None})


def _drive(c, max_turns, ignore_quota, fresh):
    """Everything call() does once it holds the job lock and request.json matches the request."""
    job, prompt = c.job, c.prompt
    if os.path.exists(job.path("answer.txt")):
        if job.status().get("state") != "done":
            job.set_status(state="done", answer=True)
        return _result(job, c.cident["json"])
    att = pending(job)
    oc = _recovered(job, att, c.timeout) if att else None
    if att and oc is None:  # the saved output does not settle the attempt; run and batch restart the turn
        got = _saved_conversation(att)
        st.write_atomic(os.path.join(att, "result.json"), {"exit": None, "outcome": "interrupted", "conversation": got, "ended": _now()})
        job.set_status(state="interrupted")
        if got and not job.status().get("conversation"):
            job.set_status(conversation=got)
    conv = job.status().get("conversation") or c.seed
    for a in reversed(job.launched()):  # an id can sit in a result or launch file when the status write was lost
        if conv:
            break
        conv = (st.read_json(os.path.join(a, "result.json"), {}) or {}).get("conversation") or \
            (st.read_json(os.path.join(a, "launch.json"), {}) or {}).get("conversation")
    if conv:
        _hold(c, conv)
    if oc is not None:  # settled exactly like a live attempt, without a launch
        r = _settle(c, att, oc, recovered=True)
        if r:
            return r
    done = job.launched()
    last_res = (st.read_json(os.path.join(done[-1], "result.json"), {}) or {}) if done else {}
    if last_res.get("outcome") == "check-error":  # the reply is saved: check it again before any new turn
        try:
            verdict = _check(c, done[-1], _saved_reply(done[-1]), last_res)
        except CheckError as e:
            job.set_status(state="check-error", findings=[str(e)])
            raise
        if verdict.ok:
            if _cancel_requested(job):
                _cancel(c, done[-1], last_res)
            st.write_atomic(job.path("answer.txt"), verdict.answer)
            job.set_status(state="done", answer=True, conversation=c.conv, findings=[])
            return _result(job, c.cident["json"])
        if last_res.get("classified") in AGY_ERRORS:  # same guard as in _settle: no follow-up after an agy error
            findings = verdict.findings + ["no follow-up was sent because agy reported an error: %s" % last_res.get("agyError")]
            last_res.update(outcome="failed", findings=findings)
            st.write_atomic(os.path.join(done[-1], "result.json"), last_res)
            job.set_status(state="failed", findings=findings)
            raise GaveUp(findings, c.conv, _usage(job), job.dir)
        last_res.update(outcome="rejected", findings=verdict.findings, lostContext=verdict.lost_context)
        st.write_atomic(os.path.join(done[-1], "result.json"), last_res)
    elif last_res.get("outcome") == "failed" and (last_res.get("classified") in AGY_ERRORS or last_res.get("conversationConflict")):
        findings = last_res.get("findings") or ["agy reported an error"]  # a rerun never sends a follow-up after these stops
        job.set_status(state="failed", findings=findings)
        raise GaveUp(findings, c.conv, _usage(job), job.dir)
    while True:
        if _cancel_requested(job):  # checked before every launch
            _cancel(c)
        atts = job.launched()
        launches = [st.read_json(os.path.join(a, "launch.json"), {}) or {} for a in atts]
        results = [st.read_json(os.path.join(a, "result.json"), {}) or {} for a in atts]
        message, kind = _next(prompt, results[-1] if results else None, launches[-1] if launches else None)
        if kind == "retry-first" and not c.conv and results[-1].get("outcome") == "retry" and _agy_output(atts[-1]):
            kind = "retry-needs-conversation"  # agy may have opened a conversation we never saw
        if (kind not in FIRST_LIKE) and not c.conv:
            if not fresh:
                job.set_status(state="no-conversation")
                raise NoConversation("job %s needs a follow-up but no conversation id was captured; replies are in %s; "
                                     "pass fresh to start a new conversation" % (job.id, job.path("attempts")))
            message, kind = (message if kind == "retry-needs-conversation" else prompt + "\n\n" + message), "fresh"
        turns = sum(1 for l in launches if l.get("kind") not in ("retry", "retry-first"))
        if kind not in ("retry", "retry-first") and turns >= max_turns:
            findings = (results[-1].get("findings") if results else None) or ["turn limit reached"]
            job.set_status(state="failed", findings=findings)
            raise GaveUp(findings, c.conv, _usage(job), job.dir)
        size = len(message.encode("utf-8"))
        if size > agy.MAX_PROMPT:
            findings = ["the next message would be %d bytes, over this system's single-argument limit of %d bytes; "
                        "rerun with --new-version and a shorter prompt" % (size, agy.MAX_PROMPT)]
            job.set_status(state="failed", findings=findings)
            raise GaveUp(findings, c.conv, _usage(job), job.dir)
        try:
            generation = quota.generation(c.key) if ignore_quota else quota.admit(c.key)
        except Quota:
            job.set_status(state="quota")
            raise
        att = job.new_attempt()
        job.set_status(state="running", attempts=len(atts) + 1, conversation=c.conv)
        try:
            oc = _launch(c, att, message, kind, generation)
        except Fatal as e:
            st.write_atomic(os.path.join(att, "result.json"), {"exit": None, "outcome": "fatal", "findings": [e.reason], "ended": _now()})
            job.set_status(state="fatal", findings=[e.reason], code=e.code)
            raise
        r = _settle(c, att, oc)
        if r:
            return r


def call(job_id, prompt, *, store=None, namespace=None, model=None, timeout="10m", cwd=None, dirs=(),
         mode=None, conversation=None, expect=None, require=None, schema=None, check_cmd=None, check_timeout=120,
         check=None, check_id=None, max_turns=4, ignore_quota=False, fresh=False, new_version=False):
    c = _prepare(job_id, prompt, store=store, namespace=namespace, model=model, timeout=timeout, cwd=cwd, dirs=dirs,
                 mode=mode, conversation=conversation, expect=expect, require=require, schema=schema,
                 check_cmd=check_cmd, check_timeout=check_timeout, check=check, check_id=check_id)
    with contextlib.ExitStack() as stack:
        _admit(c, stack, new_version)
        # cancel reads this to know whether a live process will act on cancel.request; the callback runs before the
        # job lock is released, because the stack unwinds in reverse order
        c.job.set_status(controller={"pid": os.getpid(), "pstart": agy.start_time(os.getpid())})
        stack.callback(c.job.set_status, controller=None)
        return _drive(c, max_turns, ignore_quota, fresh)


def enqueue(job_id, prompt, *, max_turns=4, ignore_quota=False, fresh=False, new_version=False, **kw):
    """Check a run exactly as call() does, record it as queued and save the arguments a background worker passes to
    call(); kw takes call()'s other keyword arguments. Returns the job."""
    if kw.get("check") is not None:
        raise ValueError("a Python check function cannot run in the background; use check_cmd, expect, require or schema")
    c = _prepare(job_id, prompt, **kw)
    with contextlib.ExitStack() as stack:
        _admit(c, stack, new_version)
        args = dict(kw, job_id=job_id, prompt=prompt, store=c.job.store, namespace=None, cwd=c.cwd, dirs=c.dirs,
                    schema=os.path.abspath(kw["schema"]) if kw.get("schema") else None, max_turns=max_turns,
                    ignore_quota=ignore_quota, fresh=fresh,
                    new_version=False)  # _admit has archived the old version already
        st.write_atomic(c.job.path("background.json"), args)
        c.job.set_status(state="queued")
    return c.job
