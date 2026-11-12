# briar 2.0: `briar serve`, the agentic development pipeline, as the core

Grill session of 2026-11-11, with no grill of the earlier PR 4 (the GitHub
device flow). The maintainer re-scoped 2.0: the core is `briar extract` and
`briar serve`, and `serve` runs the agentic development pipeline. It watches
for a ticket assigned to briar, writes a plan, reads the knowledge, builds the
change on a new branch, opens the PR, then watches the PR and answers every
review comment by fixing it or saying why not. It runs tasks in parallel, uses
existing libraries in code for access, and talks only to GitHub and Jira.

Format: `ID. Decision: chosen answer. Why. (depends on)`

Facts checked first (2026-11-11): `claude-agent-sdk` 0.2.180 on PyPI
(Python >= 3.10; needs `anyio`, `jsonschema`, `mcp`; one wheel per platform
that carries Claude Code, so no separate CLI install). PyGithub 2.9.1 has
`Auth.AppAuth` and `Auth.AppInstallationAuth` (not used: S2). The decisions
this file changes are listed at the end.

## Decisions

### The engine and the logins

- S1. Agent engine: the Claude Agent SDK (`claude-agent-sdk`), in Python. It
  is a ready coding agent (read, edit, bash, git, subagents, permissions), so
  briar writes no agent loop and no file tools. Accepted cost: Claude models
  only. Changes D3 (any model) and D9 (LangGraph). (none)
- S2. GitHub login of the bot: the fine-grained PAT per company that exists
  today (#97). The bot acts as the person who owns the token. The GitHub App
  and the device flow are not used. (none)
- S3. Model login: `ANTHROPIC_API_KEY`, or `CLAUDE_CODE_OAUTH_TOKEN` (the
  token of a Claude subscription), in the environment or `secrets.env`.
  (S1)
- S4. `claude-agent-sdk` is approved as a required dependency (in
  `dependencies`, not an extra): `serve` is the main feature. `mcp` comes
  back as an indirect dependency. (S1)
- S5. Model: `model` is required per company in the runbook; without it
  `serve` does not start (D17 kept). (S1)

### Scope

- S6. Commands of 2.0: `briar extract`, `briar serve`, `briar doctor` and
  `briar init`. `briar auth` (login, status) and `briar runbook` (extract,
  serve) go; no one-shot `briar run`. (none)
- S7. What replaces them: `briar serve <folder of runbooks>` replaces
  `runbook serve`; `runbook extract` and `auth login|status` go. Secrets go
  in the environment or in `secrets.env`, written by hand (the README lists
  the `BRIAR_*` names), and `doctor` checks them. `briar init` writes an
  example runbook. (S6)
- S8. Sources: nothing is removed from the code for now. The `serve`
  pipeline talks only to GitHub (repos, issues, PRs) and Jira; Bitbucket,
  Linear, Fireflies and the clouds stay as extractors only. The cleanup of
  those sources is left for later. (none)

### What `serve` watches

- S9. Trigger: a ticket is work for briar when it is assigned to the account
  of the token. Jira: JQL `assignee = currentUser()` with the Jira token of
  the company. GitHub: an issue assigned to the owner of the PAT. Accepted
  risk: with a personal PAT, every issue of that person becomes briar work.
  (S2)
- S10. Detection: polling. `serve` asks Jira and GitHub on a schedule; no
  public endpoint, works behind NAT. (S9)
- S11. Polling interval when the runbook sets none: 5 minutes. Per company
  and cycle that is one JQL search, one issue search and one read per open
  briar PR, far from the GitHub limit of 5,000 calls an hour up to tens of
  PRs. Not measured. (S10)
- S12. Config: the runbook YAML, many companies per `serve`: per company the
  Jira projects, the repos, the logins (`auth:`), the verify commands, the
  model, the limits and `max_parallel`. L2 (one account per source per
  company) is kept. (S6)
- S13. Repo of a Jira ticket: a map in the runbook, Jira project to repo
  (`ENG: acme/app`); a project with several repos uses a ticket field or
  label to pick one. (S12)
- S14. Order: comments on a PR that briar opened come before any new ticket;
  between tickets, the ticket priority decides (Jira priority; on GitHub, the
  priority labels the runbook lists). (S9)

### How it runs

- S15. Parallel: asyncio, one process. Up to `max_parallel` tasks at once,
  each ticket or PR in its own git worktree, never two tasks on the same
  ticket. (S1)
- S16. `max_parallel` when the runbook sets none: 3 (the D20 default, not
  measured). (S15)
- S17. State: one file per company in the local knowledge store
  (`work:<company>`): each ticket with its plan, branch, PR, and the review
  comments already answered. No SQLite. Tasks that run in parallel write it
  under a lock. A restart picks up from it. Changes D21 and D36. (S15)
- S18. Pipeline: fixed in code, two routines. Ticket: plan, implement,
  verify, self-review, open the PR, wait for CI. Review: read the new
  comments, fix or justify, reply, push. The runbook only holds values
  (verify commands, limits, names). Changes D11 to D13 and A2 to A7. (S1)
- S19. Plan: the agent writes it first; briar posts it as a comment on the
  ticket and puts it in the PR description, then goes on with no wait for a
  person (D22 kept). (S18)
- S20. Checks before the push: the local verify (the runbook commands, fixed
  until they pass or a limit hits), a self-review by a subagent against the
  plan, and after the push the wait for CI, with a fix when it fails. D6
  kept. (S18)
- S21. The PR opens ready for review, not as a draft. It becomes a draft only
  when a limit hits (S23). Changes D4. (S20)
- S22. Review: every new comment, from people and bots, on the PRs briar
  opened. Each one gets a fix and a reply that names the commit, or a reply
  that says why nothing changed. Then a push; the next cycle looks for new
  comments again. (S18)
- S23. Limits per task: `max_usd`, `max_loops` and a CI wait limit. When one
  hits: push, the PR becomes a draft with a report (last error, loops, cost),
  and the ticket gets a comment (D16). The parallel slot is freed. (S18)
- S24. The limits are required in the runbook; without them `serve` does not
  start, as with `model`: there is no cost data for a default. (S23)
- S25. Agent permissions: edits are free inside the worktree; bash runs only
  the commands of the allowlist of the company (A13). Push, PR, comments and
  ticket changes are briar code, never the agent (D10 kept). (S1)
- S26. Ticket status: `serve` moves the ticket, with the status names per
  company in the runbook (for example `In Progress` when it starts, `In
  Review` when the PR opens). With no names set it only comments. (S12)
- S27. Branch and PR title: formats per company in the runbook (`branch`,
  `title`, with `{key}`, `{title}`, `{number}`); when unset, branch
  `briar/{key}` and title `{key}: {title}`. The description holds the plan
  and the ticket link (`Closes #N` for a GitHub issue). (S12)
- S28. Knowledge: `serve` also runs the scheduled extracts of the runbook,
  as `runbook serve` does today, and each task gives the agent the path of
  the company blob (`knowledge:<company>`) and of the state file; the agent
  reads them with its read tool. No new knowledge layout (K1 to K20 wait).
  (S17)
- S29. The login check of PR #99 also checks write access, with no write:
  GitHub `GET /repos/{repo}` gives `permissions.push` for the owner of the
  PAT; Jira `mypermissions` gives `ADD_COMMENTS` (and `TRANSITION_ISSUES`
  when status names are set). A missing write stops `serve` before any task.
  Moves L5 here from spec 003. (S2, S26)

### Delivery

- S30. The PR stack #79 to #99 stays for review and merge. The new spec comes
  as PRs stacked on #99. The GitHub device flow of branch
  `logins/04-github-device` (commit `cf7bcb4`, never pushed) is dropped (S2)
  and the local branch is deleted; `git branch device-flow cf7bcb4` brings it
  back while the reflog keeps it. (S2)
- S31. Four PRs, in order: (1) the new runbook schema and `briar serve`:
  polling, the parallel tasks, the state file, the read and write checks,
  the scheduled extracts; (2) the ticket routine: plan, implement, verify,
  self-review, PR, CI wait, status; (3) the review routine; (4) `auth` and
  `runbook` leave, `init` writes the example runbook, the docs. (S6, S18)
- S32. How 2.0 is released stays open until the four PRs are ready;
  `release.yml` does not change now. (none)

## What follows from earlier decisions (no new choice)

- S33. The self-review subagent uses the model of the company (S5).
- S34. `briar extract` keeps reading `.briar.toml` (C1, E1 to E16); only
  `serve` reads runbooks.
- S35. `doctor` checks the model login (S3), the PAT and Jira logins with
  write (S29), and the runbook fields that are required (S5, S24).

## Earlier decisions this file changes

- D3 (any model) and D9 (LangGraph): the Claude Agent SDK, Claude models
  only (S1).
- D4 (PR ready only with green CI): the PR opens ready; draft only on a
  limit (S21).
- D7, D19, D20 (the board pipeline and its selector): no board; `serve`
  takes assigned tickets by priority (S9, S14).
- D11 to D13, A2 to A7, A11, A14 (pipelines in YAML): fixed routines in code
  (S18).
- D21, D36 (SQLite checkpoints, run history): one state file per company in
  the knowledge store (S17).
- D35 (`briar run`, `resume`, `runs`): none; `serve` only (S6).
- A1 (4 roles): one agent per task plus a self-review subagent (S1, S20).
- P1, P5, P12, T6 (meetings and Slack as context): not in the pipeline; only
  GitHub, Jira and the knowledge blob (S8, S28).
- C14, C15 and F1 (the `runbook` and `auth` commands): they leave (S6, S7).
- G1 to G26, J9, J13, J14 (logins): the logins model, the `[auth]` blocks,
  the `BRIAR_*` names and the check stay; the `auth` command that wrote and
  showed them leaves (S7).

## Open risks

- S2 with S9: a personal PAT makes every issue assigned to that person briar
  work, and the commits and replies carry the name of that person.
- S1: Claude models only, and the Agent SDK carries its own Claude Code
  binary, whose version follows the SDK.
- S7: without `auth login`, a person writes `secrets.env` by hand; a typo
  shows up only in `doctor` or the check.
- S11, S16: the polling interval and `max_parallel` are not measured.
- S21: a PR opens ready before CI ends, so a reviewer can see a red CI.
- S22: replying to bots can make two bots and briar talk in a loop; the
  limits of S23 bound it.
- S26: a status name that the workflow of a project does not allow from
  the current status fails the transition.
- S8: the sources that stay but that `serve` does not use keep their code and
  tests to maintain until the cleanup.
- S32: the release of 2.0 is not decided.
