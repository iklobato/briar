# `briar serve`

`briar serve DIR` is the agentic development pipeline of briar 2.0. It reads every
runbook in `DIR`, and for each company with a `serve:` block it watches GitHub and
Jira: the tickets assigned to the account of the token, and new review comments on the
PRs briar opened. It also runs the scheduled extracts of the runbooks, as
`briar runbook serve` does.

The work on a ticket (plan, build on a branch, open the PR) and on review comments
(fix or answer each one) arrives in the next PRs of spec 003. Today `serve` checks the
logins, finds the work in order, runs it in parallel, and records each ticket it found
in the state of the company. Decisions:
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
   priority first, then the oldest. A ticket briar already took is not taken again.
4. Runs up to `max_parallel` tasks of a company at once (default 3), never two on the
   same ticket. A task that fails is logged; the others go on.

## Use it when

- You want briar to pick up tickets assigned to its account, on a server.
- You run `briar runbook serve` today: `briar serve` does the same extracts, plus this.

## Before you start

- A runbook with a `serve:` block for the company (below), and the logins of its
  `auth:` block saved (`BRIAR_<COMPANY>_GITHUB_TOKEN`, and for Jira `site` under
  `auth.jira` plus `BRIAR_<COMPANY>_JIRA_EMAIL` and `BRIAR_<COMPANY>_JIRA_TOKEN`).
- A fine-grained GitHub token with read and write on Contents, Pull requests and
  Issues of the repos. briar acts as the owner of that token: every issue assigned to
  that person becomes briar work.
- The model login.

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
      status: {started: In Progress, review: In Review}   # optional; no names: no move
      branch: "briar/{key}"           # optional
      title: "{key}: {title}"         # optional
```

A missing required key, an unknown key, a bad `owner/repo` or Jira key, or a block
with neither `repos` nor `jira` stops the load with exit code 1.

## Examples

```bash
briar serve runbooks/
```

You get the check lines, then one line per company and cycle:

```text
serve: acme: 2 found, 2 started
serve: ticket ENG-42 for acme/app: Fix the login (the ticket routine is not built yet)
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
