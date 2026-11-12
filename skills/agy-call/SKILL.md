---
name: agy-call
description: "Use only when the user asks for work to go to Gemini or agy (Antigravity), by an /agy: slash command or in plain words: a coding task, a review, a question, a batch of items, or a script that calls Gemini. Every agy call goes through agy-call, which saves every reply, resumes the same conversation when a reply is unusable, and stops all callers on quota. Never hand work to Gemini on your own initiative."
---

# agy-call

Gemini gets work only when the user asks for it, by a slash command or in plain words; never hand work to Gemini on your own initiative.
Every agy call goes through `agy-call`, which saves every reply before it checks it, resumes the same conversation when a reply is unusable, and keeps a machine-wide quota hold.

## Which command

| The user asks for | Slash command | agy-call command |
|---|---|---|
| a coding task done by Gemini | `/agy:task [--background] [--continue JOB] [--model M] [--effort E] <task text>` | `agy-call task` (see Coding tasks) |
| a review of the current change | `/agy:review [--background] [--scope auto\|working-tree\|branch] [--base REF]` | `agy-call review` (see Reviews) |
| a review that challenges the design | `/agy:adversarial-review [--background] [--scope ...] [--base REF] [focus text]` | `agy-call review --adversarial --focus TEXT` |
| how a job is going | `/agy:status [JOB] [--all]` | `agy-call status` |
| the outcome of a job | `/agy:result [JOB]` | `agy-call result` |
| to stop a job | `/agy:cancel [JOB]` | `agy-call cancel` |
| to check the installation | `/agy:setup` | `agy-call setup` |
| an answer to a question, or to many items | none | `agy-call run`, `agy-call batch` (see run and batch) |

When the user asks in plain words, do what the matching slash command does, as this skill describes it.
The slash commands pass `--model gemini-4-argon-high`, and reviews also `--effort high`, unless the user names others.
The `agy` subagent (`agy:agy`) runs exactly one agy-call command and returns its output unchanged; `/agy:task` starts tasks through it.
Run every agy-call command from inside the repository it is about.

## Why it exists

Calling agy directly has lost paid replies in two ways.
A reply that failed a check was thrown away and the question was asked again in a new conversation, so the work already paid for was lost.
A quota error in one process did not stop the other processes, which kept launching calls that could not succeed.
agy-call saves the raw output of every attempt before it looks at it, resumes the same conversation with a fix message when a reply is unusable, and writes a machine-wide quota hold that every caller checks before launching.
A PreToolUse hook (`hooks/agy_guard.py`) refuses Bash commands that run `agy -p`, `agy --print`, `agy-delegate` or `agy-job` directly, so use `agy-call` for all of them.
`agy models`, `agy --version` and other non-print agy commands are not blocked.
The hook matches the whole command text, so a command that only mentions `agy-delegate` or `agy-job` (an echo, a grep pattern, a prompt text) is refused too; a command that mentions the word agy together with -p or -i on the same line (for example a grep) is refused too, so rephrase, e.g. `grep -i 'ag[y]' file`; put such text in a file and pass the file (for example `--prompt-file`) instead of writing it in the command.
A SessionStart hook exports `AGYCALL_SESSION` (the Claude session id) to the session's Bash commands, so each job records the session that started it and `status`, `result` and `cancel` put this session's jobs first.

## Coding tasks

`agy-call task` hands a coding task to Gemini in its own git worktree on a new branch `agy/<job>`, with a permission grant made for that task only; use it instead of `run` for any work that edits files.

```
agy-call task --job ID --model M [--effort E] [--allow CMD]... --check-cmd CMD [--check-timeout S] [--timeout T] [--max-turns N] [--deadline T] [--background] [--new-version] --prompt-file F
```

- It refuses with exit 26 while the checkout has staged, unstaged or untracked files that are not ignored, or a detached HEAD, and with exit 24 when the branch `agy/<job>` or the worktree folder already exists for another job.
- The job id follows the job id rule and must also make a branch name git accepts, so ids such as `a..b`, `a.lock` or `a.` exit 2.
- The worktree is `~/.agy-call/worktrees/<repo id>/<job>/` and the job record `~/.agy-call/jobs/<repo id>/<job>/`, where `<repo id>` is the first 12 hex characters of the SHA-256 of the repository's git common directory; both folders are readable by you only.
- `--check-cmd` is required; pass `--check-cmd true` when there is nothing to build or test.
  It runs in the worktree with Gemini's reply on stdin; exit 0 passes, and any other exit sends the last 4000 characters of its combined stdout and stderr back to Gemini as a fix turn in the same conversation.
  A turn that changed no file fails the check with "no file in the worktree changed".
  Only a timeout or a failure to start the check is a check error (21) [`--check-timeout` 600 seconds].
  The check runs for at most the time left in the segment's `--deadline`, and a segment whose deadline passes before or during the check fails.
- `--max-turns` counts every launch, follow-ups and retries included [3]; `--deadline` bounds the whole run across turns [`2h`]; `--timeout` limits one turn [`60m`].
- Gemini receives your task text inside fixed instructions: the worktree path, the exact commands it may run, no commits, branches or pushes, one `NEED: <command>` line instead of trying a command that is not allowed, and a final summary of at most 15 lines without diffs or file contents.
- `--background` prints the job id and runs the task in a detached worker, which keeps running after the Claude session ends; follow it with `agy-call status` and `agy-call result JOB`.
- Run in the foreground, `task` prints the hand-back (see below) and exits with the job's code; this holds after a recorded failure too, such as a failed check or a quota stop, so the worktree and the last reply are shown either way.

### Starting a task for the user

1. Run `git status --porcelain`; when it lists anything, show the user the files and ask whether to commit, stash or ignore them, because the task refuses to start.
2. Choose a job id: a short slug of the task in lowercase letters, digits and hyphens, then the date and time, for example `fix-login-redirect-20261112-1430`.
3. Write the prompt file in your scratch directory, or in `$TMPDIR`, never inside the repository: a new file there makes the checkout unclean, and the task or its merge refuses with exit 26.
   It holds the task text and whatever from the conversation the task needs, written for a reader who has not seen the conversation, without instructions about commits, branches, permitted commands or the reply format.
4. Choose `--allow` entries: the build, test, lint and read commands of this repository that the task needs, taken from its own files (README, Makefile, `package.json` scripts, `Package.swift`, `pyproject.toml`, `Cargo.toml`, CI workflows).
   Never allow a command that reaches outside the repository: network access, package installs, deletion outside the worktree, `git commit`, `git push`, or anything that reads credentials.
5. Choose `--check-cmd`: the command that builds the repository and runs its tests, or `true`.
6. Tell the user the `--allow` entries and the check in one or two lines, then have the `agy` subagent run `agy-call task ... --background --prompt-file FILE` with each value in single quotes (write each `'` inside a value as `'\''`); this applies to every value: the check command, each `--allow` entry and every path.
7. Unless the user asked for the task to run in the background, wait: run `until agy-call result JOB >/dev/null 2>&1; [ $? -ne 25 ]; do sleep 30; done` with the Bash tool and `run_in_background: true`, then `agy-call result JOB`; when the user asked for the background, report the job id and stop.

### What the grant allows

agy-call writes `grant-<n>.json` in the job record and passes it to agy with `--settings`.
It allows writes inside the worktree, the read-only commands `ls`, `cat`, `head`, `tail`, `wc`, `grep`, `rg`, `git status`, `git diff`, `git log` and `git show`, and each `--allow` entry as `command(<entry>)`.
An `--allow` entry is one command without newlines or parentheses, for example `--allow "swift build" --allow "swift test"`; a compound command such as `swift build && swift test` runs when each part is allowed.
agy adds the Antigravity app's shared grants (`userSettings.globalPermissionGrants.allow` in `~/.gemini/config/config.json`) to every `--settings` run, so agy-call puts every shared grant that is not one of the task's own entries into the grant's `deny` list, and reads the file again each time a task runs.
A shared grant that is a prefix of one of the task's commands (a shared `command(git)` against the task's `command(git status)`) then blocks that command too; the task is refused on the safe side, and Gemini reports it as a `NEED:` line.
When that file cannot be read as JSON of the expected shape, `task` refuses to start (exit 2).
agy's `--sandbox` flag did not stop a granted command from writing outside the workspace when measured, so agy-call does not use it and `--sandbox` does not contain commands.
The grant also turns off agy's access outside the workspace (`allowNonWorkspaceAccess: false`), because agy otherwise follows the global `allowNonWorkspaceAccess` setting, which a per-job `--settings` file does not replace.
The grant keeps agy's own file-writing tool inside the worktree, but agy-call cannot limit what a granted command does: a test command runs the repository's code, which can do anything your account can, and git commands follow the repository's own configuration.

### When a task ends

- `done` (exit 0): agy-call prints the hand-back: Gemini's summary, `git status --porcelain` and `git diff --stat <base commit>` of the worktree, the worktree path, the branch and the base.
- `blocked` (exit 28): Gemini's reply had `NEED:` lines, or agy refused an action.
  Any refusal that includes a file write blocks at once.
  When agy refused only commands and the reply named none, agy-call first asks once, in the same conversation, for the `NEED:` lines; that turn counts against `--max-turns`.
  `status.json` records the refused action kinds (`denied`) and the `NEED:` lines (`need`), and agy-call never widens a grant by itself.
- `failed` (exit 20): the check still failed after `--max-turns`, the deadline passed, a turn timed out (its partial reply is kept), agy returned no reply even to the one turn that asks only for the summary, or a reply that came with an agy error failed the check.
- `setup-error` (exit 11, 13, 14 or 22): agy is not signed in or missing, the model is unknown, or agy's output was not a JSON envelope.
- A rerun of the same `task` command returns the saved result, or continues the conversation after a quota stop; it creates nothing and does not need a clean checkout.
  A different request under the same job id exits 24.
- When the process running a task died and its saved output does not settle the turn, the task ends `failed` and nothing is resent.

### Continuing a task

```
agy-call task --continue --job ID [--allow CMD]... [--max-turns N] [--deadline T] [--background] --prompt-file F
```

It reserves its segment under the job lock and sends a new message in the task's own conversation, in the same worktree, with its own `--max-turns` and `--deadline`; agy's own `--continue` flag, which picks the machine's most recent conversation, is never used.
`--allow` adds commands for this and later messages, in a new grant file.
The previous answer moves to `segments/NNN-answer.txt`, and a pending `cancel.request` is set aside, so neither ends the new turns.
It exits 25 while the task runs or while an earlier continuation is still queued for its worker (a second `--continue` waits until the first is taken), 26 when its worktree is missing, and 23 when the task never got a conversation id.

After `blocked`: you may continue with `--allow <command>` for build, test or read commands confined to the repository, with a prompt file that says those commands are now allowed.
Ask the user first for anything that reaches outside it: network access, deletion outside the worktree, package installs, `git push`, or credentials.

### Hand-back and merge

1. Read the diff in the worktree and run the build and tests there yourself.
2. Commit on `agy/<job>` in the worktree with the user's git identity and a message naming the job.
3. Run `agy-call task --merge JOB` from the repository.
4. Never push.

When the change falls short, tell the user what you found and ask whether to send it back with `--continue` or to fix it yourself.
`--merge` takes the job lock (exit 25 while the task runs) and the repository lock.
It refuses with exit 26 when the worktree has uncommitted changes, when the main checkout is not on the base branch or not clean, when the branch has no commit that the base branch lacks, or when the base branch moved on; in that last case merge the base branch into `agy/<job>`, test, commit and run `--merge` again.
It runs the check command on the branch tip in the worktree; a failing check exits 20 with its output, and a check that changes the worktree or the branch exits 26.
It then runs `git merge --no-ff -m "Merge agy task <job>"` in the main checkout; when git fails (for example a hook refuses), it saves git's output in `merge-failed.json`, runs `git merge --abort`, and exits 26.
It writes `merge.json` once it has confirmed the merge commit.

`agy-call task --cleanup JOB` removes the worktree and deletes the branch once the branch is merged into the base branch.
`agy-call task --discard JOB` drops a task, merged or not, when its worktree has no uncommitted changes.
Neither passes `--force` to git or runs while the task runs, and ignored files such as build output do not stop them.
The job record is kept, and `task --new-version` starts the same job id again only after `--cleanup` or `--discard` (otherwise exit 26).

A turn's reply and its thinking share one output limit, so a reply that carries a long diff can hit it; agy then fails with "exceeded the output token limit", and later turns in that conversation have failed the same way.
agy-call still checks a reply agy wrote before that error (it is in `reply.txt`); when there is none, it exits 20 without resending.

## Reviews

`agy-call review` has Gemini review the current change of the repository and returns a list of findings with a severity each, tied to the exact code it reviewed.

```
agy-call review --model M [--effort E] [--scope auto|working-tree|branch] [--base REF] [--adversarial] [--focus TEXT] [--job ID] [--max-turns N] [--timeout T] [--background]
agy-call review --cleanup JOB
```

- `--scope working-tree` reviews the staged, unstaged and untracked files that are not ignored, against `HEAD`.
- `--scope branch` reviews `merge-base(BASE, HEAD)..HEAD`; `--base` names BASE and defaults to `origin/HEAD`, else `main`, else `master`; `--base` applies to the branch scope only.
- `--scope auto` is `working-tree` when `git status --porcelain` shows anything and `branch` otherwise [`auto`].
- When the target has no change, it exits 26 with "nothing to review".
- agy-call keeps a snapshot of the target as a commit under `refs/agy-call/<job>` and checks it out in a detached worktree, `~/.agy-call/worktrees/<repo id>/.snap/<job>/`; your index, files and branches are not touched, and edits made while the review runs are not in it.
- Finding the scope never writes your index; the working-tree snapshot is built through a copy of it.
- The snapshot worktree is created with git hooks disabled, so no hook changes its files.
  A snapshot folder that already exists is reused on a rerun only when it is a clean, detached worktree at the recorded commit; anything else exits 26 and is left as it is.
- Gemini reads the snapshot with a grant of the read-only commands only (`ls`, `cat`, `head`, `tail`, `wc`, `grep`, `rg`, `git status`, `git diff`, `git log`, `git show`), with no file writes and no `--allow` entries, and with the same deny list of shared grants as a task.
- `--adversarial` tells Gemini to assume the change has defects and to challenge the approach and the design choices as well as the code; such a finding may have no file.
- `--focus TEXT` tells Gemini what to give particular attention to.
- Without `--job` the id is `review-standard-<16 hex>` or `review-adversarial-<16 hex>`, taken from the review's identity: the repository, the base commit, the snapshot's tree, the scope, the adversarial flag, the focus text, the model, the effort and the schema.
  The same code under the same settings returns the saved review without a launch, at any later time, and any change to the code or the settings starts a new review.
- `--max-turns` counts every launch [3]; a review may run 2 hours in all across its turns; `--timeout` limits one turn [`20m`].
- `--background` takes the snapshot, prints the job id and runs the review in a detached worker; follow it with `agy-call status` and `agy-call result JOB`.
  Without it, run the command with the Bash tool and `run_in_background: true`, because a review can take longer than one Bash call may run.
- Run in the foreground, `review` prints the result and exits with the job's code; this holds after a recorded failure too, so the state, the findings and the last reply are shown either way.

Gemini replies with one JSON object: a verdict (`approve`, `changes-needed` or `incomplete`), a summary, `files_reviewed`, and findings with a severity (`MUST-FIX`, `SHOULD-FIX` or `NIT`), a title, the file and lines, how the problem is reached and the fix.
agy passes the schema to Gemini with `--json-schema`, and agy-call checks the reply again.
A reply that breaks the schema, names a file outside the change, has `line_start` after `line_end`, or approves with a MUST-FIX or SHOULD-FIX finding gets a turn in the same conversation that asks only for the format to be fixed.
A reply that leaves changed files out of `files_reviewed` is kept with its verdict set to `incomplete` and the missing files listed, and no further turn is sent, because covering them is a new review that you have to ask for.
A reply that came with an agy error is accepted only when it passes these checks, and gets no format-repair turn.
`NEED:` lines or a refused command end the review `blocked` (exit 28), as for a task.
A review that ended `failed` or `blocked` keeps that stop on a rerun; to review again, pass another `--job`.

`agy-call result JOB` for a done review prints the verdict, the number of findings at each severity, one block per finding (severity and title, where, how it is reached, the fix) and the path of the full JSON.
When the code of the same scope has changed since the snapshot, it adds `code changed since this review (snapshot X, now Y)` with the two tree ids.
Show the findings to the user as agy-call prints them.
agy-call never starts a fix or a second review by itself, and neither do you: what happens next follows the user's own rules for review loops, so do not fix a finding, review again or start another reviewer unless the user asks.
`agy-call review --cleanup JOB` removes the snapshot worktree and the ref, and keeps the job record and its findings; it exits 25 while the review runs.

## Status, result and cancel

`agy-call status [JOB] [--all] [--store DIR] [--namespace NAME]` lists the jobs of a store, one line each: the id, the state, the kind (`run`, `task` or `review`), the age and the last finding.
Without `--store` or `--namespace`, `status`, `result` and `cancel` use the store of the current git repository when they run inside one (a task's worktree counts), and the default store otherwise; pass `--namespace default` for `run` jobs of the default store from inside a repository.
`--all` lists every store under `~/.agy-call/jobs`, repository stores and namespace stores, one block per store.
Jobs started in the current Claude session (the `AGYCALL_SESSION` variable, recorded in `request.json`) come first, then the others, newest first in each group.
With a job id it prints every field of the job's `status.json` and the processes still running for it: the controller (the agy-call process running the job), agy and the check command.
A queued, running or checking job with none of these alive lost its process; rerun the same command to recover it.

`agy-call result [JOB]` prints the accepted answer of a `done` job and exits 0.
For a task it then prints the hand-back (see Coding tasks), and for a `done` review it prints the findings instead of the JSON (see Reviews).
For any other state it prints the state, the findings and the last saved reply, and exits with that state's code: 10 quota, 20 failed, 21 check error, 23 no conversation, 25 queued, running or checking, 27 cancelled, 28 blocked, and for `fatal` and `setup-error` the setup code the job recorded (11, 13, 14 or 22).
An `interrupted` job exits 1.
Without a job id it uses the newest finished job of the current session.

`agy-call cancel [JOB]` asks a queued, running or checking job to stop by writing `<store>/<job>/cancel.request`, without waiting for the job's lock.
It then waits up to 20 seconds and prints `cancelled`, `finished before cancel (STATE)` when the job ended another way first, or `still stopping (pid N)` with exit 25.
When something still holds the job but no process owned by it can be found, it prints `still stopping (pid unknown)` and exits 25.
On a job that is not running it changes nothing and prints the job's state.
Without a job id it cancels the only running job of the current session, and refuses when there are several.
agy-call looks for the request before each launch, every second while agy or the check command runs, and once more just before it saves the answer; agy and the check command get SIGTERM, then SIGKILL after 10 seconds.
When the process running the job has died but agy or the check command still runs, `cancel` stops them itself.
A cancelled job stays cancelled: a rerun exits 27 without a launch, so to do the work anyway run it under a new job id, or continue a task with `task --continue`.
Replies already received are kept, and so is a task's worktree.

### Job states

| State | Meaning |
|-------|---------|
| `queued` | a background worker was started; agy has not been launched yet |
| `running` | agy is running |
| `checking` | the check command is running |
| `done` | a reply passed its checks; `answer.txt` holds the answer |
| `blocked` | a task's or a review's reply had `NEED:` lines or agy refused an action (exit 28); waiting for a wider grant |
| `failed` | gave up (exit 20); the replies are kept |
| `quota` | a quota hold stopped the job |
| `check-error` | the check itself failed to run |
| `no-conversation` | a follow-up was needed but no conversation id was captured |
| `fatal` | agy is missing or not signed in, the model is unknown, or agy's output was not a JSON envelope |
| `setup-error` | the same as `fatal`, for a task or a review |
| `cancelled` | stopped by `cancel` |
| `interrupted` | the process running the job died and the saved output did not settle the turn; the rerun that found it restarts the turn |

## Setup

`agy-call setup` checks, without sending a prompt or spending quota: Python 3.9 or newer, macOS or Linux, the agy executable (`AGYCALL_AGY`, else `agy` on the PATH), `agy --version` 1.5.6 or newer, that `agy --help` lists `--settings`, `--json-schema`, `--effort` and `--output-format`, that `agy models` succeeds (it needs a signed-in agy), and that it lists `gemini-4-argon-high` and `gemini-3.8-flash-low`.
It prints one line per check, `ok` or `FAIL`, and exits 0 only when every check passes, 1 otherwise.
Each agy probe gets 30 seconds.
When agy cannot be found, the checks that need it are not run.

## run and batch

`agy-call run --job ID [options]` reads the prompt from `--prompt-file`, `--prompt` or stdin (in that order of preference; the first two are mutually exclusive).
It prints the accepted answer on stdout, errors on stderr, and exits with the code in the table below.
Run it with run_in_background for long jobs, or pass `--background` to hand the job to a detached worker (see Status, result and cancel).
A bad argument, job id or timeout, or a prompt larger than one command-line argument allows (512 KiB on macOS, 128 KiB on Linux), exits 2 before anything is launched.
`run` and `batch` pass no grant, so agy applies the user's own settings to any tool call; use `task` for work that edits files.

Options of `run` and `batch` (defaults in brackets):

- `--store DIR`: the job store; put it inside the project, for example `<project>/agy-jobs` [`~/.agy-call/jobs/<namespace>`].
- `--namespace NAME`: the folder under `~/.agy-call/jobs` when no `--store` is given [`default`].
- `--model NAME`: required; the exact agy model name, for example `gemini-4-argon-high`; aliases and tiers are not accepted, because one name per model keeps the quota hold exact (the hold is kept per model string).
- `--timeout T`: limit per turn, passed to agy as `--print-timeout`; agy-call stops agy 60 seconds after it; a bare number means seconds [`10m`].
- `--cwd DIR`: working directory of the call [the current directory].
- `--dir DIR`: extra directory the agent may read, passed as `--add-dir`; repeat the option for several [none].
- `--mode MODE`: agy's execution mode, `accept-edits`, `auto` or `plan` [agy's default].
- `--expect json`: the reply must contain a JSON value (the last fenced `json` block that parses, the whole reply, or unfenced JSON surrounded by prose when it is the whole span from the first `{`/`[` to the last `}`/`]`); the accepted answer is that value re-printed as JSON.
- `--require a,b`: comma-separated top-level keys the JSON object must have (implies `--expect json`).
- `--schema FILE`: JSON Schema file the JSON value must satisfy (implies `--expect json`); it may use only `type` (a name or a list of names), `enum`, `required`, `properties`, `items`, `additionalProperties: false`, `minLength`, `minimum` and `maximum`, and any other keyword exits 2 before a launch.
- `--check-cmd CMD`: shell command, run in the working directory of the call (`--cwd`), that receives the candidate answer on stdin; exit 0 accepts it, exit 1 rejects it and its stdout becomes the finding sent back to agy, any other exit is a check error (21) [none].
- `--check-timeout SECONDS`: limit for the check command [120].
- `--max-turns N`: most turns per job, counting follow-ups but not plain retries [4].
- `--ignore-quota`: launch even while a quota hold exists [off].
- `--fresh`: when a follow-up is needed but no conversation id was captured, start a new conversation instead of exiting 23 [off].

Options of `run` only:

- `--job ID`: required; `[A-Za-z0-9][A-Za-z0-9._-]{0,119}` (the first character is a letter or digit).
- `--conversation ID`: seed the job with an existing conversation, to continue it [none].
- `--new-version`: when the job exists with a different request, move the old job folder under `.versions` and start again [off].
- `--background`: check the request as a normal run would, record the job as `queued`, start a detached worker that runs it, print the job id and exit 0; the worker keeps running after the Claude session ends, and its own messages go to `<store>/<job>/worker.log`; an argument that cannot be stored as JSON exits 2 [off].

`agy-call batch JOBS.jsonl [--workers N] [common options]` runs many jobs [`--workers` default 4].
Each line of the file is a JSON object with `job` (required, unique within the batch; duplicates are rejected before anything runs) and `prompt` or `prompt_file`.
A row that cannot be prepared (invalid job id, unreadable prompt file) is reported as that row's failure and the other rows still run.
Any other key on a line is an option for that job and overrides the common option of the same name, using the Python names: `store`, `namespace`, `model`, `timeout`, `cwd`, `dirs` (a list), `mode`, `conversation`, `expect`, `require` (a list), `schema`, `check_cmd`, `check_timeout`, `max_turns`, `ignore_quota`, `fresh`, `new_version`.
Batch prints one line per outcome with its count (`done 3`, `Quota 2`, ...), then one line per job that did not finish.
Jobs that already have an answer are returned without a launch even during a quota hold.
Once a model key has a quota hold, batch launches no further job for that key and lists those jobs as `Quota`.
Batch exits 0 when every job is done, 10 when any job hit a quota hold, 27 when none did and any job was cancelled, and 20 otherwise.

In shell scripts pass each option and its value as separate words: `--conversation "$ID"`, not one string holding both.

## Python

```python
import os, sys
# the folder that holds agycall/: your clone, or for a plugin install the parent of the folder holding $(realpath $(which agy-call))
sys.path.insert(0, os.path.expanduser("~/Code/agy-call"))
from agycall import call, batch, AgyCallError, Quota
```

`call(job_id, prompt, *, store=None, namespace=None, model, timeout="10m", cwd=None, dirs=(), mode=None, conversation=None, expect=None, require=None, schema=None, check_cmd=None, check_timeout=120, check=None, check_id=None, max_turns=4, ignore_quota=False, fresh=False, new_version=False)` returns a `Result` with `job`, `answer` (the parsed JSON value, or the text when no JSON was asked for), `text`, `conversation`, `attempts`, `usage` and `dir`.
`check` is a Python function that receives the parsed value (or the text) and returns a list of problems; it needs a stable `check_id` string, which is part of the job's identity, so change the `check_id` when the function's rules change.
`batch(jobs, *, workers=4, **common)` takes a list of dicts as in the JSONL format and returns `{job_id: Result or exception}`; it never raises for a single job.

Exceptions, all subclasses of `AgyCallError` with the CLI exit code in `.code`: `Running` (25), `Mismatch` (24), `Quota` (10, with `.key` and `.until`), `Fatal` (the failure's exit code, with `.reason`), `GaveUp` (20, with `.findings`, `.conversation`, `.usage`, `.job_dir`), `CheckError` (21), `NoConversation` (23), `RepoState` (26), `Cancelled` (27), `Blocked` (28, with `.findings`).

## Job ids and stores

A job id names one request and is how a rerun finds the saved work: calling again with the same id and the same request returns the saved answer without a launch.
Choose ids that are stable across reruns and include the version of the item asked about, for example `q-0412-3fa9c1` with a short sha of the source text.
A job whose prompt, model, directories, seed conversation, check or mode changed is a different request; agy-call refuses it with exit 24 unless you pass `--new-version`.
Keep the store of `run` and `batch` jobs inside the project (for example `<project>/agy-jobs`) so the replies live with the work and are not mixed with other projects.
Task and review jobs always live in the repository's own store, `~/.agy-call/jobs/<repo id>/`.

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | ok, answer printed; or a background job was started |
| 1 | `agy-call setup` found a failing check; from `result`, the job is `interrupted` |
| 2 | a bad argument, job id, timeout, schema or prompt size |
| 10 | quota: a hold exists for the model; the message prints the time it ends |
| 11 | agy is not authenticated |
| 13 | agy is missing: set `AGYCALL_AGY` or put agy on PATH |
| 14 | model unavailable |
| 20 | gave up: no usable reply within `--max-turns`, a tool call denied twice (`run`), agy hit its output limit without a reply, agy answered in another conversation, or a merge whose check failed |
| 21 | check error: the check itself failed to run; the reply is kept |
| 22 | agy's output was not a JSON envelope; the raw output is kept in `agy.out` |
| 23 | no conversation id was captured, so a follow-up cannot be sent |
| 24 | the job exists with a different request, or its branch or worktree belongs to another job |
| 25 | the job is running in another process; from `result`, the job is still queued, running or checking; from `cancel`, it is still stopping |
| 26 | repository state: not inside a git repository, uncommitted or untracked files, a detached HEAD, a missing worktree, nothing to review, an existing snapshot folder that is not the clean detached worktree at the recorded commit, or a merge, cleanup or discard that the repository's state refuses |
| 27 | the job was cancelled |
| 28 | a task or a review is blocked on permission: its reply had `NEED:` lines or agy refused an action |

An error that is not one of these (for example a bad argument) exits with 1 or 2.

## What to do per exit

- 10: wait until the printed time and rerun the same command, or ask the user; do not use `--ignore-quota` to get around it.
- 20: read the findings; after an output-limit stop, send a new prompt that asks for a short reply, as a new job; otherwise rerun with a higher `--max-turns` to continue the same conversation, or change the prompt and rerun with `--new-version`; for a task, continue it with `task --continue`.
  After a stop because agy answered in another conversation, a rerun sends nothing; start a new job.
- 21: a rerun checks the saved reply again before any new turn, but only when the request is unchanged: repair the checker's executable or environment, not the command string, schema file or `check_id`.
  After changing the check, the saved reply is in `attempts/NNN/reply.txt` and the raw output in `agy.out`; a rerun with `--new-version` starts a new paid job.
- 23: rerun with `--fresh` if starting a new conversation is acceptable; otherwise use the replies saved under `attempts/`.
- 24: the request changed; use a new job id, or `--new-version` to replace the old job.
- 25: another process holds the job; wait for it and rerun, which then returns its answer.
- 26: the repository is not in the state the command needs; the message says what to change (commit, stash or ignore files, check out a branch, merge the base into the task branch, or clean up a task before `--new-version`).
- 27: the job was cancelled and stays cancelled; its replies are kept under `attempts/`; to do the work anyway, run it under a new job id.
- 28: read the `NEED:` lines and the refused action kinds (`agy-call status JOB`), then follow "After `blocked`" under Continuing a task.
- 11, 13, 14, 22: fix the setup (login, install, model name; `agy-call setup` shows which) and rerun; the job is kept.

## Where replies are

Under `<store>/<job>/attempts/NNN/` each launch keeps `agy.out` (agy's JSON envelope, byte for byte), `agy.err` (agy's stderr), `agy.args` (the arguments, with the prompt replaced by its size and hash), `reply.txt` (the reply text, when there was one), `launch.json` (the prompt, process ids and start time) and `result.json` (agy's exit status, the envelope status, the classified outcome and what agy-call did).
Attempts made before agy-call launched agy itself keep their older files (`delegate.out`, `delegate.err`, `agy-N.out`), and agy-call still reads them.
The accepted answer is `<store>/<job>/answer.txt`; `request.json` and `status.json` sit beside it.
Nothing is ever deleted: a replaced job moves under `.versions`, and failed attempts stay in place.
When a call fails, read `reply.txt` and `agy.err` of the last attempt to see what agy actually said.
`check.json` in the attempt folder records the check command's process while it runs.
A cancelled job has `cancel.request`, and a background job has `background.json` (the arguments its worker passes to `call()`) and `worker.log`, beside `request.json`.
When the process running a job died, the next run of the same job reads the saved `agy.out` before launching anything.
A complete reply found there is checked as if it had just arrived, but agy's exit status was lost, so it is accepted only by a check you asked for and no follow-up is sent after it.
Output that is not a complete reply restarts the turn.
A task's record is `~/.agy-call/jobs/<repo id>/<job>/`; beside the files above it holds `grant-<n>.json` (the grant passed with `--settings`), `segments/NNN.json` (each segment's prompt, grant and budget), `segments/NNN-answer.txt` (an earlier segment's answer), and `merge.json`, `merge-failed.json` and `cleanup.json` once those commands ran.
A review's record is in the same repository store; beside the attempt files it holds `schema.json` (the findings schema passed with `--json-schema`), `grant-1.json` (its read-only grant) and, once it is done, `answer.txt` with the accepted findings as JSON.

## Multi-turn work

Each turn of a multi-turn exchange is its own job, seeded with `conversation=` (`--conversation`) from the previous result's `conversation`.
agy-call serialises jobs that share a conversation, so two turns never run at the same time in one conversation.
