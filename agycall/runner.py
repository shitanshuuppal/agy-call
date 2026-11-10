"""One agy-call job: launch agy-delegate under the capture shim, check the reply, follow up in the same conversation."""
import contextlib
import dataclasses
import datetime as dt
import glob
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import types

from . import checks, quota, shim
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
_SHIM_DIR = None


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


def _delegate():
    path = os.environ.get("AGY_DELEGATE") or shutil.which("agy-delegate")
    if not path or not os.path.exists(path):
        raise Fatal(13, "agy-delegate not found (set AGY_DELEGATE or install the antigravity plugin)")
    return path


def _real_agy():
    if "AGYCALL_REAL_AGY" in os.environ:
        return os.environ["AGYCALL_REAL_AGY"] or None
    return shutil.which("agy")


def _shim_dir():
    global _SHIM_DIR
    if _SHIM_DIR is None:
        _SHIM_DIR = shim.install(tempfile.mkdtemp(prefix="agy-call-shim-"))
    return _SHIM_DIR


def parse_usage(err_text):
    for line in reversed(err_text.splitlines()):
        if line.startswith("AGY_USAGE "):
            try:
                return json.loads(line[len("AGY_USAGE "):])
            except ValueError:
                return {}
    return {}


def _reason(err_text):
    for line in reversed(err_text.splitlines()):
        if line.startswith("AGY_SIGNAL "):
            try:
                return json.loads(line[len("AGY_SIGNAL "):]).get("reason") or line
            except ValueError:
                return line
    lines = [l for l in err_text.splitlines() if l.strip() and not l.startswith("AGY_USAGE ")]
    return lines[-1][:500] if lines else "no reason given"


def _alive(pgid):
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


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
    if launch.get("pgid") and _alive(launch["pgid"]):
        raise Running("job %s is running (pid %s)" % (job.id, launch.get("pid")))
    got = parse_usage(_read(os.path.join(last, "delegate.err"))).get("conversation_id") or None
    st.write_atomic(os.path.join(last, "result.json"), {"rc": None, "outcome": "interrupted", "conversation": got, "ended": _now()})
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


def _agy_output(att):
    """True when a captured agy invocation of this attempt (not a help probe) produced output."""
    for path in glob.glob(os.path.join(att, "agy-*.out")):
        try:
            args = json.loads(_read(path[:-4] + ".args"))
        except ValueError:
            args = []
        probe = any(a in ("--help", "-h") for a in args) and not any(a in ("-p", "--print", "--prompt") for a in args)
        if not probe and _read(path).strip():
            return True
    return False


def _launch(att, message, conv, o, kind, generation, fds):
    cmd = [_delegate()]
    if o.model:
        cmd += ["--model", o.model]
    cmd += ["--timeout", o.timeout]
    for d in o.dirs:
        cmd += ["--dir", d]
    if o.mode:
        cmd += ["--mode", o.mode]
    if conv:
        cmd += ["--conversation", conv]
    cmd.append("-")
    env = dict(os.environ, CLAUDE_PLUGIN_OPTION_STRUCTURED_OUTPUT="on")
    real = _real_agy()
    if real:
        env = shim.env(env, _shim_dir(), att, real)
    launch = {"pid": None, "pgid": None, "kind": kind, "message": message, "conversation": conv,
              "generation": generation, "started": _now(), "argv": cmd}
    st.write_atomic(os.path.join(att, "launch.json"), launch)
    with open(os.path.join(att, "delegate.out"), "wb") as out, open(os.path.join(att, "delegate.err"), "wb") as err:
        try:
            p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=out, stderr=err, cwd=o.cwd, env=env, start_new_session=True,
                                 pass_fds=fds)  # the delegate tree keeps the locks held until it exits
        except OSError as e:
            raise Fatal(13, "agy-delegate could not start: %s" % e)
        launch.update(pid=p.pid, pgid=p.pid)
        st.write_atomic(os.path.join(att, "launch.json"), launch)
        try:
            p.stdin.write(message.encode("utf-8"))
            p.stdin.close()
        except BrokenPipeError:
            pass
        return p.wait()


def call(job_id, prompt, *, store=None, namespace=None, model=None, timeout="10m", cwd=None, dirs=(),
         mode=None, conversation=None, expect=None, require=None, schema=None, check_cmd=None, check_timeout=120,
         check=None, check_id=None, max_turns=4, ignore_quota=False, fresh=False, new_version=False):
    if not model:
        raise ValueError("model is required: pass the exact agy model name, e.g. gemini-4-argon-high")
    checker, cident = checks.make_check(expect, require, schema, check_cmd, check_timeout, check, check_id)
    o = types.SimpleNamespace(model=model, timeout=timeout, mode=mode,
                              cwd=os.path.abspath(cwd or os.getcwd()), dirs=[os.path.abspath(d) for d in dirs])
    key = model
    job = st.Job(st.store_dir(store, namespace), job_id)
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
            verdict = checker(_read(os.path.join(done[-1], "delegate.out")))
            if verdict.ok:
                st.write_atomic(job.path("answer.txt"), verdict.answer)
                job.set_status(state="done", answer=True, conversation=conv, findings=[])
                return _result(job, cident["json"])
            last_res.update(outcome="rejected", findings=verdict.findings, lostContext=verdict.lost_context)
            st.write_atomic(os.path.join(done[-1], "result.json"), last_res)
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
            try:
                generation = quota.generation(key) if ignore_quota else quota.admit(key)
            except Quota:
                job.set_status(state="quota")
                raise
            att = job.new_attempt()
            job.set_status(state="running", attempts=len(atts) + 1, conversation=conv)
            try:
                rc = _launch(att, message, conv, o, kind, generation, fds)
            except Fatal as e:
                st.write_atomic(os.path.join(att, "result.json"), {"rc": None, "outcome": "fatal", "findings": [e.reason], "ended": _now()})
                job.set_status(state="fatal", findings=[e.reason])
                raise
            err = _read(os.path.join(att, "delegate.err"))
            text = _read(os.path.join(att, "delegate.out"))
            usage = parse_usage(err)
            got = usage.get("conversation_id") or None
            res = {"rc": rc, "conversation": got, "usage": usage.get("usage") or {}, "ended": _now()}
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

            if rc == 0:
                try:
                    verdict = checker(text)
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
                finish(outcome="rejected", findings=verdict.findings, lostContext=verdict.lost_context)
            elif rc == 12:
                finish(outcome="timeout", partial=bool(text.strip()))
            elif rc == 15:
                if any(l.get("kind") == "denied" for l in launches):
                    finish("failed", outcome="failed", findings=["a tool call was denied twice"])
                    raise GaveUp(["a tool call was denied twice"], conv, _usage(job), job.dir)
                finish(outcome="denied")
            elif rc in (2, 3):
                if kind in ("retry", "retry-first"):
                    finish(outcome="rejected", findings=["agy-delegate exit %d: %s" % (rc, _reason(err))])
                else:
                    finish(outcome="retry", findings=["agy-delegate exit %d: %s" % (rc, _reason(err))])
                    time.sleep(RETRY_WAIT)
            elif rc == 10:
                streams = [_read(p) for p in sorted(glob.glob(os.path.join(att, "agy-*.err")))]
                streams += [_read(p) for p in sorted(glob.glob(os.path.join(att, "agy-*.out")))] + [err]
                until = quota.hold(key, _reason(err), quota.parse_reset(streams) or quota.DEFAULT_HOLD)
                finish("quota", outcome="quota", until=until.isoformat())
                raise Quota(key, until)
            elif rc in (11, 13, 14):
                finish("fatal", outcome="fatal", findings=[_reason(err)])
                raise Fatal(rc, _reason(err))
            else:
                finish("fatal", outcome="fatal", findings=["agy-delegate exited %s: %s" % (rc, _reason(err))])
                raise Fatal(22, "agy-delegate exited %s: %s" % (rc, _reason(err)))
