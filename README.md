# briar — local extraction + scaffolding CLI

Python CLI that mines live state from external systems (GitHub, AWS, …),
emits per-company markdown knowledge files, and generates JSON config
bundles that you paste into the Briar web UI.

```text
$ briar version
briar-cli 1.1.0
```

**This tool does not talk to `api.usebriar.com`.** It used to (~20 CRUD
subcommands + login/JWT plumbing) but was stripped in v2.0 — backend
resources are managed from the web UI now. The CLI's surviving job is
the part that benefits from being local: pulling data from external
services, transforming it into agent context, and rendering reusable
config templates.

---

## What it does

- **Five extractors** that mine live state into a per-company markdown
  blob agents use as context: PR archaeology, AWS infra, active work,
  GitHub deployments, codebase conventions.
- **Runbook YAML** — drive every extractor across every company in one
  declarative file.
- **Scaffold templates** that emit JSON config bundles (implementation,
  pr-fixes) for the Briar web UI to consume.
- **Local knowledge store** — file-backed CRUD over named markdown
  blobs (`category:identifier` keys).

---

## Install

```bash
make venv                     # creates .venv/ and runs `pip install -e .`
source .venv/bin/activate
briar version
```

Requires **Python 3.10+**. Runtime deps in `pyproject.toml`: `httpx`,
`pydantic>=2`, `PyYAML`, `rich`. `boto3` is lazy-imported — installs
but only loaded when you run `extract --include aws-infra`.

No login, no profiles, no `~/.briar/` directory. The five commands all
work against local files + external APIs (GitHub, AWS) directly.

---

## Commands

```
extract   — run extractors against external sources (GitHub/AWS)
runbook   — multi-company knowledge extraction from a YAML file
scaffold  — generate JSON config bundles for the web UI
context   — read/write local markdown blobs
version   — print client version
```

Global flag: `--format {table,json,yaml,csv,quiet}` (default: `table`
for lists, `json` for single records).

---

## Knowledge extractors

| `--include` name | What it mines | Auth |
|---|---|---|
| `pr-archaeology` | merged-PR patterns, median time-to-merge, top reviewers | `gh auth token` or `$GITHUB_TOKEN` |
| `aws-infra` | ECS, RDS, Lambda, SQS, CloudWatch (top 10 by size) | local AWS profile or env-var credentials |
| `active-work` | open PRs across the configured repos | GitHub PAT |
| `github-deployments` | environments, recent deployments, CI runs | GitHub PAT |
| `codebase-conventions` | per-repo language / test runner / linter / migration tool | GitHub PAT |

One-shot:

```bash
briar extract --company acme \
    --include pr-archaeology --include active-work \
    --pr-repo iklobato/lightapi --pr-max 100 \
    --active-repo iklobato/lightapi \
    --root ./knowledge
```

`--include` is repeatable; omit to run *every* extractor that's
available in the current env. Output goes to `./knowledge/<company>.md`.

### Author / assignee filters

Both PR-archaeology and active-work accept `--pr-authors-allow`,
`--pr-authors-block`, `--pr-assignees-allow`, `--pr-assignees-block`
(and the same with `--active-` prefix). Composition: `allow ∩ ¬block`.

```bash
briar extract --company demo --include active-work \
    --active-repo acme-co/acme-app \
    --active-authors-allow iklobato \
    --active-authors-block 'dependabot[bot]'
```

### Credential resolution

Per-company env vars are read by the extractors via `briar.env_vars.CredEnv`.
The `{c}` placeholder substitutes the uppercased, underscore-normalised
company name (`widget-co` → `WIDGET_CO`).

| Env var template | Used by |
|---|---|
| `AWS_{c}_ACCESS_KEY_ID` / `SECRET_ACCESS_KEY` / `SESSION_TOKEN` | `aws-infra` (per-company, overrides local AWS profile) |
| `GITHUB_TOKEN` | every GitHub extractor (single workspace-wide PAT) |
| `JIRA_{c}_EMAIL` / `_TOKEN` | reserved for a future Jira extractor |

`aws-infra` falls back to the local `~/.aws/credentials` profile when
env vars are unset — that's what local dev uses. The droplet runs
entirely off env vars (see "Deployment" below).

---

## Runbook YAML

One file drives every extractor across every company. Pure local
extraction — nothing in this YAML reaches a Briar API.

```yaml
# examples/acme.yaml
version: 1
companies:
  acme:
    knowledge:
      store: file
      name: ./knowledge/acme.md

    extract:
      - name: pr-archaeology
        args:
          pr_repo:
            - acme-co/acme-app
            - acme-co/acme-platform
          pr_max: 30
      - name: active-work
        args:
          active_repo: [acme-co/acme-app]
      - name: github-deployments
        args:
          deploy_repo: [acme-co/acme-app]
      - name: codebase-conventions
        args:
          conventions_repo: [acme-co/acme-app]
      - name: aws-infra
        args:
          aws_extract_profile: acme
          aws_extract_region: us-east-2
          aws_extract_service: [ecs, lambda, logs, rds, sqs]
```

Commands:

```bash
briar runbook extract examples/acme.yaml     # one company
briar runbook sweep   examples/                # every *.yaml in a folder
```

`sweep` is what the scheduler droplet runs nightly — it iterates the
directory, extracts per company, and keeps going past per-file
failures (cron must not abort mid-loop).

---

## Scaffold — JSON for the web UI

`briar scaffold` emits a JSON bundle describing the agent / workflow /
sources / tools / trigger you want. You paste it into the web UI; the
CLI never POSTs it anywhere.

```bash
briar scaffold implementation \
    --prefix acme-impl \
    --owner iklobato --repo lightapi \
    --source github \
    --auth-mode pat --github-secret-id <secret-uuid> \
    --shape plan-approve-act --archetype engineer \
    --trigger-kind github_webhook \
    --out acme-impl.json
```

### Two top-level templates

| Template | What it builds |
|---|---|
| `implementation` | source → agent → workflow(`plan → human_checkpoint → implement / comment`) → trigger |
| `pr-fixes` | source → agent → one-shot workflow → trigger (no human gate) |

### Composable plugin axes

| Axis | Flag | Built-in kinds |
|---|---|---|
| **Sources** | `--source <kind>` (repeatable) | `github`, `jira`, `aws` |
| **Trigger** | `--trigger-kind <kind>` | `github_webhook`, `schedule_cron`, `manual` |
| **Workflow shape** | `--shape <name>` | `plan-approve-act`, `one-shot`, `triage` |
| **Agent archetype** | `--archetype <name>` | `engineer`, `pr-fixer`, `triager` |

Adding a new kind = one file in the relevant folder under
`src/briar/iac/scaffold/` + one entry in the package `__init__.py`'s
registry. No edits elsewhere.

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

## Deployment — DigitalOcean droplet scheduler

The CLI is deployed to a small DO droplet that runs `briar runbook
sweep` nightly. Source-of-truth is the private GitHub repo
`iklobato/briar-cli`; the droplet is a `git clone` of `main`.

### One-line deploy

```bash
git push && ssh root@203.0.113.11 \
    'cd /opt/briar-scheduler && git pull --ff-only && .venv/bin/pip install -e . --quiet'
```

(Optional zsh alias: `alias briar-deploy="git push && …"` — full form
in commit history.)

### Droplet layout

| Path | Purpose |
|---|---|
| `/opt/briar-scheduler/` | git clone of `iklobato/briar-cli`, `git status` clean |
| `/opt/briar-scheduler/.venv/` | python venv (gitignored, survives `git pull`) |
| `/opt/briar-scheduler/examples/` | live company YAMLs (`lightapi-e2e.yaml`, `widgets.yaml`, `acme.yaml`) |
| `/etc/briar/secrets.env` | mode 600, root-owned. Holds `GITHUB_TOKEN` + per-company `AWS_*_KEY_ID` / `SECRET` / `SESSION` |
| `/etc/cron.d/briar-scheduler` | nightly entry at `17 3 * * * UTC` — sources `secrets.env`, runs `briar runbook sweep examples/` |
| `/var/log/briar/scheduler.log` | append-only log |

### Cron entry

```
SHELL=/bin/sh
PATH=/opt/briar-scheduler/.venv/bin:/usr/bin:/bin
17 3 * * * root set -a; . /etc/briar/secrets.env; set +a; \
    cd /opt/briar-scheduler && briar runbook sweep examples/ \
    >> /var/log/briar/scheduler.log 2>&1
```

### Refreshing secrets from the laptop

`secrets.env` holds short-lived AWS STS triplets (`acme` profile is
SSO-vended). When the SSO session ages out, re-push from the laptop:

```bash
{ for c in widget-co acme; do
    aws configure export-credentials --profile $c --format env-no-export 2>/dev/null \
      | grep -E '^AWS_(ACCESS_KEY_ID|SECRET_ACCESS_KEY|SESSION_TOKEN)=' \
      | sed -E "s/^AWS_/AWS_${c^^}_/; s/-/_/g"
  done
  echo "GITHUB_TOKEN=$(gh auth token)"
} | ssh root@203.0.113.11 \
    'cat > /etc/briar/secrets.env && chmod 600 /etc/briar/secrets.env'
```

### Firewall

Cloud firewall `briar-scheduler` allows inbound SSH (port 22) only
from the operator's home IPv4 + IPv6. Egress: all (so the cron can
reach GitHub + AWS).

### Rollback

```bash
ssh root@203.0.113.11 'cd /opt/briar-scheduler && git log --oneline -5'
ssh root@203.0.113.11 'cd /opt/briar-scheduler && git reset --hard <previous-sha>'
```

---

## Layered architecture

Every plugin family is a Strategy + Registry. Bases are `abc.ABC`
with `@abstractmethod`; missing methods surface at construct time.

```
src/briar/
├── cli.py                      argparse driver (Cli class)
├── env_vars.py                 CredEnv — every env var the CLI reads
├── pagination.py               Payload — payload-shape introspection
├── commands/                   5 commands: extract, runbook, scaffold, context, version
│   └── base.py                 Command (ABC) + .confirm() static
├── formatting/                 5 formatters (ABC Formatter)
│   ├── table.py                FormatTable (default)
│   ├── json.py                 FormatJson
│   ├── yaml.py                 FormatYaml
│   ├── csv.py                  FormatCsv
│   └── quiet.py                FormatQuiet
├── storage/                    KnowledgeStore (ABC) + StoreFile (only backend)
├── extract/                    5 extractors (ABC KnowledgeExtractor)
│   ├── _gh.py                  GithubApi (static-only)
│   ├── _user_filter.py         UserFilter (author/assignee allow-block)
│   ├── composer.py             KnowledgeComposer (markdown + JSON renderers)
│   ├── aws_services/           5 service gatherers (ABC AwsServiceGatherer)
│   └── language_detectors/     3 detectors (ABC LanguageDetector)
└── iac/
    ├── config_file.py          ConfigFile — Pydantic-backed JSON config
    ├── models.py               ConfigSpec (the IaC schema)
    ├── scaffold/
    │   ├── _composer.py        ScaffoldComposer + ScaffoldArgs (classmethods)
    │   ├── sources/            SourceGithub / SourceJira / SourceAws (ABC SourceTemplate)
    │   ├── triggers/           TriggerGithubWebhook / TriggerScheduleCron / TriggerManual
    │   ├── shapes/             ShapePlanApproveAct / ShapeOneShot / ShapeTriage
    │   ├── archetypes/         ArchetypeEngineer / ArchetypePrFixer / ArchetypeTriager
    │   └── implementation.py + pr_fixes.py
    └── runbook/                RunbookLoader + RunbookExtractor
```

**Naming convention:** folder = verb scope (`extract/`, `commands/`);
file = kind only (`github.py`, `python.py`); class = `<Verb><Kind>`
(`SourceGithub`, `DetectPython`, `ExtractAwsInfra`). Bases keep their
domain-role names (`KnowledgeExtractor`, `Formatter`, `KnowledgeStore`).

Adding any new kind = one file in the relevant folder + one entry in
the registry. No edits to the orchestrator.

### Style rules

- No `getattr` builtin → access `vars(ns).get("x")` for argparse,
  attribute access for known fields.
- No `elif` / `else` → early returns + dict dispatch.
- No `isinstance` → `type(x) is …` for narrow checks.
- Validation belongs in Pydantic models, not inline.
- Free functions live inside a class as classmethods/statics. The
  only module-level function in `src/` is `cli.main` (entry-point
  shim required by `pyproject.toml`).

---

## Testing

```bash
.venv/bin/python -m unittest discover -s tests
```

52 tests cover formatters, extractors (with mocked HTTP), AWS service
gatherers, scaffold composition, runbook YAML parsing, language
detection, storage, and the user-filter logic. No live network or
disk side-effects in the suite.

---

## Files on disk after a local run

```text
./knowledge/<company>.md         per-company markdown bundle
./knowledge/<category>/<id>.md   blobs put via `briar context put`
```

The droplet writes the same files under `/opt/briar-scheduler/knowledge/`.

---

## History

- **v1.0** — full Briar API client + IaC reconciler (20 commands).
- **v1.1** — pluggable storage backends (`file` + `briar-api`).
- **v2.0** — stripped the API surface entirely. Tool became
  extract-only; deleted ~26 modules + ~36 net source files. Cron on
  the droplet stopped needing JWTs.
- **v2.1** — SOLID refactor: ABC bases enforce contracts; every loose
  helper folded into a class (only `cli.main` remains as a free
  function).
