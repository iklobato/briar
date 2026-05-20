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

`aws-infra` falls back to the local `~/.aws/credentials` profile when
env vars are unset. The droplet runs purely off env vars.

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
            args: {aws_extract_profile: acme, aws_extract_region: us-east-2,
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
briar scaffold implementation \
    --prefix acme-impl \
    --owner iklobato --repo lightapi \
    --source github \
    --auth-mode pat --github-secret-id <secret-uuid> \
    --shape plan-approve-act --archetype engineer \
    --trigger-kind github_webhook \
    --out acme-impl.json
```

### Templates

| Template | Shape |
|---|---|
| `implementation` | source → agent → workflow(`plan → human_checkpoint → implement / comment`) → trigger |
| `pr-fixes` | source → agent → one-shot workflow → trigger (no human gate) |

### Composable plugin axes

| Axis | Flag | Built-in kinds |
|---|---|---|
| Sources | `--source <kind>` (repeatable) | `github`, `jira`, `aws` |
| Trigger | `--trigger-kind <kind>` | `github_webhook`, `schedule_cron`, `manual` |
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
re-push from your laptop:

```bash
{ for c in widget-co acme; do
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
