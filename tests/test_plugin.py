import glob
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMMANDS = ("task", "review", "adversarial-review", "status", "result", "cancel", "setup")


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def front_matter(*parts):
    """The `key: value` lines between the opening and closing `---` lines, as a dict."""
    text = read(*parts)
    assert text.startswith("---\n"), parts
    head = text.split("---\n", 2)[1]
    return dict(line.split(": ", 1) for line in head.splitlines() if ": " in line)


def test_the_plugin_is_named_agy_in_the_agy_call_marketplace():
    plugin = json.loads(read(".claude-plugin", "plugin.json"))
    assert (plugin["name"], plugin["version"]) == ("agy", "0.2.0")
    market = json.loads(read(".claude-plugin", "marketplace.json"))
    assert market["name"] == "agy-call"
    assert [(p["name"], p["source"]) for p in market["plugins"]] == [("agy", "./")]


def test_each_command_has_its_front_matter_and_runs_only_when_the_user_asks():
    assert sorted(os.listdir(os.path.join(ROOT, "commands"))) == sorted(c + ".md" for c in COMMANDS)
    for name in COMMANDS:
        fm = front_matter("commands", name + ".md")
        assert {"description", "argument-hint", "allowed-tools", "disable-model-invocation"} <= set(fm), name
        assert "only when the user asks" in fm["description"], name
        assert fm["disable-model-invocation"] == "true", name
        assert "Bash(agy-call:*)" in fm["allowed-tools"], name


def test_the_agent_forwards_with_bash_only_and_only_when_the_user_asks():
    fm = front_matter("agents", "agy.md")
    assert fm["name"] == "agy"
    assert fm["tools"] == "Bash"
    assert "only when the user asks" in fm["description"]


def test_the_shipped_text_has_no_em_dash_and_no_banned_word():
    banned = re.compile(r"\b" + "hon" + "est", re.I)  # the word is spelled in two parts so this file passes its own rule
    paths = ["README.md"] + [p for pattern in ("docs/release-notes-*.md", "commands/*.md", "agents/*.md", "skills/*/SKILL.md",
                                               "hooks/*", ".claude-plugin/*.json")
                             for p in glob.glob(os.path.join(ROOT, pattern)) if os.path.isfile(p)]
    for path in paths:
        text = read(path)
        assert "\u2014" not in text, path
        assert not banned.search(text), path
