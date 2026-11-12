# `briar serve`

`briar serve DIR` is the agentic development pipeline of briar 2.0. It reads every
runbook in `DIR`, and for each company with a `serve:` block it watches GitHub and
Jira: the tickets assigned to the account of the token, and new review comments on the
PRs briar opened. It also runs the scheduled extracts of the runbooks (see
[runbooks.md](runbooks.md)), as `briar runbook serve` did in 1.x; a folder where no
company has a `serve:` block runs only those.

It works each ticket from the plan to a pull request with green CI, then answers the
review comments on that PR and merges the base when the PR is in conflict, until the PR
is merged or closed. Decisions: `docs/decisions/2026-11-11-briar-2-serve-pipeline.md`
and `docs/decisions/2026-11-11-briar-2-review-routine.md`.

## What it does

1. Loads every `*.yaml` runbook in `DIR`; a company with no `serve:` block is left alone.
2. Checks, before anything else, and stops with exit code 1 and every failure listed:
   - the model login: `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN`, in the
     environment or `secrets.env`;
   - the agent sandbox: `sandbox-exec` on macOS, `bwrap` and `socat` on Linux;
   - the GitHub and Jira logins of each company, live (the account, each repo, each
     Jira project);
   - write access, with no write: the PAT can push to each repo
     (`permissions.push`), the Jira login can add comments (and move tickets when
     `status` names are set).
3. Every `poll_minutes` (default 5), per company: the PRs briar opened first (a
   merged or closed one is recorded and left; one with new comments or a conflict gets
   a review round), then the open tickets assigned to the account of the token,
   highest priority first, then the oldest. A ticket briar already took is not taken again,
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
6. Gives each PR with new review comments, or in conflict with its base, a review
   round:
   1. the worktree is brought up to the branch on GitHub (a fast forward: a
      suggestion a reviewer committed comes in);
   2. when the PR is in conflict, briar merges `origin/<base>` (a merge commit, never
      a rebase or a force push) and the agent resolves the conflicts; a merge it
      cannot finish is dropped;
   3. one agent call reads every new comment and answers each one: changed, or why
      not;
   4. when the code changed: the verify loop, a self-review against the comments,
      the push;
   5. a reply in the thread of each inline comment, and one comment that quotes the
      others (and says when the base was merged); a fix names its commit;
   6. when the code changed, the CI wait with fixes, as for a ticket.

## Which comments count

- Only people with write access to the repo (`author_association` OWNER, MEMBER or
  COLLABORATOR) and the bots listed in `review_bots`. A comment of anyone else is
  logged and left alone: on a public repo anyone can comment.
- Every comment briar posts on GitHub ends with the hidden marker `<!-- briar -->`,
  and briar skips only marked comments. A comment you write yourself with the account
  of the token counts, so you can steer briar from the PR.
- A comment is answered once; an edit after the answer makes it new again, and so
  does a reply in its thread. A comment in a resolved thread is skipped.
- An approval with no text is not a comment.

## Limits

Each task has the `max_usd`, `max_loops` and `ci_wait_minutes` of its company, and
each PR at most `max_review_rounds` review rounds (a round that only merges the base
counts). When one hits, briar stops the task, pushes the work so far, opens the PR as a draft (or
turns the open one into a draft) with a report, and comments the same report on the
ticket. The ticket is `blocked` in the state; briar does not take it again.

```text
briar stopped: max_loops (5) reached while fixing: the verify commands. Spent US$ 1.20 of 5.0, 6 of 5 fix loops.
```

A git, GitHub or Jira failure in the middle of a task stops it the same way.

## Use it when

- You want briar to pick up tickets assigned to its account, on a server.
- You ran `briar runbook serve` in 1.x: `briar serve` does the same extracts, plus this.

## Before you start

- A runbook with a `serve:` block for the company (below; `briar init` writes an
  example), and the logins of its `auth:` block written in the environment or in
  `secrets.env` (`BRIAR_<COMPANY>_GITHUB_TOKEN`, and for Jira `site` under
  `auth.jira` plus `BRIAR_<COMPANY>_JIRA_EMAIL` and `BRIAR_<COMPANY>_JIRA_TOKEN`; see
  [logins.md](logins.md)). `briar doctor --runbooks DIR` checks them without
  starting `serve`.
- A fine-grained GitHub token with read and write on Contents, Pull requests and
  Issues of the repos, and read on Actions (for the CI logs). briar acts as the owner
  of that token: every issue assigned to that person becomes briar work, and the
  commits are made as that person.
- The model login.
- `git` on the `PATH`.
- The agent sandbox: built in on macOS; on Linux install `bubblewrap` and `socat` (the
  Docker image has them; the container must allow user namespaces, which is not tested
  yet).

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
      max_review_rounds: 5            # required: the most review rounds of one PR
      repos: [acme/web]               # GitHub repos: an issue assigned to the token owner is a ticket
      jira: {ENG: acme/app}           # Jira project -> the repo its tickets are built in
      priority_labels: [p0, p1, p2]   # the order of GitHub issues (first is most urgent)
      max_parallel: 3                 # optional
      poll_minutes: 5                 # optional
      verify: ["make lint", "make test"]
      bash: [git, make, pytest]       # optional: the programs the agent may run (replaces the default list)
      review_bots: ["coderabbitai[bot]"]   # optional: bots whose PR comments briar answers
      allowed_domains: [pypi.org]     # optional: what the bash of the agent may reach; none by default
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
serve: acme: 1 found, 1 started
serve: ENG-42 is pr-open https://github.com/acme/app/pull/17
serve: ENG-42 is done https://github.com/acme/app/pull/17
serve: ENG-42 is merged https://github.com/acme/app/pull/17
```

The second block is a review round, then the merge of the PR.

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
  refused. They run in the sandbox of the Claude Agent SDK: no network but
  `allowed_domains`, writes only in the worktree. Edits outside the worktree are
  refused, and the Read, Glob and Grep tools stay in the worktree and the knowledge
  folder. Bash itself can still read other files of the user (`cat`), so run `serve`
  as a user with no other secrets.
- A blocked ticket is not read again. To give it a new try, remove its entry from the
  state file.
