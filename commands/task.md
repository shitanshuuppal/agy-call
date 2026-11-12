---
description: Hand a coding task to Gemini through agy, in its own git worktree and branch. Runs only when the user asks for Gemini or agy.
argument-hint: "[--background] [--continue JOB] [--model M] [--effort E] <task text>"
disable-model-invocation: true
allowed-tools: Bash(agy-call:*), Bash(git status:*), Bash(git diff:*), Read, Write, Glob, Grep, Agent
---

Hand the user's coding task to Gemini with `agy-call task`.
The user asked for Gemini by running this command; it does not make Gemini the default for other work.

Raw arguments:
$ARGUMENTS

## Read the arguments

- `--background`: start the task and report its job id without waiting for it.
- `--continue JOB`: send a new message to the existing task JOB instead of starting a new task.
- `--model M` [`gemini-4-argon-high`] and `--effort E` [not passed]: the agy model and reasoning effort of a new task; a continued task keeps the ones it started with.
- Everything else is the task text.
  If there is none, ask the user what Gemini should do, and stop.

## Prepare

1. For a new task, run `git status --porcelain` in the repository.
   `agy-call task` refuses to start (exit 26) while it lists anything, so if it does, show the user the files, ask whether to commit, stash or ignore them, and stop.
2. Choose the job id of a new task: a short slug of the task in lowercase letters, digits and hyphens, then the date and time, for example `fix-login-redirect-20261112-1430`.
   For `--continue`, the job id is JOB.
3. Write the prompt file in your scratch directory, or in `$TMPDIR` when you have none, never inside the repository: a new file there makes the checkout unclean, and the task or its merge refuses with exit 26.
   It holds the task text, plus whatever from this conversation the task needs (files, the behaviour wanted, constraints), written for a reader who has not seen the conversation.
   Do not add instructions about commits, branches, permitted commands or the reply format; agy-call adds those.
4. Choose the `--allow` entries: the build, test, lint and read commands of this repository that Gemini needs, taken from its own files (README, Makefile, `package.json` scripts, `Package.swift`, `pyproject.toml`, `Cargo.toml`, CI workflows).
   Each entry is one command without newlines or parentheses, for example `--allow 'swift build' --allow 'swift test'`.
   `ls`, `cat`, `head`, `tail`, `wc`, `grep`, `rg`, `git status`, `git diff`, `git log` and `git show` are always allowed; do not repeat them.
   Never allow a command that reaches outside the repository: network access, package installs, deletion outside the worktree, `git commit`, `git push`, or anything that reads credentials.
5. Choose the `--check-cmd`: the command that builds the repository and runs its tests, for example `swift build && swift test`, or `true` when there is nothing to build or test.
   It runs in the task's worktree after every turn; a failure goes back to Gemini as a fix turn.
   Add `--check-timeout S` when the build and tests take longer than 600 seconds.
6. Tell the user in one or two lines which `--allow` entries and which check you chose.

## Start

Use the Agent tool with `subagent_type: "agy:agy"` and give it exactly one command, with each value in single quotes.

A new task:

```
agy-call task --job JOB --model gemini-4-argon-high [--effort E] --check-cmd 'CHECK' [--check-timeout S] --allow 'CMD' ... --background --prompt-file 'FILE'
```

A continued task:

```
agy-call task --continue --job JOB [--allow 'CMD'] ... --background --prompt-file 'FILE'
```

agy-call always runs the task in a detached worker (`--background`), so the task keeps running after this session ends and is not cut off by the time limit of a Bash call.
The subagent returns agy-call's output: the job id, or an error and its exit status.
On an error, show it to the user and stop: exit 26 names the files or the repository state to fix, exit 24 means the job id or its branch is taken (choose another id), and exit 2 is a bad argument.

## Wait, or not

- With `--background`: tell the user the job id, that `/agy:status` shows its progress and that `/agy:result JOB` prints the outcome, and stop.
- Otherwise run this with the Bash tool and `run_in_background: true`; `agy-call result` exits 25 while the job is queued, running or checking:

  ```
  until agy-call result JOB >/dev/null 2>&1; [ $? -ne 25 ]; do sleep 30; done
  ```

  When it finishes, run `agy-call result JOB` and continue below.

## When the task ends

The agy-call skill describes each case in full ("When a task ends", "Continuing a task" and "Hand-back and merge").

- `done` (exit 0): read the diff in the worktree (`git -C WORKTREE diff BASE` and the untracked files that `git -C WORKTREE status --porcelain` lists) and run the build and tests there yourself.
  When they pass and the change does what was asked, commit on `agy/JOB` in the worktree with the user's git identity and a message naming the job, run `agy-call task --merge JOB` from the main checkout, then `agy-call task --cleanup JOB`.
  Never push.
  When the change falls short, tell the user what you found and ask whether to send it back with `/agy:task --continue JOB <what to change>` or to fix it yourself.
- `blocked` (exit 28): `agy-call status JOB` shows the `NEED:` lines (`need`) and the refused action kinds (`denied`).
  When every command Gemini needs is a build, test or read command confined to this repository, continue the task: write a new prompt file that says those commands are now allowed and to carry on, and run the continued-task command with an `--allow` for each.
  Ask the user first for anything that reaches outside the repository: network access, deletion outside the worktree, package installs, `git push`, or credentials.
- `failed` (exit 20): show the findings and the last reply, and ask the user how to go on.
- Exit 10 (quota): show the time the hold ends; rerunning the same command after that time continues the task's conversation.
- Exit 11, 13, 14 or 22: suggest `/agy:setup`.
