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
from .errors import CheckError, Fatal, GaveUp, Mismatch, NoConversation, Quota, Running

RETRY_WAIT = 30
FIX = ("Your last reply could not be used. Problems: {findings}. "
       "Reply again with the complete answer in the required format; keep the work you already did.")
RESTART = ("Your last turn was cut off before it finished. Write your complete answer again from the beginning, "
           "in full, in the required format; do not continue mid-sentence.")
DENIED = ("Your last tool call was denied by the permission policy. Do not repeat that call; "
          "continue the task with the tools you are allowed, and give the complete answer.")
LOST = "\n\nThe original request was:\n\n{prompt}"
FIRST_LIKE = ("first", "retry-first", "fresh")


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


def recover(job):
    """Reconcile the newest attempt after a crash; raises Running when its child is still alive."""
    atts = job.launched()
    if not atts:
        return
    last = atts[-1]
    if not os.path.exists(os.path.join(last, "launch.json")):
        os.rename(last, last + ".unlaunched")
        return
    if os.path.exists(os.path.join(last, "result.json")):
        return
    launch = st.read_json(os.path.join(last, "launch.json"), {}) or {}
    if launch.get("pgid") and agy.alive(launch["pgid"]):
        raise Running("job %s is running (pid %s)" % (job.id, launch.get("pid")))
    got = _saved_conversation(last)
    st.write_atomic(os.path.join(last, "result.json"), {"exit": None, "outcome": "interrupted", "conversation": got, "ended": _now()})
    if got and not job.status().get("conversation"):
        job.set_status(conversation=got)


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


def _launch(att, message, conv, o, kind, generation, fds):
    cmd = agy.argv(agy.path(), message, model=o.model, timeout=o.timeout, mode=o.mode, dirs=o.dirs, conversation=conv)
    launch = {"pid": None, "pgid": None, "kind": kind, "message": message, "conversation": conv,
              "generation": generation, "started": _now(), "argv": agy.redact(cmd)}
    st.write_atomic(os.path.join(att, "launch.json"), launch)

    def started(pid, when):
        launch.update(pid=pid, pgid=pid, pstart=when)
        st.write_atomic(os.path.join(att, "launch.json"), launch)

    r = agy.run(att, cmd, cwd=o.cwd, deadline=agy.seconds(o.timeout) + agy.GRACE, fds=fds, on_start=started)
    return classify.classify(r.exit, r.out, r.err, r.stopped)


def call(job_id, prompt, *, store=None, namespace=None, model=None, timeout="10m", cwd=None, dirs=(),
         mode=None, conversation=None, expect=None, require=None, schema=None, check_cmd=None, check_timeout=120,
         check=None, check_id=None, max_turns=4, ignore_quota=False, fresh=False, new_version=False):
    if not model:
        raise ValueError("model is required: pass the exact agy model name, e.g. gemini-4-argon-high")
    size = len(prompt.encode("utf-8"))
    job = st.Job(st.store_dir(store, namespace), job_id)
    if size > agy.MAX_PROMPT and not os.path.exists(job.path("answer.txt")):  # a saved answer is still returned
        raise ValueError("the prompt is %d bytes; agy takes it as one argument, and this system's limit is %d bytes: "
                         "put the material in a file under the working directory and refer to it" % (size, agy.MAX_PROMPT))
    agy.seconds(timeout)  # a bad timeout fails here, before any attempt folder exists
    checker, cident = checks.make_check(expect, require, schema, check_cmd, check_timeout, check, check_id)
    checked = any(cident[k] for k in ("json", "require", "schema", "cmd")) or check is not None
    o = types.SimpleNamespace(model=model, timeout=timeout, mode=mode,
                              cwd=os.path.abspath(cwd or os.getcwd()), dirs=[os.path.abspath(d) for d in dirs])
    key = model
    ident = st.identity(prompt, key, o.cwd, o.dirs, conversation, cident, mode)
    with contextlib.ExitStack() as stack:
        try:
            fds = [stack.enter_context(st.flock(job.lock_path, blocking=False)).fileno()]
        except st.Busy:
            raise Running("job %s is running in another process" % job_id)
        recover(job)  # before any archive: a live child must never lose its folder
        req = st.read_json(job.path("request.json"))
        if req and req.get("identity") != ident:
            if not new_version:
                raise Mismatch("job %s exists with a different request; pass new_version to replace it (%s)" % (job_id, job.dir))
            job.archive()
            req = None
        if not req:
            os.makedirs(job.dir, exist_ok=True)
            st.write_atomic(job.path("request.json"), {"identity": ident, "prompt": prompt, "key": key, "cwd": o.cwd, "dirs": o.dirs,
                                                       "seed": conversation, "check": cident, "mode": mode, "created": _now()})
        if os.path.exists(job.path("answer.txt")):
            if job.status().get("state") != "done":
                job.set_status(state="done", answer=True)
            return _result(job, cident["json"])
        done = job.launched()
        conv = job.status().get("conversation") or conversation
        for a in reversed(done):  # an id can sit in a result or launch file when the status write was lost
            if conv:
                break
            conv = (st.read_json(os.path.join(a, "result.json"), {}) or {}).get("conversation") or \
                (st.read_json(os.path.join(a, "launch.json"), {}) or {}).get("conversation")
        if conv:
            fds.append(stack.enter_context(st.flock(conv_lock(conv))).fileno())
        last_res = st.read_json(os.path.join(done[-1], "result.json"), {}) if done else {}
        if (last_res or {}).get("outcome") == "check-error":  # the reply is saved: check it again before any new turn
            verdict = checker(_saved_reply(done[-1]))
            if verdict.ok:
                st.write_atomic(job.path("answer.txt"), verdict.answer)
                job.set_status(state="done", answer=True, conversation=conv, findings=[])
                return _result(job, cident["json"])
            if last_res.get("classified") in ("reply-with-error", "output-limit"):  # same guard as in the loop: no follow-up after an agy error
                findings = verdict.findings + ["no follow-up was sent because agy reported an error: %s" % last_res.get("agyError")]
                last_res.update(outcome="failed", findings=findings)
                st.write_atomic(os.path.join(done[-1], "result.json"), last_res)
                job.set_status(state="failed", findings=findings)
                raise GaveUp(findings, conv, _usage(job), job.dir)
            last_res.update(outcome="rejected", findings=verdict.findings, lostContext=verdict.lost_context)
            st.write_atomic(os.path.join(done[-1], "result.json"), last_res)
        elif (last_res or {}).get("outcome") == "failed" and last_res.get("classified") in ("reply-with-error", "output-limit"):
            findings = last_res.get("findings") or ["agy reported an error"]  # a rerun never sends a follow-up after an agy error
            job.set_status(state="failed", findings=findings)
            raise GaveUp(findings, conv, _usage(job), job.dir)
        while True:
            atts = job.launched()
            launches = [st.read_json(os.path.join(a, "launch.json"), {}) or {} for a in atts]
            results = [st.read_json(os.path.join(a, "result.json"), {}) or {} for a in atts]
            message, kind = _next(prompt, results[-1] if results else None, launches[-1] if launches else None)
            if kind == "retry-first" and not conv and results[-1].get("outcome") == "retry" and _agy_output(atts[-1]):
                kind = "retry-needs-conversation"  # agy may have opened a conversation we never saw
            if (kind not in FIRST_LIKE) and not conv:
                if not fresh:
                    job.set_status(state="no-conversation")
                    raise NoConversation("job %s needs a follow-up but no conversation id was captured; replies are in %s; "
                                         "pass fresh to start a new conversation" % (job_id, job.path("attempts")))
                message, kind = (message if kind == "retry-needs-conversation" else prompt + "\n\n" + message), "fresh"
            turns = sum(1 for l in launches if l.get("kind") not in ("retry", "retry-first"))
            if kind not in ("retry", "retry-first") and turns >= max_turns:
                findings = (results[-1].get("findings") if results else None) or ["turn limit reached"]
                job.set_status(state="failed", findings=findings)
                raise GaveUp(findings, conv, _usage(job), job.dir)
            size = len(message.encode("utf-8"))
            if size > agy.MAX_PROMPT:
                findings = ["the next message would be %d bytes, over this system's single-argument limit of %d bytes; "
                            "rerun with --new-version and a shorter prompt" % (size, agy.MAX_PROMPT)]
                job.set_status(state="failed", findings=findings)
                raise GaveUp(findings, conv, _usage(job), job.dir)
            try:
                generation = quota.generation(key) if ignore_quota else quota.admit(key)
            except Quota:
                job.set_status(state="quota")
                raise
            att = job.new_attempt()
            job.set_status(state="running", attempts=len(atts) + 1, conversation=conv)
            try:
                oc = _launch(att, message, conv, o, kind, generation, fds)
            except Fatal as e:
                st.write_atomic(os.path.join(att, "result.json"), {"exit": None, "outcome": "fatal", "findings": [e.reason], "ended": _now()})
                job.set_status(state="fatal", findings=[e.reason])
                raise
            if oc.response:
                st.write_atomic(os.path.join(att, "reply.txt"), oc.response)
            got = oc.conversation
            res = {"exit": oc.exit, "agyStatus": oc.status, "classified": oc.name, "conversation": got,
                   "usage": oc.usage, "ended": _now()}
            if got and not conv:
                conv = got
                fds.append(stack.enter_context(st.flock(conv_lock(conv))).fileno())
                job.set_status(conversation=conv)
            elif got and got != conv:
                res["conversationConflict"] = got

            def finish(state=None, **extra):
                res.update(extra)
                st.write_atomic(os.path.join(att, "result.json"), res)
                if state:
                    job.set_status(state=state, findings=res.get("findings", []))

            def give_up(findings):
                finish("failed", outcome="failed", findings=findings)
                raise GaveUp(findings, conv, _usage(job), job.dir)

            name = oc.name
            if name == "reply" or (name in ("reply-with-error", "output-limit") and oc.response.strip()):
                if name != "reply":
                    res["agyError"] = oc.reason
                    if not checked:
                        give_up(["%s; the reply is saved in %s and is accepted after an agy error only by a check you ask for"
                                 % (oc.reason, os.path.join(att, "reply.txt"))])
                try:
                    verdict = checker(oc.response)
                except CheckError as e:
                    finish("check-error", outcome="check-error", findings=[str(e)])
                    raise
                if verdict.ok:
                    st.write_atomic(job.path("answer.txt"), verdict.answer)  # the answer first: its presence means done
                    finish(outcome="accepted")
                    job.set_status(state="done", answer=True, conversation=conv, findings=[])
                    if generation is not None:
                        quota.clear(key, generation)
                    return _result(job, cident["json"])
                if name != "reply":  # later turns in such a conversation failed the same way: send nothing more
                    give_up(verdict.findings + ["no follow-up was sent because agy reported an error: %s" % oc.reason])
                finish(outcome="rejected", findings=verdict.findings, lostContext=verdict.lost_context)
            elif name == "output-limit":
                give_up(["agy stopped at its output token limit without a reply; its output is saved in %s. "
                         "Ask for the work as files on disk and a short reply" % att])
            elif name == "timeout":
                finish(outcome="timeout", partial=bool(oc.response.strip()))
            elif name == "denied":
                if any(l.get("kind") == "denied" for l in launches):
                    give_up(["a tool call was denied twice"])
                finish(outcome="denied")
            elif name == "empty":
                if kind in ("retry", "retry-first"):
                    finish(outcome="rejected", findings=[oc.reason])
                else:
                    finish(outcome="retry", findings=[oc.reason])
                    time.sleep(RETRY_WAIT)
            elif name == "quota":
                streams = [_read(os.path.join(att, "agy.err")), _read(os.path.join(att, "agy.out"))]
                until = quota.hold(key, oc.reason, quota.parse_reset(streams) or quota.DEFAULT_HOLD)
                finish("quota", outcome="quota", until=until.isoformat())
                raise Quota(key, until)
            else:  # auth, model, malformed
                reason = oc.reason
                if name == "malformed":
                    reason += "; the raw output is in %s" % os.path.join(att, "agy.out")
                finish("fatal", outcome="fatal", findings=[reason])
                raise Fatal({"auth": 11, "model": 14}.get(name, 22), reason)
