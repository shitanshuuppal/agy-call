# agy-call

## What it does

Claude Code can hand work to Google's Gemini through the `agy` CLI: a question, a review, or a batch of tasks.
Calling `agy` directly caused two problems.
When a reply came back in the wrong format, it was thrown away and the question was asked again in a new conversation, so the paid-for answer was lost.
When one task ran out of Gemini quota, the other tasks running at the same time did not find out and kept sending requests that could not succeed.

agy-call sits between Claude and Gemini and handles both:

- It saves every reply to disk before checking it, so no paid reply is lost.
- When a reply is unusable, it goes back to the same conversation and tells Gemini what to fix, instead of starting over.
- When one call hits the quota limit, it records a hold for the whole machine; every other call checks the hold before launching and waits until the limit resets.
- It can check answers for you, for example requiring valid JSON with certain fields, and sends a reply back when the format is wrong.

The optional guard hook stops Claude from calling `agy` directly, so every call goes through agy-call.

## Details

agy-call is a standard-library Python wrapper through which every call to agy (Antigravity, Gemini) goes, so no paid reply is lost and a quota error stops all callers.
Run the tests with `uvx pytest -q tests/`.
`skills/agy-call/SKILL.md` is the user guide.

## Install

Requirements: Python 3 (standard library only), the `agy` CLI installed and signed in, and the Antigravity Claude Code plugin (`antigravity-for-claude-code`), which puts its delegate script on the PATH inside Claude Code sessions.

### As a Claude Code plugin

In Claude Code 2.1.275 or later:

```
/plugin install agy-call --marketplace shitanshuuppal/agy-call
```

In older versions:

```
/plugin marketplace add shitanshuuppal/agy-call
/plugin install agy-call@agy-call
```

Restart Claude Code afterwards.
The plugin installs the skill, puts the `agy-call` command on the PATH inside Claude Code sessions, and turns on the guard hook, which refuses Bash commands that call agy directly so every call goes through agy-call.

### Manual install

Use this if you want `agy-call` on the PATH outside Claude Code too.
The steps assume the repo is cloned to `~/Code/agy-call`; use your own path if it differs.

```sh
git clone https://github.com/shitanshuuppal/agy-call.git ~/Code/agy-call
mkdir -p ~/.claude/skills/agy-call ~/.local/bin ~/.claude/hooks
ln -s ~/Code/agy-call/skills/agy-call/SKILL.md ~/.claude/skills/agy-call/SKILL.md
ln -s ~/Code/agy-call/bin/agy-call ~/.local/bin/agy-call
ln -s ~/Code/agy-call/hooks/agy_guard.py ~/.claude/hooks/agy_guard.py
```

`~/.local/bin` must be on the PATH; check with `agy-call --help`.

The guard hook is optional but recommended.
To enable it, add this entry under `hooks.PreToolUse` in `~/.claude/settings.json`, with your own absolute home path:

```json
{
  "matcher": "Bash",
  "hooks": [
    {"type": "command", "command": "python3 /Users/<you>/.claude/hooks/agy_guard.py", "timeout": 5}
  ]
}
```

Restart Claude Code so it loads the skill and the hook.
Scripts can import the package with `sys.path.insert(0, os.path.expanduser("~/Code/agy-call"))`.
