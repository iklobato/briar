# `briar serve`

`briar serve DIR` is the agentic development pipeline of briar 2.0. It reads every
runbook in `DIR`, and for each company with a `serve:` block it watches GitHub and
Jira: the tickets assigned to the account of the token, and new review comments on the
PRs briar opened. It also runs the scheduled extracts of the runbooks, as
`briar runbook serve` does.

Today `serve` works each ticket from the plan to a pull request with green CI. The work
on review comments (fix or answer each one) arrives in the next PR of spec 003: until
then a new comment is only logged. Decisions:
`docs/decisions/2026-11-11-briar-2-serve-pipeline.md`.

## What it does

1. Loads every `*.yaml` runbook in `DIR`; a company with no `serve:` block is left alone.
2. Checks, before anything else, and stops with exit code 1 and every failure listed:
   - the model login: `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN`, in the
     environment or `secrets.env`;
   - the GitHub and Jira logins of each company, live (the account, each repo, each
     Jira project);
   - write access, with no write: the PAT can push to each repo
     (`permissions.push`), the Jira login can add comments (and move tickets when
     `status` names are set).
3. Every `poll_minutes` (default 5), per company: new comments on the PRs briar opened
   first, then the open tickets assigned to the account of the token, highest
   priority first, then the oldest. A ticket briar already took is not taken again,
   unless it stopped before the PR (then the next cycle tries again).
4. Runs up to `max_parallel` tasks of a company at once (default 3), never two on the
   same ticket. A task that fails is logged; the others go on.
5. Works each ticket in its own git worktree, on the branch `branch` (default
   `briar/{key}`), with the Claude Agent SDK and the `model` of the company:
   1. the agent reads the ticket, the knowledge of the company and the repo, and
      writes a plan; briar comments the plan on the ticket and moves it to
      `status.started`;
   2. the agent builds the plan; briar commits and runs the `verify` commands, and
      the agent fixes them until they pass;
   3. a second, read-only agent call reviews the diff against the plan; the agent
      fixes what it finds, and the verify commands run again;
   4. briar pushes the branch, opens the PR (ready, not a draft), comments the PR link
      on the ticket and moves it to `status.review`;
   5. briar waits for CI on the PR and, when it fails, gives the agent the failed job
      logs, then pushes the fix.

   The agent only edits files in the worktree and runs the allowed commands: the
   commits, the push, the PR, the comments and the status moves are done by briar.

## Limits

Each task has the `max_usd`, `max_loops` and `ci_wait_minutes` of its company. When
one hits, briar stops the task, pushes the work so far, opens the PR as a draft (or
turns the open one into a draft) with a report, and comments the same report on the
ticket. The ticket is `blocked` in the state; briar does not take it again.

```text
briar stopped: max_loops (5) reached while fixing: the verify commands. Spent US$ 1.20 of 5.0, 6 of 5 fix loops.
```

A git, GitHub or Jira failure in the middle of a task stops it the same way.

## Use it when

- You want briar to pick up tickets assigned to its account, on a server.
- You run `briar runbook serve` today: `briar serve` does the same extracts, plus this.

## Before you start

- A runbook with a `serve:` block for the company (below), and the logins of its
  `auth:` block saved (`BRIAR_<COMPANY>_GITHUB_TOKEN`, and for Jira `site` under
  `auth.jira` plus `BRIAR_<COMPANY>_JIRA_EMAIL` and `BRIAR_<COMPANY>_JIRA_TOKEN`).
- A fine-grained GitHub token with read and write on Contents, Pull requests and
  Issues of the repos, and read on Actions (for the CI logs). briar acts as the owner
  of that token: every issue assigned to that person becomes briar work, and the
  commits are made as that person.
- The model login.
- `git` on the `PATH`.

## The `serve:` block

```yaml
version: 1
companies:
  acme:
    auth:
      jira: {site: acme.atlassian.net}
    serve:
      model: claude-sonnet-4-5        # required: the model of the agent
      max_usd: 5                      # required: the most one task spends
      max_loops: 5                    # required: the most fix loops of one task
      ci_wait_minutes: 60             # required: the longest wait for CI
      repos: [acme/web]               # GitHub repos: an issue assigned to the token owner is a ticket
      jira: {ENG: acme/app}           # Jira project -> the repo its tickets are built in
      priority_labels: [p0, p1, p2]   # the order of GitHub issues (first is most urgent)
      max_parallel: 3                 # optional
      poll_minutes: 5                 # optional
      verify: ["make lint", "make test"]
      bash: [git, make, pytest]       # optional: the programs the agent may run (replaces the default list)
      status: {started: In Progress, review: In Review}   # optional; no names: no move
      branch: "briar/{key}"           # optional
      title: "{key}: {title}"         # optional
```

In `branch` and `title`, `{key}` is the Jira key or `issue-<number>` for a GitHub
issue, `{title}` the ticket title, and `{number}` the GitHub issue number.

A missing required key, an unknown key, a bad `owner/repo` or Jira key, or a block
with neither `repos` nor `jira` stops the load with exit code 1.

## Examples

```bash
briar serve runbooks/
```

You get the check lines, then one line per company and cycle, and one line each time
a ticket changes state:

```text
serve: acme: 1 found, 1 started
serve: ENG-42 is planned
serve: ENG-42 is pr-open https://github.com/acme/app/pull/17
serve: ENG-42 is done https://github.com/acme/app/pull/17
```

The state of each company is a JSON file in its knowledge store,
`<root>/work/<company>.md` (`root` of the `knowledge:` block, default `./knowledge`).

## All parameters

| Parameter | What it means | Default | Example |
|---|---|---|---|
| `directory` (positional) | The folder with the runbooks. Required. | none | `runbooks/` |

## Good to know

- `serve` runs until you stop it (Ctrl+C, exit code 130). Run it under a process
  manager such as systemd.
- A failed login check, before the first cycle or before a scheduled extract, stops
  `serve` with exit code 1.
- One `serve` per knowledge folder: two processes on the same folder would write the
  same state file.
- Jira priority uses its standard names (Highest to Lowest); GitHub issues use the
  first of their labels found in `priority_labels`. A ticket with neither comes last.
- The clones and worktrees live in `$XDG_CACHE_HOME/briar` (default
  `~/.cache/briar`). The token goes to git through a credential helper that reads a
  file only you can read; it is not in the remote URL, the git config or the logs.
- Bash commands are checked one by one: a program outside `bash` (or the default
  list), `git push`, `gh`, `curl`, `rm -rf`, `sudo` and other risky tokens are
  refused. Edits outside the worktree are refused. Reads are not limited to the
  worktree: the agent can read any file the user of `serve` can read, so run it as a
  user with no other secrets.
