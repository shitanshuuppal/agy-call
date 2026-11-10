import json

import pytest

from agycall import checks
from agycall.errors import CheckError


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
