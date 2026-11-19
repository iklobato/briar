# serve: the loop wastes less and touches less (decisions Q1 to Q7)

Date: 2026-11-19. Code: `serve/ticket.py`, `serve/base_ci.py`, `serve/flow.py`, `serve/day.py`, `serve/task.py`, `serve/agent.py`,
`extract/_providers/github.py`, `extract/_providers/gitlab.py`. Docs: `agents/serve.md`.

## Why

Before `serve` runs for days, four things still cost money or touched what was not its own, all capped but real: it kept pushing into a draft that
a person had marked ready, it chased for several fix loops a CI that was already red on the base, a pause repeated the whole build call, and a hair of
money (float dust) could start a call that the SDK stops at once.

## Decisions

| # | Decision | Why |
|---|---|---|
| Q1 | A draft that briar made at a stop for money and that a person marked ready is theirs: no more pushes, the entry is DONE, review comments are still answered. | The person took the PR over. Answering a review is what the reviewer asked for. |
| Q2 | The fix loop of a failed CI does not start when every failed job of the PR is red on the latest run of the base, by job name. | A base with one red job must not stop the fix of a real failure; two jobs with the same name count as one. A read of the base that fails never stops a fix. |
| Q3 | The entry records when the build call returned (`built`); the build is skipped only when that is set and the branch has work. | The draft that a stop makes commits `work so far`, so a commit alone does not say the build ended. |
| Q4 | `DUST` (1e-9 US$) is the least that counts as money left. | Ten bookings of 0.1 add up to 0.9999999999999999. It is a placeholder no measurement set. |
| Q5 | `sed` is allowed to the agent by default. | It was refused six times in one ticket. Other verbs stay in the runbook `bash` list. |
| Q6 | The SDK start timeout is 180 s, and the plain `Exception` it raises after that is not caught. | The rules forbid `except Exception`, and the SDK gives no type. State is saved after every call and the worktree is reset at start, so a restart (by hand from the terminal, or a shell loop on exit 2) costs the call in flight. |

## Not covered

- A CI that is red on the base for a job named differently from the one that fails on the PR is still chased.
- Nothing restarts `serve` after a crash by itself: it is run from a terminal, so the docs give a one line shell loop and nothing else.
- The CI-red-on-base rule was not run against a real red base.
