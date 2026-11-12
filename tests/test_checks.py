import json
import os
import signal
import time

import pytest

from agycall import agy, checks
from agycall.errors import Cancelled, CheckError


def kill_group(pids):
    for pid in pids:
        try:
            os.killpg(pid, signal.SIGKILL)
        except OSError:
            pass


def test_extract_json_whole_fenced_and_embedded():
    assert checks.extract_json('{"a": 1}') == {"a": 1}
    two = 'Draft:\n```json\n{"a": 1}\n```\nFinal:\n```json\n{"a": 2}\n```\nDone.'
    assert checks.extract_json(two) == {"a": 2}  # the last fenced block that parses wins
    assert checks.extract_json('Here it is: {"b": [1, 2]} thanks') == {"b": [1, 2]}
    with pytest.raises(ValueError):
        checks.extract_json("no json here")


def test_no_check_accepts_any_non_empty_text():
    check, ident = checks.make_check()
    assert check("hello").ok and check("hello").answer == "hello"
    assert not check("   ").ok
    assert ident["json"] is False


def test_expect_json_with_required_keys():
    check, ident = checks.make_check(expect="json", require=["answer", "confidence"])
    v = check('```json\n{"answer": "B", "confidence": 90}\n```')
    assert v.ok and json.loads(v.answer) == {"answer": "B", "confidence": 90}
    v = check('{"answer": "B"}')
    assert not v.ok and v.findings == ["missing key: confidence"]
    v = check("I think B.")
    assert not v.ok and "no JSON value" in v.findings[0]
    assert ident == {"json": True, "require": ["answer", "confidence"], "schema": None, "cmd": None, "check_id": None}


def test_error_envelope_is_rejected_and_marks_lost_context():
    check, _ = checks.make_check(expect="json", require=["action"])
    v = check('{"error": "missing_initial_prompt"}')
    assert not v.ok and v.lost_context and "error report" in v.findings[0]
    check, _ = checks.make_check()  # even with no check
    v = check('{"error": "missing_initial_prompt"}')
    assert not v.ok and v.lost_context


def test_schema_subset(tmp_path):
    schema = tmp_path / "s.json"
    schema.write_text(json.dumps({"type": "object", "required": ["n"], "properties": {
        "n": {"type": "integer"}, "tag": {"enum": ["a", "b"]}, "xs": {"type": "array", "items": {"type": "string"}}}}))
    check, ident = checks.make_check(schema=str(schema))
    assert check('{"n": 1, "tag": "a", "xs": ["q"]}').ok
    v = check('{"n": true, "tag": "c", "xs": [1]}')
    assert not v.ok
    assert "$.n should be integer" in v.findings
    assert any("$.tag should be one of" in f for f in v.findings)
    assert "$.xs[0] should be string" in v.findings
    assert ident["schema"] and len(ident["schema"]) == 64
    schema.write_text(json.dumps({"type": "object"}))
    assert checks.make_check(schema=str(schema))[1]["schema"] != ident["schema"]  # editing the schema changes identity


def test_check_cmd_accept_reject_and_operational_failures():
    check, _ = checks.make_check(check_cmd="grep -q GOOD")
    assert check("all GOOD").ok
    check, _ = checks.make_check(check_cmd="echo 'needs a citation'; exit 1")
    v = check("text")
    assert not v.ok and v.findings == ["needs a citation"]
    check, _ = checks.make_check(check_cmd="exit 3")
    with pytest.raises(CheckError):
        check("text")
    check, _ = checks.make_check(check_cmd="definitely-not-a-command-xyz")
    with pytest.raises(CheckError):
        check("text")


def test_check_cmd_timeout_kills_and_raises():
    check, _ = checks.make_check(check_cmd="sleep 30", check_timeout=1)
    with pytest.raises(CheckError, match="timed out"):
        check("text")


def test_python_check_function():
    with pytest.raises(ValueError):
        checks.make_check(func=lambda v: None)  # check_id is required
    check, ident = checks.make_check(expect="json", func=lambda v: [] if v["n"] > 0 else ["n must be positive"], check_id="pos:1")
    assert check('{"n": 2}').ok
    assert check('{"n": 0}').findings == ["n must be positive"]
    assert ident["check_id"] == "pos:1"

    def boom(v):
        raise KeyError("x")

    check, _ = checks.make_check(func=boom, check_id="boom:1")
    with pytest.raises(CheckError):
        check("text")


def test_truncated_json_is_not_a_fragment():
    with pytest.raises(ValueError):
        checks.extract_json('{"outer":{"answer":"B"}')


def test_fenced_error_envelope_in_text_mode():
    check, _ = checks.make_check()
    v = check('```json\n{"error": "missing_initial_prompt"}\n```')
    assert not v.ok and v.lost_context
    assert check("The answer is B.").ok


@pytest.mark.parametrize("schema", [{"type": "int"}, {"required": "a"}, {"properties": []}, {"items": []}, {"enum": "a"},
                                    {"properties": {"a": {"type": "nope"}}}])
def test_invalid_schema_raises_value_error(tmp_path, schema):
    p = tmp_path / "s.json"
    p.write_text(json.dumps(schema))
    with pytest.raises(ValueError):
        checks.make_check(schema=str(p))


def test_checker_crash_becomes_check_error(tmp_path, monkeypatch):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"type": "object"}))
    check, _ = checks.make_check(schema=str(p))
    monkeypatch.setattr(checks, "validate", lambda *a, **k: 1 / 0)
    with pytest.raises(CheckError):
        check("{}")


def test_check_cmd_runs_in_cwd_with_inherited_fds_and_reports_its_start(tmp_path):
    (tmp_path / "marker").write_text("")
    seen = []
    with open(str(tmp_path / "lock"), "w") as lk:
        fd = lk.fileno()
        assert checks.run_cmd("test -f marker && test -e /dev/fd/%d" % fd, "text", 30, cwd=str(tmp_path), fds=(fd,),
                              on_start=lambda pid, when: seen.append((pid, when))) == []
        assert checks.run_cmd("test -e /dev/fd/%d" % fd, "text", 30) != []  # not inherited unless passed
    assert seen[0][0] > 0 and seen[0][1]


def test_check_cmd_that_ignores_a_large_reply_does_not_hang():
    t0 = time.monotonic()
    assert checks.run_cmd("exit 0", "x" * 2_000_000, 30) == []
    assert checks.run_cmd("grep -q end", "x" * 2_000_000 + " end", 30) == []
    assert time.monotonic() - t0 < 10


def test_check_cmd_cancel_stops_its_process_group(monkeypatch):
    monkeypatch.setattr(agy, "POLL", 0.1)
    seen = []
    t0 = time.monotonic()
    try:
        with pytest.raises(Cancelled) as e:
            checks.run_cmd("sleep 30 & sleep 30", "text", 30, on_start=lambda pid, when: seen.append(pid),
                           should_stop=lambda: "cancel" if seen else None)
        assert e.value.code == 27 and time.monotonic() - t0 < 10 and not agy.alive(seen[0])
    finally:
        kill_group(seen)


def test_check_cmd_failing_stop_check_stops_the_group_and_propagates(monkeypatch):
    monkeypatch.setattr(agy, "POLL", 0.1)
    monkeypatch.setattr(agy, "KILL_AFTER", 1)
    seen, polls = [], []

    def should_stop():
        polls.append(1)
        if len(polls) == 2:
            raise OSError("cancel flag unreadable")

    t0 = time.monotonic()
    try:
        with pytest.raises(OSError, match="cancel flag unreadable"):
            checks.run_cmd("sleep 30 & sleep 30", "text", 30, on_start=lambda pid, when: seen.append(pid),
                           should_stop=should_stop)
        assert time.monotonic() - t0 < 10 and not agy.alive(seen[0])
    finally:
        kill_group(seen)


def test_make_check_passes_run_options_and_does_not_wrap_a_cancel(tmp_path, monkeypatch):
    monkeypatch.setattr(agy, "POLL", 0.1)
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "marker").write_text("")
    check, _ = checks.make_check(check_cmd="test -f marker")
    assert check("text", cwd=str(tmp_path / "a")).ok
    assert not check("text", cwd=str(tmp_path / "b")).ok
    check, _ = checks.make_check(check_cmd="sleep 30")
    with pytest.raises(Cancelled):
        check("text", should_stop=lambda: "cancel")
