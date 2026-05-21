---
name: no-workflow-file-edits
severity: blocking
applies_to: [pr-fixer, pr-conflict-resolver, pr-ci-fixer]
enforced_by: [prompt]
---

## No `.github/workflows/*.yml` edits from a PR branch

Most GitHub orgs ship a Push Protection ruleset that forbids modifying
`.github/workflows/*` from a PR branch (supply-chain defense). Even
"trivial" edits like updating an action's pinned SHA get rejected at
push time with `GH013: Repository rule violations found`.

If a review comment asks for a workflow change, comment on the thread
explaining the limitation and suggest the change be made in a separate
admin-merge PR or via a repo-ruleset bypass. Do NOT attempt the
Contents API as a workaround — that bypasses the same protection and
exposes the repo to supply-chain risk for no real gain.

The only edits permitted to `.github/workflows/*` are: none.
