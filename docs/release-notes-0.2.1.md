# agy 0.2.1

A security fix and four smaller fixes; upgrade with `/plugin update agy@agy-call`.

## Security

- Every task and review grant now turns off agy's access outside the workspace (`"allowNonWorkspaceAccess": false`).
  In 0.2.0 a per-job `--settings` grant replaced only agy's permission lists, so when agy's global settings had `allowNonWorkspaceAccess: true`, Gemini's file-writing tool could write outside the task's worktree.
  A real-agy check showed the write going through in 0.2.0 and being refused with this fix, while writes inside the worktree still succeed.
  `run` and `batch` are unchanged: they never pass a grant.

## Fixes

- A `--continue` refused because an earlier continuation was still queued is no longer stuck when the worker that should have taken it died (including a dead but unreaped worker); after 30 seconds without a live owner the stale reservation is released.
- A `mode` or `conversation` value that is not text now exits 2 with a message, for `run`, `batch` rows and background runs, instead of a Python error.
- Test helpers clean up the processes they start when a test fails or times out.
