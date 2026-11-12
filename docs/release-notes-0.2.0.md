# agy 0.2.0

The plugin is now called `agy` and hands coding tasks and reviews to Gemini the way the Codex plugin hands them to Codex.
The repository, the marketplace and the command-line runner keep the name `agy-call`.

## Upgrading

- Uninstall the old plugin once with `/plugin uninstall agy-call@agy-call`, then install the new one with `/plugin install agy --marketplace shitanshuuppal/agy-call`, or before Claude Code 2.1.275 with `/plugin marketplace add shitanshuuppal/agy-call` and `/plugin install agy@agy-call`.
- If you installed by hand with symlinks, remove the skill and guard hook symlinks and the guard hook's entry in `~/.claude/settings.json` before installing the plugin, so neither is loaded twice.
- Requirements: agy 1.5.6 or newer, Python 3.9 or newer, macOS or Linux.
- The Antigravity plugin is no longer needed: agy-call launches agy itself instead of going through agy-delegate.
- Run `/agy:setup` (or `agy-call setup`) after installing; it checks agy's version, flags, sign-in and model names without spending quota.

## New

- Slash commands `/agy:task`, `/agy:review`, `/agy:adversarial-review`, `/agy:status`, `/agy:result`, `/agy:cancel` and `/agy:setup`, and the `agy` subagent, which runs one agy-call command and returns its output unchanged.
  They run only when you ask for Gemini.
- `agy-call task` runs a coding task in its own git worktree on branch `agy/<job>`, with a permission grant made for that task and passed with `--settings`, a check command run after every turn, `--continue` for follow-up messages, and `--merge`, `--cleanup` and `--discard`.
- The grant lists the Antigravity app's shared grants from `~/.gemini/config/config.json` in its `deny` list, because agy adds them to every `--settings` run.
- `agy-call review` reviews the working tree or the branch in a read-only snapshot and returns findings marked MUST-FIX, SHOULD-FIX or NIT, checked against a schema; `--adversarial` and `--focus` have Gemini challenge the design.
- `--background` on `run`, `task` and `review` hands the job to a detached worker that keeps running after the Claude session ends; `agy-call status`, `result` and `cancel` follow it.
- `agy-call setup` checks the installation.
- A SessionStart hook records the Claude session in `AGYCALL_SESSION`, so `status`, `result` and `cancel` put the current session's jobs first.

## Changed

- `AGY_DELEGATE` and `AGYCALL_REAL_AGY` are replaced by `AGYCALL_AGY`, the path of the agy executable.
- `--mode` is passed to agy directly and also accepts `auto`.
- A reply that came back with an agy error, including an output-limit stop, is accepted only by a passing check you asked for (`--expect`, `--require`, `--schema` or `--check-cmd`), and no fix turn is sent in that conversation.
- `--check-cmd` runs in the job's working directory (`--cwd`).
- A `--schema` file may use only `type`, `enum`, `required`, `properties`, `items`, `additionalProperties: false`, `minLength`, `minimum` and `maximum`; any other keyword exits 2 before a launch.
- Exit 22 now means an unknown agy failure or output that is not a JSON envelope.
- New exit codes: 26 (repository state), 27 (cancelled) and 28 (blocked on permission).

## Unchanged

- The options of `run` and `batch`, the Python `call()` and `batch()` signatures, the job store layout, quota holds and existing job identities; saved answers are returned as before.
- Attempts saved by 0.1.0 (`delegate.out`, `agy-N.out`) are still read.
- The guard hook still refuses direct print-mode agy calls and the old wrapper scripts.
