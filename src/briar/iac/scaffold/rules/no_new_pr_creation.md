---
name: no-new-pr-creation
severity: blocking
applies_to: [pr-fixer, pr-conflict-resolver, pr-ci-fixer]
enforced_by: [prompt, tool-absence]
---

## Never open a new PR; only extend the existing one

A PR-archetype run is always anchored to one specific PR. NEVER call
`gh pr create` or use the `open_pr` tool — the PR already exists,
your job is to add commits and reply to threads on it.

If you discover a problem that genuinely needs a separate PR (e.g.
an unrelated bug surfaced while reading the diff), comment on the
existing PR describing the issue and let the human file the new PR.
