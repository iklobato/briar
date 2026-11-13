# Spec 008 PR 1: the selector with tracker signals

Grill of PR 1, 2026-11-12, on top of `2026-11-12-briar-2-work-selection.md` (N1 to N17).
The numbers come from one real tracker project (39 open tickets assigned to the token owner;
the company stays out of the repo): links Blocks 48 and Relates 34, 17 of 39 with an open
blocker, 14 that block others (at most 3), 9 in progress and 30 new, 11 due dates from 6 days
to 8 months away, 37 of 39 with priority Medium, 37 of 39 in an epic.

| # | Decision | Answer | Why |
|---|---|---|---|
| Q1 | Score weights (N11) | Due: overdue +50, within 7 days +40, within 30 days +20, later +5. Unblocks open tickets: +10 each, at most +30. Priority: highest +20, high +10, low -5, lowest -10. A `priority_labels` label: +10. In progress: +30. Chat asks (PR 2): +25 each in 14 days, at most +50. Epic: 0. Ties: the oldest first | priority hardly tells tickets apart (37 of 39 Medium); due dates and unblocking do; an epic is on almost every ticket |
| Q2 | Tickets in progress assigned to the token owner | Taken, and ranked up (+30) | the maintainer wants work in flight finished first, even when a person started it |
| Q3 | Dependencies on GitHub Issues | The native issue dependencies: `issue_dependencies_summary` (`blocked_by`, `blocking`) on the issue itself | no extra call for an issue with no blocker; text parsing is fragile |
| Q4 | A runbook with the old `jira: {PROJ: owner/repo}` | Stops with "`jira.PROJ`: write `{repos: [owner/repo]}`" | nothing implicit (N7) |

Not workable (N6) in this PR: a status in `not_workable` (default `Blocked`, `In Review`,
set per Jira project) and an open blocker. The open PR/MR by someone else waits for PR 4.
