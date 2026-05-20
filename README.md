# briar — terminal client

Python package that wraps the entire Briar API (`api.usebriar.com`) plus a
declarative configuration layer for **multi-company** agent pipelines.

```text
briar version
briar-cli 1.1.0
```

What ships in this CLI:

- **API client** for every Briar endpoint (35 subcommands)
- **Profiles** — one config slot per company
- **Infrastructure-as-code** — declare workspaces, agents, workflows, tools
  in JSON; `apply` reconciles
- **Multi-source scaffolds** — `github` + `jira` + `aws` mixed in one workflow,
  pluggable per company
- **Runbook YAML** — drive N companies × N pipelines from a single file
- **Knowledge extractors** — 5 strategies (PR archaeology, AWS infra,
  active work, GitHub deployments, codebase conventions) writing per-company
  knowledge files used as agent context
- **Pluggable storage** — knowledge blobs live in local files **or** in the
  Briar workspace (visible to server-side agents)
- **`briar context`** — CRUD over arbitrary named blobs (knowledge / memory /
  lessons / scratch)

---

## Install

```bash
make venv                     # creates .venv/ and runs `pip install -e .`
source .venv/bin/activate
briar version
```

Requires **Python 3.10+** and four runtime deps declared in `pyproject.toml`:
`httpx`, `pydantic>=2`, `PyYAML`, `rich`. Plus `boto3` for the AWS extractor
(lazy-imported — installs but only used when you run `extract --include aws-infra`).

---

## First-time setup

```bash
briar login                           # prompts for email + password
briar whoami                          # confirms the session
briar workspace list                  # see every workspace you belong to
briar workspace use <ws-id>           # pin one
```

Credentials are persisted to `~/.briar/<profile>/config.json` (mode `600`).

---

## Global flags

These work with **every** subcommand and can appear before *or* after the
subcommand:

| Flag | Effect |
|---|---|
| `--profile <name>` | Use a named profile instead of `default` / `$BRIAR_PROFILE` / active-file |
| `--workspace <id>` | Override the pinned workspace id for this call only |
| `--api-base <url>` | Override the API base URL (point at a dev backend) |
| `--format {table,json,yaml,csv,quiet}` | Output format (default: `table` for lists, `json` for single records) |

```bash
briar --profile acme agents list
briar agents list --format yaml
briar --workspace 11111111-… tasks list --format quiet | xargs -I{} briar tasks get {}
```

---

## Multi-tenant profiles

Each profile is an isolated credential bundle at `~/.briar/<name>/config.json`.

```bash
briar --profile acme   login          # saves to ~/.briar/acme/config.json
briar --profile zenith login          # saves to ~/.briar/zenith/config.json

briar --profile acme   agents list    # talks to acme's data
briar --profile zenith sources list   # talks to zenith's data

briar profile list                    # show all profiles + which is active
briar profile use <name>              # make it the default for new shells
```

Selection priority: `--profile > $BRIAR_PROFILE > ~/.briar/active > default`.

---

## API client subcommands (CRUD across the whole catalogue)

Every catalogue resource shares the same verb pattern — `list / get / create /
patch / delete`. The resource list:

| Command | Backend route | Verbs |
|---|---|---|
| `agents` | `/api/v1/agents/` | full CRUD |
| `tools` | `/api/v1/tools/` | full CRUD |
| `skills` | `/api/v1/skills/` | full CRUD |
| `sources` | `/api/v1/sources/` | full CRUD |
| `triggers` | `/api/v1/triggers/` | full CRUD |
| `llm-providers` | `/api/v1/llm/providers/` | full CRUD |
| `llm-models` | `/api/v1/llm/models/` | full CRUD |
| `secrets` | `/api/v1/secrets/` | full CRUD (values write-only) |
| `budgets` | `/api/v1/budgets/` | full CRUD |
| `budget-alerts` | `/api/v1/budget-alerts/` | full CRUD |
| `audit-events` | `/api/v1/audit-events/` | read-only |
| `workspace` | `/api/v1/workspaces/` | full CRUD + `use` / `show` |
| `memberships` | `/api/v1/workspaces/<ws>/memberships/` | list / add / patch / remove |

Common pattern:

```bash
briar agents list --ordering=-created_at --limit 20
briar agents get <id>
briar agents create --field name=demo --field model_alias=gpt-4o
briar agents create --from-file new-agent.json --field name=override
briar agents patch  <id> --field model_alias=sonnet
briar agents delete <id> --yes
```

### `--field` value rules

| Form | Meaning |
|---|---|
| `--field name=demo` | Plain string |
| `--field count=42` | Parsed as JSON → integer |
| `--field tags=["a","b"]` | Parsed as JSON → list |
| `--field config=@payload.json` | Read the value from `payload.json` |
| `--field value=-` | Read the value from **stdin** (newline-stripped) |

```bash
cat token.txt | briar secrets create --field name=anthropic --field value=-
```

### List flags

```bash
briar tasks list --limit 50 --offset 100 --ordering=-created_at \
                 --query "status=running" --query "title__icontains=lightapi"
```

---

## Workflows

```bash
briar workflows list
briar workflows get      <wf-id>
briar workflows versions <wf-id>
briar workflows fork     <wf-id> [--field name="copy"]
briar workflows set-active <wf-id> --version <version-id>
```

Workflow templates (read-only) can be forked into a workspace:

```bash
briar workflow-templates list
briar workflow-templates fork <tpl-id>
```

## Tasks, runs, checkpoints

```bash
briar tasks list
briar tasks get    <task-id>
briar tasks create --field workflow=<wf-id> --field title="..." \
                   --field context='{"key":"value"}'
briar tasks cancel      <task-id>
briar tasks retry       <task-id>
briar tasks runs        <task-id>
briar tasks checkpoints <task-id>

briar runs follow <run-id>          # tail the WebSocket feed in real time
briar runs follow <run-id> --raw    # print each frame verbatim

briar checkpoints approve <ckpt-id> --decision approve --note "..."
briar checkpoints reject  <ckpt-id> --note "..."
```

## OAuth

```bash
briar oauth providers              # list supported provider kinds
briar oauth connections            # list connected accounts
briar oauth start github           # prints an authorize URL — open it
briar oauth refresh    <conn-id>
briar oauth disconnect <conn-id>
```

## Raw escape hatch

```bash
briar api GET    /api/v1/anything/
briar api POST   /api/v1/anything/ --field key=value
briar api PATCH  /api/v1/anything/<id>/ --from-file body.json
briar api DELETE /api/v1/anything/<id>/
```

## Local config

```bash
briar config show
briar config set api_base https://api.usebriar.com
briar config set workspace <ws-id>
```

---

## Infrastructure-as-code (JSON configs)

Declarative config files reconciled by `briar apply`. Same upsert-by-name
semantics as Terraform.

```bash
briar scaffold implementation --owner iklobato --repo lightapi --out lightapi.json
briar plan    lightapi.json                 # dry-run diff
briar apply   lightapi.json                 # apply (interactive confirm)
briar apply   lightapi.json --yes           # skip confirm
briar destroy lightapi.json --yes
briar export  --out current-state.json      # dump live → file
```

### Built-in scaffold templates

| Template | What it builds |
|---|---|
| `implementation` | source → agent → workflow(`plan → human_checkpoint → implement / comment`) → trigger |
| `pr-fixes` | source → agent → one-shot workflow → trigger (no human gate) |

### Composable scaffolds (the v0.6+ shape)

Both templates accept these pluggable axes via repeatable / choice flags:

| Axis | Flag | Built-in kinds |
|---|---|---|
| **Sources** (data the agent reads) | `--source <kind>` (repeatable) | `github`, `jira`, `aws` |
| **Trigger** (how runs get created) | `--trigger-kind <kind>` | `github_webhook`, `schedule_cron`, `manual` |
| **Workflow shape** (graph topology) | `--shape <name>` | `plan-approve-act`, `one-shot`, `triage` |
| **Agent archetype** (persona + tool filter) | `--archetype <name>` | `engineer`, `pr-fixer`, `triager` |

Each source kind brings its own tools:
- `github` → `comment_on_issue`, `open_pr`, `commit_files`
- `jira` → `comment`, `transition`, `update_issue`
- `aws` → read-only (no write tools; gathered as context)

Each archetype filters which of those tools the agent actually binds (e.g.
`triager` drops `commit_files` and `open_pr`).

Example — acme company with all three source kinds + hourly cron + the
plan-approve-act flow:

```bash
briar --profile acme scaffold implementation \
    --prefix acme-impl \
    --owner iklobato --repo lightapi \
    --source github --source jira --source aws \
    --jira-project ACME --jira-secret-id <atlassian-pat-uuid> \
    --aws-role-arn arn:aws:iam::123:role/briar-reader \
    --aws-external-id acme-briar --aws-services ec2 --aws-services logs \
    --trigger-kind schedule_cron --schedule "0 * * * *" \
    --shape plan-approve-act --archetype engineer \
    --out acme-impl.json
briar --profile acme apply acme-impl.json --yes
```

### Adding a new kind

Every plugin family lives in a folder where each file is one kind:

```text
src/briar/iac/scaffold/sources/      # github.py, jira.py, aws.py  → SourceGithub, SourceJira, SourceAws
src/briar/iac/scaffold/triggers/     # github_webhook.py, schedule_cron.py, manual.py
src/briar/iac/scaffold/shapes/       # plan_approve_act.py, one_shot.py, triage.py
src/briar/iac/scaffold/archetypes/   # engineer.py, pr_fixer.py, triager.py
```

Adding a new source kind (e.g. `linear`): one file + one entry in
`sources/__init__.py`. Same pattern for triggers / shapes / archetypes.

---

## Runbook YAML — multi-company hourly pipelines

One file drives every company × every pipeline.

```yaml
# examples/runbook.yaml
version: 1
companies:
  acme:
    profile: acme-test
    workspace_id: 49c8b9d7-…
    defaults:
      llm_provider_key: anthropic
      model: claude-sonnet-4-6
      auth_mode: pat
      github_secret_id: 7e1ee226-…

    # Optional: per-company knowledge binding (see "Knowledge" below)
    knowledge:
      store: briar-api
      name: knowledge:acme
      mode: bind

    # Optional: which extractors to run for `briar runbook extract <file>`
    extract:
      - name: pr-archaeology
        args: {pr_repo: [iklobato/lightapi], pr_max: 100}
      - name: aws-infra
        args: {aws_extract_profile: acme-prod, aws_extract_region: us-east-1}
      - name: active-work
        args: {active_repo: [iklobato/lightapi]}

    runbooks:
      - template: implementation
        prefix: acme-impl
        owner: iklobato
        repo: lightapi
        sources:
          - kind: jira
            project: [ACME]
          - kind: github
          - kind: aws
            role_arn: arn:aws:iam::123:role/briar-reader
            external_id: acme-briar
            services: [ec2, logs, iam]
        trigger:
          kind: schedule_cron
          schedule: "0 * * * *"

      - template: pr-fixes
        prefix: acme-prfix
        owner: iklobato
        repo: lightapi
        sources:
          - kind: github
        trigger: {kind: schedule_cron, schedule: "0 * * * *"}

  widgets:
    profile: widgets-test
    # ... same structure
```

Commands:

```bash
briar runbook extract examples/runbook.yaml   # populate the knowledge files
briar runbook plan    examples/runbook.yaml   # dry-run diff across all companies
briar runbook apply   examples/runbook.yaml   # reconcile per company
briar runbook destroy examples/runbook.yaml --yes
```

Per-company `defaults:` inherit into each runbook (overridable per entry).
Each runbook resolves to one `scaffold <template> + apply` invocation under
the company's profile.

---

## Knowledge extractors

Five extractors that mine live state into a per-company markdown blob.
Agents use the result as context on every run.

| `--include` name | What it mines | Auth |
|---|---|---|
| `pr-archaeology` | merged-PR patterns, median time-to-merge, top reviewers | `gh auth token` or `$GITHUB_TOKEN` |
| `aws-infra` | ECS, RDS, Lambda, SQS, CloudWatch (top 10 by size) | local AWS profile via boto3 |
| `active-work` | open PRs across the configured repos | GitHub PAT |
| `github-deployments` | environments, recent deployments, CI runs | GitHub PAT |
| `codebase-conventions` | per-repo language / test runner / linter / migration tool | GitHub PAT |

### `briar extract` — one-shot

```bash
briar extract --company acme \
    --include pr-archaeology --include active-work \
    --pr-repo iklobato/lightapi --pr-max 100 \
    --active-repo iklobato/lightapi \
    --storage file --root ./knowledge
```

`--include` is repeatable; omit to run *all* extractors that are available
in the current env. `--storage` picks where the result lands (see next
section).

### Driving extractors from the runbook

The runbook YAML's `extract:` section drives per-company runs:

```bash
briar runbook extract examples/runbook.yaml
```

This walks every company's `extract:` list, runs each named extractor with
its `args`, and writes the merged markdown via the company's
`knowledge.store`. Re-run on a schedule (cron, launchd) to keep the
knowledge fresh.

### Adding a new extractor

One file in `src/briar/extract/` + one entry in `EXTRACTORS`. Class name
follows `Extract<Kind>`. AWS service gatherers (under
`extract/aws_services/`) and language detectors (under
`extract/language_detectors/`) follow the same pattern at a sub-level —
adding S3 / Rust is one file inside the relevant subfolder.

---

## `briar context` — CRUD over arbitrary named blobs

Knowledge files are one use case. The same storage layer holds **any** named
markdown blob: extracted knowledge, accumulated memory, codified lessons,
ad-hoc scratch notes. Blob names use the `category:identifier` convention.

```bash
# Store
briar context put knowledge:acme          --from-file knowledge/acme.md
briar context put memory:reviewer-iklobato  --content "Focuses on typing rigor"
briar context put lessons:python-typing     --content - < lessons/typing.md
briar context put scratch:notes             --content "any markdown here"

# Read
briar context get knowledge:acme
briar context list                                  # all blobs
briar context list --prefix lessons:                # one category
briar context categories                            # distinct category prefixes
briar context delete memory:stale --yes
```

Backend is pluggable via `--store {file,briar-api}`:

| Store | Where it lives | Visible to server-side agents |
|---|---|---|
| `file` (default) | `./knowledge/<category>/<name>.md` | ✗ |
| `briar-api` | `Source(kind="static")` row in the workspace | ✓ — orchestrator delivers it as `task.context[source_<name>]` on every run |

```bash
briar context --store briar-api put knowledge:acme --from-file knowledge/acme.md
briar context --store briar-api list --prefix knowledge:
```

### Two integration modes (when used via the runbook)

The runbook YAML's per-company `knowledge:` field controls how the stored
blob reaches the agent:

```yaml
companies:
  acme:
    knowledge:
      store: briar-api      # or "file"
      name: knowledge:acme
      mode: inject          # or "bind"
```

| `mode: inject` (default) | `mode: bind` (briar-api only) |
|---|---|
| Runbook reads the blob, prepends to every agent's `system_prompt` at apply | Runbook adds the static-Source `source_key` to every agent. Orchestrator gathers via `task.context` on every run |
| Works with any store | Only `briar-api` — content lives server-side |
| Adds prompt tokens | No prompt bloat |

Legacy shortcut `knowledge_file: ./knowledge/acme.md` is equivalent to
`{store: file, name: ./knowledge/acme.md, mode: inject}`.

---

## Secrets (with stdin)

```bash
briar secrets list
briar secrets get <id>            # values are write-only — get() returns metadata
briar secrets create --field name=stripe --field scope=workspace --field value=-
                                  # value read from stdin → not in shell history
briar secrets patch <id> --field value=-   # rotate
briar secrets delete <id> --yes
```

---

## Recipes

**Bootstrap a new project from a template**

```bash
TPL=$(briar --format quiet workflow-templates list | head -1)
NEW_WF=$(briar workflow-templates fork "$TPL" --field name="my-project" \
         | jq -r .id)
briar workflows get "$NEW_WF"
```

**Cancel every queued task at once**

```bash
briar --format quiet tasks list --query status=queued \
  | xargs -I{} briar tasks cancel {}
```

**Tail a run while triggering work**

```bash
TASK=$(briar tasks create --field workflow=$WF --field title=demo | jq -r .id)
RUN=$(briar tasks runs "$TASK" --format quiet | head -1)
briar runs follow "$RUN"
```

**Extract knowledge across many repos via Python (sidesteps shell word-splitting)**

```python
import subprocess, json
repos = json.loads(subprocess.check_output(
    ["gh", "repo", "list", "<org>", "--limit", "100",
     "--json", "name,isArchived"]
))
active = [f"<org>/{r['name']}" for r in repos if not r['isArchived']]
cmd = [
    "briar", "extract", "--company", "<co>",
    "--storage", "file", "--root", "./knowledge",
    "--pr-max", "25",
    "--aws-extract-profile", "<aws-profile>",
]
for repo in active:
    for flag in ("--pr-repo", "--active-repo", "--deploy-repo", "--conventions-repo"):
        cmd.extend([flag, repo])
subprocess.run(cmd, check=True)
```

**Multi-tenant: same workflow id across two companies**

```bash
briar --profile acme   workflows get "$WF"
briar --profile zenith workflows get "$WF"   # different DB, different result
```

**Audit dump as CSV**

```bash
briar --format csv audit-events list --ordering=-created_at --limit 1000 > audit.csv
```

---

## Files on disk

```text
~/.briar/
├── active                    one-line file: name of the active profile
├── default/config.json       default profile (mode 600)
├── acme/config.json          additional profile
└── zenith/config.json
```

Plus the local knowledge file root (when `--store file`):

```text
./knowledge/
├── knowledge/acme.md       knowledge:<x> blobs live here
├── memory/reviewer-x.md      memory:<x> blobs
├── lessons/typing.md
└── …
```

---

## Layered architecture (every plugin family is a Strategy + Registry)

```
src/briar/
├── cli.py / commands/        35 commands, registry-assembled
├── http.py                   ApiClient — httpx + JWT refresh + 5xx retry
├── formatting/               5 formatters (table / json / yaml / csv / quiet)
├── extract/                  5 extractors (Extract<Kind>)
│   ├── aws_services/         5 service gatherers (Gather<Kind>)
│   └── language_detectors/   3 detectors (Detect<Kind>)
├── storage/                  KnowledgeStore: StoreFile + StoreBriarApi
├── ws.py                     RFC 6455 client (text frames, ping/pong, mask)
└── iac/
    ├── models.py             Pydantic ConfigSpec (the IaC schema)
    ├── reconcilers/          7 reconcilers (Reconcile<Kind>)
    ├── scaffold/
    │   ├── sources/          SourceGithub / SourceJira / SourceAws
    │   ├── triggers/         TriggerGithubWebhook / TriggerScheduleCron / TriggerManual
    │   ├── shapes/           ShapePlanApproveAct / ShapeOneShot / ShapeTriage
    │   ├── archetypes/       ArchetypeEngineer / ArchetypePrFixer / ArchetypeTriager
    │   └── implementation.py + pr_fixes.py  (ScaffoldImplementation, ScaffoldPrFixes)
    └── runbook/              Multi-company YAML driver
```

**Naming convention**: folder = verb scope (`extract/`, `commands/`,
`reconcilers/`); file = kind only (`github.py`, `python.py`); class =
`<Verb><Kind>` (`SourceGithub`, `DetectPython`, `ExtractAwsInfra`). Bases
keep their domain-role names (`KnowledgeExtractor`, `ResourceReconciler`,
`Formatter`).

Adding any new kind = one file in the relevant folder + one entry in the
registry. No edits to the orchestrator.

---

## Troubleshooting

| Symptom | Cause | Workaround |
|---|---|---|
| `error: refresh token rejected` | Refresh token expired/rotated | Re-login |
| `error: not logged in` | Access token cleared | `briar --profile X login` |
| `error: network error talking to …` | API unreachable | `briar api GET /healthz` to isolate |
| `HTTP 500 (server returned HTML)` | Backend DB pool exhaustion | The CLI retries 5xx 4 times with backoff; usually self-heals |
| `HTTP 409 (referenced_by: [...])` | FK-protected delete | Delete the referencing rows first |
| `extract` says `(not available in this env)` | Missing GitHub PAT or AWS profile | `gh auth login` or set `$GITHUB_TOKEN`; check `~/.aws/credentials` |
| WS disconnects after a few seconds | Run finished cleanly | Check `briar runs get <id>` |
| Long arg list errors out of bash | Shell quoting / word-splitting | Drive via `subprocess.run(cmd_list)` from Python — see "Recipes" |

---

## What's not (yet) implemented

- Tab completion for bash / zsh
- SQLite storage backend (only `file` and `briar-api` today)
- Interactive ID pickers
- Multipart file upload (no API surface uses it today)

Adding any of these is small — see the layered architecture above for where
the extension point lives.
