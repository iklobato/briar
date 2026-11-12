# briar serve: the agent reads the knowledge, the PR opens as a draft, verify checks the commit

Decided 2026-11-11 with the maintainer, after the first real `briar serve` run
(https://github.com/iklobato/briar-plan-test/pull/5), where the agent wrote "The knowledge
files (knowledge/knowledge/qa.md, knowledge/work/qa.md) do not exist in the worktree" and a
`pytest.ini` that the global gitignore kept out of the commit made verify pass locally only.

| # | Decision | Answer | Why |
|---|---|---|---|
| W1 | When briar takes the PR out of draft | Opens it as a draft; marks it ready once CI passes (or the repo has no CI) after the self-review | reviewers are asked only for work that passed; a stop (S23) leaves it a draft |
| W2 | How briar justifies its work on the PR | The review routine of spec 003 part 3: one reply per review comment, saying what changed or why nothing did | already built; this PR proves it on a real PR |
| W3 | Where verify runs | On a clean detached worktree of HEAD; when a command fails, the agent also gets the files git ignores in its folder | a file git ignores is not pushed, so it must not make verify pass |
| W4 | The knowledge path the agent gets | Absolute (`Path(...).resolve()` when the runbook loads) | the agent runs in the worktree, where the relative path pointed nowhere |

Added after the validation run of 2026-11-11 (the maintainer picked every group):

| # | Decision | Answer | Why |
|---|---|---|---|
| W5 | When CI counts as passed | Also read the GitHub Actions workflow runs of the head: a queued or running one keeps CI pending | PR #11 of the test repo was marked ready while its Actions runs were `queued` |
| W6 | Scheduled extracts | Every task writes the company blob and keeps the sections it did not run (K9) | a task not named `extractors` wrote a blob the agent never read; `extractors` dropped the other sections |
| W7 | Doing work twice | Atomic state writes; any task failure blocks the task; the PR number and the answered comments are saved at once; the plan is posted once; a PR of the task branch is recorded before building | each one made a ticket or a reply happen twice, live or by reading the code |
| W8 | A failed poll | Logged and tried again next poll; a failed login check of a scheduled extract still stops `serve` (J8) | one GitHub or Jira error stopped every company |
| W9 | Verify fix against the self-review | The self-review prompt says a change made so verify passes is part of the work, unless it hides a failure | PR #7 looped until `max_loops` |

A failure before a task starts (`TaskPrep`: clone, fetch, worktree) is still tried again
next poll (V15): it costs no model money and a network blip heals by itself.

Open:

- Verify commands must install what they need (`npm ci && npm test`): the clean copy has no
  `node_modules` or virtualenv of the task folder.
- A fix the build agent makes only to get verify green (a test config) can be rejected by
  the self-review as outside the ticket, and the two loop until `max_loops`
  (https://github.com/iklobato/briar-plan-test/pull/7). Not fixed here.
- `make_draft` on a PR that is still a draft (a stop before CI passed) was not run for real.
