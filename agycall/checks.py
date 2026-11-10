"""What a usable reply is: optional JSON extraction, required keys, a schema subset, a command or a Python function."""
import collections
import hashlib
import json
import os
import re
import signal
import subprocess

from .errors import CheckError

FENCE = re.compile(r"```(?:json)?[ \t]*\n?([\s\S]*?)```")
Verdict = collections.namedtuple("Verdict", "ok answer findings lost_context")
_NAMES = {"object", "array", "string", "number", "integer", "boolean", "null"}
TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


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
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if t == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    return isinstance(value, TYPES[t])


def validate(value, schema, where="$"):
    t = schema.get("type")
    if t and not _type_ok(value, t):
        return ["%s should be %s" % (where, t)]
    out = []
    if "enum" in schema and value not in schema["enum"]:
        out.append("%s should be one of %s" % (where, schema["enum"]))
    if isinstance(value, dict):
        out += ["%s is missing %s" % (where, k) for k in schema.get("required", []) if k not in value]
        for k, sub in schema.get("properties", {}).items():
            if k in value:
                out += validate(value[k], sub, "%s.%s" % (where, k))
    if isinstance(value, list) and "items" in schema:
        for i, v in enumerate(value):
            out += validate(v, schema["items"], "%s[%d]" % (where, i))
    return out


def _check_schema(s, where="$"):
    if not isinstance(s, dict):
        raise ValueError("invalid schema: %s is not an object" % where)
    t = s.get("type")
    if t is not None and (not isinstance(t, str) or t not in _NAMES):
        raise ValueError("invalid schema: unknown type %r at %s" % (t, where))
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


def run_cmd(cmd, text, timeout):
    try:
        p = subprocess.Popen(cmd, shell=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, start_new_session=True)
    except OSError as e:
        raise CheckError("check command could not start: %s" % e)
    try:
        out, err = p.communicate(text, timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
        p.communicate()
        raise CheckError("check command timed out after %ss: %s" % (timeout, cmd))
    if p.returncode == 0:
        return []
    if p.returncode == 1:
        return [out.strip() or "the check command rejected the reply"]
    raise CheckError("check command exited %s: %s" % (p.returncode, (err or out).strip()[-500:]))


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

    def check(text):
        try:
            return _check(text)
        except CheckError:
            raise
        except Exception as e:
            raise CheckError("check crashed: %s: %s" % (type(e).__name__, e))

    def _check(text):
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
            findings += run_cmd(check_cmd, answer, check_timeout)
        if not findings and func is not None:
            try:
                res = func(value if want_json else text)
            except Exception as e:
                raise CheckError("check %s failed: %s: %s" % (check_id, type(e).__name__, e))
            findings += list(res or [])
        return Verdict(not findings, None if findings else answer, findings, False)

    return check, ident
