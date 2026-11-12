import time

from agycall import cli, setup


def run_setup(capsys):
    code = cli.main(["setup"])
    return code, capsys.readouterr().out


def failed(out):
    return [line for line in out.splitlines() if line.startswith("FAIL")]


def test_setup_passes_with_a_signed_in_agy_and_sends_no_prompt(fake, capsys):
    _, launches = fake
    code, out = run_setup(capsys)
    assert code == 0, out
    assert failed(out) == []
    lines = out.splitlines()
    assert len(lines) == 8
    assert lines[3] == "ok   agy version 1.5.6 (1.5.6 or newer)"
    assert lines[4] == "ok   agy --help lists --settings, --json-schema, --effort, --output-format"
    assert lines[5] == "ok   agy models (agy is signed in)"
    assert lines[6:] == ["ok   model gemini-4-argon-high listed", "ok   model gemini-3.8-flash-low listed"]
    assert launches() == []


def test_an_old_agy_fails_the_version_check(fake, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_VERSION", "1.5.5")
    code, out = run_setup(capsys)
    assert code == 1
    assert failed(out) == ["FAIL agy version 1.5.5 (1.5.6 or newer)"]


def test_a_newer_minor_version_is_compared_as_a_number(fake, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_VERSION", "agy 1.10.0")
    code, out = run_setup(capsys)
    assert code == 0, out
    assert "ok   agy version 1.10.0 (1.5.6 or newer)" in out.splitlines()


def test_a_help_without_one_flag_fails_the_flag_check(fake, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_HELP_DROP", "--json-schema")
    code, out = run_setup(capsys)
    assert code == 1
    assert failed(out) == ["FAIL agy --help does not list --json-schema; update agy"]


def test_a_failing_agy_models_fails_the_login_check(fake, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_MODELS_FAIL", "1")
    code, out = run_setup(capsys)
    assert code == 1
    assert failed(out) == ["FAIL agy models (needs a signed-in agy): Error: unauthenticated; run agy once to sign in"]
    assert "model gemini" not in out


def test_a_model_listed_only_under_a_longer_name_is_not_listed(fake, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_MODELS", "gemini-4-argon-high-preview gemini-3.8-flash-low")
    code, out = run_setup(capsys)
    assert code == 1
    assert failed(out) == ["FAIL model gemini-4-argon-high not listed by agy models"]


def test_a_missing_agy_is_one_fail_line(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AGYCALL_AGY", str(tmp_path / "no-such-agy"))
    code, out = run_setup(capsys)
    assert code == 1
    assert failed(out) == ["FAIL agy not found (set AGYCALL_AGY or put agy on PATH)"]


def test_an_agy_that_is_not_executable_fails_without_a_traceback(tmp_path, monkeypatch, capsys):
    exe = tmp_path / "agy"
    exe.write_text("not a program\n")
    exe.chmod(0o644)
    monkeypatch.setenv("AGYCALL_AGY", str(exe))
    code, out = run_setup(capsys)
    assert code == 1
    assert [line.split(":")[0] for line in failed(out)] == ["FAIL agy --version", "FAIL agy --help", "FAIL agy models (needs a signed-in agy)"]
    assert "could not start it" in out


def test_an_agy_that_does_not_answer_is_stopped_after_the_timeout(tmp_path, monkeypatch, capsys):
    exe = tmp_path / "agy"
    exe.write_text("#!/bin/sh\nexec sleep 30\n")
    exe.chmod(0o755)
    monkeypatch.setenv("AGYCALL_AGY", str(exe))
    monkeypatch.setattr(setup, "TIMEOUT", 1)
    began = time.monotonic()
    code, out = run_setup(capsys)
    assert time.monotonic() - began < 20
    assert code == 1
    assert len(failed(out)) == 3
    assert "no answer within 1s" in out
