# agy plugin: Codex-style delegation and review for Gemini

Status: design, approved section by section on 2026-11-12, then revised after two Codex spec reviews; not yet built.

## 1. Purpose

agy-call becomes a Claude Code plugin named `agy` that lets the user hand work to Gemini through Google's Antigravity CLI (`agy`) the way the OpenAI Codex plugin hands work to Codex.
The user can ask for a coding task, a review or an adversarial review, run any of them in the background, check status, fetch results, cancel, and continue the same conversation.

### 1.1 What the user decided

- Write access comes from a per-job permission grant passed with `agy --settings`, not from the user's global agy settings and not from `--dangerously-skip-permissions`.
- Every coding task runs in its own git worktree on a new branch; Gemini edits files only; Claude checks the result, commits it on the task branch and merges it locally.
- Claude chooses the extra commands a task may run (`--allow`), on top of a fixed read-only set.
- Claude uses agy only when the user asks, by slash command or in plain words; it never delegates on its own initiative.
- A task refuses to start while the checkout has uncommitted edits or untracked files that are not ignored.
- A background job keeps running when the Claude session that started it ends.
- The plugin is renamed `agy`, so its commands are `/agy:task`, `/agy:review` and so on; the repository and the CLI keep the name `agy-call`.
- The Antigravity plugin (`antigravity-for-claude-code`) is removed once agy-call no longer depends on it.

### 1.2 Success criteria

- A coding task runs from start to a mergeable branch without the user editing agy's settings.
- A review returns a list of findings with a severity each, tied to the exact code state it reviewed.
- No paid reply is lost: every attempt's raw output is on disk before anything inspects it.
- A quota error stops every caller on the machine for that model until the reset time.
- Claude's context receives a short summary or a findings list; full output stays on disk.

### 1.3 Non-goals

- No stop-time review gate (the Codex plugin's optional Stop hook).
- No claim of filesystem containment for commands the user or Claude allows (see 4.1).
- No automatic review, re-review or fix round started by agy-call.
- No support for agy versions older than 1.5.6 and no Windows support (process groups and `fcntl` locks are POSIX).

## 2. Background

### 2.1 Today

agy-call launches agy-delegate, a shell wrapper from the Antigravity plugin, which launches `agy --output-format json -p <prompt>`.
A capture shim placed first on agy-delegate's PATH saves agy's raw stdout and stderr for each attempt.
agy-call saves every attempt before checking it, resumes the same conversation with a fix message when a reply fails its check, and keeps a machine-wide quota hold per model name.
A PreToolUse hook (`hooks/agy_guard.py`) refuses Bash commands that run `agy -p`, `agy-delegate` or `agy-job` directly.

### 2.2 The failure that started this (2026-11-10)

A coding handover failed in two ways.
First, agy in headless mode refused a shell command because no allow rule covered it, and Claude Code's safety classifier blocks a session from editing agy's global settings, so the rule could not be added.
Second, Gemini finished the work, but agy ended the turn with status `ERROR` and "Your previous response was cut off because it exceeded the output token limit" and exited 1.
agy-delegate prints nothing on a non-zero agy exit, so the reply in agy's JSON envelope was discarded; agy-call resent in the same conversation eleven times and every later turn failed with the same error, although those replies were about 800 tokens long.
Commit 18bb3a7 made agy-call read the reply from the saved envelope and stop on an output-limit error that has no reply; section 6 tightens that change.

### 2.3 Facts measured on agy 1.5.6 (three small headless calls, 2026-11-12)

- `--settings <file>` with `{"permissions": {"allow": ["write_file(<dir>)", "command(date)"]}}` granted exactly those for one run: a write inside `<dir>` succeeded, a write elsewhere was refused, and `date` ran.
- agy's log listed only the two `--settings` rules as the run's permissions; the rules in the user's global `settings.json` were not active.
- A second file, `~/.gemini/config/config.json`, was logged as loaded with its own allow rules; how those combine with `--settings` is not known yet (see 10.4).
- In headless mode the first refused action ends the whole run: agy returns an envelope with an empty `response` and `denied_actions` such as `[{"action": "command", "display_name": "RunCommand"}]`, and later steps never run.
- `denied_actions` names the kind of action only, not the command or path.
- A successful run's envelope `response` is byte-identical to what agy-delegate printed on stdout.

## 3. Architecture

### 3.1 Modules

All code stays standard-library Python in `agycall/`.

| Module | Purpose |
|---|---|
| `agy.py` (new) | Launch one agy run and save its streams; enforce the deadline; stop it on cancel. |
| `classify.py` (new) | Turn one finished run into exactly one outcome (section 5). |
| `runner.py` | Job loop: store, locks, quota hold, checks, follow-up turns; uses `agy.py` and `classify.py`. |
| `control.py` (new) | `status`, `result`, `cancel`, and the detached background worker. |
| `worktree.py` (new) | Create, inspect, hand back and remove task worktrees; build review snapshots. |
| `task.py` (new) | Build a coding task's prompt, grant and checks on top of `runner.call`. |
| `review.py` (new) | Build a review's target, prompt, grant, schema and checks on top of `runner.call`. |
| `checks.py` | Reply checks; the schema validator refuses keywords it does not enforce (8.4). |
| `quota.py`, `store.py`, `batch.py` | Kept; `store.identity` gains the fields listed in 7.2. |
| `cli.py` | Adds `task`, `review`, `status`, `result`, `cancel`, `setup`; keeps `run` and `batch`. |
| `shim.py` | Removed. |

### 3.2 Plugin surface

The plugin manifest name becomes `agy`; the marketplace stays `agy-call`.

| Slash command | Runs |
|---|---|
| `/agy:task [--background] [--continue JOB] [--model M] [--effort E] <task text>` | `agy-call task` through the `agy` subagent |
| `/agy:review [--background] [--scope auto\|working-tree\|branch] [--base REF]` | `agy-call review` |
| `/agy:adversarial-review [--background] [--scope ...] [--base REF] [focus text]` | `agy-call review --adversarial` |
| `/agy:status [JOB] [--all]` | `agy-call status` |
| `/agy:result [JOB]` | `agy-call result` |
| `/agy:cancel [JOB]` | `agy-call cancel` |
| `/agy:setup` | `agy-call setup` |

The `agy` subagent runs exactly one `agy-call` command and returns its stdout and stderr unchanged, including failures and the job id.
Its description says it is used only when the user asks for Gemini, and no command, skill or agent description invites proactive use.
`/agy:task` is where Claude decides the `--allow` list and the `--check-cmd` from the repository (build and test commands), and writes the task text to a prompt file.
The skill `agy-call` is rewritten around these commands and keeps the `run` and `batch` reference.
The guard hook stays and keeps blocking direct `agy -p`, `agy-delegate` and `agy-job`.
A SessionStart hook appends `export AGYCALL_SESSION=<session id>` to `$CLAUDE_ENV_FILE` so jobs record the Claude session that started them.

### 3.3 Where files live

- Task and review job records: `~/.agy-call/jobs/<repo id>/<job>/`, where `<repo id>` is the first 12 hex characters of the SHA-256 of the canonical path of the repository's git common directory.
- Task worktrees: `~/.agy-call/worktrees/<repo id>/<job>/`; review snapshot worktrees: `~/.agy-call/worktrees/<repo id>/<job>-snap/`.
- Job records are never inside a worktree, so a worktree write grant never covers them.
- Directories under `~/.agy-call` are created with mode 0700 and files with mode 0600.
- `run` and `batch` keep `--store` and `--namespace` and their current defaults.
- The quota holds stay in `~/.agy-call` as today.

## 4. Permissions and the task worktree flow

### 4.1 What the grant guarantees

agy's own file-writing tool can write only inside the task's worktree, provided the live checks in 10.4 pass; until they do, this is the design target and the plugin is not released.
Commands in the grant are trusted, not contained: a test command runs the repository's code, which can do anything the user's account can, and git commands honour the repository's own configuration.
The README and the skill state exactly this.
`--sandbox` is not used until a live check (10.4) shows what it restricts.

### 4.2 The grant file

For a task, agy-call writes `<record>/grant-<n>.json` and passes it with `--settings`:

```json
{"permissions": {"allow": ["write_file(<worktree>)", "command(ls)", "command(cat)", "command(head)", "command(tail)",
  "command(wc)", "command(grep)", "command(rg)", "command(git status)", "command(git diff)", "command(git log)",
  "command(git show)", "command(<each --allow entry>)"]}}
```

`find` is not in the read-only set because `find -exec` runs arbitrary commands.
A review's grant is the same list without `write_file` and without `--allow` entries.
`run` and `batch` never pass `--settings`, so existing scripts keep using the user's global rules.
The first grant's SHA-256 is part of the job identity, and the same grant file is passed again on every turn of its segment.
Adding `--allow` entries on `--continue` writes `grant-<n+1>.json`, recorded in that segment (4.7); each launch records which grant it used.
An `--allow` entry must be a non-empty string without newlines or parentheses; agy-call does not otherwise interpret it.

### 4.3 Starting a task

`agy-call task --job ID --model M [--effort E] [--allow CMD]... --check-cmd CMD [--check-timeout S] [--max-turns N] [--deadline T] [--background] --prompt-file F`, run from inside the repository.
`--check-cmd` is required; a repository with nothing to build or test passes `--check-cmd true` explicitly.
Steps, under a repository-wide lock (`<repo record dir>/repo.lock`) for steps 1 to 4:

1. Refuse with exit 26 when `git status --porcelain` shows staged, unstaged or untracked-but-not-ignored files; the message lists them and says to commit, stash or ignore them.
2. Record the current branch name and commit as `base_branch` and `base_commit`; refuse with exit 26 on a detached HEAD.
3. Refuse with exit 24 when branch `agy/<job>` or the worktree directory already exists for a different job record.
4. Run `git worktree add -b agy/<job> <worktree> <base_commit>`.
5. Write the grant, the wrapped prompt (4.4) and `request.json`, then run the job loop with `cwd=<worktree>`.

Job ids for tasks follow the existing rule `[A-Za-z0-9][A-Za-z0-9._-]{0,119}`, and in addition `git check-ref-format --branch agy/<job>` must succeed before any record or worktree is created (ids such as `a..b`, `a.lock` or `a.` are refused with exit 2); `run` and `batch` keep the existing rule only.

### 4.4 The wrapped prompt

agy-call puts the user's task text inside fixed instructions:

- the worktree path, and that every change goes there;
- the exact list of commands the grant allows;
- "Do not commit, branch or push";
- "Before running any command that is not on the list, stop and reply with one line `NEED: <command>` per command instead of trying it";
- "End with at most 15 lines: files changed, checks run and their results, anything not done; never include diffs or file contents".

### 4.5 Outcomes of a task turn

- Reply, no `NEED:` lines, worktree changed, check passes: state `done`.
- Check fails: a fix turn in the same conversation whose message is the last 4000 characters of the check's combined stdout and stderr, up to the turn budget (5.3); fix turns only repair failures of the caller's check.
- A task's check command runs with the worktree as its working directory and the reply on stdin; exit 0 passes, any other exit status is a repairable failure, and only a check timeout or a failure to start it is a check error (exit 21). `run` and `batch` keep today's contract (0 pass, 1 reject, other exits are check errors) and gain the same working-directory rule: the check runs in the job's `cwd`.
- The worktree has no change at all (`git status --porcelain` in the worktree is empty): treated as a failed check with the finding "no file in the worktree changed".
- `NEED:` lines in the reply, or a refused action (5.1): state `blocked` (4.6).
- Output-limit error with a reply: the reply goes through the same checks; pass gives `done` with the error recorded, fail gives `failed` and no further automatic turn in that conversation.

### 4.6 Blocked on permission

The job keeps its worktree, conversation and reply, and records the denied action kinds and any `NEED:` lines in `status.json`.
When the refused kind is `command` and the reply has no `NEED:` line, agy-call sends one follow-up turn, with the same grant, asking only for the `NEED:` lines of the commands it tried; this turn counts against the budget.
agy-call never widens a grant by itself.
The skill tells Claude it may continue with `--continue --allow <cmd>` for build, test or read commands confined to the repository, and must ask the user first for anything that reaches outside it: network access, deletion outside the worktree, package installs, `git push`, or credentials.

### 4.7 Continuing a task

`agy-call task --continue --job ID [--allow CMD]... [--max-turns N] [--deadline T] [--background] --prompt-file F` appends a segment to the existing job instead of creating a new request.
Under the job lock it verifies that the recorded repository id, worktree and conversation id still match, then writes `segments/NNN.json` with the new prompt, the grant file and digest it uses, and its own budget.
The job's identity in `request.json` stays the original one, so `--continue` never exits 24; the previous accepted answer moves from `answer.txt` to `segments/NNN-answer.txt` before the new segment starts, so the saved-answer shortcut does not return it.
Each segment has its own `--max-turns` (default 3) and `--deadline` (default `2h`); attempt numbering and history continue across segments.
A pending `cancel.request` is renamed to `cancel.request.<segment>.done` when the segment starts, so an old cancel does not stop the new turn.
It refuses with exit 25 while the job is running and with exit 26 when the worktree is missing.
It never uses agy's `--continue` flag, which picks the machine's most recent conversation.
`--new-version` on a task archives the old record only when its worktree has been cleaned up or discarded (4.9); otherwise it refuses with exit 26.

### 4.8 Hand-back and merge

`agy-call result JOB` for a task prints Gemini's summary, `git status --porcelain` and `git diff --stat` of the worktree, the worktree path, the branch and the base commit.
The skill then directs Claude to:

1. Read the diff and run the build and tests itself in the worktree.
2. Commit on `agy/<job>` with the user's git identity and a message naming the job.
3. Run `agy-call task --merge JOB`.
4. Never push.

`task --merge JOB` takes the job lock without blocking (exit 25 when the job is running or another command holds it) and the repository lock, and holds both for all of its steps, so no turn changes the worktree and no two tasks integrate at the same time:

1. Refuse with exit 26 when the task worktree has uncommitted changes, when the main checkout is not on `base_branch`, or when the main checkout is not clean; record the task branch tip as the task commit.
2. Refuse with exit 26 and the message "base moved: merge <base_branch> into agy/<job>, test, commit, and run --merge again" when the current tip of `base_branch` is not an ancestor of `agy/<job>`; the user's or Claude's merge of the base into the task branch happens outside agy-call, under normal review.
3. Run the job's check command in the task worktree on the task branch tip; a failure refuses the merge with exit 20 and prints the check output.
   After the check, refuse with exit 26 unless the worktree is still clean and both its HEAD and the tip of `agy/<job>` still equal the recorded task commit; any change the check made stays in the worktree.
4. Run `git merge --no-ff --no-edit -m "Merge agy task <job>" agy/<job>` in the main checkout with `GIT_EDITOR=true`.
   On any non-zero exit (a conflict, a failing hook, a signing failure), save git's output in `merge-failed.json`, run `git merge --abort` when `MERGE_HEAD` exists, and exit 26 with the conflicting files if any; if the abort also fails, exit 26 saying the main checkout has an unfinished merge, and leave it for the user.
5. Confirm that the new `HEAD` of `base_branch` is a merge commit whose parents are the base tip and the task commit, then write `merge.json`: the task commit, the merge commit, the base tip, the check command, its exit status and the time.

Because step 2 requires the base tip to be inside the task branch, the tree that step 3 tested is exactly the tree the merge produces.

### 4.9 Cleanup

`agy-call task --cleanup JOB` removes the worktree and deletes the branch only when `agy/<job>` is an ancestor of `base_branch`.
`agy-call task --discard JOB` removes an unmerged task only when its worktree has no uncommitted changes; it never passes `--force` to git.
Both take the repository lock, and neither runs while the job is running.
The job record is kept after cleanup.

## 5. Failure handling

### 5.1 Classification

`classify.py` takes the process exit status, the raw stdout bytes, the stderr text, and the controller's own flags (deadline passed, cancel requested) and returns one outcome.
It reads stderr and the envelope's `status`, `error` and `denied_actions` fields; it never searches the text of `response`.
Rules, applied in this order:

| Order | Outcome | Detection |
|---|---|---|
| 1 | `cancelled` | the controller stopped the run because of a cancel request |
| 2 | `timeout` | the controller's deadline fired, or stderr matches `print timeout after .*returning partial output`, or the error text contains "timed out" or "deadline exceeded" |
| 3 | `missing` | agy could not be started (spawn error) |
| 4 | `denied` | `denied_actions` is non-empty, or the error text contains "user denied permission", "permission check failed", "auto-denied" or "permission that headless" |
| 5 | `output-limit` | the error text contains "exceeded the output token limit" |
| 6 | `quota` | the error text contains "quota", "rate limit" or "resource exhausted" |
| 7 | `auth` | the error text contains "unauthenticated", "unauthorized", "sign in", "please authenticate" or "reauth" |
| 8 | `model` | the error text contains "invalid --model", "is not recognized as a known model" or "not a known model" |
| 9 | `malformed` | stdout is not one JSON object with a string `status` (raw bytes are kept) |
| 10 | `reply` | `status` is `SUCCESS`, exit status 0, and `response` is not blank |
| 11 | `reply-with-error` | `response` is not blank but `status` is not `SUCCESS` or the exit status is not 0 |
| 12 | `empty` | `response` is blank and nothing above matched |
| 13 | `unknown` | anything else |

"Error text" is the envelope's `error` field followed by stderr, compared case-insensitively.
Row 3 is raised by the launcher as exit 13 when agy cannot be found or started, rather than returned by the classifier.
Row 13 cannot be reached once rows 1 to 12 exist (a blank reply is `empty`, a non-blank one is `reply` or `reply-with-error`, and an envelope whose `response` is not a string counts as `malformed`), so it is not implemented.
Rules 4 to 8 apply whatever the exit status is, and before the envelope is required to parse, because agy has reported failures with exit 0, with exit 1, and with no JSON on stdout.
The outcome record keeps the raw exit status, the envelope `status`, the outcome name and the public exit code as separate fields.

### 5.2 Actions per outcome

| Outcome | Task and review jobs | `run` and `batch` |
|---|---|---|
| `reply` | run the checks; pass gives `done`, fail gives a fix turn within the budget | unchanged |
| `reply-with-error`, `output-limit` with a reply | run the checks; pass gives `done` with the agy error recorded; fail gives `failed`, no further automatic turn in that conversation | same as tasks; this replaces the fix turn added in 18bb3a7 |
| `output-limit` without a reply | `failed`, no further automatic turn | same |
| `empty` | with a conversation id: one turn asking for the final summary only; without one: `failed` (a coding task is never resent blind) | one resend of the same message, as today |
| `denied` | `blocked` (4.6) | today's denied follow-up, then exit 20 on a second denial |
| `quota` | set the hold, state `quota`, exit 10; a later rerun continues the same conversation | unchanged |
| `auth`, `model`, `missing`, `malformed` | state `setup-error`, no retry | exit 11, 14, 13 and 22 respectively, as today's codes |
| `timeout` | `failed` with the partial reply kept; `--continue` can resume | today's restart turn |
| `cancelled` | `cancelled` | `cancelled` (exit 27) |
| `unknown` | `failed`, nothing resent | exit 22 |

For both `reply-with-error` and `output-limit` with a reply, acceptance requires a check the caller asked for (`--expect`, `--require`, `--schema`, `--check-cmd` or a Python check); without one, the reply is saved and the job ends `failed` (exit 20). This applies identically to `run`, `batch`, tasks and reviews; tasks and reviews always have a check.

### 5.3 Budgets

For task and review jobs, `--max-turns` (default 3) counts every launch in the current segment (the first run, or one `--continue`, 4.7), including retries and the `NEED:` follow-up, and `--deadline` (default `2h`) bounds that segment's total wall-clock time across turns; per-turn `--timeout` defaults to `60m` for tasks and `20m` for reviews.
`run` and `batch` keep today's counting, where plain retries do not use up turns.

### 5.4 Job states

`status.json` holds one `state`:

| State | Meaning |
|---|---|
| `queued` | the background worker exists; agy not launched yet |
| `running` | agy is running |
| `checking` | the check command is running |
| `done` | reply received and checks passed |
| `blocked` | refused action or `NEED:` lines; waiting for a widened grant |
| `quota` | a quota hold stopped the job |
| `failed` | gave up; reply and worktree kept |
| `setup-error` | agy missing, not logged in, unknown model, malformed output |
| `cancelled` | stopped by `cancel` |
| `interrupted` | the controller died and the saved output did not settle the outcome |

## 6. Launcher

`agy.py` builds `[agy, "--model", M, "--print-timeout", T, "--output-format", "json"]`, then `--settings F`, `--effort E`, `--json-schema S`, `--mode MODE`, `--add-dir D` and `--conversation ID` when given, then `-p <prompt>`.
The agy path comes from `AGYCALL_AGY` or `agy` on PATH; `AGY_DELEGATE` and `AGYCALL_REAL_AGY` are no longer read.
The prompt is passed as one argument and stdin is `/dev/null`; a prompt over 512 KiB is refused before launch with exit 2 and a message to put the material in a file under the working directory.
stdout and stderr go straight to `attempts/NNN/agy.out` and `agy.err`; the argument list goes to `agy.args` with the prompt replaced by its length and SHA-256 (the prompt itself is in `launch.json`).
After the run, the envelope's `response`, when present, is also written to `reply.txt`, so a person can read the reply without parsing JSON.
agy runs in its own process group; `launch.json` records the pid, the process group id and the process start time (from `ps -o lstart= -p <pid>`), written immediately after the process starts.
The controller polls once per second instead of blocking: it stops the run when the turn deadline (the turn timeout plus 60 seconds) passes or a cancel request appears (7.3), sending SIGTERM to the process group, then SIGKILL after 10 seconds.
The job lock descriptors are inherited by agy, as today, so a second controller cannot launch while agy lives.
The capture shim and every agy-delegate code path are removed.

## 7. Job control

### 7.1 Background worker

`--background` on `task` or `review` validates the request, creates the job record with state `queued`, starts a detached worker (`python3 -m agycall.cli worker <record>` in a new session with stdio on `<record>/worker.log`), prints the job id and exits 0.
The worker survives the end of the Claude session that started it.
Without `--background`, the command runs the job in the foreground and prints the result.

### 7.2 Identity

A task job's identity adds the repository id, `base_commit`, the grant hash and the check to today's fields.
A review job's identity does not use `store.identity`; it is the SHA-256 of a canonical JSON payload with exactly these fields: repository id, resolved base commit, snapshot tree id, scope, adversarial flag, focus text, model, effort and the schema's SHA-256.
It excludes the job id, the snapshot commit id and its metadata, every generated path and the rendered prompt, so recreating a snapshot of identical content at another time gives the same identity.
agy-call computes the identity and the job id first (8.6), and only then creates the snapshot ref, the snapshot worktree and the prompt.
Rerunning with the same id and identity returns the saved result; a different identity exits 24 unless `--new-version` is given, as today.
A returned conversation id that differs from the requested one stops the job with state `failed` instead of only being recorded.

### 7.3 Status, result, cancel

`status [JOB] [--all]` lists jobs of the current repository; jobs whose recorded session equals `AGYCALL_SESSION` come first, then the others, with state, kind, age and last finding; `--all` lists every repository.
`result [JOB]` prints a finished job's output (the task hand-back of 4.8 or the review rendering of 8.5) and prints failed and blocked jobs' findings and saved reply as well; with no job it uses the newest finished job of the current session, and when several jobs qualify for `cancel` without an id it refuses and lists them.
`cancel JOB` writes `<record>/cancel.request` without taking the job lock, then waits up to 20 seconds and prints `cancelled`, `finished before cancel` or `still stopping (pid N)`.
The worker checks for `cancel.request` before each launch, every second during a run or a check, and once more immediately before writing `done`.
When no live controller holds the job lock but the recorded process group is alive and its start time matches `launch.json`, `cancel` signals that group itself.
Cancel is idempotent and keeps partial output and the worktree.
Both agy and the check command run in their own process groups and both inherit the job lock descriptors; the worker records each in a durable ownership file (`launch.json` for agy, `attempts/NNN/check.json` for the check) with pid, process group id and start time, written immediately after the process starts, and cancel stops both.

### 7.4 Crash recovery

Recovery starts only after agy-call acquires the job lock without blocking; because agy and the check command both inherit that lock, acquiring it proves that neither is still running, and while either lives the job shows as `running` or `checking` and nothing is launched, checked or cleaned up.
`status` and `result` never wait for the lock; they report the recorded state and the live processes found from the ownership files.
When a rerun holds the lock and finds an attempt with `launch.json` but no `result.json`, it reads `agy.out`.
If it holds a complete envelope, the attempt is classified as if it had just finished, with these limits: the exit status is unknown, so a reply counts as `reply-with-error` (accepted only by a passing check, 5.2); a `cancel.request` or a passed deadline makes it `cancelled` or `timeout`; the conversation id is recovered; and nothing is resent.
When `check.json` exists without a recorded check result, the check runs again from the start on the saved reply, which is safe because the earlier check process is known to have exited.
Otherwise the attempt is marked `interrupted`; a task reports the worktree's current changes and sends no automatic turn, and `run` and `batch` keep today's restart turn.
If the controller died between starting agy and writing its pid, the inherited job lock still blocks a second launch, and `status` shows `running (pid unknown)` until agy exits; this window is a known limit.

## 8. Reviews

### 8.1 Command

`agy-call review [--job ID] --model M [--effort E] [--scope auto|working-tree|branch] [--base REF] [--adversarial] [--focus TEXT] [--background]`.
The slash commands pass `--model gemini-4-argon-high --effort high` unless the user names others.

### 8.2 Target

| Scope | Target |
|---|---|
| `working-tree` | staged, unstaged and untracked-not-ignored changes against `HEAD` |
| `branch` | `merge-base(BASE, HEAD)..HEAD`; `BASE` defaults to `origin/HEAD`, else `main`, else `master` |
| `auto` | `working-tree` when `git status --porcelain` is not empty, otherwise `branch` |

An empty target exits 26 with "nothing to review".

### 8.3 Snapshot

For `working-tree`, agy-call copies the index to a temporary index file, runs `git add -A` with `GIT_INDEX_FILE` pointing at the copy, writes a tree and creates a commit with parent `HEAD`; the user's index, files and branches are untouched.
For `branch`, the snapshot is `HEAD`.
The snapshot commit is kept under `refs/agy-call/<job>` until the review is cleaned up, and the base commit is recorded.
Gemini reads the code in a detached worktree of the snapshot (`<job>-snap`), with the read-only grant, so edits made during the review are not seen.
The prompt names the base and snapshot commits, tells Gemini to run `git diff <base> <snapshot>`, lists the changed files, and gives the severity bar: report MUST-FIX and SHOULD-FIX with how a user or author reaches each, list NITs separately.
The adversarial prompt also tells Gemini to assume the change has defects and to challenge the approach and design choices, with the focus text when given; findings may be design-level with no file.
`agy-call review --cleanup JOB` removes the snapshot worktree and the ref; the job record stays.

### 8.4 Findings format and checks

The reply must be one JSON object matching this schema, passed to agy with `--json-schema` and validated again locally:

```json
{"type": "object", "additionalProperties": false,
 "required": ["verdict", "summary", "files_reviewed", "findings"],
 "properties": {
  "verdict": {"enum": ["approve", "changes-needed", "incomplete"]},
  "summary": {"type": "string", "minLength": 1},
  "files_reviewed": {"type": "array", "items": {"type": "string"}},
  "findings": {"type": "array", "items": {"type": "object", "additionalProperties": false,
    "required": ["severity", "title", "file", "line_start", "line_end", "how_hit", "fix"],
    "properties": {
      "severity": {"enum": ["MUST-FIX", "SHOULD-FIX", "NIT"]},
      "title": {"type": "string", "minLength": 1},
      "file": {"type": ["string", "null"]},
      "line_start": {"type": ["integer", "null"], "minimum": 1},
      "line_end": {"type": ["integer", "null"], "minimum": 1},
      "how_hit": {"type": "string", "minLength": 1},
      "fix": {"type": "string", "minLength": 1}}}}}}
```

Local checks after the schema:

- each non-null `file` is one of the changed files;
- `line_start` is not greater than `line_end` when both are set;
- `approve` has no MUST-FIX or SHOULD-FIX finding;
- `files_reviewed` contains every changed file; when files are missing, the job ends `done` with the verdict forced to `incomplete` and the missing files listed, and no further turn is sent, because covering them is a new substantive review that the caller has to ask for.

Automatic fix turns in a review repair only the reply's format (schema and the line and file rules above), and an output-limit stop (5.2) overrides them.

The schema validator in `checks.py` supports `type` (including type lists), `enum`, `required`, `properties`, `items`, `additionalProperties: false`, `minLength`, `minimum` and `maximum`, and raises `CheckError` before any launch when a schema uses any other keyword.

### 8.5 What Claude receives

`result` prints the verdict, the number of findings at each severity, one block per finding (severity, title, file and lines, how it is reached, the fix), and the path of the full JSON.
For a review, `result` also compares the snapshot tree with the current state of the same scope and prints "code changed since this review (snapshot X, now Y)" when they differ.
agy-call never starts a fix or a second review; the skill tells Claude to follow the user's review-loop rules.

### 8.6 Default job id

When `--job` is omitted, a review's id is `review-<standard|adversarial>-<first 16 hex of the review's identity hash>`, where the identity is the review payload defined in 7.2.
A rerun with the same code and the same settings therefore returns the saved review without a launch, and any change to the code or the settings gives a different id and a new review.

## 9. Setup

`agy-call setup` checks, without spending quota: Python 3.9 or newer, macOS or Linux, the agy executable, `agy --version` at least 1.5.6, that `agy --help` lists `--settings`, `--json-schema`, `--effort` and `--output-format`, `agy models` (which needs a login, so it also confirms authentication), and that the model names in the skill are listed.
It prints one line per check and exits 0 only when all pass.

## 10. Testing

### 10.1 Offline suite

All tests run without network or quota.
A new `tests/fake_agy.py` replaces `tests/fake_delegate.py` and the shim tests: it plays a scenario of turns, each giving the exit status, the envelope fields (`status`, `response`, `error`, `denied_actions`, `conversation_id`, `usage`), stderr text, a delay, and files to write in its working directory; it logs its argument list, its working directory and the content of the `--settings` file.
The 117 existing tests are ported to it; tests that only described agy-delegate behaviour are replaced by classifier tests.

### 10.2 New tests

- Classifier: one test per row of 5.1, using agy's own message lines (not reply text from any real job).
- Launcher: deadline kill of a sleeping fake, process group recorded, prompt size limit, stdin closed.
- Task worktrees, in temporary git repositories: refusal on dirty or untracked files, detached HEAD, branch clash, a job id whose branch name git refuses, the grant written and passed, the check running in the worktree with a non-zero exit sent back as a fix turn, the `NEED:` and denial paths to `blocked`, no-change failure, cleanup refusing an unmerged branch, discard refusing uncommitted changes.
- Continuation: `--continue` after `done`, `blocked` and `cancelled` reuses the worktree and conversation, writes a segment with a new grant and budget, moves the old answer aside, consumes an old cancel request, and never exits 24.
- Merge: refusal on a running job, a dirty worktree, a dirty or wrong main checkout, a moved base, a failing check and a check that edits tracked files; a conflict and a failing merge-commit hook both abort cleanly and leave no `MERGE_HEAD`; `merge.json` is written only after the merge commit is confirmed; two merges in parallel are serialised by the repository lock.
- Reviews: snapshot includes staged, unstaged and untracked files and leaves the index unchanged; empty target; schema and local checks, with format-repair turns tested separately; missing coverage returns the saved findings with verdict `incomplete`, lists the missing files and launches no follow-up; stale-result warning; identical working-tree content snapshotted at two different times gives the same identity and reuses the saved review.
- Cancel: during a run, during a check, with the worker dead and agy alive, finishing before the cancel, a repeated cancel.
- Crash recovery: complete envelope without `result.json` (counted as `reply-with-error`), incomplete output, a check process still alive (nothing runs until it exits), a check that died without a result (it runs again), and a cancel request found during recovery.
- Schema validator: unsupported keyword raises `CheckError` before launch; an object with an empty summary, `line_start: -4` and an extra field is rejected.
- `reply-with-error` without a check is not accepted.
- Guard hook tests stay.

### 10.3 Process

Each build step is test-first; each gets one Codex review following the user's review-loop rules.

### 10.4 Live checks

`tests/live/smoke.sh` runs real agy on Gemini 3.8 Flash Low and is run only after the user approves the quota use.
It repeats the permission measurement, runs a three-file task and a review in a temporary repository, cancels a running job, and answers four open questions: whether a compound command such as `swift build && swift test` matches a `command(...)` grant, what `~/.gemini/config/config.json` adds to a `--settings` run, whether the grant applies on a resumed conversation, and what `--sandbox` restricts.
The answers are written into this document before the plugin is released.
Release requires all of these to pass:
- with `--settings`, no write outside the granted worktree succeeds, whatever `~/.gemini/config/config.json` contains;
- on a resumed conversation (`--conversation`), the same grant applies and nothing outside it is allowed;
- the command-matching results are recorded, and the grant construction in 4.2 is adjusted to them (for example, telling Gemini to run commands one at a time if compound commands do not match).
If either of the first two fails, release is blocked until the design is revised.

## 11. Migration

- Plugin name changes from `agy-call` to `agy`; install becomes `/plugin install agy@agy-call`; existing installs uninstall `agy-call@agy-call` once; the README and the v0.2.0 release notes say so.
- A user with the manual symlink install removes the skill and hook symlinks before installing the plugin, so the skill is not loaded twice.
- Unchanged: the `run` and `batch` options, the Python `call()` and `batch()` signatures, the job store layout, quota holds and existing job identities; saved answers are returned as before.
- Recovery reads both attempt layouts: old (`delegate.out`, `agy-N.out`) and new (`agy.out`).
- Changed: `AGY_DELEGATE` and `AGYCALL_REAL_AGY` are replaced by `AGYCALL_AGY`; exit 22 means an unknown agy failure; `--mode` goes to agy directly and also accepts `auto`; a reply recovered after an agy error needs a passing check; exit 26 (repository state) and 27 (cancelled) are new.

### 11.1 Removing the Antigravity plugin

1. Run the live checks (10.4) with the user's approval; stop here if they fail.
2. Release v0.2.0.
3. Search the user's code for scripts that call `agy-delegate` or `agy-job` directly and report each one.
4. Uninstall `antigravity@antigravity-for-claude-code`; leave its job folder untouched.
5. Keep the guard hook blocking `agy-delegate` and `agy-job`.
6. Propose wording for the user's own instructions that mention agy, for the user to approve.

## 12. Build order

1. Launcher, classifier and fake agy; port the existing tests; `run` and `batch` keep their behaviour except the changes listed in 5.2 and 11.
2. Background worker, `status`, `result`, `cancel`, crash recovery.
3. Worktrees and `task`.
4. `review`, snapshots and the schema validator.
5. Plugin rename, slash commands, subagent, skill, SessionStart hook, `setup`, README.
6. Live checks (10.4), which gate the release; then release, then removal of the Antigravity plugin (11.1).

## 13. Exit codes

| Code | Meaning |
|---|---|
| 0 | done; or a background job was started |
| 10 | quota hold |
| 11 | agy not authenticated |
| 13 | agy missing |
| 14 | model unavailable |
| 20 | gave up: budget used, a second denial in `run`, or an output-limit stop |
| 21 | the check itself failed to run |
| 22 | unknown agy failure or malformed output |
| 23 | no conversation id was captured for a needed follow-up (`run` and `batch`) |
| 24 | the job exists with a different request, or its branch or worktree belongs to another job |
| 25 | the job is running in another process |
| 26 | repository state: uncommitted or untracked files, detached HEAD, missing worktree, nothing to review |
| 27 | cancelled |
| 28 | blocked on permission |
