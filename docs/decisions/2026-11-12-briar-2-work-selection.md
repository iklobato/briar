# briar 2.0: `serve` chooses its next tickets, works GitLab repos and reads chat (spec 008)

Grill session, 2026-11-12. Generic: it holds for any company, any tracker project and any
set of repos briar is pointed at. Not built yet. It changes S9 (every open ticket assigned
to the token owner becomes work) and the single repo per Jira project of S6.

Why: a real tracker project of a company had 39 open tickets assigned to the token owner.
`briar serve` would have taken all of them, in priority order, although 37 of the 39 had the
same priority and 25 had an open "is blocked by" link. The first one it would have taken was
an infrastructure incident. Dependencies, status, due dates and what people ask in chat say
more about what to work on next than the priority field.

## Decisions

| # | Decision | Answer | Why | Depends on |
|---|---|---|---|---|
| N1 | Who starts a ticket | `serve` chooses and starts on its own, no human approval step | the pipeline runs unattended | - |
| N2 | How it chooses | Code drops the tickets that cannot be worked (N6) and scores the rest; the model reads the 5 best and picks, with a reason for each pick | code is cheap, testable and explains the ranking; the model weighs text (chat asks, ticket bodies) that a score cannot | N1 |
| N3 | The signals | Tracker dependencies (`is blocked by` open = not workable; a ticket that unblocks others ranks higher), status (in progress before backlog), due date, chat mentions and asks about the ticket (N8), epic, labels, and an open PR/MR of the ticket | the real project above: priority set the same on almost every ticket; links on 33 of 39, due dates on 11 | N2 |
| N4 | How many at once | Up to `max_parallel` per company, the best N of each cycle | the slots of S15 stay the limit | N2 |
| N5 | The repo of a ticket | The model picks it from a closed list of allowed repos in the runbook, or answers "none"; a ticket with no repo is skipped and logged | one tracker project often spans many repos | N2 |
| N6 | Never taken | Tickets in the statuses listed as not workable (default: the names that mean blocked and in review, set per tracker in the runbook), and tickets with an open PR/MR by someone else | blocked work waits for someone; in review waits for a reviewer; a PR of a colleague is their work | N2 |
| N7 | What makes `serve` start | Each tracker project in the runbook lists its allowed repos (`jira: {PROJ: {repos: [...]}}`, and the same for GitHub Issues); without the list `serve` and `doctor --runbooks` refuse to start | no runbook can turn every assigned ticket into work by accident | N5 |
| N8 | Chat as a signal | A chat source interface: mentions and DMs of the token owner in the last 14 days, the team channels named in the runbook, and messages that cite the ticket key; Slack is the first source, read only | asks in chat are the most direct signal of what is wanted now; the interface lets other chat tools follow without changing the selector | N3 |
| N9 | Cost of the choice | The model call of N2 is paid from the `max_usd` of the task it picks | one budget per task, no new key | N2 |
| N10 | Where the reason lives | In the log and in the state entry of the ticket (`work:<company>`); nothing extra is written on the ticket | no extra visible write on the tracker | N2 |
| N11 | Weights of the score | Fixed in code and documented; a change is a PR with a test | fewer settings; the model already decides among the top 5 | N2 |
| N12 | Code hosts and trackers covered | Code hosts GitHub and GitLab (new); trackers Jira and GitHub Issues | what `serve` uses today plus GitLab; GitLab Issues and Bitbucket stay out | - |
| N13 | GitLab login | Personal or project access token `BRIAR_<COMPANY>_GITLAB_TOKEN`, base URL in `[auth.gitlab] url` (default `https://gitlab.com`, any self-hosted URL) | same pattern as the GitHub PAT; a session cookie expires and cannot clone | N12 |
| N14 | GitLab in `serve` | The same verbs as GitHub: clone and push over HTTPS with the token, draft MR, mark ready, MR notes and discussions (inline and general), resolved discussions, pipeline status and failed job logs, merge status against the base, a MR of the task branch | the ticket and review routines stay the same; only the adapter changes | N13 |
| N15 | Slack login | `BRIAR_<COMPANY>_SLACK_TOKEN` and, for a browser session token, `BRIAR_<COMPANY>_SLACK_COOKIE`; read only scopes; `[auth.slack] workspace` and the team channel ids in the runbook | a session token needs its cookie; a bot or user token works alone | N8 |
| N16 | One spec, several PRs | Spec 008: PR 1 the selector with tracker signals and N7; PR 2 the chat interface and Slack; PR 3 the GitLab provider (extract and serve); PR 4 the repo choice (N5) and the open MR/PR rule (N6) across both hosts; a grill before each PR | each PR is proved on its own; the selector cuts the "every assigned ticket" risk first | all |
| N17 | Truncated ticket lists in `extract` | `active-tickets` says "first N of M open tickets" (M from the tracker count) instead of "N open tickets" when it cuts | a real project showed "25 open ticket(s)" with 881 open | - |

## What every PR must show before merge

- A regression test that fails on the current code for each behavior it adds.
- A real run on a test company: the selector picks from real tickets and the log shows the
  score, the top 5 and the reason; nothing is written to a tracker that the maintainer did
  not approve for that run.
- For GitLab: a draft MR, a review round and a pipeline wait on a test project.

## Open risks

- N1 with N6 only: a ticket about production infrastructure can be picked. The bash allowlist
  of the agent has no infrastructure tools by default and briar never merges, so a person
  still gates any deploy; a runbook that adds such tools to `bash` removes that guard.
- N8: chat text reaches the selector and the agent; it is data, never an order (E21).
- N9: a cycle that picks several tickets pays several selector calls; each comes out of its
  own task budget.
- N15: a session token and cookie give full access to the account in that workspace; briar
  only reads, but the secret is as strong as the person who owns it.
- The dependency signal needs the tracker links to be current; stale links rank badly.

## Left undecided

- The exact scoring formula and its numbers (written in PR 1, from data of a real project).
- Teams, Discord or other chat sources (the interface of N8 allows them later).
