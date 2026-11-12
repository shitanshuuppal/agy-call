import json

import pytest

from agycall.classify import classify, envelope

CUT_OFF = ("Your previous response was cut off because it exceeded the output token limit\n"
           "Please continue from where you left off, keeping your response shorter\nRetries remaining: 3")
DENIED_NOTICE = ('jetski: no output produced, a tool required the "write_file" permission that headless mode cannot '
                 "prompt for, so it was auto-denied.\n")


def env(**fields):
    fields.setdefault("status", "SUCCESS")
    return json.dumps(fields).encode("utf-8")


def test_reply():
    o = classify(0, env(response="the answer", conversation_id="c1",
                        usage={"input_tokens": 5, "output_tokens": 10, "thinking_tokens": 2, "bogus": "x"}), "")
    assert o.name == "reply" and o.response == "the answer" and o.conversation == "c1"
    assert o.usage == {"input": 5, "output": 10, "thinking": 2} and o.status == "SUCCESS" and o.exit == 0


def test_denied_from_denied_actions_beats_everything_after_it():
    o = classify(0, env(response="", conversation_id="c1",
                        denied_actions=[{"action": "write_file", "display_name": "WriteFile"}]), DENIED_NOTICE + "quota\n")
    assert o.name == "denied" and o.denied == ["write_file"] and o.conversation == "c1"


def test_denied_from_stderr_without_json():
    assert classify(1, b"", "Error: permission check failed for RunCommand\n").name == "denied"


def test_output_limit_keeps_the_reply():
    o = classify(1, env(status="ERROR", response="- Files changed: a.py", error=CUT_OFF, conversation_id="c1"), "")
    assert o.name == "output-limit" and o.response == "- Files changed: a.py"
    assert "output token limit" in o.reason


def test_quota_auth_and_model_from_error_text():
    assert classify(1, env(status="ERROR", response="", error="RESOURCE_EXHAUSTED: quota exceeded. Resets in 10m"), "").name == "quota"
    assert classify(1, b"", "429 Rate Limit reached\n").name == "quota"
    assert classify(1, b"", "Error: unauthenticated; run agy once to sign in\n").name == "auth"
    o = classify(1, b"", 'Error: invalid --model "nope"\n')
    assert o.name == "model" and o.reason == 'Error: invalid --model "nope"'


def test_timeouts():
    assert classify(None, b"", "", stopped="deadline").name == "timeout"
    o = classify(0, env(response="half an ans", conversation_id="c1"), "print timeout after 10m0s, returning partial output\n")
    assert o.name == "timeout" and o.response == "half an ans"
    assert classify(1, b"", "context deadline exceeded\n").name == "timeout"


def test_cancel_wins():
    assert classify(None, env(response="x"), "quota", stopped="cancel").name == "cancelled"


def test_reply_with_error():
    assert classify(1, env(status="ERROR", response="partial work", error="stream closed"), "").name == "reply-with-error"
    assert classify(1, env(response="ok"), "").name == "reply-with-error"  # SUCCESS but a non-zero exit


def test_empty():
    assert classify(0, env(response="", conversation_id="c1"), "").name == "empty"
    assert classify(1, env(status="ERROR", response=""), "").name == "empty"


def test_malformed_keeps_nothing_accepted():
    noisy = b"Warning: update available\n" + env(response="x")
    assert classify(0, noisy, "").name == "malformed"
    assert classify(0, env(response=["not", "a", "string"]), "").name == "malformed"
    assert classify(2, b"", "flag provided but not defined: -zzz\n").name == "malformed"


def test_signal_kill_is_named():
    o = classify(-9, b"", "")
    assert o.name == "malformed" and "killed by signal 9" in o.reason


def test_envelope_tolerates_raw_newlines_and_bad_bytes():
    assert envelope(b'{"status": "SUCCESS", "response": "a\nb \xff"}')["response"] == "a\nb �"
    assert envelope(b"[1, 2]") is None
