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
| Anthropic credentials | `ANTHROPIC_API_KEY` or per-company `ANTHROPIC_<COMPANY>_API_KEY` |
| Repo provider auth | `GITHUB_<COMPANY>_TOKEN` / `BITBUCKET_<COMPANY>_APP_PASSWORD` |
| Tracker auth (for `implement`) | `JIRA_<COMPANY>_*` / `LINEAR_<COMPANY>_API_KEY` / `GITHUB_<COMPANY>_TOKEN` for `github-issues` |
| Runbook YAML (optional, recommended) | `--runbook examples/all_features.yaml`. Without it, the agent has no `send_message` tool and must fall back to bash `gh pr comment`/`curl` |
| Knowledge blob (optional) | `briar extract --company <name>` beforehand, plus `briar plan build ... --company <name>` if part of a plan flow |

## Commands

### Implement one ticket (engineer flow)

```bash
briar agent implement \
    --company <COMPANY> \
    --owner <OWNER> --repo <REPO> \
    --ticket-project <PROJECT> --ticket-key <KEY> \
    --tracker <jira|github-issues|bitbucket-issues|linear> \
    --runbook examples/all_features.yaml
```

Worked example:

```bash
briar agent implement \
    --company acme --owner acme --repo widgets \
    --ticket-project KAN --ticket-key KAN-7 \
    --tracker jira \
    --runbook examples/acme.yaml
```

### Fix review comments on a PR (pr-fixer flow)

```bash
briar agent prfix \
    --company <COMPANY> \
    --owner <OWNER> --repo <REPO> \
    --pr <NUMBER> --branch <HEAD_BRANCH> \
    --runbook examples/all_features.yaml
```

### Dry-run before spending tokens

```bash
briar agent implement ... --dry-run
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
```

### Keep the worktree for inspection

```bash
briar agent implement ... --keep-worktree
# After: cd /tmp/briar-*/<repo> to inspect the agent's actual changes
```

Without `--keep-worktree` the temp dir is deleted whether the run
succeeded or not.

## Verifying success

For `implement`:
1. Exit code `0`.
2. Output includes a `pr_url` field (when a PR was opened).
3. `gh pr view <pr> --json state,headRefName` shows the PR exists on
   the expected branch.

For `prfix`:
1. Exit code `0`.
2. New commits appear on the PR's head branch.
3. Review threads have replies prefixed with `[AI]`.

For both: `briar journal list --command-prefix agent.` shows a new
session with `agent.run.start`, per-iteration tool calls, and
`agent.run.completed` (or `agent.run.failed`).

## Common failures

| Symptom | Fix |
|---|---|
| Exit 3 / `CredentialError` | Run `briar secrets doctor --company <name>` and fix what's missing |
| `Anthropic API 429` | Hit rate limit. The provider's error policy aborts fast on 429 (no silent retries); back off and re-run |
| Worktree clone fails | Token lacks `repo` scope, or repo is in an org you can't access. Verify with `gh auth status` |
| Ticket not found | Wrong `--tracker`, wrong `--ticket-project`, or token can't read that Jira project |
| Agent loops without writing code | Likely no `send_message` tool wired AND no Bash tool result triggered a stop. Pass `--runbook <yaml>` so it has a real message channel |
| Need to debug what the agent saw | Add `--dry-run` and read the printed prompt + tool list |
| Postgres store but `BRIAR_DATABASE_URL` unset | `--store postgres` requires the env var; default to `--store file` if unsure |
