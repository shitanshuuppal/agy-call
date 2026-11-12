"""What a usable reply is: optional JSON extraction, required keys, a schema subset, a command or a Python function."""
import collections
import hashlib
import json
import os
import re
import signal
import subprocess
import tempfile
import time

from . import agy
from .errors import Cancelled, CheckError

FENCE = re.compile(r"```(?:json)?[ \t]*\n?([\s\S]*?)```")
Verdict = collections.namedtuple("Verdict", "ok answer findings lost_context")
_NAMES = {"object", "array", "string", "number", "integer", "boolean", "null"}
TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}
KEYWORDS = {"type", "enum", "required", "properties", "items", "additionalProperties", "minLength", "minimum",
            "maximum"}  # spec 8.4: every keyword the validator enforces; any other one is refused
TAIL = 4000  # characters of a task check's output sent back as the finding


def extract_json(text):
    try:
        return json.loads(text.strip())
    except ValueError:
        pass
    for block in reversed(FENCE.findall(text)):
        try:
            return json.loads(block.strip())
        except ValueError:
            continue
    lo = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    hi = max(text.rfind("}"), text.rfind("]"))
    if lo and hi > min(lo):
        try:
            return json.loads(text[min(lo):hi + 1])
        except ValueError:
            pass
    raise ValueError("no JSON value in the reply")


def _type_ok(value, t):
    if isinstance(t, list):
        return any(_type_ok(value, x) for x in t)
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if t == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    return isinstance(value, TYPES[t])


def validate(value, schema, where="$"):
    t = schema.get("type")
    if t is not None and not _type_ok(value, t):
        return ["%s should be %s" % (where, t if isinstance(t, str) else " or ".join(t))]
    out = []
    if "enum" in schema and value not in schema["enum"]:
        out.append("%s should be one of %s" % (where, schema["enum"]))
    if isinstance(value, str) and len(value) < schema.get("minLength", 0):
        out.append("%s should have length %s or more" % (where, schema["minLength"]))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            out.append("%s should be at least %s" % (where, schema["minimum"]))
        if "maximum" in schema and value > schema["maximum"]:
            out.append("%s should be at most %s" % (where, schema["maximum"]))
    if isinstance(value, dict):
        out += ["%s is missing %s" % (where, k) for k in schema.get("required", []) if k not in value]
        props = schema.get("properties", {})
        for k, sub in props.items():
            if k in value:
                out += validate(value[k], sub, "%s.%s" % (where, k))
        if schema.get("additionalProperties") is False:
            out += ["%s has a key the schema does not allow: %s" % (where, k) for k in value if k not in props]
    if isinstance(value, list) and "items" in schema:
        for i, v in enumerate(value):
            out += validate(v, schema["items"], "%s[%d]" % (where, i))
    return out


def _check_schema(s, where="$"):
    if not isinstance(s, dict):
        raise ValueError("invalid schema: %s is not an object" % where)
    unknown = sorted(set(s) - KEYWORDS)
    if unknown:  # spec 8.4: a keyword the validator does not enforce is refused before any launch
        raise ValueError("invalid schema: %s uses %s, which agy-call does not enforce; it supports %s"
                         % (where, ", ".join(unknown), ", ".join(sorted(KEYWORDS))))
    t = s.get("type")
    names = t if isinstance(t, list) else [t]
    if t is not None and not (names and all(isinstance(n, str) and n in _NAMES for n in names)):
        raise ValueError("invalid schema: unknown type %r at %s" % (t, where))
    if s.get("additionalProperties", False) is not False:
        raise ValueError("invalid schema: additionalProperties at %s must be false" % where)
    for k in ("minLength", "minimum", "maximum"):
        if k in s and (isinstance(s[k], bool) or not isinstance(s[k], (int, float))):
            raise ValueError("invalid schema: %s at %s must be a number" % (k, where))
    if "required" in s and not isinstance(s["required"], list):
        raise ValueError("invalid schema: required at %s must be a list" % where)
    if "enum" in s and not isinstance(s["enum"], list):
        raise ValueError("invalid schema: enum at %s must be a list" % where)
    if "properties" in s:
        if not isinstance(s["properties"], dict):
            raise ValueError("invalid schema: properties at %s must be an object" % where)
        for k, sub in s["properties"].items():
            _check_schema(sub, "%s.%s" % (where, k))
    if "items" in s:
        _check_schema(s["items"], where + "[]")


def run_cmd(cmd, text, timeout, cwd=None, fds=(), on_start=None, should_stop=None, combined=False):
    """Run a check command in its own process group, with text on stdin and the descriptors fds inherited.
    on_start(pid, start time) is called once it runs; should_stop() is polled every agy.POLL seconds, and "cancel" stops
    the group and raises Cancelled. Exit 0 passes ([]), exit 1 rejects (its stdout is the finding), and any other exit,
    a timeout or a failure to start is a CheckError. With combined (a task's check, spec 4.5) stderr joins stdout, and
    every exit but 0 rejects with the last TAIL characters of that output as the finding."""
    with tempfile.TemporaryFile() as inp, tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        inp.write(text.encode("utf-8"))
        inp.seek(0)  # a file, not a pipe: a command that never reads its stdin cannot block on a large reply
        try:
            p = subprocess.Popen(cmd, shell=True, stdin=inp, stdout=out, stderr=subprocess.STDOUT if combined else err,
                                 cwd=cwd, start_new_session=True, pass_fds=tuple(fds))
        except OSError as e:
            raise CheckError("check command could not start: %s" % e)
        if on_start:
            try:
                on_start(p.pid, agy.start_time(p.pid))
            except BaseException:  # without a recorded pid nothing can find this command later
                agy.stop_group(p.pid, p.poll)
                p.wait()
                raise
        end = time.monotonic() + timeout
        while True:
            try:
                p.wait(timeout=agy.POLL)
                break
            except subprocess.TimeoutExpired:
                try:
                    asked = should_stop() if should_stop else None
                except BaseException:  # a failing cancel check must not leave the group running
                    agy.stop_group(p.pid, p.poll)
                    p.wait()
                    raise
                if asked == "cancel":
                    agy.stop_group(p.pid, p.poll)
                    p.wait()
                    raise Cancelled("the check command was stopped by a cancel request")
                if time.monotonic() >= end:
                    os.killpg(p.pid, signal.SIGKILL)
                    p.wait()
                    raise CheckError("check command timed out after %ss: %s" % (timeout, cmd))
        out.seek(0)
        err.seek(0)
        stdout, stderr = (f.read().decode("utf-8", "replace") for f in (out, err))
    if p.returncode == 0:
        return []
    if combined:
        return ["the check command exited %s; the end of its output:\n%s" % (p.returncode, stdout[-TAIL:])]
    if p.returncode == 1:
        return [stdout.strip() or "the check command rejected the reply"]
    raise CheckError("check command exited %s: %s" % (p.returncode, (stderr or stdout).strip()[-500:]))


def make_check(expect=None, require=None, schema=None, check_cmd=None, check_timeout=120, func=None, check_id=None):
    if func is not None and not check_id:
        raise ValueError("check_id is required with a check function")
    require = sorted(require or [])
    want_json = expect == "json" or bool(require) or bool(schema)
    schema_obj = schema_sha = None
    if schema:
        raw = open(schema, "rb").read()
        schema_sha, schema_obj = hashlib.sha256(raw).hexdigest(), json.loads(raw)
        _check_schema(schema_obj)
    ident = {"json": want_json, "require": require, "schema": schema_sha, "cmd": check_cmd, "check_id": check_id}

    def check(text, **run):
        """Check one reply; run holds run_cmd's cwd, fds, on_start and should_stop for a check command."""
        try:
            return _check(text, run)
        except (CheckError, Cancelled):
            raise
        except Exception as e:
            raise CheckError("check crashed: %s: %s" % (type(e).__name__, e))

    def _check(text, run):
        if not text.strip():
            return Verdict(False, None, ["the reply was empty"], False)
        if want_json:
            try:
                value = extract_json(text)
            except ValueError as e:
                return Verdict(False, None, ["%s; reply with one JSON value in a ```json block" % e], False)
        else:
            try:
                value = extract_json(text)
            except ValueError:
                value = None
        if isinstance(value, dict) and "error" in value and not (require and all(k in value for k in require)):
            return Verdict(False, None, ["the reply is an error report, not an answer: %s" % str(value["error"])[:300]], True)
        findings = []
        if want_json:
            findings += ["missing key: %s" % k for k in require if not (isinstance(value, dict) and k in value)]
            if schema_obj is not None:
                findings += validate(value, schema_obj)
        answer = json.dumps(value, ensure_ascii=False, indent=1) if want_json else text
        if not findings and check_cmd:
            findings += run_cmd(check_cmd, answer, check_timeout, **run)
        if not findings and func is not None:
            try:
                res = func(value if want_json else text)
            except Exception as e:
                raise CheckError("check %s failed: %s: %s" % (check_id, type(e).__name__, e))
            findings += list(res or [])
        return Verdict(not findings, None if findings else answer, findings, False)

    return check, ident
