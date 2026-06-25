# `briar scaffold`

## Purpose
Generate a JSON config bundle that a downstream orchestrator (a
hosted Briar deployment, a custom worker) consumes to wire up a
full agent flow. The CLI doesn't run the flow itself — it just
emits the bundle.

Two templates today:

| Template | Shape |
|---|---|
| `implementation` | Issue → plan → human approval → implement / comment |
| `pr-fixes` | Read PR review comments → push fixes → reply (no human gate) |

## When to use
- Stand up a new agent flow against a fresh repo / company.
- You want a JSON contract a deployment can consume.
- You want a default config you can hand-edit.

If you are running the agent directly on your machine, you don't
need scaffold — call `briar agent implement` / `prfix` straight.

## Prerequisites
- A naming `--prefix` you want resources keyed under.
- For source-specific flags: the relevant secret UUIDs (PAT auth)
  or OAuth client info, depending on `--auth-mode`.
- For each source you include, the identity fields (GitHub `--owner
  --repo`, Bitbucket `--bitbucket-workspace --bitbucket-repo`, Jira
  `--jira-project`, AWS `--aws-role-arn`, Sentry `--sentry-org
  --sentry-project`).

## Commands

### Issue → plan → approve → implement (most common)

```bash
briar scaffold implementation \
    --prefix <NAME> \
    --source github --owner <OWNER> --repo <REPO> \
    --archetype engineer --shape plan-approve-act \
    --trigger-kind github_webhook \
    --auth-mode oauth \
    --out <PATH>.json

# or with Docker:
docker run --rm -v "$PWD":/work -w /work \
    -v "$HOME/.config/briar":/home/briar/.config/briar -e ANTHROPIC_API_KEY \
    iklob1/briar scaffold implementation \
    --prefix <NAME> \
    --source github --owner <OWNER> --repo <REPO> \
    --archetype engineer --shape plan-approve-act \
    --trigger-kind github_webhook \
    --auth-mode oauth \
    --out <PATH>.json
```

### PR-fixes flow (no human gate)

```bash
briar scaffold pr-fixes \
    --prefix <NAME> \
    --source github --owner <OWNER> --repo <REPO> \
    --auth-mode pat --github-secret-id <UUID> \
    --out <PATH>.json

# or with Docker:
docker run --rm -v "$PWD":/work -w /work \
    -v "$HOME/.config/briar":/home/briar/.config/briar -e ANTHROPIC_API_KEY \
    iklob1/briar scaffold pr-fixes \
    --prefix <NAME> \
    --source github --owner <OWNER> --repo <REPO> \
    --auth-mode pat --github-secret-id <UUID> \
    --out <PATH>.json
```

### Multi-source bundle (GitHub + Jira + AWS + Sentry)

```bash
briar scaffold implementation \
    --prefix <NAME> \
    --source github --owner <OWNER> --repo <REPO> \
    --source jira --jira-project <KEY> \
    --source aws --aws-role-arn <ARN> --aws-external-id <ID> --aws-region us-east-1 --aws-services ec2,s3 \
    --source sentry --sentry-org <SLUG> --sentry-project <PROJ> --sentry-secret-id <UUID> \
    --auth-mode pat \
    --github-secret-id <UUID> --jira-secret-id <UUID> \
    --out <PATH>.json

# or with Docker:
docker run --rm -v "$PWD":/work -w /work \
    -v "$HOME/.config/briar":/home/briar/.config/briar -e ANTHROPIC_API_KEY \
    iklob1/briar scaffold implementation \
    --prefix <NAME> \
    --source github --owner <OWNER> --repo <REPO> \
    --source jira --jira-project <KEY> \
    --source aws --aws-role-arn <ARN> --aws-external-id <ID> --aws-region us-east-1 --aws-services ec2,s3 \
    --source sentry --sentry-org <SLUG> --sentry-project <PROJ> --sentry-secret-id <UUID> \
    --auth-mode pat \
    --github-secret-id <UUID> --jira-secret-id <UUID> \
    --out <PATH>.json
```

### Filter issues by author / assignee

Shared `--authors-allow` / `--authors-block` / `--assignees-allow` /
`--assignees-block` apply to **every** `--source` (repeatable):

```bash
briar scaffold implementation --prefix <NAME> \
    --source github --source jira \
    --owner <OWNER> --repo <REPO> --jira-project <KEY> \
    --authors-block "dependabot[bot]" --assignees-allow alice

# or with Docker:
docker run --rm -v "$PWD":/work -w /work \
    -v "$HOME/.config/briar":/home/briar/.config/briar -e ANTHROPIC_API_KEY \
    iklob1/briar scaffold implementation --prefix <NAME> \
    --source github --source jira \
    --owner <OWNER> --repo <REPO> --jira-project <KEY> \
    --authors-block "dependabot[bot]" --assignees-allow alice
```

The per-source forms (`--jira-authors-allow`, …) still parse and override
the shared flag for that one source, but are hidden from `-h`.

### Print to stdout (no file)

Omit `--out` — bundle prints to stdout, pipe into `jq` or a deployer.

```bash
briar scaffold implementation --prefix demo --source github --owner foo --repo bar | jq '.'

# or with Docker:
docker run --rm -v "$PWD":/work -w /work \
    -v "$HOME/.config/briar":/home/briar/.config/briar -e ANTHROPIC_API_KEY \
    iklob1/briar scaffold implementation --prefix demo --source github --owner foo --repo bar | jq '.'
```

## Choices that matter

| Flag | Options | When to pick |
|---|---|---|
| `--archetype` | `engineer`, `pr-fixer`, `pr-ci-fixer`, `pr-conflict-resolver`, `triager` | Which agent persona |
| `--shape` | `plan-approve-act`, `one-shot`, `triage` | Whether a human gate sits in the middle |
| `--trigger-kind` | `github_webhook`, `bitbucket_webhook`, `schedule_cron`, `manual` | What kicks off a run |
| `--auth-mode` | `oauth` (default), `pat` | OAuth for GitHub/Bitbucket/Jira; Sentry is always PAT |

## Verifying success

1. Exit `0`.
2. The JSON parses: `jq . <PATH>.json`.
3. Top-level keys include `id`, `triggers`, `sources`, `agent`,
   `tools`, `messages` — exactly what your downstream consumes.
4. Hand to the consumer; it should accept without schema errors.

## Common failures

| Symptom | Fix |
|---|---|
| `--<source>-secret-id` is required | You picked `--auth-mode pat` (or chose Sentry). Either pass the secret UUID or switch to `--auth-mode oauth` (not for Sentry) |
| `at least one --sentry-project required` | Sentry source needs an explicit project list (repeatable flag) |
| Bundle missing a section you expect | You didn't include that `--source`. The bundle only contains sources you explicitly named |
