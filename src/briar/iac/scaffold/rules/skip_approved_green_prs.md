---
name: skip-approved-green-prs
severity: blocking
applies_to: [pr-fixer, pr-conflict-resolver, pr-ci-fixer]
enforced_by: [prompt]
---

## Skip PRs that are already done

NEVER modify a PR that is APPROVED **and** whose CI is green **and**
whose open review threads contain only positive comments. Approved +
correctly-implementing = leave it alone.

Check `gh pr view <N> --json reviewDecision,statusCheckRollup` before
opening a worktree. If `reviewDecision == "APPROVED"` and every
required check has `conclusion == "SUCCESS"`, report 'skipped' and end
the run.
