---
description: Stop a queued or running agy-call job (a Gemini task or review). Runs only when the user asks for it.
argument-hint: "[JOB]"
disable-model-invocation: true
allowed-tools: Bash(agy-call:*)
---

Run this with the Bash tool from the current directory:

```
agy-call cancel $ARGUMENTS
```

Show its stdout and stderr to the user unchanged, with the exit status when it is not 0.
Do not summarise it or run anything else.
