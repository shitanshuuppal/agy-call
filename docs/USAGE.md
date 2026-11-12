# Using the agy plugin day to day

This guide shows when and how to hand work to Gemini through the `agy` plugin.
The README lists every command, and `skills/agy-call/SKILL.md` documents every option, exit code and job state.
Start a new Claude Code session after installing, so the `/agy:*` commands load.

## Which tool for which job

| Work | Best fit |
|---|---|
| A small edit, a judgment call, anything you want done now in your own checkout | Claude itself |
| A well-defined coding task that a test or build can check | Gemini, with `/agy:task` |
| A second opinion on a change before you merge it | `/agy:review` or `/agy:adversarial-review` |
| Hundreds of similar items, such as classifying or checking records | `agy-call batch` or the Python `call()` |
| Reading a large codebase or document set and summarising it | Gemini, in plain words |

Gemini runs on your Google account's agy quota, so work you send to it does not use Claude tokens.
Claude only uses Gemini when you ask for it, by a slash command or in plain words.

## Asking in plain words

You do not need the slash commands.
Any request that names Gemini or agy goes through the plugin; a request that names neither is handled by Claude itself.

| You say | Claude does |
|---|---|
| "Have Gemini add tests for parse_date." | Runs a coding task and waits for the result, like `/agy:task` |
| "Give this refactor to agy and run it in the background." | Starts the task in the background and reports the job id |
| "Ask Gemini to review my changes." | Runs `/agy:review` on your uncommitted changes or the branch |
| "Get Gemini to review this branch against main." | Runs `/agy:review --scope branch --base main` |
| "Have Gemini tear apart the retry logic." or "Get an adversarial Gemini review of the auth flow." | Runs `/agy:adversarial-review` with that focus |
| "Ask Gemini to read docs/ and list what is out of date." | Sends a one-off question and shows the answer |
| "What is Gemini working on?" | Runs `/agy:status` |
| "Show me what Gemini did on the signup task." | Runs `/agy:result` for that job |
| "Stop the Gemini job." | Runs `/agy:cancel` |
| "Let Gemini continue, and allow it to run the linter." | Continues a blocked task with the linter added to its grant |
| "Check that agy is set up." | Runs `/agy:setup` |

A plain-language task waits for the result unless you say "in the background".
Saying only "review this" or "fix this" keeps the work with Claude.

## 1. Hand off a coding task

Commit or stash your changes first: a task refuses to start while the checkout has uncommitted edits, so Gemini never works on stale code.

```
/agy:task Add input validation to the signup form: reject empty names and malformed email addresses, with unit tests for both
```

You can also say it in plain words: "Have Gemini add tests for parse_date covering leap years and time zones."

What happens next:

- Claude picks the check command from the repository, for example `npm test`, `pytest -q` or `cargo test`, and the build, test and read commands Gemini may run.
- agy-call creates a git worktree on a new branch `agy/<job>` and a permission grant made for this task only.
- Gemini edits files inside that worktree; your own checkout is not touched.
- After every turn agy-call runs the check; when it fails, Gemini gets a fix turn in the same conversation.
- When the check passes, Claude reads the diff, runs the build and tests itself, commits on the task branch and merges it locally with `agy-call task --merge`.
- Nothing is pushed; pushing stays your decision.

### Write tasks with a clear finish line

| Works well | Works poorly |
|---|---|
| "Make `pytest -q` pass with new tests for the CSV parser's quoting rules" | "Improve the CSV parser" |
| "Rename `fetchUser` to `loadUser` across the project; the build and tests must pass" | "Clean up the naming" |
| "Add a `--dry-run` flag to the export command that prints the files it would write" | "Make the export safer" |

The check command is how agy-call knows the task is done, so a task that a test or build can confirm finishes reliably.

### Long tasks run in the background

```
/agy:task --background Migrate the date handling from moment to date-fns across src/
/agy:status
/agy:result <job>
```

A background job keeps running after you close the session, and `/agy:status` in any later session shows it.

### When a task stops as blocked

A task ends with state `blocked` (exit 28) when Gemini needs a command its grant does not allow, for example a linter the grant did not list.
Claude may add build, test and read commands for the same repository and continue the task with `--continue --allow <command>`.
For anything that reaches outside the repository, such as network access, installing packages, deleting files outside the worktree or `git push`, Claude asks you first.

### What the grant protects

- Gemini's own file-writing tool can write only inside the task's worktree.
- The Antigravity app's shared grants are turned into deny rules for the task, so a command allowed there is not allowed here.
- The commands a task is allowed to run are trusted: a test command runs your project's code, which can do anything your account can.
- Allow only commands you would run yourself.

## 2. Get a review

```
/agy:review
/agy:review --scope branch --base main
/agy:adversarial-review the retry logic in the payment client
```

- With no options, the review covers your uncommitted changes, or the current branch when the working tree is clean.
- The answer is a list of findings, each marked MUST-FIX, SHOULD-FIX or NIT, tied to the exact code that was reviewed.
- If you edit the code afterwards, `/agy:result` warns that the code changed since the review.
- The adversarial review assumes the change has defects and challenges its design; use it for code that can lose data, touch money or affect security.
- Claude never starts a fix or a second review on its own after a review; you decide what to act on.

## 3. Use it from scripts

For one question at a time, the Python `call()` keeps every reply on disk and returns a saved answer on a rerun:

```python
import os, sys
sys.path.insert(0, os.path.expanduser("~/agy-call"))  # the folder that holds agycall/
from agycall import call, AgyCallError

result = call(
    "ticket-1042-a3f9c1",          # a stable job id: the item id plus a short hash of its text
    "Classify this support ticket as bug, question or feature request. "
    "Reply with JSON {\"label\": ..., \"reason\": ...}.\n\n" + ticket_text,
    store="agy-jobs",              # keep the replies next to your project
    model="gemini-4-argon-high",
    expect="json",
    require=["label", "reason"],
)
print(result.answer["label"])
```

- A stable job id means a rerun of the same item costs nothing: the saved answer comes back without a call to Gemini.
- A check (`expect`, `require`, a JSON Schema, a check command or your own function) means a reply in the wrong format gets one fix turn in the same conversation instead of being thrown away.
- A quota error raises `Quota` with the reset time, and every other caller on the machine waits instead of failing one call after another.

For many items, put one JSON object per line in a file and run them in parallel:

```
{"job": "ticket-1042-a3f9c1", "prompt_file": "prompts/1042.txt"}
{"job": "ticket-1043-77be20", "prompt_file": "prompts/1043.txt"}
```

```
agy-call batch jobs.jsonl --workers 4 --store agy-jobs --model gemini-4-argon-high --require label,reason
```

The batch prints how many jobs ended in each state and lists the ones that did not finish.

## 4. Ask Gemini to read a lot

Gemini can take a large amount of text in one turn, which makes it a good fit for questions such as these:

- "Ask Gemini to read everything under `docs/` and list the places where the docs disagree with the CLI's `--help` output."
- "Have Gemini read the last 30 migration files and summarise which tables changed and why."

The answer comes back as text, and the full reply is kept in the job's folder.

## 5. Keep track of jobs

| Command | Use |
|---|---|
| `/agy:status` | Jobs of this repository, with state and age; `--all` lists every repository |
| `/agy:result <job>` | A task's hand-back (summary, changed files, worktree and branch) or a review's findings |
| `/agy:cancel <job>` | Stops a job and keeps what it produced |
| `/agy:setup` | Checks agy's version, sign-in and model names without spending quota |

After you merge a task, remove its worktree and branch with `agy-call task --cleanup <job>`.
To throw away a task you do not want, use `agy-call task --discard <job>`; it refuses while the worktree has uncommitted changes.

## A first run

1. Commit your work in a repository that has tests.
2. Run `/agy:setup` and check that every line says ok.
3. Run `/agy:task --background` with a small change that a test can confirm.
4. Watch it with `/agy:status`, then read the hand-back with `/agy:result <job>`.
5. Let Claude verify, commit and merge it, then run `agy-call task --cleanup <job>`.
