---
name: minimum-correct-fix
severity: mandatory
applies_to: [pr-fixer, pr-ci-fixer]
enforced_by: [prompt]
---

## Smallest correct fix, one commit per concern

Apply the smallest fix that addresses the specific feedback. Resist
the urge to refactor adjacent code "while you're there" — every extra
line is another reviewer round-trip.

One commit per concern. Subject ≤ 72 chars. The body cites the
comment id or check name being addressed. NEVER squash unrelated
fixes into one commit, even if they touch the same file.

Touch only files inside the diff already under review. If a fix
requires editing a file outside the diff, comment on the PR
explaining why before editing.
