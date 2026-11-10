"""Machine-wide quota hold per model key, in ~/.agy-call/quota.json, guarded by locks/quota.lock."""
import datetime as dt
import os
import re

from . import store
from .errors import Quota

DEFAULT_HOLD = 1800
RESET = re.compile(r"[Rr]esets?\s+in\s+(?:(\d+)h)?\s*(?:(\d+)m)?\s*(?:(\d+)s)?")


def _paths():
    h = store.home()
    return os.path.join(h, "quota.json"), os.path.join(h, "locks", "quota.lock")


def _now():
    return dt.datetime.now(dt.timezone.utc)


def parse_reset(texts):
    for t in texts:
        for m in RESET.finditer(t or ""):
            h, mi, s = (int(x) if x else 0 for x in m.groups())
            if h or mi or s:
                return h * 3600 + mi * 60 + s
    return None


def _until(entry):
    u = (entry or {}).get("until")
    return dt.datetime.fromisoformat(u) if u else None


def held(key):
    qf, lk = _paths()
    with store.flock(lk):
        until = _until((store.read_json(qf, {}) or {}).get(key))
    return until if until and until > _now() else None


def admit(key):
    qf, lk = _paths()
    with store.flock(lk):
        entry = (store.read_json(qf, {}) or {}).get(key) or {}
        until = _until(entry)
        if until and until > _now():
            raise Quota(key, until)
        return entry.get("generation", 0)


def generation(key):
    qf, lk = _paths()
    with store.flock(lk):
        return ((store.read_json(qf, {}) or {}).get(key) or {}).get("generation", 0)


def hold(key, reason, seconds):
    qf, lk = _paths()
    with store.flock(lk):
        data = store.read_json(qf, {}) or {}
        entry = data.get(key) or {}
        until = _now() + dt.timedelta(seconds=seconds)
        old = _until(entry)
        if old and old > until:
            until = old  # a hold never shortens
        data[key] = {"until": until.isoformat(), "set_at": _now().isoformat(),
                     "generation": entry.get("generation", 0) + 1, "reason": (reason or "")[:500]}
        store.write_atomic(qf, data)
    return until


def clear(key, generation):
    qf, lk = _paths()
    with store.flock(lk):
        data = store.read_json(qf, {}) or {}
        entry = data.get(key)
        if entry and entry.get("until") and entry.get("generation", 0) == generation:
            entry["until"] = None
            store.write_atomic(qf, data)
