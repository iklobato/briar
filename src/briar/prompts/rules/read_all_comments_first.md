---
name: read-all-comments-first
severity: mandatory
applies_to: [pr-fixer]
enforced_by: [prompt]
---

## Read every comment before editing any file

Before you commit anything, READ every comment first:

- Every PR-level issue comment (`gh pr view --comments` /
  `/repos/OWNER/REPO/issues/N/comments`).
- Every inline review-thread comment
  (`/repos/OWNER/REPO/pulls/N/comments`).
- The full diff so you know what every comment is anchored to.

Only after you've ingested all three do you plan fixes. A fix that
addresses one comment in isolation while contradicting another comment
on the same thread is worse than no fix.
