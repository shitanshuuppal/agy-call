---
description: Show the agy-call jobs (Gemini tasks and reviews) of this repository, or one job in full. Runs only when the user asks for it.
argument-hint: "[JOB] [--all]"
disable-model-invocation: true
allowed-tools: Bash(agy-call:*)
---

Run this with the Bash tool from the current directory:

```
agy-call status $ARGUMENTS
```

Show its stdout and stderr to the user unchanged, with the exit status when it is not 0.
Do not summarise it, poll, or run anything else.
