---
name: commit-as-human
severity: blocking
applies_to: [pr-fixer, pr-conflict-resolver, pr-ci-fixer]
enforced_by: [prompt, runtime-check]
---

## Identity rule — commit as a human, never a bot

Every commit and push MUST use the human author's GitHub identity. Set
`git config user.name <github-login>` and `git config user.email
<github-noreply-email>` on the working tree before the first commit
(the no-reply form is `<id>+<login>@users.noreply.github.com`).

NEVER commit as `github-actions[bot]`, `briar-bot`, `claude[bot]`, or
any other bot identity. If you cannot resolve the human author's
identity, STOP and surface the missing config rather than silently
committing under a bot.

Verify with `git config user.name` and `git config user.email` before
the first commit. If either reads as a bot account, abort and report
the issue — do not push a commit that misattributes the work.
