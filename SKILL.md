---
name: agy-call
description: "Use whenever any work goes to agy (Antigravity, Gemini): a one-off question, a review, a batch of items, or a script that calls Gemini. Every agy call goes through agy-call, which saves every reply, resumes the same conversation when a reply is unusable, and stops all callers on quota."
---

# agy-call

## Why it exists

Calling agy directly has lost paid replies in two ways.
A reply that failed a check was thrown away and the question was asked again in a new conversation, so the work already paid for was lost.
A quota error in one process did not stop the other processes, which kept launching calls that could not succeed.
agy-call saves the raw output of every attempt before it looks at it, resumes the same conversation with a fix message when a reply is unusable, and writes a machine-wide quota hold that every caller checks before launching.
A PreToolUse hook (`hooks/agy_guard.py`) refuses Bash commands that run `agy -p`, `agy --print`, `agy-delegate` or `agy-job` directly, so use `agy-call` for all of them.
`agy models`, `agy --version` and other non-print agy commands are not blocked.
The hook matches the whole command text, so a command that only mentions `agy-delegate` or `agy-job` (an echo, a grep pattern, a prompt text) is refused too; a command that mentions the word agy together with -p or -i on the same line (for example a grep) is refused too, so rephrase, e.g. `grep -i 'ag[y]' file`; put such text in a file and pass the file (for example `--prompt-file`) instead of writing it in the command.

## CLI

`agy-call run --job ID [options]` reads the prompt from `--prompt-file`, `--prompt` or stdin (in that order of preference; the first two are mutually exclusive).
It prints the accepted answer on stdout, errors on stderr, and exits with the code in the table below.
Run it with run_in_background for long jobs.

Options of `run` and `batch` (defaults in brackets):

- `--store DIR`: the job store; put it inside the project, for example `<project>/agy-jobs` [`~/.agy-call/jobs/<namespace>`].
- `--namespace NAME`: the folder under `~/.agy-call/jobs` when no `--store` is given [`default`].
- `--model NAME`: required; the exact agy model name, for example `gemini-4-argon-high`; aliases and tiers are not accepted, because one name per model keeps the quota hold exact (the hold is kept per model string).
- `--timeout T`: wall-clock limit per turn, passed to agy-delegate [`10m`].
- `--cwd DIR`: working directory of the call [the current directory].
- `--dir DIR`: extra directory the agent may read; repeat the option for several [none].
- `--mode MODE`: agy-delegate mode [agy-delegate's default].
- `--expect json`: the reply must contain a JSON value (the last fenced `json` block that parses, the whole reply, or unfenced JSON surrounded by prose when it is the whole span from the first `{`/`[` to the last `}`/`]`); the accepted answer is that value re-printed as JSON.
- `--require a,b`: comma-separated top-level keys the JSON object must have (implies `--expect json`).
- `--schema FILE`: JSON Schema file (a subset) the JSON value must satisfy (implies `--expect json`).
- `--check-cmd CMD`: shell command that receives the candidate answer on stdin; exit 0 accepts it, exit 1 rejects it and its stdout becomes the finding sent back to agy, any other exit is a check error (21) [none].
- `--check-timeout SECONDS`: limit for the check command [120].
- `--max-turns N`: most turns per job, counting follow-ups but not plain retries [4].
- `--ignore-quota`: launch even while a quota hold exists [off].
- `--fresh`: when a follow-up is needed but no conversation id was captured, start a new conversation instead of exiting 23 [off].

Options of `run` only:

- `--job ID`: required; `[A-Za-z0-9][A-Za-z0-9._-]{0,119}` (the first character is a letter or digit).
- `--conversation ID`: seed the job with an existing conversation, to continue it [none].
- `--new-version`: when the job exists with a different request, move the old job folder under `.versions` and start again [off].

`agy-call batch JOBS.jsonl [--workers N] [common options]` runs many jobs [`--workers` default 4].
Each line of the file is a JSON object with `job` (required, unique within the batch; duplicates are rejected before anything runs) and `prompt` or `prompt_file`.
A row that cannot be prepared (invalid job id, unreadable prompt file) is reported as that row's failure and the other rows still run.
Any other key on a line is an option for that job and overrides the common option of the same name, using the Python names: `store`, `namespace`, `model`, `timeout`, `cwd`, `dirs` (a list), `mode`, `conversation`, `expect`, `require` (a list), `schema`, `check_cmd`, `check_timeout`, `max_turns`, `ignore_quota`, `fresh`, `new_version`.
Batch prints one line per outcome with its count (`done 3`, `Quota 2`, ...), then one line per job that did not finish.
Jobs that already have an answer are returned without a launch even during a quota hold.
Once a model key has a quota hold, batch launches no further job for that key and lists those jobs as `Quota`.
Batch exits 0 when every job is done, 10 when any job hit a quota hold, and 20 otherwise.

## Python

```python
import os, sys
sys.path.insert(0, os.path.expanduser("~/Code/agy-call"))
from agycall import call, batch, AgyCallError, Quota
```

`call(job_id, prompt, *, store=None, namespace=None, model, timeout="10m", cwd=None, dirs=(), mode=None, conversation=None, expect=None, require=None, schema=None, check_cmd=None, check_timeout=120, check=None, check_id=None, max_turns=4, ignore_quota=False, fresh=False, new_version=False)` returns a `Result` with `job`, `answer` (the parsed JSON value, or the text when no JSON was asked for), `text`, `conversation`, `attempts`, `usage` and `dir`.
`check` is a Python function that receives the parsed value (or the text) and returns a list of problems; it needs a stable `check_id` string, which is part of the job's identity, so change the `check_id` when the function's rules change.
`batch(jobs, *, workers=4, **common)` takes a list of dicts as in the JSONL format and returns `{job_id: Result or exception}`; it never raises for a single job.

Exceptions, all subclasses of `AgyCallError` with the CLI exit code in `.code`: `Running` (25), `Mismatch` (24), `Quota` (10, with `.key` and `.until`), `Fatal` (the delegate's code, with `.reason`), `GaveUp` (20, with `.findings`, `.conversation`, `.usage`, `.job_dir`), `CheckError` (21), `NoConversation` (23).

## Job ids and stores

A job id names one request and is how a rerun finds the saved work: calling again with the same id and the same request returns the saved answer without a launch.
Choose ids that are stable across reruns and include the version of the item asked about, for example `q-0412-3fa9c1` with a short sha of the source text.
A job whose prompt, model, directories, seed conversation, check or mode changed is a different request; agy-call refuses it with exit 24 unless you pass `--new-version`.
Keep the store inside the project (for example `<project>/agy-jobs`) so the replies live with the work and are not mixed with other projects.

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | ok, answer printed |
| 10 | quota: a hold exists for the model; the message prints the time it ends |
| 11 | agy is not authenticated (agy-delegate code passed through) |
| 13 | agy is missing (agy-delegate code passed through) |
| 14 | model unavailable (agy-delegate code passed through) |
| 20 | gave up: no usable reply within `--max-turns`, or a tool call denied twice |
| 21 | check error: the check itself failed to run; the reply is kept |
| 22 | unknown agy-delegate exit |
| 23 | no conversation id was captured, so a follow-up cannot be sent |
| 24 | the job exists with a different request |
| 25 | the job is running in another process |

An error that is not one of these (for example a bad argument) exits with 1 or 2.

## Where replies are

Under `<store>/<job>/attempts/NNN/` each launch keeps `agy-N.out` and `agy-N.err` (agy's raw streams, one pair per agy invocation, with `agy-N.args`), `delegate.out` and `delegate.err`, `launch.json` and `result.json`.
The accepted answer is `<store>/<job>/answer.txt`; `request.json` and `status.json` sit beside it.
Nothing is ever deleted: a replaced job moves under `.versions`, and failed attempts stay in place.
When a call fails, read `agy-N.out` of the last attempt to see what agy actually said.

## What to do per exit

- 10: wait until the printed time and rerun the same command, or ask the author; do not use `--ignore-quota` to get around it.
- 20: read the findings; rerun with a higher `--max-turns` to continue the same conversation, or change the prompt and rerun with `--new-version`.
- 21: a rerun checks the saved reply again before any new turn, but only when the request is unchanged: repair the checker's executable or environment, not the command string, schema file or `check_id`. After changing the check, the saved reply is in `attempts/NNN/delegate.out` and the raw output in `agy-N.out`; a rerun with `--new-version` starts a new paid job.
- 23: rerun with `--fresh` if starting a new conversation is acceptable; otherwise use the replies saved under `attempts/`.
- 24: the request changed; use a new job id, or `--new-version` to replace the old job.
- 25: another process holds the job; wait for it and rerun, which then returns its answer.
- 11, 13, 14, 22: fix the setup (login, install, model name) and rerun; the job is kept.

## Multi-turn work

Each turn of a multi-turn exchange is its own job, seeded with `conversation=` (`--conversation`) from the previous result's `conversation`.
agy-call serialises jobs that share a conversation, so two turns never run at the same time in one conversation.
