---
name: ai-prefix-on-comments
severity: blocking
applies_to: [engineer, pr-fixer, pr-conflict-resolver, pr-ci-fixer, triager]
enforced_by: [prompt]
---

## Authorship rule — prefix `[AI] ` on every issue/PR comment

Any comment you post via `gh issue comment`, `gh pr comment`, `gh pr review
--comment`, or any other channel that publishes a comment under the human
operator's account MUST begin with the literal marker `[AI] ` (square
brackets, capital `AI`, then one space) at the very start of the body.

The marker goes inside the body, not as a label, footer, or commit
trailer. It applies equally to status-only updates ("PR created:
<url>", "Issue already resolved by #1415") and substantive feedback —
no exceptions.

Does NOT apply to commit messages or PR descriptions; those are authored
work, not impersonated comments.

Examples:

  GOOD: `gh issue comment 1398 -b "[AI] This issue is already covered by PR #1415."`
  BAD:  `gh issue comment 1398 -b "This issue is already covered by PR #1415."`

If a tool wrapper strips or reformats the body, verify with `gh issue
view <N> --json comments` immediately after posting and re-edit the
comment via `gh issue comment <N> --edit-last -b "[AI] ..."` if the
prefix is missing.
