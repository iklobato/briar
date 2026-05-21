# briar — local extraction + scheduling CLI

Python CLI that mines live state from external systems (GitHub, AWS, …),
schedules per-company / per-task knowledge extraction in-process, and
ships a read-only HTML dashboard so you can watch it run.

```text
$ briar version
briar-cli 1.1.0

$ briar --help
extract   — run extractors against external sources
runbook   — multi-company knowledge extraction; long-lived scheduler
scaffold  — generate JSON config templates (e.g. for downstream tools)
context   — read/write local markdown knowledge blobs
dashboard — serve a read-only HTML dashboard summarising the droplet
version   — print client version
```

**This is a standalone client tool.** There is no `api.usebriar.com`
service, no `app.usebriar.com` web app, no login, no profiles, no
remote workspace. Every command runs against local files + the
external APIs (GitHub, AWS) that the extractors talk to directly.

---

## What it does

- **Five extractors** that mine live state into a per-company markdown
  knowledge blob: PR archaeology, AWS infra, active work, GitHub
  deployments, codebase conventions.
- **In-process scheduler** (`briar runbook serve`) that runs each
  `(company, task)` pair on its own cron-equivalent schedule, using
  the [`schedule`](https://schedule.readthedocs.io/) library — no
  system cron, no separate scheduler binary.
- **Scaffold templates** that emit JSON config bundles (implementation,
  pr-fixes) for human consumption — each agent persona declares which
  extractor outputs it consumes so the prompts are extractor-aware.
- **Read-only HTML dashboard** with 22 sections: at-a-glance system
  tiles, per-task schedule + next-fire, knowledge-file inventory,
  connectivity probes, plugin registries, recent activity log tail,
  Chart.js visualisations.
- **File-backed knowledge store** — markdown blobs keyed by
  `category:identifier`, suitable for `cat`, `grep`, `diff`.

---

## Install

```bash
make venv                     # creates .venv/ and runs `pip install -e .`
source .venv/bin/activate
briar version
```

Requires **Python 3.10+**. Runtime deps in `pyproject.toml`: `httpx`,
`pydantic>=2`, `PyYAML`, `rich`, `jinja2`, `schedule`, `pytz`. `boto3`
is lazy-imported — installs but only loaded when you run
`extract --include aws-infra`.

Dev extras (`pip install -e ".[dev]"`): `black`, `mypy`.

---

## Commands

```
extract    — run extractors against external sources (GitHub, AWS)
runbook    — multi-company orchestration; long-lived scheduler
scaffold   — generate JSON config bundles
context    — read/write local markdown blobs
dashboard  — serve the read-only HTML dashboard
version    — print client version
```

Global flags:
- `--format {table,json,yaml,csv,quiet}` — output formatter (default: table)
- `--verbose` / `-v` — DEBUG-level logging (also `BRIAR_VERBOSE=1`)

Set `BRIAR_LIB_DEBUG=1` to additionally surface noisy third-party
loggers (httpx, boto3, …) — useful when debugging wire traffic.

---

## Knowledge extractors

| `--include` name | What it mines | Auth |
|---|---|---|
| `pr-archaeology` | merged-PR patterns, median time-to-merge, top reviewers | `gh auth token` or `$GITHUB_TOKEN` |
| `aws-infra` | ECS, RDS, Lambda, SQS, CloudWatch (top 10 log groups by size) | local AWS profile or per-company env-var credentials |
| `active-work` | open PRs across configured repos | GitHub PAT |
| `github-deployments` | environments, recent deployments, CI runs | GitHub PAT |
| `codebase-conventions` | per-repo language / test runner / linter / migration tool | GitHub PAT |

One-shot run:

```bash
briar extract --company acme \
    --include pr-archaeology --include active-work \
    --pr-repo iklobato/lightapi --pr-max 100 \
    --active-repo iklobato/lightapi \
    --root ./knowledge
```

Author/assignee filters: `--pr-authors-allow`, `--pr-authors-block`,
`--pr-assignees-allow`, `--pr-assignees-block` (and `--active-*`
equivalents). Composition: `allow ∩ ¬block`.

### Per-company env-var credentials

`briar.env_vars.CredEnv` translates `(extractor, company)` into env
var names (`{c}` = company name uppercased, hyphens → underscores):

| Template | Used by |
|---|---|
| `AWS_{c}_ACCESS_KEY_ID` / `SECRET_ACCESS_KEY` / `SESSION_TOKEN` | `aws-infra` (per-company AWS credentials) |
| `GITHUB_TOKEN` | every GitHub extractor (workspace-wide PAT) |
| `JIRA_{c}_EMAIL` / `_TOKEN` | reserved for a future Jira extractor |
| `BITBUCKET_{c}_USERNAME` / `_APP_PASSWORD` / `_WORKSPACE` | reserved for a future Bitbucket extractor |

`aws-infra` falls back to the local `~/.aws/credentials` profile when
env vars are unset. The droplet runs purely off env vars.

#### Multi-company example

Three companies showcasing the patterns side-by-side:

- `widget-co` — GitHub + AWS
- `acme` — GitHub + AWS + Jira
- `acme` — Bitbucket + AWS

`/etc/briar/secrets.env`:

```bash
# ─── workspace-wide (no {c} substitution) ───────────────────────────
GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-xxxxxxxxxxxxxxxxxxxxxxxx   # only if running `briar agent`
BRIAR_DATABASE_URL=postgresql://briar_kb:xxx@db:5432/briar?sslmode=require  # optional; file backend otherwise

# ─── widget-co: GitHub + AWS ───────────────────────────────────
AWS_WIDGET_CO_ACCESS_KEY_ID=ASIAEXAMPLEAAAAAAAAA
AWS_WIDGET_CO_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
AWS_WIDGET_CO_SESSION_TOKEN=IQoJb3JpZ2luX2VjEJr////...truncated
AWS_WIDGET_CO_REGION=us-east-1

# ─── acme: GitHub + AWS (different region) + Jira ─────────────────
AWS_ACME_ACCESS_KEY_ID=ASIAEXAMPLEBBBBBBBBB
AWS_ACME_SECRET_ACCESS_KEY=Ke7MDENG/bPxRfiCYEXAMPLEKEYxxxxxxxxxx
AWS_ACME_SESSION_TOKEN=IQoJb3JpZ2luX2VjEK4////...truncated
AWS_ACME_REGION=us-east-2
JIRA_ACME_EMAIL=ops@acme.example
JIRA_ACME_TOKEN=ATATT3xFfGN0xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# ─── acme: Bitbucket + AWS ──────────────────────────────────────────
BITBUCKET_ACME_USERNAME=acme-machine-user
BITBUCKET_ACME_APP_PASSWORD=ATBBxxxxxxxxxxxxxxxxxxxxxxxxxxxx
BITBUCKET_ACME_WORKSPACE=acme
AWS_ACME_ACCESS_KEY_ID=ASIAEXAMPLECCCCCCCCC
AWS_ACME_SECRET_ACCESS_KEY=fiCYEXAMPLEKEYxxxxxxxxxxxxxxxxxxxxxx
AWS_ACME_SESSION_TOKEN=IQoJb3JpZ2luX2VjEPj////...truncated
AWS_ACME_REGION=eu-west-1
```

**How names resolve** (`CredEnv.{template}.for_company({company})`):

| Call | Resolves to |
|---|---|
| `CredEnv.AWS_KEY_ID.for_company("widget-co")` | `AWS_WIDGET_CO_ACCESS_KEY_ID` |
| `CredEnv.JIRA_TOKEN.for_company("acme")` | `JIRA_ACME_TOKEN` |
| `CredEnv.BITBUCKET_APP_PASSWORD.for_company("acme")` | `BITBUCKET_ACME_APP_PASSWORD` |
| `CredEnv.GITHUB_TOKEN.value` | `GITHUB_TOKEN` (no `{c}`) |

**The runbook YAML uses the company name verbatim** — the executor
passes it to `CredEnv.for_company()` when each extractor asks for its
credentials. So `companies.acme.schedules[].extract[].aws-infra`
runs against `AWS_ACME_*`, `companies.acme` against `AWS_ACME_*` +
`BITBUCKET_ACME_*`, etc. See `## Runbook YAML` below for the full
schedule structure.

**Verify a company's credential surface** without leaking values:

```bash
sudo -u briar bash -c '
  set -a; source /etc/briar/secrets.env; set +a
  for c in WIDGET_CO ACME ACME; do
    echo "=== $c ==="
    env | grep -E "^(AWS|JIRA|BITBUCKET)_${c}_" | sed "s/=.*/=<set>/" | sort
  done
  echo "=== workspace ==="
  env | grep -E "^(GITHUB_TOKEN|CLAUDE_CODE_OAUTH_TOKEN|BRIAR_DATABASE_URL)=" \
      | sed "s/=.*/=<set>/"
'
```

Expected output:

```
=== WIDGET_CO ===
AWS_WIDGET_CO_ACCESS_KEY_ID=<set>
AWS_WIDGET_CO_REGION=<set>
AWS_WIDGET_CO_SECRET_ACCESS_KEY=<set>
AWS_WIDGET_CO_SESSION_TOKEN=<set>
=== ACME ===
AWS_ACME_ACCESS_KEY_ID=<set>
AWS_ACME_REGION=<set>
AWS_ACME_SECRET_ACCESS_KEY=<set>
AWS_ACME_SESSION_TOKEN=<set>
JIRA_ACME_EMAIL=<set>
JIRA_ACME_TOKEN=<set>
=== ACME ===
AWS_ACME_ACCESS_KEY_ID=<set>
AWS_ACME_REGION=<set>
AWS_ACME_SECRET_ACCESS_KEY=<set>
AWS_ACME_SESSION_TOKEN=<set>
BITBUCKET_ACME_APP_PASSWORD=<set>
BITBUCKET_ACME_USERNAME=<set>
BITBUCKET_ACME_WORKSPACE=<set>
=== workspace ===
BRIAR_DATABASE_URL=<set>
CLAUDE_CODE_OAUTH_TOKEN=<set>
GITHUB_TOKEN=<set>
```

A `<set>` missing for a company an extractor is configured against
shows up at scheduler run time as an empty section in
`./knowledge/<company>.md` (the extractor's `is_available()` returns
False; the executor logs `extractor-skip: is_available() returned
False — likely missing credentials`). Catching it via this one-liner
beats catching it via a 4 AM empty extract.

---

## The provider ABCs — vendor-neutral by construction

Briar ships **five** Strategy + Registry families that abstract over
vendors. All follow the same shape (ABC + concrete adapters +
registry + factory function) so adding a new vendor never edits a
caller:

| Family | ABC | Adapters that ship | Where consumed |
|---|---|---|---|
| **Repository** | `RepositoryProvider` | GitHub (full), Bitbucket Cloud (full) | `pr-archaeology`, `active-work`, `github-deployments`, `codebase-conventions` |
| **Tracker** | `TrackerProvider` | Jira (full), GitHub Issues (full), Bitbucket Issues (full), Linear (stub) | `active-tickets`, `ticket-archaeology` |
| **Cloud** | `CloudProvider` | AWS (full, via existing `aws_services/`), GCP (stub), Azure (stub) | `aws-infra` (provider-agnostic now, kept the legacy name) |
| **LLM** | `LLMProvider` | Anthropic (full, OAuth + API key), OpenAI (stub), Gemini (stub), Bedrock (stub) | `briar agent` runner |
| **Notification** | `NotificationSink` | Telegram (full), Slack (stub), Email (stub), PagerDuty (stub) | future: scheduler failure alerts |
| **Credentials** | `CredentialStore` | EnvFile (full), AWS Secrets Manager (stub), SSM (stub), Vault (stub) | `briar secrets doctor` |

The full adapters work today. The stubs implement `is_available()`
honestly + raise `NotImplementedError` on data verbs with the exact
SDK call signature embedded in the message — so the next reader
knows what to wire up without guessing.

### Adding a new vendor anywhere

One module + one entry in the registry tuple. Zero edits to extractors,
the runner, the executor, or commands. Example for a hypothetical
GitLab repo provider:

```
src/briar/extract/_providers/gitlab.py     # new file: GitlabProvider(RepositoryProvider)
src/briar/extract/_providers/__init__.py   # tuple += (GitlabProvider,)
```

That's the whole change. The substring `tool_filter` on archetypes
(`"commit"`, `"open_pr"`, `"comment_on_issue"`) keeps working as long
as the scaffold-side `Source<Vendor>.build_tools` follows the
`<vendor>.<verb>` naming convention.

---

## Repository providers — one ABC, one runtime, every vendor

Every layer that needs to talk to a code host goes through the same
abstraction now: `RepositoryProvider` (in `extract/_provider.py`).
Each concrete provider lives in `extract/_providers/` and self-registers.
Extractors call provider verbs (`list_pulls`, `read_file`,
`list_environments`, `list_deployments`, `list_ci_runs`); the provider
adapts those onto its vendor's SDK.

| Layer | GitHub | Bitbucket | Pattern |
|---|---|---|---|
| **Scaffold source** (`iac/scaffold/sources/`) | `SourceGithub` | `SourceBitbucket` | Strategy + Registry. |
| **Scaffold trigger** (`iac/scaffold/triggers/`) | `TriggerGithubWebhook` | `TriggerBitbucketWebhook` | Strategy + Registry. |
| **CredEnv** (`env_vars.py`) | `GITHUB_TOKEN` (workspace-wide) | `BITBUCKET_{c}_USERNAME` + `_APP_PASSWORD` + `_WORKSPACE` (per-company) | Bitbucket Cloud app passwords are user/workspace-scoped, so they fit the per-tenant `{c}` pattern. |
| **Runtime extractor provider** (`extract/_providers/`) | `GithubProvider` (PyGithub) | `BitbucketProvider` (`atlassian-python-api` Cloud client) | Strategy + Registry behind `RepositoryProvider` ABC. |

### How extractors stay provider-agnostic

Every PR-aware extractor (`pr-archaeology`, `active-work`,
`github-deployments`, `codebase-conventions`) inherits from
`RepoBackedExtractor` (in `extract/base.py`). That base class:

1. Registers a shared `--provider` argparse flag with `choices` driven
   by `RepositoryProviderRegistry.kinds()`.
2. Exposes `self._provider(args)` which reads `args.provider` +
   `args.company` and hands back a configured `RepositoryProvider`.

The runbook executor injects `args.company = <company_name>` before
each extractor runs, so per-tenant creds (`BITBUCKET_<COMPANY>_*`)
resolve correctly. GitHub treats `company` as inert; Bitbucket uses
it as the env-var prefix.

```yaml
# examples/acme.yaml — same extractors, different provider
companies:
  acme:
    schedules:
      - task: extractors
        every: "day at 05:17"
        extract:
          - name: pr-archaeology
            args:
              provider: bitbucket                    # ← routes onto BitbucketProvider
              pr_repo: [acme/widgets]                # workspace/repo
              pr_max: 30
          - name: codebase-conventions
            args:
              provider: bitbucket
              conventions_repo: [acme/widgets]
```

### Adding a new vendor

Adding GitLab / Forgejo / SourceHut / … is one file + one entry:

1. Implement `RepositoryProvider` in `extract/_providers/<vendor>.py`
   — five verbs minimum (`is_available`, `list_pulls`, `read_file`
   plus the three optional `list_environments` / `list_deployments`
   / `list_ci_runs`).
2. Add `<VendorProvider>` to the tuple in
   `extract/_providers/__init__.py`'s `PROVIDERS` dict.

Zero edits to any extractor. Zero edits to the executor. The
substring `tool_filter` on archetypes (`"commit"`, `"open_pr"`,
`"comment_on_issue"`) keeps working as long as the scaffold-side
`Source<Vendor>.build_tools` follows the same `<vendor>.<verb>`
naming convention.

### Normalised data shapes

The dataclasses in `_provider.py` are the contract every provider
must populate. Each vendor's adapter translates its native JSON into
these:

| Dataclass | Fields | GitHub source | Bitbucket source |
|---|---|---|---|
| `PullRequest` | `number, title, author, is_draft, head_ref, base_ref, review_comment_count, created_at, merged_at, requested_reviewers` | PyGithub PR dict | `atlassian.bitbucket.cloud` typed PR object |
| `Environment` | `name, protection_rule_count, url` | `/repos/{repo}/environments` | `Repository.deployment_environments` |
| `Deployment` | `id, environment, sha, creator, created_at` | `/repos/{repo}/deployments` | `/2.0/repositories/.../deployments` |
| `CiRun` | `name, status, conclusion, head_branch, created_at` | `/repos/{repo}/actions/runs` | `Repository.pipelines` |

---

## Runbook YAML — multi-company, per-task schedules

```yaml
# examples/acme.yaml
version: 1
companies:
  acme:
    knowledge:
      store: file
      name: ./knowledge/acme.md

    schedules:
      - task: extractors                # heavy, slow
        every: "day at 03:17"
        extract:
          - name: pr-archaeology
            args: {pr_repo: [acme-co/acme-app], pr_max: 30}
          - name: aws-infra
            # No aws_extract_profile — the company key "acme" already
            # drives AWS_ACME_* via CredEnv.for_company().
            args: {aws_extract_region: us-east-2,
                   aws_extract_service: [ecs, lambda, logs, rds, sqs]}

      - task: implementation            # repo-shape
        every: "4 hours"
        extract:
          - name: codebase-conventions
            args: {conventions_repo: [acme-co/acme-app]}
          - name: github-deployments
            args: {deploy_repo: [acme-co/acme-app]}

      - task: prfix                     # hot
        every: "hour"
        extract:
          - name: active-work
            args: {active_repo: [acme-co/acme-app]}
```

**`every:` DSL** (parsed by `EveryParser` into a `schedule.Job`):

- `"minute"`, `"N minutes"`
- `"hour"`, `"N hours"`, `"hour at :MM"`
- `"day"`, `"N days"`, `"day at HH:MM"`
- `"monday at HH:MM"` (and every other weekday)

### Commands

```bash
# one-shot — run every schedule once and exit
briar runbook extract examples/acme.yaml

# one-shot — run only one task
briar runbook extract examples/acme.yaml --task prfix

# one-shot — every YAML in a directory
briar runbook sweep examples/

# long-lived scheduler — registers all (company, task) jobs and runs forever
briar runbook serve examples/
```

`serve` is what runs persistently on the droplet (see "Deployment").
Each scheduled job invokes the equivalent of
`briar runbook extract <yaml> --task <name>` at the configured
cadence.

---

## Scaffold — JSON for downstream tools

`briar scaffold` emits a JSON bundle describing the agent / workflow /
sources / tools / trigger you want. The CLI does not POST it anywhere
— consumers paste it into whatever downstream system needs the shape.

```bash
# GitHub repository, OAuth, webhook-driven plan→approve flow
briar scaffold implementation \
    --prefix acme-impl \
    --source github \
    --owner iklobato --repo lightapi \
    --auth-mode pat --github-secret-id <secret-uuid> \
    --shape plan-approve-act --archetype engineer \
    --trigger-kind github_webhook \
    --out acme-impl.json

# Bitbucket repository, app-password auth, Bitbucket webhook
briar scaffold implementation \
    --prefix acme-impl \
    --source bitbucket \
    --bitbucket-workspace acme --bitbucket-repo widgets \
    --auth-mode pat --bitbucket-secret-id <secret-uuid> \
    --shape plan-approve-act --archetype engineer \
    --trigger-kind bitbucket_webhook \
    --out acme-impl.json
```

**Identity flags belong to the source, not the scaffold.** GitHub uses
`--owner` / `--repo`; Bitbucket uses `--bitbucket-workspace` /
`--bitbucket-repo`; Jira uses `--jira-project`. The scaffold template
itself is provider-agnostic — it asks each selected source for its
`target()` identifier (`SourceTemplate.target` on the ABC) and uses
the first non-empty answer.

### Templates

| Template | Shape |
|---|---|
| `implementation` | source → agent → workflow(`plan → human_checkpoint → implement / comment`) → trigger |
| `pr-fixes` | source → agent → one-shot workflow → trigger (no human gate) |

### Composable plugin axes

| Axis | Flag | Built-in kinds |
|---|---|---|
| Sources | `--source <kind>` (repeatable) | `github`, `bitbucket`, `jira`, `aws` |
| Trigger | `--trigger-kind <kind>` | `github_webhook`, `bitbucket_webhook`, `schedule_cron`, `manual` |
| Workflow shape | `--shape <name>` | `plan-approve-act`, `one-shot`, `triage` |
| Agent archetype | `--archetype <name>` | `engineer`, `pr-fixer`, `triager` |

### Archetype `consumes` — extractor-aware prompts

Each archetype declares which extractor outputs it reads, in order.
The generated `system_prompt` and node prompts reference these by
name so the agent knows which knowledge sections drive each decision.

| Archetype | Consumes (read order) | Tool filter |
|---|---|---|
| `engineer` | `codebase-conventions → active-work → pr-archaeology → github-deployments → aws-infra` | every tool |
| `pr-fixer` | `active-work → pr-archaeology → codebase-conventions` | `commit`, `comment_on_issue`, `open_pr` |
| `triager` | `codebase-conventions → github-deployments → pr-archaeology → active-work` | `comment_on_issue`, `add_labels`, `comment` |

Adding a new kind in any axis = one file in the relevant folder under
`src/briar/iac/scaffold/` + one entry in the registry. No edits
elsewhere.

---

## Dashboard

```bash
briar dashboard --host 0.0.0.0 --port 8080 \
    --examples ./examples --knowledge ./knowledge --repo-path .
```

Read-only Jinja-rendered HTML page with 22 sections covering deploy
state, schedules, knowledge files, plugin registries, system stats,
connectivity probes, secrets inventory (names + lengths only — never
values), and a tailing log panel. Chart.js for the disk/memory/load
visualisations + per-cycle stacked bars.

GET-only by construction — POST/PUT/DELETE return 501. Safe to expose
publicly behind a basic firewall (see "Deployment").

---

## `briar context` — local markdown CRUD

The same store the extractors write to is also exposed as a CRUD
surface for arbitrary markdown blobs:

```bash
briar context put knowledge:acme --from-file knowledge/acme.md
briar context put memory:reviewer-iklobato --content "Focuses on typing rigor"
briar context put lessons:python-typing --content - < lessons/typing.md
briar context list
briar context list --prefix lessons:
briar context categories
briar context get knowledge:acme
briar context delete memory:stale --yes
```

Blob names use the `category:identifier` convention; the store maps
each blob to `./knowledge/<category>/<identifier>.md`.

---

## Deployment — DigitalOcean droplet

The reference deployment is a single $4/mo DO droplet running two
long-lived `briar` processes (scheduler + dashboard). Source-of-truth
is the private GitHub repo `iklobato/briar-cli`.

### One-line deploy

```bash
git push && ssh root@<droplet> \
    'cd /opt/briar-scheduler && git pull --ff-only && .venv/bin/pip install -e . --quiet'
```

### Droplet layout

| Path | Purpose |
|---|---|
| `/opt/briar-scheduler/` | git clone of `iklobato/briar-cli`, `git status` clean |
| `/opt/briar-scheduler/.venv/` | venv (gitignored, survives `git pull`) |
| `/opt/briar-scheduler/examples/` | runbook YAMLs (one per company) |
| `/etc/briar/secrets.env` | mode 600, root-owned. `GITHUB_TOKEN` + per-company `AWS_*_KEY_ID` / `SECRET` / `SESSION` |
| `/var/log/briar/scheduler.log` | `briar runbook serve` log (append-only) |
| `/var/log/briar/dashboard.log` | `briar dashboard` log |

### Persistent processes

```bash
# scheduler: registers every (company, task) and runs the schedule loop
PYTHONUNBUFFERED=1 nohup .venv/bin/briar runbook serve examples/ \
    > /var/log/briar/scheduler.log 2>&1 < /dev/null &

# dashboard: serves the read-only HTML page on port 8080
PYTHONUNBUFFERED=1 nohup .venv/bin/briar dashboard \
    --host 0.0.0.0 --port 8080 \
    --examples examples --knowledge knowledge --repo-path . \
    > /var/log/briar/dashboard.log 2>&1 < /dev/null &
```

(`PYTHONUNBUFFERED=1` ensures `nohup`'d Python flushes log lines
without buffering. A systemd unit would normally take care of this —
see `Caveats`.)

### Firewall

DO cloud firewall: inbound TCP/22 from operator IPs only; TCP/8080
from `0.0.0.0/0` for the dashboard; outbound all (so the scheduler
can reach GitHub + AWS).

### Refreshing secrets

`secrets.env` holds short-lived AWS STS triplets (the SSO-vended ones
expire on the local SSO session timeout). When the session ages out,
re-push from your laptop. See `#### Multi-company example` above for
the full structure of `secrets.env` — this one-liner only rotates the
AWS STS triplets + `GITHUB_TOKEN`; static creds (Bitbucket app
passwords, Jira tokens) carry across.

```bash
# Edit the company list to match the runbooks you actually have.
COMPANIES="widget-co acme acme"

{ for c in $COMPANIES; do
    aws configure export-credentials --profile $c --format env-no-export 2>/dev/null \
      | grep -E '^AWS_(ACCESS_KEY_ID|SECRET_ACCESS_KEY|SESSION_TOKEN)=' \
      | sed -E "s/^AWS_/AWS_${c^^}_/; s/-/_/g"
  done
  echo "GITHUB_TOKEN=$(gh auth token)"
} | ssh root@<droplet> \
    'cat > /etc/briar/secrets.env && chmod 600 /etc/briar/secrets.env'
```

### Rollback

```bash
ssh root@<droplet> 'cd /opt/briar-scheduler && git log --oneline -5'
ssh root@<droplet> 'cd /opt/briar-scheduler && git reset --hard <previous-sha>'
```

### Caveats

- The two long-lived processes are nohup-detached, not under systemd.
  A reboot loses them. Adding a systemd unit is a 20-line change if
  you need that.

---

## Logging

Stdlib `logging` everywhere. Default level is INFO. Format:

```
2026-05-20T16:15:12Z [INFO   ] briar.iac.runbook.scheduler: fire task=prfix company=lightapi yaml=lightapi-e2e.yaml
```

Every broad-except site in the codebase calls `log.exception(...)` so
unforeseen errors print full tracebacks to the log without crashing
the scheduler or 500-ing the dashboard.

- `--verbose` (or `BRIAR_VERBOSE=1`) → DEBUG-level briar logs.
- `BRIAR_LIB_DEBUG=1` → DEBUG on httpx / httpcore / boto3 / schedule.

---

## Glossary

Terms used throughout this README, the code, and the per-company
markdown blobs. Grouped by subsystem; alphabetised inside each group.

### Storage + content

- **Blob.** One unit stored in a `KnowledgeStore`. Markdown content keyed
  by a string `name` (e.g. `knowledge:acme`). Blobs are the only thing
  the store knows about; structure inside the markdown is convention.
- **Blob name.** `<category>:<identifier>` by convention
  (`knowledge:acme`, `memory:reviewer-iklobato`, `lessons:python-typing`).
  The store treats the whole name as an opaque key; the colon-prefix is
  used purely for grouping in `list()`/dashboard/file layout.
- **Category.** Everything before the first `:` in a blob name. The
  Postgres backend stores it as a column for indexing; the file backend
  uses it as the parent directory name.
- **Fingerprint.** Hex MD5 of a blob's content. `KnowledgeStore.fingerprint()`
  returns the stored blob's md5 — server-side on Postgres, local hash on
  file. Used by `put_if_changed` to skip no-op writes.
- **KnowledgeBinding.** Per-company "where do my blobs live" record:
  `store` (`file` | `postgres`), `name` (blob-name template), and optional
  `root` (file backend only). Parsed from `companies.<x>.knowledge:` in
  the runbook YAML.
- **KnowledgeRef.** Metadata-only handle returned by `KnowledgeStore.list()`:
  name, category, byte count, updated-at, extras. Does NOT carry the
  content — callers re-`get()` if they need bytes.
- **KnowledgeStore.** The four-verb (`put`/`get`/`list`/`delete`) blob-store
  contract in `storage/base.py`. Two implementations: `StoreFile` (one
  markdown file per blob on disk) and `StorePostgres` (two tables with an
  append-only history). Every part of the system reads/writes via this
  one interface.
- **`put_if_changed`.** The write call extractors actually make. Compares
  the new content's md5 against the stored blob's; only writes when they
  differ. Returns a `PutIfChangedResult` (`wrote: bool`, `byte_count`,
  `new_hash`, `prev_hash`). On Postgres it's a single-connection atomic
  compare-and-set; on file it's a two-step read-then-write.

### Extraction

- **Composer.** `KnowledgeComposer.markdown(company, sections)` — turns
  a list of `ExtractedSection` objects into the final per-company
  markdown blob (with timestamp header, `## <heading>` per section,
  nested subsections). Also has a `.json()` form for programmatic
  consumers.
- **Company.** A tenant. The top-level key under `companies:` in a
  runbook YAML (`acme`, `widget-co`, …). Drives credential
  lookup (`AWS_<COMPANY>_*` env vars), blob naming
  (`knowledge:<company>`), and the dashboard's per-company grouping.
- **Every-DSL.** The cadence syntax in runbook YAML's `every:` field:
  `"minute"`, `"N minutes"`, `"hour"`, `"hour at :MM"`, `"day at HH:MM"`,
  `"monday at HH:MM"`. Parsed by `EveryParser` into a `schedule.Job`.
- **ExtractedSection.** One result-fragment returned by an extractor's
  `.extract(args)`: `title`, `body` (markdown), structured `data` (for
  the JSON form), and nested `subsections`. The "no data" sentinel is
  `ExtractedSection(title="")` (`EMPTY_SECTION`) — the composer skips
  empty sections instead of needing `Optional`.
- **Extractor.** A `KnowledgeExtractor` subclass that mines one source
  family (GitHub, AWS, local checkout) and returns one
  `ExtractedSection`. Doesn't know the store exists; the runbook
  executor handles persistence. Code-host-aware extractors inherit
  from `RepoBackedExtractor` so they pick up the `--provider` flag
  and `_provider(args)` helper for free.
- **Knowledge file / knowledge blob.** The composed per-company markdown
  bundle: `knowledge:<company>` (or `knowledge:<company>.<task>` for
  non-default tasks). What agents read.
- **RepoBackedExtractor.** Base class in `extract/base.py` for
  extractors that need a code host. Registers `--provider` (choices
  pulled from `RepositoryProviderRegistry.kinds()`) and exposes
  `self._provider(args)` so subclasses never construct a vendor
  client directly.
- **RepositoryProvider.** Vendor-neutral facade extractors call
  instead of GitHub / Bitbucket / GitLab APIs directly
  (`extract/_provider.py`). Five verbs: `is_available`, `list_pulls`,
  `read_file`, `list_environments`, `list_deployments`,
  `list_ci_runs`. Returns the dataclasses (`PullRequest`,
  `Environment`, `Deployment`, `CiRun`) so the extractor never sees
  vendor-specific field names. Strategy + Registry behind
  `_providers/`; built by `make_provider(kind, company)`.
- **Runbook.** A YAML file describing one or more companies and their
  per-task schedules. Validated by Pydantic via `RunbookFile.model_validate`.
  One file per company by convention (`examples/acme.yaml`,
  `examples/widgets.yaml`).
- **Schedule (entry).** One `(task, every, extract)` triple inside a
  company. Distinct tasks for the same company can run on different
  cadences — common pattern is `extractors: day at 03:17` for the
  heavy AWS run, `prfix: hour` for the hot GitHub poll.
- **Task.** Name of one schedule within a company (`extractors`,
  `implementation`, `prfix`). Used to filter `briar runbook extract --task`
  and to suffix the blob name for non-default tasks.

### Scaffold (agent + workflow generator)

- **Archetype.** A `AgentArchetype` subclass — agent persona definition:
  `role`, `goal`, `backstory_template`, `max_iter`, `tool_filter`,
  `consumes`. Five shipped: `engineer`, `pr-fixer`, `pr-ci-fixer`,
  `pr-conflict-resolver`, `triager`. The archetype is what `--archetype`
  picks on the `briar scaffold` CLI.
- **Backstory template.** The persona prose an archetype declares; gets
  rendered by `build_persona(target)` with `{target}` interpolation, then
  spliced with every applicable rule's body, severity-sorted.
- **`consumes`.** A tuple on each archetype declaring which extractors'
  output it reads, in order. Used both in the prose ("READ
  `codebase-conventions` first") and by `KnowledgeSplicer` to decide
  which sections to splice into the agent's `system_prompt`.
- **KnowledgeSplicer.** At scaffold time, pulls every `knowledge:<company>*`
  blob, parses it by `## <heading>` markers, and concatenates the slices
  the archetype declares it `consumes` into a `system_prompt` prologue.
  Lets the scaffold output be self-contained — the downstream runtime
  doesn't need DB access.
- **Persona.** The dict produced by `archetype.build_persona(target)` —
  `{role, goal, backstory}` with `{target}` filled in and inherited rules
  appended. Goes into the agent's record in the scaffold JSON.
- **Prefix.** The `--prefix` CLI flag — prepended to every resource key
  in the generated bundle (`acme-impl-engineer`,
  `acme-impl-workflow`, …). Lets multiple bundles coexist in one
  downstream runtime.
- **Prologue.** The system-prompt header `KnowledgeSplicer.prologue()`
  emits: `# Gathered knowledge for <company>` plus the consumed
  extractor sections. Appended above the archetype's backstory inside
  the agent record.
- **Rule.** A markdown-with-frontmatter file in
  `iac/scaffold/rules/` (`commit-as-human.md`, `no-force-push.md`, …).
  Frontmatter declares `severity`, `applies_to` (archetype names or
  `[all]`), and `enforced_by`. Loaded into `RuleRegistry`; rules that
  match an archetype's name get spliced into its backstory at compose
  time. To add a new rule across N archetypes, drop one file — no
  archetype edits.
- **RuleRegistry.** The auto-loader for `iac/scaffold/rules/*.md`.
  `RuleRegistry.for_archetype("pr-fixer")` returns every rule that
  applies, sorted blocking → mandatory → advisory.
- **Scaffold.** The JSON bundle (`{version, llm_models, sources, tools,
  agents, workflows, triggers}`) emitted by `briar scaffold`. The CLI
  doesn't POST it anywhere; consumers paste it into a downstream
  orchestrator that understands the shape.
- **Severity.** A rule's enforcement priority: `blocking` |
  `mandatory` | `advisory`. Renders as the heading prefix
  (`### [blocking] no-force-push`) and controls ordering in the
  archetype's backstory.
- **Shape.** A `WorkflowShape` subclass — the topology of the workflow
  graph. Three shipped: `plan-approve-act` (plan → human-approval → act
  or comment), `one-shot` (single agent, no checkpoint), `triage`
  (read-only, no implement tools). `--shape` picks one.
- **Source.** A `SourceTemplate` subclass — declares one external system
  (GitHub, Bitbucket, Jira, AWS) the workflow's agents will read from.
  Three roles: emit a `Source` dict (context provider), emit
  zero-or-more action `Tool` dicts (mutating verbs like
  `github.commit_files` / `bitbucket.commit_files`), and declare the
  source's own identity flags + a `target(args)` method returning the
  human-readable identifier (`owner/repo` for GitHub,
  `workspace/repo` for Bitbucket). Cloud sources are read-only;
  tracker sources bring action tools.
- **Target.** Human-readable string like `iklobato/lightapi` or
  `acme/widgets` passed to `archetype.build_persona(target)` —
  interpolated into every `{target}` placeholder in role/goal/backstory.
  Derived by `ScaffoldResolver.target_for(args)`, which walks the
  selected sources in declared order and takes the first non-empty
  `SourceTemplate.target(args)`. GitHub returns
  `<owner>/<repo>`, Bitbucket returns `<workspace>/<repo>`, Jira returns
  the first project key, AWS returns `""`.
- **Tool filter.** An archetype's `tool_filter` tuple — substring
  whitelist applied to each source-contributed tool's
  `implementation_ref`. Empty tuple = bind every tool. A triager has
  `tool_filter = ("comment_on_issue", "add_labels", "comment")` so it
  literally cannot open a PR, regardless of what the LLM "wants" to do.
- **Trigger.** A `TriggerTemplate` subclass — declares what creates
  tasks for the workflow. Three shipped: `github_webhook`,
  `schedule_cron`, `manual`. `--trigger-kind` picks one.

### Runtime + delivery

- **Agent runner.** `briar agent`, implemented in `agent/runner.py`. The
  Anthropic-API tool-use loop: loads the archetype, splices the
  knowledge prologue, drives `client.messages.create` until the model
  emits `end_turn`, dispatches tool calls to `BashTool` / `ReadFileTool`
  / `WriteFileTool` / `EditFileTool`. Auths via `CLAUDE_CODE_OAUTH_TOKEN`.
- **Collector.** A `Collector` subclass in `dashboard/collectors.py` —
  one fact-gatherer per dashboard section (22 of them: disk, memory,
  knowledge inventory, scheduler state, etc.). Same Strategy + Registry
  pattern as everywhere else.
- **Dashboard.** The read-only HTTP server (`briar dashboard`). GET-only
  by construction; POST/PUT/DELETE return 501. Renders 22 Collector
  outputs through a single Jinja template, port 8080 by default.
- **Scheduler.** `briar runbook serve` — the long-lived process that
  registers every `(company, task)` from the runbook YAMLs in
  `examples/` and runs them on their declared `every:` cadence using
  the `schedule` library. No system cron, no separate scheduler binary.

### Cross-cutting

- **Bootstrap (Postgres).** `StorePostgres.bootstrap_admin(admin_dsn,
  password)` — one-time setup that creates the two tables and the
  scoped `briar_kb` role. Runs with a high-privilege DSN (e.g.
  `doadmin`); runtime uses the scoped role with only DML grants.
- **CredEnv.** The env-var name templating helper in `env_vars.py`.
  `CredEnv.AWS_KEY_ID.for_company("widget-co")` →
  `"AWS_WIDGET_CO_ACCESS_KEY_ID"`. One source of truth for which
  env vars exist.
- **Frontmatter.** YAML header at the top of a rule file, between two
  `---` lines, declaring `name`, `severity`, `applies_to`,
  `enforced_by`. Parsed by `parse_rule_file`.
- **Plugin axis.** One of the four registries that compose into a
  scaffold output: sources, archetypes, shapes, triggers. Each axis is
  a directory of subclasses + an `__init__.py` registry dict. Adding a
  kind on any axis = one file + one registry entry.
- **Registry.** The dict-of-strategies pattern repeated throughout:
  `EXTRACTORS`, `SOURCE_TEMPLATES`, `ARCHETYPES`, `WORKFLOW_SHAPES`,
  `TRIGGER_TEMPLATES`, `KnowledgeStoreRegistry.STORES`, `RuleRegistry`,
  `FormatterRegistry`, `CommandRegistry`. Same shape every time:
  abstract base in `base.py`, concrete subclasses in sibling files,
  package `__init__.py` wires the registry.
- **Strategy + Registry.** The single design pattern this codebase
  bets on: every plugin family is an `abc.ABC` contract + a registry
  dict + concrete implementations. Adding a new kind doesn't edit any
  caller — only the registry grows.

---

## Layered architecture

Every plugin family is a Strategy + Registry. Bases are `abc.ABC`
with `@abstractmethod` so missing methods surface at construct time.

```
src/briar/
├── cli.py                      argparse driver + logging bootstrap
├── logging.py                  one-place log config
├── env_vars.py                 CredEnv — every env var the CLI reads
├── pagination.py               Payload — payload-shape introspection
├── commands/                   6 commands; CommandRegistry build()
│   └── base.py                 Command (ABC) + .confirm() static
├── formatting/                 5 formatters (ABC Formatter)
│   ├── table.py / json.py / yaml.py / csv.py / quiet.py
│   └── FormatterRegistry
├── storage/                    KnowledgeStore (ABC) + StoreFile
├── extract/                    5 extractors (ABC KnowledgeExtractor)
│   ├── _gh.py                  GithubApi (static-only)
│   ├── _user_filter.py         UserFilter (author/assignee allow-block)
│   ├── composer.py             KnowledgeComposer (markdown + JSON)
│   ├── aws_services/           5 service gatherers (ABC AwsServiceGatherer)
│   └── language_detectors/     3 detectors (ABC LanguageDetector)
├── dashboard/                  read-only HTTP server + 22 collectors
│   ├── server.py               DashboardServer
│   ├── collectors.py           Collector (ABC) + 22 concretes
│   └── templates/index.html    Jinja2 + Chart.js
└── iac/
    ├── config_file.py          ConfigFile — Pydantic-backed JSON config
    ├── models.py               ConfigSpec (the IaC schema)
    ├── scaffold/
    │   ├── _composer.py        ScaffoldComposer + ScaffoldArgs
    │   ├── sources/            SourceGithub / SourceJira / SourceAws
    │   ├── triggers/           TriggerGithubWebhook / ScheduleCron / Manual
    │   ├── shapes/             ShapePlanApproveAct / OneShot / Triage
    │   ├── archetypes/         ArchetypeEngineer / PrFixer / Triager
    │   └── implementation.py + pr_fixes.py
    └── runbook/
        ├── models.py           RunbookFile, CompanyEntry, ScheduleEntry
        ├── executor.py         RunbookLoader, RunbookExtractor, RunbookSchedules
        └── scheduler.py        EveryParser + RunbookScheduler
```

**Naming convention:** folder = verb scope (`extract/`, `commands/`);
file = kind only (`github.py`, `python.py`); class = `<Verb><Kind>`
(`SourceGithub`, `DetectPython`, `ExtractAwsInfra`). Bases keep their
domain-role names (`KnowledgeExtractor`, `Formatter`, `KnowledgeStore`).

Adding a new kind = one file in the relevant folder + one entry in
the registry. No edits to the orchestrator.

### Style rules

- No `getattr` builtin → access `vars(ns).get("x")` for argparse,
  attribute access for known fields.
- No `elif` / `else` → early returns + dict dispatch.
- No `isinstance` → `type(x) is …` for narrow checks.
- **No `Optional[...]`** — empty defaults (`""`, `[]`, `{}`) + truthy
  checks. The only `Optional` references left in the codebase are
  docstrings explaining the convention.
- Validation belongs in Pydantic models, not inline.
- Free functions live inside a class as classmethods/statics. The
  only module-level function in `src/` is `cli.main` (entry-point
  shim required by `pyproject.toml`).
- Line length 160. `black` enforces; `mypy` enforces no-`Optional`.

---

## Testing

```bash
.venv/bin/python -m unittest discover -s tests
.venv/bin/mypy
.venv/bin/black --check src/ tests/
```

75 tests cover formatters, extractors (with mocked HTTP), AWS service
gatherers, scaffold composition, runbook YAML parsing, language
detection, storage, user-filter logic, the EveryParser DSL,
RunbookScheduler registration, and every dashboard collector + the
full Jinja render. No live network or disk side-effects in the suite.

---

## Files on disk after a local run

```text
./knowledge/<company>.md            per-company markdown bundle
./knowledge/<company>.<task>.md     per-task fragment (non-default tasks)
./knowledge/<category>/<id>.md      blobs put via `briar context put`
```

---

## History

- **v1.0** — full Briar API client + IaC reconciler (20 commands).
- **v1.1** — pluggable storage backends.
- **v2.0** — stripped the API surface entirely. Tool became
  extract + scaffold only.
- **v2.1** — SOLID refactor: ABC bases enforce contracts; every
  loose helper folded into a class.
- **v2.2** — read-only dashboard (Jinja + Chart.js, 22 sections).
- **v2.3** — per-(company, task) schedules; in-process `schedule`
  library replaces cron.
- **v2.4** — archetype `consumes` lists; sharpened prompts; persona
  `{target}` substitution everywhere.
- **v2.5** — `black` + `mypy` (line-length 160); dropped every
  `Optional[...]`; long signatures folded into dataclasses;
  multi-type returns named; stdlib `logging` with stack traces on
  every broad-except site.
