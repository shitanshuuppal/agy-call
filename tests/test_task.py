import json

import pytest

from agycall import task


def _config(tmp_path, allow):
    """agy's shared config with these shared grants, at the path the conftest points AGYCALL_AGY_CONFIG to."""
    (tmp_path / "agy-config.json").write_text(json.dumps({"userSettings": {"globalPermissionGrants": {"allow": allow}}}))


def test_grant_mirrors_every_other_shared_grant_into_deny(tmp_path):
    _config(tmp_path, ["command(gcloud run)", "command(git status)", "write_file(/elsewhere)", "command(git)"])
    g = task.grant("/wt", ["npm test", "ls"])["permissions"]
    assert g["allow"] == ["write_file(/wt)"] + ["command(%s)" % c for c in task.READ_ONLY] + ["command(npm test)"]
    assert g["deny"] == ["command(gcloud run)", "write_file(/elsewhere)", "command(git)"]


def test_a_missing_shared_config_or_key_means_no_deny(tmp_path):
    assert task.grant("/wt", [])["permissions"]["deny"] == []
    (tmp_path / "agy-config.json").write_text(json.dumps({"userSettings": {"other": 1}}))
    assert task.grant("/wt", [])["permissions"]["deny"] == []


@pytest.mark.parametrize("content", [
    "{not json", "[]",
    json.dumps({"userSettings": {"globalPermissionGrants": {"allow": "command(x)"}}}),
    json.dumps({"userSettings": {"globalPermissionGrants": {"allow": [1]}}}),
    json.dumps({"userSettings": ["x"]})])
def test_an_unreadable_shared_config_is_refused(tmp_path, content):
    (tmp_path / "agy-config.json").write_text(content)
    with pytest.raises(ValueError, match="shared grants"):
        task.grant("/wt", [])


def test_allow_entries_and_the_wrapped_prompt():
    task.check_allow(["npm run build", "swift test"])
    for bad in ("", " ", "a\nb", "rm (x)"):
        with pytest.raises(ValueError):
            task.check_allow([bad])
    p = task.prompt("/wt", ["npm test", "ls"], "Add a flag.")
    assert "git worktree /wt." in p and "- npm test\n" in p and p.count("- ls\n") == 1 and p.endswith("\n\nAdd a flag.")
    assert "`NEED: <command>`" in p and "Do not commit, branch or push." in p and "at most 15 lines" in p
