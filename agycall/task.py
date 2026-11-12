"""Coding tasks (spec 4): the grant file with its deny mirror, the wrapped prompt, and starting, continuing and driving a
task through the runner's task policy."""
import hashlib
import json
import os

from . import store as st

READ_ONLY = ("ls", "cat", "head", "tail", "wc", "grep", "rg", "git status", "git diff", "git log", "git show")
PROMPT = """You are working on a coding task in the git worktree {worktree}.
Make every change inside that folder; do not write files anywhere else.
The commands you may run are exactly these:
{commands}
Before running any command that is not on the list, stop and reply with one line `NEED: <command>` per command instead of trying it.
Do not commit, branch or push.
End with at most 15 lines: files changed, checks run and their results, anything not done; never include diffs or file contents.

The task:

{text}"""


def config_path():
    """agy's shared config; AGYCALL_AGY_CONFIG overrides the path (tests always set it)."""
    return os.path.expanduser(os.environ.get("AGYCALL_AGY_CONFIG") or "~/.gemini/config/config.json")


def shared_grants():
    """userSettings.globalPermissionGrants.allow of agy's shared config, which agy adds to every --settings run
    (spec 4.2). A missing file or key means none; a file of any other shape raises ValueError, because the deny mirror
    cannot be built from it."""
    path = config_path()
    bad = "cannot read agy's shared grants in %s; fix the file before starting a task" % path
    try:
        with open(path, encoding="utf-8") as fh:
            node = json.load(fh)
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as e:
        raise ValueError("%s (%s)" % (bad, e))
    for key in ("userSettings", "globalPermissionGrants", "allow"):
        if not isinstance(node, dict):
            raise ValueError(bad)
        node = node.get(key)
        if node is None:
            return []
    if not isinstance(node, list) or not all(isinstance(g, str) for g in node):
        raise ValueError(bad)
    return node


def check_allow(allow):
    for a in allow:
        if not isinstance(a, str) or not a.strip() or any(ch in a for ch in "\r\n()"):
            raise ValueError("an --allow entry must be a non-empty command without newlines or parentheses: %r" % (a,))


def commands(allow):
    """The commands a task may run: the read-only set, then the --allow entries, each once."""
    return list(dict.fromkeys(READ_ONLY + tuple(allow)))


def grant(worktree, allow):
    """The --settings content of a task (spec 4.2): writes inside the worktree and the allowed commands; every shared
    grant that is not exactly one of these goes in deny, which agy checks before allow."""
    own = ["write_file(%s)" % worktree] + ["command(%s)" % c for c in commands(allow)]
    return {"permissions": {"allow": own, "deny": list(dict.fromkeys(g for g in shared_grants() if g not in own))}}


def write_grant(path, worktree, allow):
    """Write the grant file with the deny mirror as the shared config is now; returns the file's SHA-256."""
    data = json.dumps(grant(worktree, allow), indent=1)
    st.write_atomic(path, data)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def prompt(worktree, allow, text):
    """The task text inside the fixed instructions of spec 4.4."""
    return PROMPT.format(worktree=worktree, commands="\n".join("- " + c for c in commands(allow)), text=text)
