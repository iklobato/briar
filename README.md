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
    --repo acme-co/acme-app --max 50

# 3. Read it back
briar context get knowledge:acme
```

> **Telemetry:** `briar` ships with opt-out error/usage analytics (Sentry). No prompts, file contents, ticket keys, repo names, paths, or secret values ever leave the machine. Turn it off any time with `briar telemetry off`, `BRIAR_TELEMETRY=off`, or `DO_NOT_TRACK=1`.

### Less typing: shared flags, project config, inference

briar resolves every flag through one chain — **CLI flag > env var > project config > built-in default** — so the stable values move off the command line:

```toml
# .briar.toml (or [tool.briar] in pyproject.toml), searched upward from cwd
company = "acme"
store   = "postgres"

[repo]
owner = "acme-co"
repo  = "acme-app"
```

With that file present, and inside the git checkout, the same extract is just:

```bash
briar extract --include pr-archaeology        # company + repo come from config/git
```

- **Canonical extractor flags.** One shared knob per concept — `--repo`, `--since-days`, `--max`, `--top-n`, `--sample`, `--authors-allow/-block`, `--assignees-allow/-block` — applies to every extractor selected with `--include`. The old per-extractor flags (`--pr-repo`, `--risk-since-days`, …) still work but are hidden from `-h`; run `briar extract --advanced-help` to see them.
- **Inference.** `--owner`/`--repo` are read from the git `origin` remote when neither the flag nor config supplies them.
- A per-extractor override always wins over the shared flag when both are given.

Helpers for the config + setup loop:

```bash
briar init                 # write a starter .briar.toml (repo inferred from git)
briar config show          # see each setting's resolved value AND its source
briar doctor               # check config, git, credentials, store (CI-usable exit code)
eval "$(briar completion bash)"   # tab-completion (also: zsh)
```

Other niceties: `briar --version`, quiet-by-default logs (logs to stderr, `--verbose` for DEBUG), and a once-a-day "new version available" nudge (opt out with `BRIAR_NO_UPDATE_CHECK=1` / `DO_NOT_TRACK=1`).

---

## What you can do

### `briar extract` — mine live state into knowledge

```bash
# PRs + AWS infra in one shot, filtered to your team
briar extract --company acme \
    --include pr-archaeology --include aws-infra \
    --repo acme-co/acme-app \
    --authors-allow alice --authors-allow bob \
    --aws-extract-region us-east-1 \
    --aws-extract-service ecs --aws-extract-service rds

# Account-wide inventory: every tagged AWS resource across all services
briar extract --company acme --include aws-infra \
    --aws-extract-service tagging-inventory

# Last 14 days of Fireflies meeting summaries for an attendee list
FIREFLIES_ACME_API_KEY=ff_xxx briar extract --company acme \
    --include meeting-digest --meeting-since-days 14 \
    --meeting-attendee-allow alice@acme.com

# Code-quality signal from git history + the repo-host API.
# One --repo feeds every selected extractor.
briar extract --company acme --repo acme-co/acme-app \
    --include defect-hotspots --include pr-hygiene \
    --include review-nits --include ci-health
```

**Code-quality extractors** (all `--provider github|bitbucket`): `defect-hotspots`
(churn × bug-fix × size risk), `pr-hygiene` (size/rubber-stamp/time-to-review),
`review-nits` (recurring reviewer asks → lint candidates), `revert-signals`,
`commit-message-quality`, `stale-prs`, `ci-health`, `dependency-health`,
`code-scanning`, `repo-governance`, `test-discipline`, `release-cadence`,
`todo-density`. See [`agents/extract.md`](agents/extract.md).

#### Feed the knowledge to Claude Code — on demand

```bash
# Merge a knowledge index into CLAUDE.md; full detail lands in
# .briar/knowledge/<company>.md for the agent to read when relevant.
briar extract --company acme --repo acme-co/acme-app \
    --include defect-hotspots --include ci-health \
    --merge-claude-md
```

`--merge-claude-md` writes the full bundle to `.briar/knowledge/<company>.md`
and splices a short, marker-bounded index — section titles plus a pointer to
that file — into `CLAUDE.md` (override with `--claude-md-path`). Because
`CLAUDE.md` is auto-loaded into every Claude Code session but the detail file
is not, the knowledge stays available **on demand** without paying a
per-session context cost: the agent reads the detail file only when a task
touches one of the listed topics. Re-runs replace just briar's block, leaving
your hand-written `CLAUDE.md` untouched.

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

### Repeatable flags — fan out across repos, projects, services

Most list-style flags accept multiple occurrences: repeat the flag, once
per value (there's no comma form — `--repo a,b` is one repo named `a,b`).
The canonical `--repo` feeds every extractor selected with `--include`.

```bash
# Mine several repos, keep the team's PRs, drop the bots — one --repo,
# every selected extractor. --max applies per repo; author allow/block
# compose as allow ∩ ¬block.
briar extract --company acme \
    --include pr-archaeology --include defect-hotspots \
    --repo acme-co/web --repo acme-co/api --repo acme-co/mobile \
    --max 75 \
    --authors-block "dependabot[bot]" --authors-block "renovate[bot]"

# Scaffold a triage flow from two sources — one shared author/assignee
# filter applies to every --source.
briar scaffold implementation --prefix acme-triage \
    --source github --source jira \
    --owner acme-co --repo acme-app --github-secret-id <uuid> \
    --authors-block "dependabot[bot]" \
    --assignees-allow alice --assignees-allow bob \
    --jira-project ACME --jira-project PLAT --jira-secret-id <uuid> \
    --auth-mode pat
```

The same lists map onto runbook YAML as arrays — e.g. `repo:
[acme-co/web, acme-co/api]` under an extractor's `args:`.

> **Divergent identifiers in one run.** Some extractors key off a tracker
> *project* (`active-tickets`, `ticket-archaeology`) rather than an
> `owner/repo` slug. When you run those alongside repo-based extractors in
> a single invocation and they need *different* values, reach for the
> per-extractor override flags (`--ticket-project`, `--pr-repo`, … — listed
> by `briar extract --advanced-help`), or split into two invocations. The
> runbook YAML models this cleanly: one `args:` block per extractor.

### Handy patterns

```bash
# Script briar: --format json pipes straight into jq (works on every command).
briar plan status acme-q3 --format json | jq -r '.blocked[].key'

# Gate CI on credential coverage — exits non-zero if any runbook is missing creds.
briar secrets doctor --examples runbooks/

# Cost-safe agent rollout: preview for free, then one paid card, then go wide.
briar agent implement --company acme --owner acme-co --repo acme-app \
    --ticket-project ACME --ticket-key ACME-42 --dry-run
briar plan run acme-q3 --company acme --owner acme-co --repo acme-app \
    --tracker jira --llm anthropic --limit 1 --max-iter 20
```

---

## Install options

```bash
pip install briar-cli                # base: GitHub/Bitbucket/AWS, Jira/Linear, Anthropic + Bedrock, file + Postgres stores
pip install 'briar-cli[openai]'      # OpenAI LLM
pip install 'briar-cli[gemini]'      # Google Gemini LLM
pip install 'briar-cli[mcp]'         # MCP-server tools for `briar agent` (runbook `mcp:` block)
pip install 'briar-cli[gcp]'         # GCP cloud provider
pip install 'briar-cli[azure]'       # Azure cloud provider
pip install 'briar-cli[vault]'       # HashiCorp Vault credential store
pip install 'briar-cli[all]'         # everything
```

Each adapter fails loudly with the right install command if its SDK is missing. **Python 3.10+** (tested through 3.12).

---

## Documentation

Full command reference, every flag, runbook-YAML schema, configuration, and recipes:

**📖 [usebriar.com/docs](https://usebriar.com/docs)**

- **End-to-end usage flows** (14 multi-feature recipes — onboard a company, extract AWS + Fireflies + fix a PR, build & run a plan, full lifecycle in one sitting, …): [`agents/flows.md`](agents/flows.md)
- Per-command operator manual: [`agents/`](agents/README.md)
- A comprehensive multi-company runbook lives in [`examples/all_features.yaml`](examples/all_features.yaml).

---

## License

See [LICENSE](LICENSE).
