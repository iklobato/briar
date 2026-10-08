# `briar agent`

## Purpose
Run an autonomous LLM-driven flow that clones a repo, reads context,
edits code, and opens a PR (or pushes fixes to an existing one).
Two archetypes ship today:

| Op | Archetype | What it does |
|---|---|---|
| `prfix` | pr-fixer | Reads open review comments on a PR, pushes fix commits, replies to threads |
| `implement` | engineer | Picks up one ticket, designs a change, writes the code, opens a draft PR |

Both share the same `AgentRunner` core — only the JIT-context module
and the system prompt differ.

## When to use

| Trigger | Op |
|---|---|
| There's a ticket and you want a draft PR | `implement` |
| A PR has unresolved review comments you want addressed | `prfix` |
| You're orchestrating a multi-card sweep | Don't call this directly — use `briar plan run` |

## Prerequisites

| Need | Source |
|---|---|
| Anthropic credentials | `CLAUDE_CODE_OAUTH_TOKEN` (tried first) or `ANTHROPIC_API_KEY` |
| Repo provider auth | `GITHUB_TOKEN` / `BITBUCKET_<COMPANY>_USERNAME` + `BITBUCKET_<COMPANY>_APP_PASSWORD` |
| Tracker auth (for `implement`) | `JIRA_<COMPANY>_*` / `LINEAR_<COMPANY>_TOKEN` / `GITHUB_TOKEN` for `github-issues` |
| Runbook YAML (optional, recommended) | `--runbook examples/all_features.yaml`. Without it, the agent has no `send_message` tool and must fall back to bash `gh pr comment`/`curl` |
| Meeting / Slack context (optional) | `FIREFLIES_<COMPANY>_API_KEY` and/or `SLACK_<COMPANY>_TOKEN` + `SLACK_<COMPANY>_COOKIE_D`. When set, matching transcripts / threads are fetched into the prompt (`--meeting-key`, `--meeting-query`, `--slack-query`; the queries default to the PR id or ticket key). Skipped when unset |
| Knowledge blob (optional) | `briar extract --company <name>` beforehand, plus `briar plan build ... --company <name>` if part of a plan flow |

## Commands

> `--company` and the repo target resolve through **CLI > env >
> `.briar.toml` > git remote**. Pass the repo as a single
> `--repo owner/repo` slug (or a bare name with `--owner`); inside a
> checkout with a `[repo]` section in `.briar.toml` you can drop it
> entirely. `--ticket-project` is derived from the ticket key for
> Jira/Linear (`KAN-7` → `KAN`) and from owner/repo for GitHub/Bitbucket
> Issues, so the smallest call is `briar agent implement --ticket-key <KEY>`.
> The explicit flags below still win when given.

### Implement one ticket (engineer flow)

```bash
briar agent implement \
    --company <COMPANY> \
    --repo <OWNER>/<REPO> \
    --ticket-key <KEY> \
    --tracker <jira|github-issues|bitbucket-issues|linear> \
    --runbook examples/all_features.yaml

# or with Docker:
docker run --rm -v "$PWD":/work -w /work \
    -v "$HOME/.config/briar":/home/briar/.config/briar \
    -v "$HOME/.ssh":/home/briar/.ssh:ro -v "$HOME/.gitconfig":/home/briar/.gitconfig:ro \
    -e ANTHROPIC_API_KEY \
    iklob1/briar agent implement \
    --company <COMPANY> \
    --repo <OWNER>/<REPO> \
    --ticket-key <KEY> \
    --tracker <jira|github-issues|bitbucket-issues|linear> \
    --runbook examples/all_features.yaml
```

`--ticket-project` is derived from the ticket key (Jira/Linear) or
owner/repo (GitHub/Bitbucket) when omitted; pass it explicitly only when
the project differs from what the key implies.

Worked example:

```bash
briar agent implement \
    --company acme --repo acme/widgets \
    --ticket-key KAN-7 \
    --tracker jira \
    --runbook examples/simple-single-repo.yaml

# or with Docker:
docker run --rm -v "$PWD":/work -w /work \
    -v "$HOME/.config/briar":/home/briar/.config/briar \
    -v "$HOME/.ssh":/home/briar/.ssh:ro -v "$HOME/.gitconfig":/home/briar/.gitconfig:ro \
    -e ANTHROPIC_API_KEY \
    iklob1/briar agent implement \
    --company acme --repo acme/widgets \
    --ticket-key KAN-7 \
    --tracker jira \
    --runbook examples/simple-single-repo.yaml
```

### Fix review comments on a PR (pr-fixer flow)

```bash
briar agent prfix \
    --company <COMPANY> \
    --repo <OWNER>/<REPO> \
    --pr <NUMBER> --branch <HEAD_BRANCH> \
    --runbook examples/all_features.yaml

# or with Docker:
docker run --rm -v "$PWD":/work -w /work \
    -v "$HOME/.config/briar":/home/briar/.config/briar \
    -v "$HOME/.ssh":/home/briar/.ssh:ro -v "$HOME/.gitconfig":/home/briar/.gitconfig:ro \
    -e ANTHROPIC_API_KEY \
    iklob1/briar agent prfix \
    --company <COMPANY> \
    --repo <OWNER>/<REPO> \
    --pr <NUMBER> --branch <HEAD_BRANCH> \
    --runbook examples/all_features.yaml
```

### Dry-run before spending tokens

```bash
briar agent implement ... --dry-run

# or with Docker:
docker run --rm -v "$PWD":/work -w /work \
    -v "$HOME/.config/briar":/home/briar/.config/briar \
    -v "$HOME/.ssh":/home/briar/.ssh:ro -v "$HOME/.gitconfig":/home/briar/.gitconfig:ro \
    -e ANTHROPIC_API_KEY \
    iklob1/briar agent implement ... --dry-run
```

Prints the assembled system prompt + user message + tool list, then
exits without calling the LLM. Use this to validate that the JIT
context fetch (`ticket-context` for `implement`, `pr-review-context`
for `prfix`) is wiring correctly before committing real tokens.

### Override model / iteration cap

```bash
briar agent implement ... \
    --model claude-sonnet-4-6 \
    --max-iter 30

# or with Docker:
docker run --rm -v "$PWD":/work -w /work \
    -v "$HOME/.config/briar":/home/briar/.config/briar \
    -v "$HOME/.ssh":/home/briar/.ssh:ro -v "$HOME/.gitconfig":/home/briar/.gitconfig:ro \
    -e ANTHROPIC_API_KEY \
    iklob1/briar agent implement ... \
    --model claude-sonnet-4-6 \
    --max-iter 30
```

### Keep the worktree for inspection

```bash
briar agent implement ... --keep-worktree
# After: cd into the briar-agent-implement-* temp dir (path is in the -v log)

# or with Docker:
docker run --rm -v "$PWD":/work -w /work \
    -v "$HOME/.config/briar":/home/briar/.config/briar \
    -v "$HOME/.ssh":/home/briar/.ssh:ro -v "$HOME/.gitconfig":/home/briar/.gitconfig:ro \
    -e ANTHROPIC_API_KEY \
    iklob1/briar agent implement ... --keep-worktree
```

Without `--keep-worktree` the temp dir is deleted after a successful
run. A failed run keeps it so you can inspect what the agent did.

## Verifying success

For `implement`:
1. Exit code `0`.
2. Output ends with `--- agent final text ---` and, when the agent
   committed, a `--- commits: ... ---` line.
3. `gh pr view <pr> --json state,headRefName` shows the PR exists on
   the expected branch.

For `prfix`:
1. Exit code `0`.
2. New commits appear on the PR's head branch.
3. Review threads have replies prefixed with `[AI]`.

`briar agent` does not write a journal session on its own. When it
runs inside `briar plan run`, `briar journal list --command plan.run`
shows the per-card events.

## Common failures

| Symptom | Fix |
|---|---|
| Exit 1 with a missing-credential error | Run `briar secrets doctor --examples examples/` and fix what's missing |
| `Anthropic API 429` | Hit rate limit. The provider's error policy aborts fast on 429 (no silent retries); back off and re-run |
| Worktree clone fails | Token lacks `repo` scope, or repo is in an org you can't access. Verify with `gh auth status` |
| Ticket not found | Wrong `--tracker`, wrong `--ticket-project`, or token can't read that Jira project |
| Agent loops without writing code | Likely no `send_message` tool wired AND no Bash tool result triggered a stop. Pass `--runbook <yaml>` so it has a real message channel |
| Need to debug what the agent saw | Add `--dry-run` and read the printed prompt + tool list |
| Postgres store but `BRIAR_DATABASE_URL` unset | `--store postgres` requires the env var. The default is derived (postgres when `BRIAR_DATABASE_URL` is set, else `file`), so just unset the var or pass `--store file` |
| Git identity error on commit | Pass `--git-user-name`/`--git-user-email`, set `git_identity` in the runbook, or configure git locally (`git config user.name` / `user.email`); ambient git config is used outside CI |
