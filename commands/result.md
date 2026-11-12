---
description: Print the outcome of an agy-call job (a Gemini task's hand-back or a review's findings). Runs only when the user asks for it.
argument-hint: "[JOB]"
disable-model-invocation: true
allowed-tools: Bash(agy-call:*)
---

Run this with the Bash tool from the current directory:

```
agy-call result $ARGUMENTS
```

Show its stdout and stderr to the user unchanged, with the exit status when it is not 0.
Do not summarise it or run anything else.
When the output is the hand-back of a `done` task, offer to check, commit and merge it as "Hand-back and merge" in the agy-call skill describes, and start only when the user agrees.
