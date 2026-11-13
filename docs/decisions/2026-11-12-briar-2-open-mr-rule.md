# briar 2.0 spec 008: the open PR/MR rule (N6) and `serve.only`

Short grill of 2026-11-12, during the validation of `extract` and `serve` on a real company
(the plan asked for a first real `serve` run on one ticket, and for a ticket that already has
an open MR of the token owner to be skipped). It refines N6 of
`2026-11-12-briar-2-work-selection.md` ("tickets with an open PR/MR by someone else").

Fact that started it: a real ticket in progress has an open MR by the token owner, on a branch
named after the ticket key, and a Jira remote link to that MR. N6 as first written would not
skip it, and `serve` would start a second change for the same ticket.

| # | Decision | Answer | Why | Depends on |
|---|---|---|---|---|
| S1 | A first run on a real tracker | `serve.only: [KEY, ...]`: when set, only those tickets are candidates | the maintainer wants one real ticket, not any of the assigned ones (some are production infrastructure) | N1 |
| M1 | What says "the ticket has an open PR/MR" | Any of: an open PR/MR in an allowed repo of the ticket whose source branch, title or description holds the key; a Jira remote link of the ticket to an open PR/MR | the branch and the title are the team habit; the remote link covers a branch named otherwise; the description catches the rest (accepted risk: an MR that only cites the ticket) | N6 |
| M2 | Whose PR/MR counts | Anyone but briar: the PR/MR on the branch briar made for the ticket keeps going through the review flow it has today | the owner own MR is work in flight too; two changes for one ticket is the worst case | M1 |
| M3 | What `serve` does | Skip the ticket and log one line with the link of the PR/MR; nothing is written on the ticket | no extra write on the tracker; the log says why | M1 |
| M4 | Where it is checked | In the selector path, after `Workable` and `only`, before the score; one search per allowed repo of each candidate per poll | cheap enough for the few candidates of a poll; resumed tickets (briar already started them) are not checked | M1 |

## Open risks

- M1: a key in the description of an MR about something else skips the ticket; the log line
  shows which MR, so a person can see it.
- M4: a merged or closed MR does not count; a ticket whose MR was merged and is still open in
  the tracker is a candidate again (the status rules of N6 still apply).
