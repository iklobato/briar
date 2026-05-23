# briar — local extraction + scheduling CLI

Python CLI that mines live state from external systems (GitHub,
Bitbucket, AWS, GCP, Azure, Jira, Linear, …), schedules per-company
extraction in-process, and runs autonomous LLM-driven agents
against the resulting knowledge.

Everything runs locally — no `api.usebriar.com` service, no remote
workspace. Each command shells out to the external APIs directly
(via PyGithub, atlassian-python-api, boto3, anthropic, etc.) and
writes its output to local markdown files or a Postgres knowledge
store.

```
briar version
briar-cli 1.1.1
```

---

## Install

From PyPI (recommended):

```bash
pip install briar-cli
briar version
```

From source (for development):

```bash
git clone git@github.com:iklobato/briar-cli.git
cd briar-cli
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .
briar version
```

Optional pip extras — install only what you'll actually use. Each
adapter fails loudly if its SDK is missing, with the right install
command in the error message:

```bash
pip install 'briar-cli[openai]'         # OpenAI LLM
pip install 'briar-cli[gemini]'         # Google Gemini LLM
pip install 'briar-cli[vault]'          # HashiCorp Vault credential store
pip install 'briar-cli[gcp]'            # GCP cloud provider
pip install 'briar-cli[azure]'          # Azure cloud provider
pip install 'briar-cli[infisical]'      # Infisical credential bootstrap
pip install 'briar-cli[all]'            # everything above
```

Replace `pip install` with `pip install -e` and quote the source-tree
path (`-e '.[openai]'`) when working from a local checkout.

Base install always works for: GitHub + Bitbucket extractors, AWS
infra, Jira/Linear/GitHub-Issues/Bitbucket-Issues trackers,
Anthropic LLM, AWS Bedrock LLM, all 6 message writers (jira-comment,
jira-transition, slack-channel, telegram-chat, github-pr-comment,
bitbucket-pr-comment), all 4 notification sinks, file + postgres
knowledge stores, AWS Secrets Manager / SSM credential stores.

Python 3.10+. Tested through 3.12.

---

## Commands

```
briar version
briar extract       — one-shot extraction
briar runbook       — scheduled extraction (extract / sweep / serve)
briar agent         — autonomous LLM-driven flows (prfix / implement)
briar scaffold      — emit JSON config bundles for downstream tools
briar context       — read/write local markdown blobs
briar dashboard     — read-only HTML status page
briar auth          — interactive credential acquisition (login / logout / refresh / list / status)
briar secrets       — credential coverage (doctor / bootstrap)
```

**Global flags** (apply to every subcommand):

| Flag | Purpose | Default |
|---|---|---|
| `--format {table,json,yaml,csv,quiet}` | output format | `table` |
| `--verbose` / `-v` | DEBUG-level logging | INFO |

**Global env vars:**

| Env var | Effect |
|---|---|
| `BRIAR_VERBOSE=1` | same as `--verbose` |
| `BRIAR_LIB_DEBUG=1` | also surface third-party loggers (httpx, boto3) |
| `BRIAR_DATABASE_URL` | switch the default knowledge store from `file` to `postgres` — also the final fallback DSN when no per-company override is set |
| `BRIAR_{COMPANY}_DATABASE_URL` | per-company Postgres DSN. Auto-detected when the YAML has no `knowledge.config.dsn_env`. Hyphens in company keys are uppercased + replaced with `_` (e.g. `widget-co` → `BRIAR_WIDGET_CO_DATABASE_URL`). |
| `<custom>` (when YAML sets `knowledge.config.dsn_env: MY_PG`) | reads the named env var as the DSN — fully explicit override |
| `BRIAR_NOTIFY_SINKS=telegram,slack` | scheduler failure alerts |
| `BRIAR_DEFAULT_STORE={envfile,infisical,vault,aws-secretsmanager,ssm}` | default `--store` for `briar auth login`. When set, credentials acquired interactively land here without `--store` on every invocation. |
| `BRIAR_SECRETS_FILE=/path/to/secrets.env` | override the secrets file path. Resolution order: this env var → `/etc/briar/secrets.env` (if exists) → `$XDG_CONFIG_HOME/briar/secrets.env` (or `~/.config/briar/secrets.env`) |
| `INFISICAL_CLIENT_ID` / `_SECRET` / `_PROJECT_ID` (+ optional `_ENV`, `_HOST`) | Infisical machine-identity. Drives both bootstrap (auto-hydrate at startup) AND `InfisicalStore` (`--store infisical` writes). Acquire interactively via `briar auth login infisical`. |
| `JIRA_{COMPANY}_AUTH_KIND={token,session}` | force a Jira auth strategy. Default = auto-detect (session wins when a session-token env var is set) |
| `JIRA_{COMPANY}_EMAIL` + `JIRA_{COMPANY}_TOKEN` | token-auth credentials (Atlassian-recommended) |
| `JIRA_{COMPANY}_SESSION_TOKEN` / `JIRA_{COMPANY}_TENANT_SESSION_TOKEN` | session-auth credentials (browser-extracted cookies). Either one alone is sufficient. |
| `JIRA_{COMPANY}_XSRF_TOKEN` / `JIRA_{COMPANY}_USER_AGENT` | optional session-auth extras |

---

## `briar version`

Prints client version. Takes no arguments.

```bash
briar version
# briar-cli 1.1.1
```

---

## `briar extract` — one-shot extraction

Run one or more extractors against external sources and write the
result to a knowledge blob.

```
briar extract --company <name> [--include <extractor>] ...
              [--storage {file,postgres}] [--root <dir>]
              [--provider {github,bitbucket}]
              [--tracker {jira,github-issues,bitbucket-issues,linear}]
              [--cloud {aws,gcp,azure}]
              [extractor-specific flags]
```

**Required:**
- `--company` — drives the markdown title + the blob name

**Pick which extractors to run** with `--include` (repeatable; default = all available):

| Extractor | What it mines | Backed by |
|---|---|---|
| `pr-archaeology` | merged-PR patterns, top reviewers | repo |
| `active-work` | open PRs across configured repos | repo |
| `github-deployments` | environments, deployments, CI runs | repo |
| `codebase-conventions` | language, test runner, linter, migration tool | repo |
| `reviewer-profile` | per-reviewer comment cadence + sample asks | repo |
| `code-hotspots` | files that change together (co-change clusters) | repo |
| `active-tickets` | open tickets per project | tracker |
| `ticket-archaeology` | closed-ticket patterns, top assignees | tracker |
| `aws-infra` | cloud resources (compute, databases, queues, logs) | cloud |

**Extractor-specific flags** (only relevant when the matching `--include` is set):

| Flag | Used by | Notes |
|---|---|---|
| `--pr-repo <slug>` | `pr-archaeology` | repeatable; `owner/repo` |
| `--pr-max <N>` | `pr-archaeology` | default 100 |
| `--pr-authors-allow` / `--pr-authors-block` | `pr-archaeology` | allow ∩ ¬block |
| `--pr-assignees-allow` / `--pr-assignees-block` | `pr-archaeology` | |
| `--active-repo <slug>` | `active-work` | repeatable |
| `--active-authors-allow` / `--active-authors-block` | `active-work` | filter open PRs |
| `--deploy-repo <slug>` | `github-deployments` | repeatable |
| `--conventions-repo <slug>` | `codebase-conventions` | repeatable |
| `--reviewer-repo <slug>` | `reviewer-profile` | repeatable |
| `--reviewer-pr-sample <N>` | `reviewer-profile` | default 20 |
| `--reviewer-top-n <N>` | `reviewer-profile` | default 5 |
| `--hotspots-repo <slug>` | `code-hotspots` | repeatable |
| `--hotspots-since-days <N>` | `code-hotspots` | default 30 |
| `--hotspots-max-commits <N>` | `code-hotspots` | default 100 |
| `--hotspots-top-n <N>` | `code-hotspots` | default 10 |
| `--ticket-project <key>` | `active-tickets` | repeatable; Jira project / Linear team key / `owner/repo` for GH+BB Issues |
| `--ticket-archaeology-project <key>` | `ticket-archaeology` | repeatable |
| `--ticket-max <N>` | `ticket-archaeology` | default 100 |
| `--aws-extract-region <region>` | `aws-infra` | default `us-east-1` |
| `--aws-extract-service <svc>` | `aws-infra` | one of `ecs lambda logs rds sqs`; repeatable |
| `--aws-extract-profile <name>` | `aws-infra` | local AWS profile; falls back to per-company env vars |

**Storage flags** (apply to every extraction):

| Flag | Purpose |
|---|---|
| `--storage {file,postgres}` | default `file` |
| `--blob-name <name>` | default `knowledge:<company>` |
| `--root <dir>` | file-store root (default `./knowledge`) |
| `--out-json <path>` | parallel JSON output (empty = skip) |

**Examples:**

```bash
# Just PR archaeology against one GitHub repo
briar extract --company acme \
    --include pr-archaeology \
    --pr-repo acme-co/acme-app --pr-max 50

# PRs + AWS infra in one shot, filter to team members only
briar extract --company acme \
    --include pr-archaeology --include aws-infra \
    --pr-repo acme-co/acme-app \
    --pr-authors-allow alice --pr-authors-allow bob \
    --aws-extract-region us-east-1 \
    --aws-extract-service ecs --aws-extract-service rds

# Bitbucket repo + Jira tickets
briar extract --company acme \
    --provider bitbucket --tracker jira \
    --include pr-archaeology --include active-tickets \
    --pr-repo acme/api \
    --ticket-project ACME

# Hotspots against a GitHub repo, 60-day window
briar extract --company acme \
    --include code-hotspots \
    --hotspots-repo acme-co/acme-app \
    --hotspots-since-days 60

# Write to Postgres instead of files
BRIAR_DATABASE_URL=postgresql://... briar extract --company acme \
    --include active-work --active-repo acme-co/acme-app \
    --storage postgres
```

---

## `briar runbook` — scheduled extraction

Three subcommands. All take a YAML file or a directory of YAMLs.
See [`examples/all_features.yaml`](examples/all_features.yaml) for
the comprehensive multi-company reference; [`examples/multi_company.yaml`](examples/multi_company.yaml)
is the lighter-touch tutorial.

### `briar runbook extract <file.yaml>`

Walks a runbook YAML's `schedules:` once and writes per-company
knowledge files. Exits after one pass.

| Flag | Purpose |
|---|---|
| `--task <name>` | run only the schedule whose `task:` field matches |

```bash
# Run everything in one runbook
briar runbook extract examples/all_features.yaml

# Run only the `prfix` task across every company in the runbook
briar runbook extract examples/all_features.yaml --task prfix
```

### `briar runbook sweep <directory>`

Runs `extract` for every `*.yaml` in the directory. One-shot.

```bash
briar runbook sweep examples/
```

### `briar runbook serve <directory>`

Long-running scheduler. Registers every `(company, task)` from every
YAML in the directory and runs the schedule loop forever. This is
what runs persistently on the droplet.

| Flag | Purpose |
|---|---|
| `--tick <seconds>` | scheduler tick interval (default 1) |

```bash
briar runbook serve examples/

# Tighter polling for low-cadence schedules
briar runbook serve examples/ --tick 5
```

---

## `briar agent` — autonomous LLM flows

Two ops. Each clones a worktree, fetches JIT context for the specific
ticket/PR, and drives an LLM tool-use loop until completion.

### `briar agent prfix`

Address unresolved review comments + failing CI on one PR.

| Flag | Required | Purpose |
|---|---|---|
| `--company <name>` | ✓ | matches a runbook YAML |
| `--owner <name>` | ✓ | repo owner |
| `--repo <name>` | ✓ | repo name |
| `--pr <N>` | ✓ | PR number |
| `--branch <name>` | ✓ | PR head branch |
| `--store {file,postgres}` | | knowledge store |
| `--knowledge <dir>` | | file-store root |
| `--runbook <yaml>` | | binds the `send_message` tool to the company's `messages:` block |
| `--dry-run` | | print rendered prompt + tool list, skip LLM call |
| `--model <name>` | | override Anthropic model |
| `--max-iter <N>` | | iteration ceiling |
| `--git-user-name` / `--git-user-email` | | commit identity. Per-field resolution: CLI flag > YAML `companies.<name>.git_identity.{name,email}` (when `--runbook` is set) > hardcoded `iklobato` default. |
| `--keep-worktree` | | leave `/tmp/...` after run |

```bash
briar agent prfix \
    --company acme --owner acme-co --repo acme-app \
    --pr 42 --branch fix-typo \
    --runbook examples/all_features.yaml

# Validate the rendered prompt without spending tokens
briar agent prfix \
    --company acme --owner acme-co --repo acme-app \
    --pr 42 --branch fix-typo \
    --dry-run
```

### `briar agent implement`

Implement one ticket end-to-end: clones default branch, fetches
ticket-context, agent branches + commits + opens a draft PR.

| Flag | Required | Purpose |
|---|---|---|
| `--company <name>` | ✓ | |
| `--owner <name>` | ✓ | repo owner / Bitbucket workspace |
| `--repo <name>` | ✓ | |
| `--ticket-project <key>` | ✓ | Jira `PROJ` / Linear team / `owner/repo` for GH+BB Issues |
| `--ticket-key <key>` | ✓ | `PROJ-123` / `#42` / `ENG-7` |
| `--tracker {jira,github-issues,bitbucket-issues,linear}` | | default `jira` |
| `--provider {github,bitbucket}` | | default `github` |
| `--runbook <yaml>` | | binds `send_message` tool |
| `--dry-run` | | print rendered prompt, skip LLM call |
| `--store` / `--knowledge` / `--model` / `--max-iter` | | as above |
| `--git-user-name` / `--git-user-email` / `--keep-worktree` | | as above |

```bash
briar agent implement \
    --company acme --owner acme-co --repo acme-app \
    --ticket-project ACME --ticket-key ACME-42 \
    --tracker jira \
    --runbook examples/all_features.yaml

# Bitbucket repo, Linear tickets
briar agent implement \
    --company bitspark --owner bitspark --repo api \
    --ticket-project ENG --ticket-key ENG-7 \
    --provider bitbucket --tracker linear \
    --runbook examples/all_features.yaml
```

---

## `briar scaffold` — JSON config bundles for downstream tools

Emits a JSON bundle that a downstream orchestrator can consume. Two
templates today.

### `briar scaffold implementation`

Issue → plan → human approval → implement / comment.

| Flag | Required | Purpose |
|---|---|---|
| `--prefix <name>` | ✓ | prepended to every resource key |
| `--source {github,bitbucket,jira,aws}` | | repeatable; selects which sources contribute |
| `--archetype <name>` | | default `engineer`; one of `engineer`, `pr-fixer`, `pr-ci-fixer`, `pr-conflict-resolver`, `triager` |
| `--shape <name>` | | default `plan-approve-act`; one of `plan-approve-act`, `one-shot`, `triage` |
| `--trigger-kind <name>` | | default `github_webhook`; one of `github_webhook`, `bitbucket_webhook`, `schedule_cron`, `manual` |
| `--owner` / `--repo` | when `--source github` | GitHub identity |
| `--bitbucket-workspace` / `--bitbucket-repo` | when `--source bitbucket` | Bitbucket identity |
| `--auth-mode {oauth,pat}` | | default `oauth` |
| `--github-secret-id` / `--bitbucket-secret-id` | with `--auth-mode pat` | |
| `--company <name>` | | splice the company's extracted knowledge into the agent's system_prompt |
| `--model <name>` / `--llm-provider-key <key>` | | LLM defaults baked into the bundle |
| `--out <path>` | | write to file (default: stdout) |

```bash
# GitHub source, OAuth, draft PR after plan-approve flow
briar scaffold implementation \
    --prefix acme-impl \
    --source github \
    --owner iklobato --repo lightapi

# Bitbucket source, app-password auth, hourly cron
briar scaffold implementation \
    --prefix acme-impl \
    --source bitbucket \
    --bitbucket-workspace acme --bitbucket-repo widgets \
    --auth-mode pat --bitbucket-secret-id <uuid> \
    --trigger-kind schedule_cron --schedule "0 * * * *"

# Multi-source (GitHub + Jira + AWS), one-shot agent
briar scaffold implementation \
    --prefix acme-hourly \
    --source github --source jira --source aws \
    --owner iklobato --repo lightapi \
    --shape one-shot --out acme-hourly.json
```

### `briar scaffold pr-fixes`

PR review-comment sweep (no human gate). Same flags as `implementation`
but archetype defaults to `pr-fixer` and shape to `one-shot`.

```bash
briar scaffold pr-fixes \
    --prefix acme-prfix \
    --source github \
    --owner iklobato --repo lightapi \
    --trigger-kind schedule_cron --schedule "0 * * * *"
```

---

## `briar context` — local markdown CRUD

Read/write named blobs in the knowledge store. Same store the
extractors write to. Blob names follow `category:identifier`.

**Common flags:**

| Flag | Purpose |
|---|---|
| `--store {file,postgres}` | default `file` |
| `--root <dir>` | file-store root (default `./knowledge`) |

### `briar context put <name>`

| Flag | Purpose |
|---|---|
| `--content <text>` | inline content; pass `-` to read from stdin |
| `--from-file <path>` | read from file |
| `--category <name>` | override the derived category prefix |

```bash
briar context put knowledge:acme --from-file knowledge/acme.md
briar context put memory:reviewer-iklobato --content "Focuses on typing rigor"
briar context put lessons:python-typing --content - < lessons/typing.md
```

### `briar context get <name>`

Prints the markdown body to stdout. No flags.

```bash
briar context get knowledge:acme
briar context get memory:reviewer-iklobato
```

### `briar context list`

| Flag | Purpose |
|---|---|
| `--prefix <s>` | filter to blobs whose name starts with `<s>` |

```bash
briar context list
briar context list --prefix lessons:
```

### `briar context delete <name>`

| Flag | Purpose |
|---|---|
| `--yes` | skip confirmation |

```bash
briar context delete memory:stale --yes
```

### `briar context categories`

Prints distinct category prefixes. No flags.

```bash
briar context categories
```

---

## `briar dashboard` — read-only HTML status page

Runs an HTTP server with the system status across 24 collector
sections. GET-only by construction.

| Flag | Default | Purpose |
|---|---|---|
| `--host <ip>` | `0.0.0.0` | bind address |
| `--port <n>` | `8080` | bind port |
| `--examples <dir>` | `./examples` | runbook YAML directory |
| `--knowledge-store {file,postgres}` | postgres if `BRIAR_DATABASE_URL` else file | |
| `--knowledge <dir>` | `./knowledge` | file-store root |
| `--log-file <path>` | `/var/log/briar/scheduler.log` | scheduler log to tail |
| `--disk-path <path>` | `/` | which mount to size-watch |
| `--repo-path <dir>` | `.` | git repo to show deploy status from |
| `--secrets-file <path>` | `/etc/briar/secrets.env` | for the secrets-name (no values) panel |
| `--du-path <dir>` | (repeatable) | extra directories to track disk usage on |
| `--once` | — | render once and exit (smoke test) |

```bash
# Standard production invocation
briar dashboard --host 0.0.0.0 --port 8080 \
    --examples ./examples --knowledge ./knowledge --repo-path .

# Postgres-backed knowledge store
BRIAR_DATABASE_URL=postgresql://... briar dashboard --host 127.0.0.1

# Smoke test — render the HTML once and exit
briar dashboard --once > /tmp/dashboard.html
```

---

## `briar auth` — interactive credential acquisition

Five subcommands. The thing-you're-logging-into is the **positional
target** (like `gh auth login`, `vault login`, `op signin`). `--store`
controls *where the resulting credentials are persisted* and defaults
to `$BRIAR_DEFAULT_STORE` then to `envfile`.

### Targets (registered acquirers)

| Target | Flow | Writes (per company) |
|---|---|---|
| `github-pat` | Paste a Personal Access Token | `GITHUB_TOKEN` |
| `github-device` | OAuth device flow (needs `BRIAR_GITHUB_CLIENT_ID`) | `GITHUB_TOKEN` |
| `bitbucket-app-password` | Paste workspace + username + app password | `BITBUCKET_{c}_WORKSPACE` / `_USERNAME` / `_APP_PASSWORD` |
| `aws-static` | Paste static IAM access key | `AWS_{c}_ACCESS_KEY_ID` / `_SECRET_ACCESS_KEY` / `_REGION` |
| `aws-sso` | IAM Identity Center OIDC device-code flow → STS vend | `AWS_{c}_*` (+ records expiry) |
| `jira-token` | Paste API token | `JIRA_{c}_URL` / `_EMAIL` / `_TOKEN` / `_AUTH_KIND=token` |
| `jira-session` | DevTools cookie extraction walkthrough | `JIRA_{c}_URL` / `_TENANT_SESSION_TOKEN` / `_AUTH_KIND=session` |
| `linear-api-key` | Paste personal API key | `LINEAR_{c}_TOKEN` |
| `infisical` | Bootstrap — paste machine-identity creds. Always persists to envfile regardless of `--store`. | `INFISICAL_CLIENT_ID` / `_CLIENT_SECRET` / `_PROJECT_ID` / `_ENV` / `_HOST` |

### Stores (registered persistence backends)

| Store | Notes |
|---|---|
| `envfile` | Resolves to `$BRIAR_SECRETS_FILE` → `/etc/briar/secrets.env` (if exists) → `~/.config/briar/secrets.env`. Atomic replace-in-place. |
| `infisical` | Universal-auth machine identity (configure via `briar auth login infisical` first) |
| `vault` | HashiCorp Vault KV v2 (needs `VAULT_ADDR` + `VAULT_TOKEN`) |
| `aws-secretsmanager` | One secret per name under `briar/` prefix |
| `ssm` | SSM Parameter Store, `SecureString`, `/briar/` prefix |

### `briar auth login <target>`

```
briar auth login <target> [--company <name>] [--store <kind>]
```

| Flag | Purpose |
|---|---|
| `target` (positional, required) | What to log into — one of the targets above |
| `--company <name>` | Per-company namespace (required for vendor targets; ignored for `infisical`) |
| `--store <kind>` | Destination. Default = `$BRIAR_DEFAULT_STORE` or `envfile`. **Ignored** when target is a bootstrap flow (`infisical`) — those always land in envfile, with a warning if you passed something else. |

```bash
# Bootstrap a password manager (one-time, per laptop)
briar auth login infisical
briar auth login vault    # (when VaultLoginFlow lands — placeholder for now)

# Vendor credentials → land in envfile
briar auth login github-pat --company acme
briar auth login aws-sso --company acme

# Vendor credentials → land in Infisical
briar auth login github-pat --company acme --store infisical
briar auth login aws-sso --company acme --store infisical

# Pick a default store once, never re-type --store
export BRIAR_DEFAULT_STORE=infisical
briar auth login jira-session --company acme
```

### `briar auth logout <target>`

Removes every env-var name the target would write. Confirms unless `--yes`.

```bash
briar auth logout aws-sso --company acme --yes
briar auth logout infisical                   # removes the machine identity (forgets the connection)
```

### `briar auth refresh <target>`

Renews short-lived bundles without re-prompting. Paste-based targets (PATs, app passwords, Jira API tokens, Jira session cookies, Infisical machine identity) raise `CredentialExpired` → re-run `login`.

```bash
briar auth refresh aws-sso --company acme   # vends fresh STS creds from cached SSO token
```

### `briar auth list [--store <kind>] [--company <name>]`

Enumerates the credential names held in the chosen store. Names only — never values.

```bash
briar auth list --store envfile
briar auth list --store infisical --company acme
```

### `briar auth status <target>`

Per-key `ok` / `MISS` report for the bundle a target writes. Exits non-zero on any miss.

```bash
briar auth status aws-sso --company acme
briar auth status jira-session --company acme --store infisical
```

---

## `briar secrets` — credential coverage + remote-vault hydrate

Two subcommands.

### `briar secrets doctor`

Walks every runbook YAML's `schedules:` and `messages:` blocks. For
each `(company, extractor, provider)` and `(company, messages,
writer)` tuple, queries the provider/writer's `required_env_vars(company)`
classmethod and reports `ok` / `X MISSING:` per row against the
chosen credential store. Values are never printed.

| Flag | Default | Purpose |
|---|---|---|
| `--examples <dir>` | `./examples` | runbook YAML directory |
| `--store {envfile,aws-secretsmanager,ssm,vault}` | `envfile` | which credential backend to audit against |

```bash
# Default: audit env vars
briar secrets doctor --examples examples/

# Audit against AWS Secrets Manager (paths under /briar/<NAME>)
briar secrets doctor --store aws-secretsmanager

# Audit a single YAML directory
briar secrets doctor --examples examples/
```

Exits non-zero when any row is missing.

### `briar secrets bootstrap`

Fetches secrets from a remote vault and writes them to `os.environ`.
Normally runs automatically at CLI startup via `auto_bootstrap()`;
this subcommand is for testing.

| Flag | Purpose |
|---|---|
| `--kind {infisical}` | force a backend; default is auto-detect via `is_available()` |
| `--dry-run` | run the fetch but DON'T write to env; prints keys that would be set |

```bash
# Auto-detect (runs Infisical if INFISICAL_CLIENT_ID is set)
briar secrets bootstrap

# Dry-run — see what would be hydrated without leaking values
briar secrets bootstrap --dry-run

# Force one backend
briar secrets bootstrap --kind infisical
```

Operator-supplied env vars take precedence over the vault — already-set
keys are preserved (reported as `skipped`).

---

## How the pieces fit together

Three command families, three concerns. Each diagram shows what a
command reads, what it invokes, and what comes out — so you can
predict the blast radius of a change.

### `briar runbook serve <dir>` — the long-running scheduler

```
            ┌──────────────────────────────────────────┐
            │ briar runbook serve companies/ --tick 5  │
            └─────────────────────┬────────────────────┘
                                  │
        reads at startup          │           reads at every fire
   ┌──────────────────────┐       │       ┌────────────────────────┐
   │ companies/*.yaml     │       │       │ /etc/briar/secrets.env │
   │  (CompanyEntry +     │◄──────┴──────►│  per-fire env vars     │
   │   ScheduleEntry)     │               │  (GITHUB_TOKEN,        │
   └──────────────────────┘               │   JIRA_*, AWS_*, ...)  │
                                          └────────────────────────┘
                                  │
                                  ▼
                     ┌─────────────────────────┐
                     │  scheduler loop (tick)  │
                     │  • registers cron-ish   │
                     │    jobs per ScheduleEntry│
                     │  • fires due jobs       │
                     └────────────┬────────────┘
                                  │
                                  ▼
                     ┌─────────────────────────┐
                     │ RunbookExecutor.extract │
                     └────────────┬────────────┘
                                  │
            ┌─────────────────────┼─────────────────────┐
            ▼                     ▼                     ▼
   ┌────────────────┐  ┌────────────────────┐  ┌──────────────────┐
   │ EXTRACTORS[..] │  │ KnowledgeComposer  │  │ make_store(...)  │
   │  for each      │──▶  .markdown(...)    │──▶  .put_if_changed │
   │  ExtractEntry  │  │ assembles sections │  │   (md5 compare-  │
   │  in schedule   │  │ into one blob      │  │    and-set)      │
   └───────┬────────┘  └────────────────────┘  └────────┬─────────┘
           │                                            │
           ▼                                            ▼
   ┌────────────────┐                          ┌──────────────────┐
   │ provider_class │                          │ KnowledgeStore   │
   │   _for(args)   │                          │  • StoreFile     │
   │  ┌────────────┐│                          │  • StorePostgres │
   │  │ Repository ││                          └──────────────────┘
   │  │ Tracker    ││                                   │
   │  │ Cloud      ││                                   ▼
   │  └────────────┘│                          DO managed PG / files
   └────────────────┘                          ./knowledge/*.md
```

A change in `companies/*.yaml` is picked up on the **next** schedule
fire because the executor re-loads the YAML on every iteration. Code
changes need a scheduler restart (the `briar` Python process caches
imported modules).

### `briar runbook extract <file.yaml>` — one-shot

```
   briar runbook extract companies/acme.yaml [--task tickets]
                              │
                              ▼
                  Same executor path as `serve`,
                  but runs once and exits.
                  --task filters which ScheduleEntry to fire.
```

Useful for manual smoke tests against a specific task (e.g.
verifying Jira session auth before letting the scheduler run for
24h on its own cadence).

### `briar agent prfix` / `briar agent implement` — autonomous LLM

```
       ┌─────────────────────────────────────────────────────┐
       │ briar agent prfix --company acme                  │
       │   --owner acme-co --repo acme-app              │
       │   --pr 42 --branch feature/x                        │
       │   --runbook companies/acme.yaml                   │
       └────────────────────────┬────────────────────────────┘
                                │
        ┌───────────────────────┼────────────────────────┐
        ▼                       ▼                        ▼
   ┌──────────┐         ┌────────────┐         ┌─────────────────┐
   │ secrets  │         │ companies/ │         │ KnowledgeStore  │
   │ .env     │         │ acme.yaml│         │ .get("knowledge:│
   │ • GITHUB │         │ • messages │         │      acme")   │
   │ • JIRA_* │         │ • git_id   │         │  (previously    │
   │ • CLAUDE │         └─────┬──────┘         │  written by     │
   └────┬─────┘               │                │  serve)         │
        │                     ▼                └────────┬────────┘
        │           ┌─────────────────┐                 │
        │           │_resolve_git_id  │                 │
        │           │  (CLI > YAML >  │                 │
        │           │   default)      │                 │
        │           └────────┬────────┘                 │
        │                    │                          │
        ▼                    ▼                          ▼
   ┌──────────────────────────────────────────────────────────┐
   │ RepoCloner.clone(branch) → /tmp/<worktree>               │
   │   • sets user.name + user.email on the clone             │
   └────────────────────────────┬─────────────────────────────┘
                                │
                                ▼
   ┌──────────────────────────────────────────────────────────┐
   │ FetchPrContext (JIT extractor: reads PR + review thread) │
   └────────────────────────────┬─────────────────────────────┘
                                │
                                ▼
   ┌──────────────────────────────────────────────────────────┐
   │ AgentRunner (Anthropic API + tool-use loop)              │
   │   tools available:                                       │
   │     • bash, read_file, write_file, edit_file             │
   │     • send_message ←──┐                                  │
   └────────────┬──────────│──────────────────────────────────┘
                │          │
                │          │ resolved via messages: block:
                │          │   handle → MessageWriter
                │          │     ├── jira-comment / jira-transition
                │          │     │     (uses JiraAuthStrategy)
                │          │     ├── github-pr-comment
                │          │     ├── bitbucket-pr-comment
                │          │     ├── slack-channel
                │          │     └── telegram-chat
                │          │
                ▼
       commits + push via the same RepositoryProvider
       used in the scheduler — closes the loop.
```

`briar agent implement` is the same shape, replacing
`FetchPrContext` with `FetchTicketContext` (which reads from the
TrackerProvider for the company's chosen tracker).

### DSN resolution — `knowledge.store: postgres`

```
KnowledgeBinding (from YAML)
   │
   │   knowledge:
   │     store: postgres
   │     config:
   │       dsn_env: BRIAR_KB_DATABASE_URL   ← explicit
   │
   ▼
StoreBinding(company="acme", config={...})
   │
   ▼
StorePostgres.from_binding(binding):
   ┌──────────────────────────────────────────────────────────┐
   │ 1. binding.config["dsn_env"]      → ${BRIAR_KB_DATABASE_URL}
   │ 2. BRIAR_{COMPANY}_DATABASE_URL   → ${BRIAR_ACME_DATABASE_URL}
   │ 3. BRIAR_DATABASE_URL             → ${BRIAR_DATABASE_URL}
   │ 4. CliError naming all 3 keys tried, in order
   └──────────────────────────────────────────────────────────┘
   │
   ▼
returns psycopg-backed StorePostgres instance
```

The first non-empty env var wins. Every downstream caller
(`scheduler`, `briar context`, `briar agent`'s `KnowledgeStore.get`)
goes through the same factory — no parallel resolution paths.

### Jira auth strategy chain

```
JiraTracker(company="acme")
   │
   ▼
JiraAuthRegistry.autodetect(company="acme"):
   ┌──────────────────────────────────────────────────────────┐
   │ 1. JIRA_{COMPANY}_AUTH_KIND env (explicit override)      │
   │     "token"   → JiraTokenAuth                            │
   │     "session" → JiraSessionAuth                          │
   │ 2. JIRA_{COMPANY}_SESSION_TOKEN OR _TENANT_SESSION_TOKEN │
   │    set        → JiraSessionAuth                          │
   │ 3. fallback   → JiraTokenAuth                            │
   └──────────────────────────────────────────────────────────┘
   │
   ▼
strategy.configure(company, base_url)
   │
   ├── token   →  {username, password}        ── HTTP Basic
   │
   └── session →  {session: requests.Session(
                    cookies={cloud.session.token, tenant.session.token,
                             atlassian.xsrf.token},
                    headers={Origin, Referer, User-Agent,
                             sec-ch-ua-*, sec-fetch-*,
                             X-Atlassian-Token: no-check})}
                  ── browser-mimicking
   │
   ▼
atlassian.Jira(url=..., cloud=True, **kwargs)
```

The same `JiraTracker` is used by both the scheduler's
`active-tickets` / `ticket-archaeology` extractors AND the agent's
`jira-comment` / `jira-transition` message writers — so a working
session auth for one path is a working session auth for the other.

### `briar auth login <target>` — acquisition + persistence

```
                briar auth login <target> [--company X] [--store Y]
                                  │
              positional target   │   --store decides persistence
                  resolves to     │   IF the target's policy is EXTERNAL
                  ▼               │   (forced to envfile if BOOTSTRAP_LOCAL)
          ┌─────────────────┐     │
          │ AcquirerRegistry│     │
          │  .make(target)  │     │
          └────────┬────────┘     │
                   │              │
                   ▼              │
          ┌─────────────────┐     │
          │ acquirer.acquire│     │
          │  (company,      │     │
          │   prompt)       │     │
          └────────┬────────┘     │
                   │              │
                   ▼              │
              Credentials         │
            (provider_kind,       │
             entries dict,        │
             expires_at)          │
                   │              │
                   ▼              │
        ┌───────────────────┐     │
        │ _effective_store  │◄────┘
        │ honours policy:   │
        │  EXTERNAL → as-is │
        │  BOOTSTRAP_LOCAL  │
        │   → "envfile"     │
        └─────────┬─────────┘
                  │
                  ▼
        ┌──────────────────────┐
        │ CredentialStore      │
        │  .write(name, value) │
        │   for each entry     │
        │                      │
        │ Backends:            │
        │  • EnvFileStore      │
        │  • InfisicalStore    │
        │  • VaultStore        │
        │  • AwsSecretsMgr     │
        │  • SsmParameterStore │
        └──────────────────────┘
```

The target's `destination_policy` ClassVar splits two flavours:
- **EXTERNAL** (default) — vendor credentials (GitHub, AWS, Jira,
  Linear, Bitbucket). The operator picks `--store` freely.
- **BOOTSTRAP_LOCAL** — the credentials *describe how to reach a
  store* (Infisical machine identity; future `VaultLoginFlow`).
  Must persist to envfile or the bootstrap is unrecoverable
  (chicken-and-egg). The CLI logs a warning if `--store` is ignored.

Adding a new acquirer = one class + one registry entry. Adding a
new store = one class + one registry entry. Adding a new
destination policy (rare — e.g. "must persist to keychain") = one
enum value + one branch in `_effective_store_kind`.

### What invalidates what

| You changed... | Restart needed | Effect |
|---|---|---|
| `companies/*.yaml` | no (next fire) | scheduler re-reads on every tick |
| `/etc/briar/secrets.env` | yes | scheduler holds env in process memory |
| `src/briar/` (editable install) | yes | imported modules are cached |
| Postgres `briar_knowledge` table | no | scheduler reads fresh on each fire |
| Jira session-token cookie | no — but log it | scheduler reads from env at startup; restart picks up rotation |

---

## Releases

Releases are fully automated. Every push to `main` triggers
[`.github/workflows/release.yml`](.github/workflows/release.yml), which:

1. Bumps the patch version in `pyproject.toml` (1.1.1 → 1.1.2 → …).
2. Builds the sdist + wheel with `uv build`.
3. Publishes to PyPI via `pypa/gh-action-pypi-publish` using the
   `PYPI_API_TOKEN` repo secret.
4. Commits the bump as `chore(release): vX.Y.Z` and pushes a matching
   `vX.Y.Z` tag back to `main`.

The workflow guards against its own bump commit re-triggering itself by
skipping when `github.event.head_commit.message` starts with
`chore(release):`. `concurrency: release` serializes overlapping merges.

No manual `git tag` or `twine upload` step. To publish a release without
a merge, use the workflow's `workflow_dispatch` trigger from the Actions
tab.

---

## Examples + further reading

- `companies/` (gitignored — keep your real runbooks here so they
  don't leak into the public repo). Recommended pattern: one YAML
  per company, all with `knowledge.config.dsn_env: BRIAR_KB_DATABASE_URL`
  so they share a managed-Postgres knowledge store with row-level
  partitioning by the `company` column.
- [`examples/all_features.yaml`](examples/all_features.yaml) — every
  abstraction × provider × writer combination across 4 companies.
  Schema reference for `knowledge.config`, `messages:`, `git_identity:`,
  and the Jira auth-strategy selector.
- [`examples/multi_company.yaml`](examples/multi_company.yaml) +
  [`.env.example`](examples/multi_company.env.example) — 3-company
  tutorial without the `messages:` block
- [`examples/acme.yaml`](examples/acme.yaml) — real deployment
  (Bitbucket workspace token + AWS instance role)
- [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) — per-provider
  credential acquisition guide (incl. Jira API token AND
  browser-session-cookie paths)
- [`DEPLOY_EC2.md`](DEPLOY_EC2.md) — systemd deployment recipe
- [`ARCHITECTURE.md`](ARCHITECTURE.md) +
  [`ARCHITECTURE_DEEP.md`](ARCHITECTURE_DEEP.md) — abstraction
  inventory + SOLID audit
