# briar

**Turn the live state of your tools into agent-ready context — then let autonomous agents act on it. All on your machine.**

`briar` is a local-first Python CLI that mines what's actually happening across your stack — GitHub, Bitbucket, AWS/GCP/Azure, Jira, Linear, Fireflies — into a knowledge store, keeps it fresh on a schedule, and runs LLM agents that fix PRs and ship tickets against it.

No SaaS, no remote workspace, no data leaving your laptop. Your credentials, your machine, your APIs — `briar` just shells out to them directly and writes the results to local markdown or Postgres.

```bash
pip install briar-cli
briar version
```

---

## Why briar

- **🔒 Local-first.** Everything runs on your machine against your own API tokens. Nothing is uploaded to a service.
- **🔌 One CLI, every system.** GitHub · Bitbucket · AWS · GCP · Azure · Jira · Linear · Fireflies — behind a single, consistent interface.
- **🧠 Context, not dashboards.** Extraction produces clean markdown knowledge an LLM (or a human) can actually read — PR archaeology, codebase conventions, infra inventory, reviewer profiles, meeting digests.
- **⏰ Cron, replaced.** An in-process scheduler keeps per-company knowledge fresh with one long-running command.
- **🤖 Agents that do the work.** Point an agent at a PR to address review comments, or at a ticket to implement it end-to-end — branch, code, open a draft PR.
- **🗺️ Plans from your board.** Turn a Jira/GitHub Projects board into an ordered, LLM-synthesised implementation plan and run it card by card.

---

## Quickstart

```bash
pip install briar-cli

# 1. Authenticate the providers you'll use (tokens land in ~/.config/briar/secrets.env)
briar auth login github-pat --company acme
briar auth login jira-token --company acme
export ANTHROPIC_API_KEY=sk-ant-...        # LLM key comes from the environment

# 2. Mine a repo's PR history into a knowledge blob
briar extract --company acme \
    --include pr-archaeology \
    --pr-repo acme-co/acme-app --pr-max 50

# 3. Read it back
briar context get knowledge:acme
```

> **Telemetry:** `briar` ships with opt-out error/usage analytics (Sentry). No prompts, file contents, ticket keys, repo names, paths, or secret values ever leave the machine. Turn it off any time with `briar telemetry off`, `BRIAR_TELEMETRY=off`, or `DO_NOT_TRACK=1`.

---

## What you can do

### `briar extract` — mine live state into knowledge

```bash
# PRs + AWS infra in one shot, filtered to your team
briar extract --company acme \
    --include pr-archaeology --include aws-infra \
    --pr-repo acme-co/acme-app \
    --pr-authors-allow alice --pr-authors-allow bob \
    --aws-extract-region us-east-1 \
    --aws-extract-service ecs --aws-extract-service rds

# Last 14 days of Fireflies meeting summaries for an attendee list
FIREFLIES_ACME_API_KEY=ff_xxx briar extract --company acme \
    --include meeting-digest --meeting-since-days 14 \
    --meeting-attendee-allow alice@acme.com
```

### `briar runbook serve` — scheduled extraction, in-process

Describe every company + task in one YAML and let `briar` run the schedule forever — no cron, no external job runner.

```bash
briar runbook serve runbooks/
```

### `briar agent` — autonomous LLM flows

```bash
# prfix: read a PR's open review comments, push fixes, reply inline
briar agent prfix \
    --company acme --owner acme-co --repo acme-app \
    --pr 42 --branch fix-typo \
    --runbook runbooks/acme.yaml

# implement: take a ticket end-to-end — clone, branch, code, open a draft PR
briar agent implement \
    --company acme --owner acme-co --repo acme-app \
    --ticket-project ACME --ticket-key ACME-42 --tracker jira \
    --runbook runbooks/acme.yaml

# Preview the exact prompt + tools without spending a token
briar agent prfix --company acme --owner acme-co --repo acme-app \
    --pr 42 --branch fix-typo --dry-run
```

### `briar plan` — LLM-driven implementation plans from a board

```bash
# Build an ordered plan from a GitHub Projects board, with company knowledge spliced in
briar plan build https://github.com/orgs/acme/projects/1 \
    --name acme-q3 --company acme --llm anthropic --with-knowledge

# Run the loop: the selector picks the next card, the engineer agent ships it,
# the knowledge store learns what changed — card by card.
briar plan run acme-q3 \
    --company acme --owner acme-co --repo acme-app \
    --tracker github-issues --llm anthropic

# Smoke one card with --dry-run before letting the loop go wide
briar plan run acme-q3 --limit 1 --dry-run --llm anthropic \
    --company acme --owner acme-co --repo acme-app
```

### `briar scaffold` — JSON config bundles for downstream tools

```bash
briar scaffold implementation \
    --prefix acme-impl --source github \
    --owner acme --repo widgets
```

Plus `briar context` (local knowledge blobs), `briar dashboard` (read-only HTML status page), `briar secrets doctor` (credential coverage), and `briar journal` (decision-journal inspection).

---

## Install options

```bash
pip install briar-cli                # base: GitHub/Bitbucket/AWS, Jira/Linear, Anthropic + Bedrock, file + Postgres stores
pip install 'briar-cli[openai]'      # OpenAI LLM
pip install 'briar-cli[gemini]'      # Google Gemini LLM
pip install 'briar-cli[gcp]'         # GCP cloud provider
pip install 'briar-cli[azure]'       # Azure cloud provider
pip install 'briar-cli[vault]'       # HashiCorp Vault credential store
pip install 'briar-cli[infisical]'   # Infisical credential bootstrap
pip install 'briar-cli[all]'         # everything
```

Each adapter fails loudly with the right install command if its SDK is missing. **Python 3.10+** (tested through 3.12).

---

## Documentation

Full command reference, every flag, runbook-YAML schema, configuration, and recipes:

**📖 [usebriar.com/docs](https://usebriar.com/docs)**

A comprehensive multi-company runbook lives in [`examples/all_features.yaml`](examples/all_features.yaml).

---

## License

See [LICENSE](LICENSE).
