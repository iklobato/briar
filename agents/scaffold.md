# `briar scaffold`

## What it does

`briar scaffold` writes a **bundle**: one JSON file that describes a
whole agent flow. The bundle says which model to use, where the agent
reads from (GitHub, Jira, AWS and so on), which tools it may use, the
steps of the workflow, and what starts a run. You hand the file to the
system that runs agent flows. The CLI itself does not run the flow.

There are two templates:

| Template | The flow it describes |
|---|---|
| `implementation` | A new issue arrives. The agent writes a plan, a person approves or rejects it, then the agent makes the change (or comments on the issue). |
| `pr-fixes` | Every hour, the agent reads open review comments on pull requests, pushes fixes and replies. No person approves first. |

Both templates take the same flags. Only three defaults differ (see
below). Each run also records a session in the decision journal, under
`scaffold.implementation` or `scaffold.pr-fixes` (see
[journal.md](journal.md)).

If you want to run an agent on your own machine now, you do not need a
bundle. Use `briar agent implement` or `briar agent prfix` (see
[agent.md](agent.md)).

## Use it when

- You set up a flow on a server that runs agents, and it reads this bundle format.
- You want a starting config that you edit by hand.
- You want the same flow for many repos, with only the names changed.

## Examples

```bash
# The simplest bundle: GitHub issues in acme-co/acme-app, printed to the screen.
briar scaffold implementation --prefix acme-impl --owner acme-co --repo acme-app
```

You get the JSON on stdout. The GitHub source is used because no `--source` was given.

```bash
# Save the PR fixer for the same repo to a file.
briar scaffold pr-fixes --prefix acme-prfix --owner acme-co --repo acme-app \
    --out acme-prfix.json
```

You get `acme-prfix.json` with an hourly cron trigger, and `wrote acme-prfix.json` on stderr.

```bash
# Use a stored GitHub token instead of an OAuth login.
briar scaffold pr-fixes --prefix acme-prfix --owner acme-co --repo acme-app \
    --auth-mode pat --github-secret-id 7c1e4a52-9b0d-4c55-8f0e-2d3b6a1f9e10
```

You get a bundle whose GitHub source and tools point at that secret ID.

```bash
# GitHub plus Jira plus AWS, and skip issues opened by bots.
briar scaffold implementation --prefix acme-impl \
    --source github --owner acme-co --repo acme-app \
    --source jira --jira-project ACME \
    --source aws --aws-role-arn arn:aws:iam::123456789012:role/briar-read \
    --aws-external-id acme-briar --aws-services ecs --aws-services rds \
    --authors-block "dependabot[bot]" --authors-block "renovate[bot]"
```

You get three sources in one bundle. The author filter is applied to the GitHub and Jira sources.

```bash
# Run on a cron schedule, every weekday at 09:00, with no human check.
briar scaffold implementation --prefix acme-daily --owner acme-co --repo acme-app \
    --shape one-shot --trigger-kind schedule_cron --schedule "0 9 * * 1-5"
```

You get a bundle with a `schedule` trigger and a one-step workflow.

```bash
# Check the bundle with jq.
briar scaffold implementation --prefix acme-impl --owner acme-co --repo acme-app | jq 'keys'
```

You get the top-level keys: `agents`, `llm_models`, `sources`, `tools`, `triggers`, `version`, `workflows`.

## The three defaults that differ

| Flag | `implementation` | `pr-fixes` |
|---|---|---|
| `--archetype` | `engineer` | `pr-fixer` |
| `--shape` | `plan-approve-act` | `one-shot` |
| `--trigger-kind` | `github_webhook` | `schedule_cron` |

`pr-fixes` uses a cron trigger because the webhook trigger only reads
issue events, not pull request events.

## All parameters

`implementation` and `pr-fixes` take every flag below.

### Core flags

| Parameter | What it means | Default | Example |
|---|---|---|---|
| `--prefix` | A name put in front of every key in the bundle, such as `acme-impl-model`. Required. | none | `--prefix acme-impl` |
| `--out`, `-o` | The file to write. `-` means stdout. | `-` (stdout) | `--out acme-impl.json` |
| `--source` | Where the agent reads from: `github`, `bitbucket`, `jira`, `aws` or `sentry`. Repeat the flag for more than one. | `github` when you give none. | `--source jira` |
| `--archetype` | The agent's role. It sets the persona, the step limit and which tools it keeps (see the table below). | `engineer` (`pr-fixer` for `pr-fixes`) | `--archetype triager` |
| `--shape` | The workflow steps: `plan-approve-act`, `one-shot` or `triage` (see below). | `plan-approve-act` (`one-shot` for `pr-fixes`) | `--shape one-shot` |
| `--trigger-kind` | What starts a run: `github_webhook`, `bitbucket_webhook`, `schedule_cron` or `manual` (see below). | `github_webhook` (`schedule_cron` for `pr-fixes`) | `--trigger-kind manual` |
| `--llm-provider-key` | The model provider name written to `llm_models[].provider_key`. | `anthropic` | `--llm-provider-key openai` |
| `--model` | The model name written to `llm_models[].name`. The runner passes it on as `<provider>/<model>`. | `claude-sonnet-4-6`, or `model` under `[agent]` in `.briar.toml` | `--model gpt-4o` |
| `--auth-mode` | How the GitHub and Bitbucket sources log in. `oauth` means an OAuth link set up in the runner. `pat` means a stored token, named by its secret ID. | `oauth` | `--auth-mode pat` |
| `--github-secret-id` | The ID (a UUID) of the stored GitHub token. Required with `--auth-mode pat` and `--source github`. | none | `--github-secret-id 7c1e4a52-9b0d-4c55-8f0e-2d3b6a1f9e10` |
| `--company` | A company whose saved knowledge briar copies into the agent's system prompt. Only the sections the archetype reads are copied (see the archetype table below). Without it, the prompt has no knowledge sections. | `$BRIAR_COMPANY`, else `company` in `.briar.toml`, else empty. | `--company acme` |
| `--knowledge-store` | The knowledge store to read that knowledge from: `file` or `postgres`. | `postgres` if `BRIAR_DATABASE_URL` is set, else `file`. | `--knowledge-store file` |

### Filters for every source

These four flags apply to each `--source` that reads issues (`github`,
`bitbucket` and `jira`). You can repeat each one. briar writes them
into the `config` of each of those sources in the bundle.

| Parameter | What it means | Default | Example |
|---|---|---|---|
| `--authors-allow` | Only include issues opened by these users. | none (all) | `--authors-allow alice` |
| `--authors-block` | Skip issues opened by these users. | none | `--authors-block "dependabot[bot]"` |
| `--assignees-allow` | Only include issues assigned to these users. | none (all) | `--assignees-allow bob` |
| `--assignees-block` | Skip issues assigned to these users. | none | `--assignees-block carol` |

There is also one hidden copy of each filter per source. They do not
show in `-h`, but they work. When you set one, it replaces the shared
filter for that one source only:

| Hidden parameter | Replaces, for that source |
|---|---|
| `--github-authors-allow`, `--github-authors-block`, `--github-assignees-allow`, `--github-assignees-block` | The shared filter, for `github` only. |
| `--bitbucket-authors-allow`, `--bitbucket-authors-block`, `--bitbucket-assignees-allow`, `--bitbucket-assignees-block` | The shared filter, for `bitbucket` only. |
| `--jira-authors-allow`, `--jira-authors-block`, `--jira-assignees-allow`, `--jira-assignees-block` | The shared filter, for `jira` only. |

For example, `--authors-allow alice --jira-authors-allow bob` keeps
alice's GitHub issues and bob's Jira issues. Each hidden flag is
repeatable and empty by default.

### GitHub source (`--source github`)

| Parameter | What it means | Default | Example |
|---|---|---|---|
| `--owner` | The GitHub user or org. Required for this source. | From `[repo]` in `.briar.toml`, else from your git `origin` remote. | `--owner acme-co` |
| `--repo` | The repo name, without the owner. Required for this source. | From `[repo]` in `.briar.toml`, else from your git `origin` remote. | `--repo acme-app` |

### Bitbucket source (`--source bitbucket`)

| Parameter | What it means | Default | Example |
|---|---|---|---|
| `--bitbucket-workspace` | The workspace slug: the part before `/` in the repo URL. Required for this source. | none | `--bitbucket-workspace acme` |
| `--bitbucket-repo` | The repo slug: the part after `/`. Required for this source. | none | `--bitbucket-repo acme-app` |
| `--bitbucket-secret-id` | The ID of the stored Bitbucket app password (username and password saved together). Required with `--auth-mode pat`. | none | `--bitbucket-secret-id 2b8f6d1e-4c3a-4e7b-9a10-5d6c7e8f9a01` |

### Jira source (`--source jira`)

| Parameter | What it means | Default | Example |
|---|---|---|---|
| `--jira-project` | A Jira project key to read. Repeatable. | none (all projects) | `--jira-project ACME` |
| `--jira-jql` | An extra JQL filter, applied on top of the projects. | none | `--jira-jql "labels = briar"` |
| `--jira-secret-id` | The ID of a stored Atlassian token. When set, Jira uses it. When empty, Jira uses an Atlassian OAuth link. `--auth-mode` does not change this. | none | `--jira-secret-id 5e2a9c71-0f4b-4d8e-b6a3-1c9d8e7f6a52` |

### AWS source (`--source aws`)

The AWS source is read-only context. It adds no tools.

| Parameter | What it means | Default | Example |
|---|---|---|---|
| `--aws-role-arn` | The IAM role the runner assumes to read AWS. | none | `--aws-role-arn arn:aws:iam::123456789012:role/briar-read` |
| `--aws-external-id` | The external ID that the role's trust policy asks for. | none | `--aws-external-id acme-briar` |
| `--aws-region` | The AWS region to read. | `us-east-1` | `--aws-region eu-west-1` |
| `--aws-services` | An AWS service to read. Repeat the flag for each one. | `ec2`, `iam` and `logs` when you give none. | `--aws-services s3` |

### Sentry source (`--source sentry`)

| Parameter | What it means | Default | Example |
|---|---|---|---|
| `--sentry-org` | The Sentry organization slug. Required for this source. | none | `--sentry-org acme` |
| `--sentry-project` | A Sentry project slug. Repeatable. At least one is required. | none | `--sentry-project acme-api` |
| `--sentry-environment` | Only read these environments. Repeatable. | none (all) | `--sentry-environment production` |
| `--sentry-query` | A Sentry issue search. | none | `--sentry-query "is:unresolved level:error"` |
| `--sentry-level` | Only read these levels: `fatal`, `error`, `warning`, `info` or `debug`. Repeatable. | none (all) | `--sentry-level error` |
| `--sentry-secret-id` | The ID of the stored Sentry auth token. Always required for this source: Sentry has no OAuth option yet. | none | `--sentry-secret-id 9d4c2b1a-8e7f-4a6b-b5c4-3d2e1f0a9b87` |

### Trigger flags

| Parameter | What it means | Default | Example |
|---|---|---|---|
| `--webhook-events` | GitHub events that start a run (with `github_webhook`). Repeatable. | `issues.opened` and `issues.labeled` when you give none. | `--webhook-events issues.reopened` |
| `--webhook-labels` | Only issues with one of these labels start a run (with `github_webhook`). Repeatable. Your labels are added to `briar`. | `briar` | `--webhook-labels agent` |
| `--bitbucket-webhook-events` | Bitbucket events that start a run (with `bitbucket_webhook`). Repeatable. | `issue:created` and `issue:updated` when you give none. | `--bitbucket-webhook-events issue:created` |
| `--bitbucket-webhook-labels` | Only issues with one of these labels start a run (with `bitbucket_webhook`). Repeatable. Your labels are added to `briar`. | `briar` | `--bitbucket-webhook-labels agent` |
| `--schedule` | A cron line for `schedule_cron`. | `0 * * * *` (at the start of every hour) | `--schedule "*/30 * * * *"` |

## Choices explained

### Archetypes (`--archetype`)

| Archetype | What the agent does | Tools it keeps | Step limit | Knowledge sections it reads |
|---|---|---|---|---|
| `engineer` | Makes changes from tickets, commits, opens PRs. | All tools from the sources. | 8 | `ticket-context`, `meeting-context`, `meeting-digest`, `codebase-conventions`, `code-hotspots`, `reviewer-profile`, `active-work`, `pr-archaeology` |
| `pr-fixer` | Answers open review comments with new commits. | commit, comment, open PR | 12 | `pr-review-context`, `meeting-context`, `meeting-digest`, `reviewer-profile`, `codebase-conventions`, `code-hotspots`, `active-work` |
| `pr-ci-fixer` | Fixes failing CI checks on open PRs, from the failure logs. | commit, comment | 20 | `pr-review-context`, `codebase-conventions`, `code-hotspots`, `active-work` |
| `pr-conflict-resolver` | Merges the base branch into old PRs and fixes conflicts. | commit, comment | 16 | `pr-review-context`, `active-work`, `codebase-conventions`, `reviewer-profile` |
| `triager` | Labels tickets and posts a short summary comment. | comment, add labels | 5 | `codebase-conventions`, `github-deployments`, `pr-archaeology`, `active-work` |

### Shapes (`--shape`)

| Shape | The steps |
|---|---|
| `plan-approve-act` | The agent writes a plan. A person approves or rejects it. On approve, the agent makes the change. On reject, it comments on the issue. |
| `one-shot` | One agent step. No human check. |
| `triage` | One agent step that labels and comments. It makes no commits. |

### Trigger kinds (`--trigger-kind`)

| Trigger | What starts a run |
|---|---|
| `github_webhook` | A GitHub issue event, filtered by `--webhook-events` and `--webhook-labels`. |
| `bitbucket_webhook` | A Bitbucket issue event, filtered by `--bitbucket-webhook-events` and `--bitbucket-webhook-labels`. |
| `schedule_cron` | The cron line in `--schedule`. |
| `manual` | Nothing. The bundle has no `triggers` key, and you start runs yourself. |

## Good to know

- Repeat list flags. `--aws-services ec2,s3` gives one service named
  `ec2,s3`. Write `--aws-services ec2 --aws-services s3` instead.
- The label `briar` is always in `--webhook-labels` and
  `--bitbucket-webhook-labels`. Your values are added to it.
- Missing identity flags stop the run with an error, such as
  `--source github requires --owner AND --repo`.
- `--auth-mode pat` without the matching secret ID stops with
  `--source github with --auth-mode pat requires --github-secret-id <secret-uuid>`.
- An AWS-only bundle has no repo name. briar then uses `--prefix` as the
  agent's target and prints a warning.
- Inside a repo, `--owner` and `--repo` come from `.briar.toml` or your
  git remote, and `--company` comes from `.briar.toml`. With a company
  set, briar reads every blob whose name starts with `knowledge:<company>`
  (so also task blobs such as `knowledge:acme.prs`), from the store in
  `--knowledge-store` (the `file` store reads `./knowledge`). It keeps
  only the sections the archetype reads. Other sections, and plan notes
  such as `knowledge:acme.q3`, are left out of the prompt. If the store
  cannot be opened, briar logs the error and leaves the knowledge out.
- The bundle only holds secret IDs, never secret values.
- The bundle has these top-level keys: `version`, `llm_models`,
  `sources`, `tools`, `agents`, `workflows`, and `triggers` (except
  with `manual`).
