---
name: no-force-push
severity: blocking
applies_to: [pr-fixer, pr-conflict-resolver, pr-ci-fixer]
enforced_by: [prompt, tool-absence]
---

## No force-push, rebase, squash, amend, or filter-branch

The agent's bash tool rejects any command containing `--force`,
`-f origin`, `--amend`, `rebase`, `squash`, or `filter-branch`. The
prohibition is also enforced at the prompt level so the model does
not even try.

Use `git push origin HEAD:<branch>` (default behaviour is fast-forward
only). If the push fails because the remote has moved, do NOT retry
with `--force` — fetch the remote, create a fresh worktree at the new
remote tip, re-apply your fix, and push again. Force-pushing destroys
work and breaks reviewers' local checkouts.
