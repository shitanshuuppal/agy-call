import datetime as dt
import json
import os

import pytest

from agycall import quota, store
from agycall.errors import Quota


def test_parse_reset():
    assert quota.parse_reset(["QUOTA_EXHAUSTED ... Resets in 4h28m41s"]) == 4 * 3600 + 28 * 60 + 41
    assert quota.parse_reset(["nothing", "resets in 12m"]) == 720
    assert quota.parse_reset(["Resets in 45s"]) == 45
    assert quota.parse_reset(["agy quota / rate limit"]) is None
    assert quota.parse_reset([None, ""]) is None


def test_hold_blocks_admission_and_success_of_newer_generation_clears():
    assert quota.admit("m") == 0
    until = quota.hold("m", "agy quota / rate limit", 600)
    with pytest.raises(Quota) as e:
        quota.admit("m")
    assert e.value.key == "m" and e.value.until == until
    assert quota.held("m") == until
    assert quota.admit("other-model") == 0  # holds are per key
    quota.clear("m", 0)  # a call launched before the hold cannot clear it
    assert quota.held("m") == until
    quota.clear("m", 1)
    assert quota.held("m") is None
    assert quota.admit("m") == 1


def test_expired_hold_admits(agy_home):
    quota.hold("m", "r", 600)
    path = os.path.join(store.home(), "quota.json")
    data = json.load(open(path))
    data["m"]["until"] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)).isoformat()
    json.dump(data, open(path, "w"))
    assert quota.admit("m") == 1


def test_until_is_timezone_aware(agy_home):
    quota.hold("m", "r", 60)
    data = json.load(open(os.path.join(store.home(), "quota.json")))
    parsed = dt.datetime.fromisoformat(data["m"]["until"])
    assert parsed.tzinfo is not None


def test_generation_reads_without_hold_check(agy_home):
    assert quota.generation("m") == 0
    quota.hold("m", "r", 600)
    assert quota.generation("m") == 1


def test_hold_never_shortens(agy_home):
    long = quota.hold("m2", "r", 4 * 3600)
    quota.hold("m2", "r", 1800)
    assert quota.held("m2") == long
    assert json.load(open(os.path.join(str(agy_home), "quota.json")))["m2"]["generation"] == 2
