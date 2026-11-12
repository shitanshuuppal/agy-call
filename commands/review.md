---
description: Have Gemini review the current change of this repository through agy. Runs only when the user asks for a Gemini or agy review.
argument-hint: "[--background] [--scope auto|working-tree|branch] [--base REF] [--model M] [--effort E]"
disable-model-invocation: true
allowed-tools: Bash(agy-call:*)
---

Run a Gemini review of the repository's current change with `agy-call review`.
This command only reviews: do not edit files, and do not start a fix or another review.

Raw arguments:
$ARGUMENTS

If the arguments contain text that is not one of the options above, tell the user that focus text goes to `/agy:adversarial-review`, and stop.

Run this from the repository; the user's arguments come after the defaults, so a `--model` or `--effort` the user gives replaces the default:

```
agy-call review --model gemini-4-argon-high --effort high $ARGUMENTS
```

- Without `--background`, run it with the Bash tool and `run_in_background: true`, because a review can take longer than one Bash call may run, and wait until it finishes.
- With `--background`, run it in the foreground: agy-call takes the snapshot, starts a detached worker and prints the job id.
  Tell the user that `/agy:status` shows its progress and that `/agy:result JOB` prints the findings, and stop.

Show agy-call's output to the user unchanged: the verdict, the number of findings at each severity, one block per finding, and the path of the full JSON.
On exit 26 with "nothing to review", say that there is no change to review.
On any other non-zero exit, show the message; for exit 11, 13, 14 or 22 suggest `/agy:setup`.

Then stop.
What happens to the findings follows the user's own rules for review loops: do not fix anything, review again, or start another reviewer unless the user asks.
