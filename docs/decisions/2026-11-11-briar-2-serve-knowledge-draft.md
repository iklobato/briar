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

Open:

- Verify commands must install what they need (`npm ci && npm test`): the clean copy has no
  `node_modules` or virtualenv of the task folder.
- A fix the build agent makes only to get verify green (a test config) can be rejected by
  the self-review as outside the ticket, and the two loop until `max_loops`
  (https://github.com/iklobato/briar-plan-test/pull/7). Not fixed here.
- `make_draft` on a PR that is still a draft (a stop before CI passed) was not run for real.
