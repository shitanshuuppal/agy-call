import json
import os
import re
import subprocess
import time

import pytest

from agycall import quota, review, runner, store, task, worktree
from agycall.errors import Blocked, Fatal, GaveUp, Mismatch, Quota, RepoState
from agyturns import denied, empty, error_reply, git, model_error, partial, quota_error, reply


def _rv(job):
    return store.read_json(job.path("request.json"))["review"]


def _refs(repo):
    return git(repo, "for-each-ref", "--format=%(refname)", "refs/agy-call/").split()


def test_a_snapshot_holds_staged_unstaged_and_untracked_files_and_leaves_the_checkout_alone(repo):
    (repo / "a.txt").write_text("staged\n")
    git(repo, "add", "a.txt")
    (repo / "a.txt").write_text("staged\nunstaged\n")
    (repo / "c.txt").write_text("new\n")
    (repo / "build").mkdir()
    (repo / "build" / "out.o").write_text("ignored\n")
    index = (repo / ".git" / "index").read_bytes()
    status = git(repo, "status", "--porcelain")
    job = review.start(model="m")
    assert (repo / ".git" / "index").read_bytes() == index and git(repo, "status", "--porcelain") == status
    assert git(repo, "symbolic-ref", "--short", "HEAD").strip() == "main"
    rv = _rv(job)
    head = git(repo, "rev-parse", "HEAD").strip()
    assert rv["scope"] == "working-tree" and rv["base"] == head and rv["files"] == ["a.txt", "c.txt"]
    assert re.fullmatch(r"review-standard-[0-9a-f]{16}", job.id)
    assert git(repo, "show", rv["snapshot"] + ":a.txt") == "staged\nunstaged\n"
    assert git(repo, "rev-parse", rv["snapshot"] + "^").strip() == head
    assert git(repo, "rev-parse", rv["snapshot"] + "^{tree}").strip() == rv["tree"]
    assert git(repo, "ls-tree", "-r", "--name-only", rv["snapshot"]).split() == [".gitignore", "a.txt", "c.txt"]
    assert _refs(repo) == ["refs/agy-call/" + job.id]
    assert git(repo, "rev-parse", "refs/agy-call/" + job.id).strip() == rv["snapshot"]
    snap = rv["worktree"]
    assert snap == worktree.worktree_path(worktree.repo(), job.id + "-snap")
    assert git(snap, "rev-parse", "HEAD").strip() == rv["snapshot"] and open(os.path.join(snap, "c.txt")).read() == "new\n"
    assert subprocess.run(["git", "symbolic-ref", "-q", "HEAD"], cwd=snap).returncode == 1  # detached
    assert open(rv["schema"]).read() == review.SCHEMA_TEXT
    req = store.read_json(job.path("request.json"))
    assert req["kind"] == "review" and req["cwd"] == snap
    assert "git diff %s %s" % (head, rv["snapshot"]) in req["prompt"] and "\n- a.txt\n- c.txt\n" in req["prompt"]


def test_nothing_to_review_exits_26_and_writes_nothing(repo):
    with pytest.raises(RepoState, match="nothing to review"):
        review.start(model="m")  # auto on a clean checkout is the branch scope, and main has nothing past main
    with pytest.raises(RepoState, match="nothing to review"):
        review.start(model="m", scope="working-tree")
    assert _refs(repo) == [] and not os.path.exists(worktree.repo().store)


def test_the_branch_scope_reviews_from_the_merge_base_to_head(repo):
    git(repo, "checkout", "-q", "-b", "feature")
    (repo / "b.txt").write_text("b\n")
    git(repo, "add", "b.txt")
    git(repo, "commit", "-q", "-m", "b")
    fork = git(repo, "merge-base", "main", "HEAD").strip()
    git(repo, "checkout", "-q", "main")
    (repo / "m.txt").write_text("m\n")
    git(repo, "add", "m.txt")
    git(repo, "commit", "-q", "-m", "m")
    git(repo, "checkout", "-q", "feature")
    rv = _rv(review.start(model="m"))  # a clean checkout: auto is the branch scope
    assert rv["scope"] == "branch" and rv["base"] == fork and rv["files"] == ["b.txt"]
    assert rv["snapshot"] == git(repo, "rev-parse", "HEAD").strip()
    git(repo, "update-ref", "refs/remotes/origin/main", git(repo, "rev-parse", "main").strip())
    git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    git(repo, "branch", "-q", "-m", "main", "old")  # origin/HEAD is found first
    assert _rv(review.start(model="m", scope="branch", job_id="r2"))["base"] == fork


def test_a_branch_review_without_a_base_branch_asks_for_base(repo):
    git(repo, "branch", "-q", "-m", "main", "trunk")
    git(repo, "checkout", "-q", "-b", "feature")
    (repo / "b.txt").write_text("b\n")
    git(repo, "add", "b.txt")
    git(repo, "commit", "-q", "-m", "b")
    with pytest.raises(RepoState, match="pass --base REF"):
        review.start(model="m")
    with pytest.raises(RepoState, match="unknown base"):
        review.start(model="m", base="nope")
    assert _rv(review.start(model="m", base="trunk"))["files"] == ["b.txt"]
    with pytest.raises(ValueError, match="branch scope only"):
        review.start(model="m", scope="working-tree", base="trunk")


def test_a_review_from_a_subfolder_covers_the_whole_checkout_with_exact_file_names(repo, monkeypatch):
    (repo / "docs").mkdir()
    (repo / "docs" / "my file.txt").write_text("x\n")
    (repo / "a.txt").write_text("changed\n")
    monkeypatch.chdir(repo / "docs")
    assert _rv(review.start(model="m"))["files"] == ["a.txt", "docs/my file.txt"]


def test_a_checkout_without_a_git_identity_can_be_reviewed(repo):
    git(repo, "config", "--unset", "user.name")
    git(repo, "config", "--unset", "user.email")
    git(repo, "config", "user.useConfigOnly", "true")  # git refuses to guess one from the account and host names
    (repo / "a.txt").write_text("changed\n")
    rv = _rv(review.start(model="m"))
    assert git(repo, "show", rv["snapshot"] + ":a.txt") == "changed\n"


def test_the_identity_follows_the_content_and_the_settings_not_the_time(repo):
    (repo / "a.txt").write_text("changed\n")
    job = review.start(model="m")
    time.sleep(1.1)  # a later commit time
    (repo / "a.txt").write_text("changed\n")  # the same content written again
    again = review.start(model="m")
    assert again.id == job.id and _rv(again)["snapshot"] == _rv(job)["snapshot"]  # the record is reused
    r = worktree.repo()
    t = review.target(r.top)
    ident = review.identity(r.id, t, False, None, "m", None)
    assert review.default_id(ident, False) == job.id
    others = {review.identity(r.id, t, True, None, "m", None), review.identity(r.id, t, False, "races", "m", None),
              review.identity(r.id, t, False, None, "m2", None), review.identity(r.id, t, False, None, "m", "high")}
    assert ident not in others and len(others) == 4
    assert review.default_id(review.identity(r.id, t, True, None, "m", None), True).startswith("review-adversarial-")
    (repo / "a.txt").write_text("changed again\n")
    assert review.start(model="m").id != job.id


def test_a_job_id_given_twice_for_different_reviews_exits_24(repo):
    (repo / "a.txt").write_text("changed\n")
    review.start(model="m", job_id="r1")
    (repo / "a.txt").write_text("changed again\n")
    with pytest.raises(Mismatch, match="another --job"):
        review.start(model="m", job_id="r1")
    with pytest.raises(ValueError, match="branch name"):
        review.start(model="m", job_id="a..b")


def _finding(file, severity="MUST-FIX", start=1, end=2, **extra):
    f = {"severity": severity, "title": "Crash on empty input", "file": file, "line_start": start, "line_end": end,
         "how_hit": "Run it on an empty file.", "fix": "Return early on empty input."}
    f.update(extra)
    return f


def _review_json(files, verdict="changes-needed", findings=None, **extra):
    data = {"verdict": verdict, "summary": "One problem.", "files_reviewed": list(files),
            "findings": [_finding(files[0])] if findings is None else findings}
    data.update(extra)
    return json.dumps(data)


def _changed(repo, *names):
    for n in names:
        (repo / n).write_text("changed %s\n" % n)


def test_a_review_runs_in_its_snapshot_with_the_read_only_grant_and_the_schema(repo, fake, tmp_path):
    set_turns, launches = fake
    (tmp_path / "agy-config.json").write_text(json.dumps(
        {"userSettings": {"globalPermissionGrants": {"allow": ["command(gcloud run)", "command(git diff)"]}}}))
    _changed(repo, "a.txt")
    set_turns(reply(_review_json(["a.txt"])))
    job = review.start(model="m", effort="high")
    r = review.drive(job)
    rv = _rv(job)
    assert job.status()["state"] == "done" and json.loads(r.text)["verdict"] == "changes-needed"
    first = launches()[0]
    assert os.path.realpath(first["cwd"]) == os.path.realpath(rv["worktree"])
    assert first["settings"]["permissions"] == {"allow": ["command(%s)" % c for c in task.READ_ONLY],
                                                "deny": ["command(gcloud run)"]}
    argv = first["argv"]
    assert argv[argv.index("--json-schema") + 1] == rv["schema"] and argv[argv.index("--effort") + 1] == "high"
    assert argv[argv.index("--settings") + 1] == rv["grant"] and "--conversation" not in argv
    assert first["prompt"] == store.read_json(job.path("request.json"))["prompt"]
    assert "Assume the change has defects" not in first["prompt"]


def test_run_still_passes_no_json_schema_to_agy(tmp_path, fake):
    set_turns, launches = fake
    set_turns(reply('{"a": 1}'))
    (tmp_path / "s.json").write_text('{"type": "object"}')
    runner.call("j", "p", store=str(tmp_path / "s"), model="m", schema=str(tmp_path / "s.json"))
    assert "--json-schema" not in launches()[0]["argv"]


def test_the_adversarial_prompt_and_the_focus_text(repo):
    _changed(repo, "a.txt")
    p = store.read_json(review.start(model="m", adversarial=True, focus="error paths").path("request.json"))["prompt"]
    assert "Assume the change has defects" in p and "Give particular attention to: error paths\n" in p
    assert "Give particular attention" not in store.read_json(review.start(model="m").path("request.json"))["prompt"]


def test_the_findings_check_applies_the_schema_and_the_local_rules():
    check = review.checker(["a.txt", "b.txt"])
    v = check(_review_json(["a.txt", "b.txt"]))
    assert v.ok and json.loads(v.answer)["verdict"] == "changes-needed"
    v = check(_review_json(["a.txt", "b.txt"], findings=[_finding("a.txt", start=-4)], summary="", extra=1))
    assert not v.ok and sorted(v.findings) == ["$ has a key the schema does not allow: extra",
                                               "$.findings[0].line_start should be at least 1",
                                               "$.summary should have length 1 or more"]
    v = check(_review_json(["a.txt", "b.txt"], findings=[_finding("a.txt", start=5, end=2)]))
    assert v.findings == ["$.findings[0].line_start is greater than its line_end"]
    v = check(_review_json(["a.txt", "b.txt"], verdict="approve", findings=[_finding("a.txt", "SHOULD-FIX")]))
    assert v.findings == ["$.verdict is approve but there is a MUST-FIX or SHOULD-FIX finding"]
    v = check(_review_json(["a.txt", "b.txt"], verdict="approve",
                           findings=[_finding(None, "NIT", None, None), _finding("b.txt", "NIT", 3, None)]))
    assert v.ok
    assert "no JSON value" in check("Looks good to me.").findings[0]


def test_a_finding_on_a_file_outside_the_change_gets_a_format_repair_turn(repo, fake):
    set_turns, launches = fake
    _changed(repo, "a.txt")
    set_turns(reply(_review_json(["a.txt"], findings=[_finding("./a.txt")])), reply(_review_json(["a.txt"])))
    job = review.start(model="m")
    assert json.loads(review.drive(job).text)["findings"][0]["file"] == "a.txt"
    second = launches()[1]
    assert second["prompt"].startswith("Your review could not be used because its format is wrong")
    assert "'./a.txt' is not one of the changed files; use null or one of: a.txt" in second["prompt"]
    assert second["argv"][second["argv"].index("--conversation") + 1] == "c1"


def test_format_repairs_stop_at_the_turn_limit(repo, fake):
    set_turns, launches = fake
    _changed(repo, "a.txt")
    set_turns(reply("Looks good to me."))
    job = review.start(model="m")
    with pytest.raises(GaveUp, match="no JSON value"):
        review.drive(job)
    assert len(launches()) == 3 and job.status()["state"] == "failed"


def test_files_left_out_of_files_reviewed_end_done_as_incomplete_without_another_turn(repo, fake):
    set_turns, launches = fake
    _changed(repo, "a.txt", "b.txt")
    set_turns(reply(_review_json(["a.txt"], verdict="approve", findings=[])))
    job = review.start(model="m")
    data = json.loads(review.drive(job).text)
    assert data["verdict"] == "incomplete" and data["missing_files"] == ["b.txt"]
    assert len(launches()) == 1 and job.status()["state"] == "done"


def test_a_file_name_with_a_space_counts_as_reviewed(repo, fake):
    set_turns, _ = fake
    (repo / "docs").mkdir()
    (repo / "docs" / "my file.txt").write_text("x\n")
    set_turns(reply(_review_json(["docs/my file.txt"])))
    data = json.loads(review.drive(review.start(model="m")).text)
    assert data["verdict"] == "changes-needed" and "missing_files" not in data


def test_a_reply_that_came_with_an_agy_error_is_judged_by_the_check_alone(repo, fake):
    set_turns, launches = fake
    _changed(repo, "a.txt")
    set_turns(error_reply(_review_json(["a.txt"])))
    job = review.start(model="m")
    assert json.loads(review.drive(job).text)["verdict"] == "changes-needed"
    assert "agyError" in store.read_json(os.path.join(job.launched()[0], "result.json"))
    set_turns(error_reply("Looks good to me."))
    with pytest.raises(GaveUp, match="no follow-up was sent"):  # no format repair after an output-limit stop
        review.drive(review.start(model="m", job_id="r2"))
    assert len(launches()) == 2


def test_need_lines_and_refused_commands_block_a_review(repo, fake):
    set_turns, launches = fake
    _changed(repo, "a.txt")
    set_turns(reply("NEED: find . -name '*.py'"), denied(), reply("NEED: find ."))
    job = review.start(model="m")
    with pytest.raises(Blocked) as e:
        review.drive(job)
    assert e.value.code == 28 and job.status()["state"] == "blocked"
    assert job.status()["need"] == ["NEED: find . -name '*.py'"]
    with pytest.raises(Blocked):
        review.drive(job)  # a rerun keeps the stop and launches nothing
    assert len(launches()) == 1
    job = review.start(model="m", job_id="r2")
    with pytest.raises(Blocked):
        review.drive(job)
    assert launches()[2]["prompt"] == runner.NEED_ONLY and job.status()["denied"] == ["command"]


def test_an_empty_reply_gets_one_turn_that_asks_for_the_json_only(repo, fake):
    set_turns, launches = fake
    _changed(repo, "a.txt")
    set_turns(empty("c1"), reply(_review_json(["a.txt"])))
    assert review.drive(review.start(model="m")).text
    assert launches()[1]["prompt"] == runner.SUMMARY_REVIEW
    set_turns(empty("c2"))
    with pytest.raises(GaveUp, match="a review is never resent blind; review again under another --job"):
        review.drive(review.start(model="m", job_id="r2"))


def test_a_timeout_fails_the_review_and_keeps_the_partial_reply(repo, fake):
    set_turns, _ = fake
    _changed(repo, "a.txt")
    set_turns(partial('{"verdict": "app'))
    job = review.start(model="m")
    with pytest.raises(GaveUp, match="review again under another --job"):
        review.drive(job)
    assert open(os.path.join(job.launched()[0], "reply.txt")).read() == '{"verdict": "app'


def test_setup_errors_end_a_review_as_setup_error(repo, fake):
    set_turns, _ = fake
    _changed(repo, "a.txt")
    set_turns(model_error())
    job = review.start(model="m")
    with pytest.raises(Fatal):
        review.drive(job)
    assert job.status()["state"] == "setup-error" and job.status()["code"] == 14


def test_the_same_content_reviewed_later_returns_the_saved_review(repo, fake):
    set_turns, launches = fake
    _changed(repo, "a.txt")
    set_turns(reply(_review_json(["a.txt"])))
    first = review.drive(review.start(model="m")).text
    time.sleep(1.1)
    _changed(repo, "a.txt")  # the same content written again later
    assert review.drive(review.start(model="m")).text == first and len(launches()) == 1


def test_a_rerun_after_a_quota_stop_continues_the_conversation(repo, fake):
    set_turns, launches = fake
    _changed(repo, "a.txt")
    set_turns(dict(quota_error(), conversation="c1"), reply(_review_json(["a.txt"])))
    job = review.start(model="m")
    with pytest.raises(Quota):
        review.drive(job)
    quota.clear("m", quota.generation("m"))
    assert review.drive(review.start(model="m")).text and len(launches()) == 2
    assert job.status()["state"] == "done"
