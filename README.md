# briar — local extraction + scheduling CLI

Python CLI that mines live state from external systems (GitHub,
Bitbucket, AWS, GCP, Azure, Jira, Linear, Fireflies, …), schedules
per-company extraction in-process, and runs autonomous LLM-driven
agents against the resulting knowledge.

Everything runs locally — no `api.usebriar.com` service, no remote
workspace. Each command shells out to the external APIs directly
(via PyGithub, atlassian-python-api, boto3, anthropic, etc.) and
writes its output to local markdown files or a Postgres knowledge
store.

```
briar version
briar-cli 1.1.11   # or whatever the installed version is — read from package metadata
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
Fireflies meeting transcripts, Anthropic LLM, AWS Bedrock LLM,
all 6 message writers (jira-comment, jira-transition, slack-channel,
telegram-chat, github-pr-comment, bitbucket-pr-comment), all 4
notification sinks, file + postgres knowledge stores, AWS Secrets
Manager / SSM credential stores.

Python 3.10+. Tested through 3.12.

---

## Commands

```
briar version
briar extract       — one-shot extraction
briar runbook       — scheduled extraction (extract / sweep / serve)
briar agent         — autonomous LLM-driven flows (prfix / implement)
briar plan          — LLM-driven implementation plans from a tracker board (build / show / status / next / advance / run / list / clear)
briar scaffold      — emit JSON config bundles for downstream tools
briar context       — read/write local markdown blobs
briar dashboard     — read-only HTML status page
briar auth          — interactive credential acquisition (login / logout / refresh / list / status)
briar secrets       — credential coverage (doctor / bootstrap)
briar journal       — inspect decision-journal sessions (list / show / export)
briar telemetry     — error + usage analytics to Sentry (status / preview / off / errors-only / full / reset)
```

**Telemetry quick note.** briar ships with opt-out telemetry enabled
by default (errors + usage analytics, sent to Sentry). The first run
prints a one-time banner. Disable via `briar telemetry off`,
`BRIAR_TELEMETRY=off`, or `DO_NOT_TRACK=1`. No prompts, file contents,
ticket keys, repo names, paths, or env values ever leave the machine.
See [`agents/telemetry.md`](agents/telemetry.md) for the full
collected/never-collected matrix.

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
| `BRIAR_JOURNAL=off` | disable the decision journal entirely (default: on) |
| `BRIAR_JOURNAL_STORE={file}` | system-of-record backend (default `file`) |
| `BRIAR_JOURNAL_SINKS=file` | comma-separated publish sinks (default `file`, writes a markdown summary per session) |
| `BRIAR_JOURNAL_ROOT=./journal` | filesystem root for the file store + file sink |
| `BRIAR_DEFAULT_STORE={envfile,infisical,vault,aws-secretsmanager,ssm}` | default `--store` for `briar auth login`. When set, credentials acquired interactively land here without `--store` on every invocation. |
| `BRIAR_SECRETS_FILE=/path/to/secrets.env` | override the secrets file path. Resolution order: this env var → `/etc/briar/secrets.env` (if exists) → `$XDG_CONFIG_HOME/briar/secrets.env` (or `~/.config/briar/secrets.env`) |
| `INFISICAL_CLIENT_ID` / `_SECRET` / `_PROJECT_ID` (+ optional `_ENV`, `_HOST`) | Infisical machine-identity. Drives both bootstrap (auto-hydrate at startup) AND `InfisicalStore` (`--store infisical` writes). Acquire interactively via `briar auth login infisical`. |
| `JIRA_{COMPANY}_AUTH_KIND={token,session}` | force a Jira auth strategy. Default = auto-detect (session wins when a session-token env var is set) |
| `JIRA_{COMPANY}_EMAIL` + `JIRA_{COMPANY}_TOKEN` | token-auth credentials (Atlassian-recommended) |
| `JIRA_{COMPANY}_SESSION_TOKEN` / `JIRA_{COMPANY}_TENANT_SESSION_TOKEN` | session-auth credentials (browser-extracted cookies). Either one alone is sufficient. |
| `JIRA_{COMPANY}_XSRF_TOKEN` / `JIRA_{COMPANY}_USER_AGENT` | optional session-auth extras |
| `FIREFLIES_{COMPANY}_API_KEY` | Fireflies.ai personal API key — drives both the scheduled `meeting-digest` extractor and the JIT `meeting-context` extractor. Acquire from your Fireflies workspace dashboard. |

---

## `briar version`

Prints client version. Takes no arguments.

```bash
briar version
# briar-cli 1.1.11   (or whichever version the installed wheel was built from)
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
              [--meeting {fireflies}]
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
| `meeting-digest` | recent meetings: summaries + action items | meeting |

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
| `--meeting {fireflies}` | `meeting-digest` (and `meeting-context` JIT) | default `fireflies` |
| `--meeting-since-days <N>` | `meeting-digest` | how many days back to scan; default 7 |
| `--meeting-max <N>` | `meeting-digest` | cap on meetings in the digest; default 25 |
| `--meeting-attendee-allow <email>` | `meeting-digest` | repeatable; only include meetings whose attendees overlap. Empty = no filter |

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

# Pull last 14 days of Fireflies meeting summaries for an attendee list
FIREFLIES_ACME_API_KEY=ff_xxx briar extract --company acme \
    --include meeting-digest \
    --meeting fireflies \
    --meeting-since-days 14 --meeting-max 50 \
    --meeting-attendee-allow alice@acme.com \
    --meeting-attendee-allow bob@acme.com
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
| `--meeting {fireflies}` | | meeting provider (default `fireflies`); requires `FIREFLIES_{c}_API_KEY` |
| `--meeting-key <id>` | | splice ONE specific meeting's full transcript into the agent prompt |
| `--meeting-query <text>` | | keyword search across transcripts. When omitted, defaults to `owner/repo#pr` so meetings that mentioned the PR surface automatically |
| `--meeting-top-k <N>` | | max meetings to fetch in search mode (default 3) |
| `--meeting-max-bytes <N>` | | per-meeting transcript byte cap (default 50 000) |

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

# Pin a specific meeting transcript into the agent's context
# (use when a reviewer's comment cites "as discussed Thursday")
briar agent prfix \
    --company acme --owner acme-co --repo acme-app \
    --pr 42 --branch fix-typo \
    --runbook examples/all_features.yaml \
    --meeting-key 01HABCDEF...
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
| `--meeting {fireflies}` | | meeting provider (default `fireflies`); requires `FIREFLIES_{c}_API_KEY` |
| `--meeting-key <id>` | | splice ONE specific meeting's full transcript into the agent prompt |
| `--meeting-query <text>` | | keyword search across transcripts. When omitted, defaults to the ticket key so meetings that mentioned `ACME-123` surface automatically |
| `--meeting-top-k <N>` | | max meetings to fetch in search mode (default 3) |
| `--meeting-max-bytes <N>` | | per-meeting transcript byte cap (default 50 000) |

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

# Override the auto-derived meeting query (default = the ticket key)
# when the standup discussed the topic by feature name, not key
briar agent implement \
    --company acme --owner acme-co --repo acme-app \
    --ticket-project ACME --ticket-key ACME-42 \
    --runbook examples/all_features.yaml \
    --meeting-query "oauth refresh token rollout"
```

---

## `briar plan` — LLM-driven implementation plans

Take a tracker board (Jira board or GitHub Projects v2), pull every
card, synthesise per-card scope / out-of-scope / risks / dependencies,
and persist the result as a markdown + JSON blob in the chosen
`KnowledgeStore`. The implementer flow (`briar agent implement`) and
any operator can then ask `briar plan next` for the LLM selector's
choice of what to do next — pick a card, replan, complete, or stop.

The dependency-graph picker is gone. There is no `topological_sort`,
no `apply_cascade`, no `next_pending` algorithm. Cards are kept in
board order; the LLM reads the full state (past completed cards,
failed cards with their last attempt summary, pending cards with
scope and risks, the live knowledge blob) and judges what to pick.
`depends_on` survives as a hint the LLM sees, not as a gate.

A plan is stored under `plan:<name>` in whichever store you pick —
local file (default) or postgres. The blob is human-readable markdown
with the canonical JSON payload in a fenced block at the end, so the
file backend doubles as a review surface.

Alongside the plan, `plan build` seeds a plan-scoped knowledge blob
at `knowledge:<company>.<plan>`. Every successful `plan run` card
triggers a `KnowledgeWriter` pass that merges new learnings into the
same blob, so the next selector call always reads the freshest source
of truth. The implement agent's existing `KnowledgeSplicer` picks
this blob up automatically by the `knowledge:<company>*` prefix.

### URL shapes

| Form | Example | Reader |
|---|---|---|
| Jira board URL | `https://acme.atlassian.net/jira/software/projects/KAN/boards/34` | `jira` |
| Jira short form | `jira:KAN` | `jira` |
| GitHub Projects v2 (org) | `https://github.com/orgs/bitspark-co/projects/34` | `github-project` |
| GitHub Projects v2 (user) | `https://github.com/users/iklobato/projects/2` | `github-project` |

Adding another tracker (Linear, Trello, …) is one module under
`src/briar/plan/_boards/` plus one entry in the registry — the
`build` subcommand has no per-vendor branching.

### Common flags (every subcommand)

| Flag | Default | Purpose |
|---|---|---|
| `--store {file,postgres}` | `file` | KnowledgeStore backend used to persist + reload the plan |
| `--root <dir>` | `./knowledge` | File-store root (only when `--store=file`) |
| `--company <name>` | `""` | Used by the postgres store for DSN resolution and by tracker providers for per-company credentials (e.g. `JIRA_{COMPANY}_*`) |

### `briar plan build <board>`

Fetch the board, enrich each card via the synthesiser, persist the
plan, and seed `knowledge:<company>.<plan>`.

| Flag | Required | Default | Purpose |
|---|---|---|---|
| `board` | ✓ | — | Board URL or short form (see table above) |
| `--name <slug>` | | derived from URL | Plan name (becomes blob `plan:<name>`) |
| `--default-branch <name>` | | `main` | Branch each card branches from by default. The LLM selector may override per pick at run time |
| `--max-cards <N>` | | 50 | Cap on cards pulled from the board |
| `--llm {anthropic,openai,gemini,bedrock}` | | `""` | LLM provider for per-card synthesis. Empty = heuristics only. When unavailable, silently falls back to heuristics |
| `--model <name>` | | provider default | Override the LLM's default model |
| `--with-knowledge` | | off | Splice the company's existing `knowledge:<company>` + `active-tickets:<company>` + `active-work:<company>` blobs into each card's synthesis context |
| `--print` | | off | After building, print the markdown plan to stdout |
| `--dry-run` | | off | Build the plan but do NOT persist it. Implies `--print` |

```bash
# Jira board, heuristic synthesis only
briar plan build \
    https://example.atlassian.net/jira/software/projects/KAN/boards/34 \
    --name acme-q3 --company acme

# GitHub Projects v2, LLM synthesis with the company's knowledge spliced in
briar plan build \
    https://github.com/orgs/bitspark-co/projects/34 \
    --name bitspark-roadmap --company bitspark \
    --llm anthropic --with-knowledge

# Persistent postgres-backed plan
BRIAR_DATABASE_URL=postgresql://... briar plan build \
    jira:ACME --name acme-impl --company acme \
    --store postgres

# One-off, no persistence — print the synthesised markdown and exit
briar plan build jira:ENG --name preview --dry-run
```

When `--company` is set, `plan build` writes a seed body to
`knowledge:<company>.<plan>` capturing the board context and every
card's title / summary / scope / risks. The selector and the
implement agent both read this blob; the run loop keeps it current.

### `briar plan show <name>`

Print the stored plan's markdown body to stdout (header + ordered
cards + raw JSON payload). No extra flags beyond the common store
flags.

```bash
briar plan show acme-q3
briar plan show bitspark-roadmap --store postgres --company bitspark
```

### `briar plan status <name>`

Show every card grouped by status — `done`, `in_progress`, `blocked`,
`pending` — with the journal artifacts the run loop wrote (commit
shas, PR urls, start timestamps, failure rationales). Pure projection
over the plan blob and the journal store; no new persistence.

```bash
briar plan status acme-q3                       # human-readable table
briar plan status acme-q3 --format json         # structured snapshot
```

### `briar plan next <name> --llm <provider>`

Ask the LLM selector what to do next given the past completed cards,
failed cards (with their `last_attempt_summary`), the current in-flight
card if any, every pending card, and the live `knowledge:<company>.<plan>`
blob. Emits a single record so it can be piped into the next step.

`--llm` is required — there is no deterministic fallback selector.

The selector returns one of four actions:

| Action | Meaning |
|---|---|
| `pick` | Run this card next. Includes `key`, an optional `branch_parent` override, and a short `why` |
| `replan` | Re-fetch the board and re-derive cards; statuses of overlapping keys are preserved |
| `complete` | Plan is done |
| `blocked` | No forward progress without operator intervention |

```bash
# Ask for the next decision
briar plan next acme-q3 --llm anthropic --format json

# Human-readable
briar plan next acme-q3 --llm anthropic
```

When the action is `pick`, the record includes `branch_name` +
`branch_parent` so the implementer agent can `git checkout -b
<branch_name> origin/<branch_parent>` directly.

### `briar plan advance <name>`

Mark a specific card with a chosen status. `--card` is required —
there is no auto-pick (the LLM selector owns that).

| Flag | Required | Default | Purpose |
|---|---|---|---|
| `--card <key>` | ✓ | — | Card key (e.g. `KAN-7`, `acme/api#42`) |
| `--status {pending,in_progress,done,blocked}` | | `done` | Status to set |

```bash
# Mark a specific card in_progress (the implementer agent picked it up)
briar plan advance acme-q3 --card KAN-7 --status in_progress

# A card got blocked on an external dep
briar plan advance acme-q3 --card KAN-9 --status blocked
```

### `briar plan run <name> --llm <provider>` — orchestrate the LLM-driven loop

Iterate the plan end-to-end with the LLM as the picker:

1. Build `PlanContext` from the plan blob, the journal store, and
   the live `knowledge:<company>.<plan>` blob.
2. Ask the selector for a decision.
3. On `pick`: run `briar agent implement` for that card.
4. On `pick` + rc=0: ask `KnowledgeWriter` to merge the new learning
   into `knowledge:<company>.<plan>`; mark card `done`.
5. On `pick` + rc≠0: capture the failure in `last_attempt_summary`,
   mark card `blocked`, stop unless `--continue-on-failure` is set.
6. On `replan`: re-fetch the board, preserve statuses of overlapping
   card keys, save the new plan, loop. Capped by `--max-replans`.
7. On `complete` / `blocked`: terminate.

`--llm` is required.

The implement step is invoked through the public `run_implement` seam
in `briar.commands.agent` — same code path as `briar agent implement`
on the command line, so the engineer-archetype behaviour is identical
(JIT ticket-context, knowledge splice, draft-PR opening). No shelling
out, no duplication.

| Flag | Required | Default | Purpose |
|---|---|---|---|
| `name` (positional) | ✓ | — | Plan name (slug used at build time) |
| `--company <key>` | ✓ | — | Credential-resolution key (matches a runbook YAML) |
| `--owner <slug>` | ✓ | — | Repository owner / workspace |
| `--repo <slug>` | ✓ | — | Repository name |
| `--tracker-project <key>` | | `<owner>/<repo>` | Tracker project key passed to `agent implement` |
| `--tracker <kind>` | | `github-issues` | Tracker provider (matches `briar agent implement`) |
| `--provider <kind>` | | `github` | Repository provider |
| `--llm {anthropic,openai,gemini,bedrock}` | ✓ | — | LLM provider for the selector and the post-card knowledge writer |
| `--limit <N>` | | `0` (unlimited) | Stop after N cards — useful for a smoke run |
| `--continue-on-failure` | | off | Mark failed cards `blocked` and keep going (default: stop on first failure) |
| `--max-replans <N>` | | `3` | Cap on `replan` actions per invocation |
| `--dry-run` | | off | Propagate `--dry-run` to every implement call (prints prompts, skips LLM) |
| `--model` / `--max-iter` / `--git-user-name` / `--git-user-email` / `--keep-worktree` / `--runbook` / `--meeting*` | | (defaults from `briar agent implement`) | Pass-through to each implement call |
| `--journal-store {file}` / `--journal-root <dir>` | | `file` / `./journal` | Where past `plan.run` sessions live; used by the selector to read past decisions |

```bash
# Walk the full plan against bitspark-co/widgets
briar plan run bitspark-roadmap-v1 \
    --company bitspark \
    --owner bitspark-co --repo widgets \
    --tracker github-issues --provider github \
    --store postgres --llm anthropic \
    --model claude-sonnet-4-6

# Smoke a single card with dry-run before letting the loop go wide
briar plan run bitspark-roadmap-v1 --limit 1 --dry-run --llm anthropic \
    --company bitspark --owner bitspark-co --repo widgets

# Batch run that tolerates individual card failures
briar plan run bitspark-roadmap-v1 --continue-on-failure --llm anthropic \
    --company bitspark --owner bitspark-co --repo widgets
```

**Per-card journal entries.** Each run opens one journal session
(`plan.run`) and records: `plan.next.decision` for every selector
call (with the chosen action and rationale), `plan.run.card.start`
when a pick begins, `plan.run.card.completed` on success,
`plan.run.card.failed` on non-zero exit, `plan.replan.requested`
when the selector returns `replan`, and `plan.run.stopped` if the
loop terminates early. Inspect with `briar journal show <session-id>`.

**Branch-parent override.** Each card carries `branch_parent =
--default-branch` after `plan build`. The LLM selector may return a
different `branch_parent` inside a `pick` decision — useful for
stacked PRs. `briar agent implement` reads `branch_parent` when
checking out the worktree.

### `briar plan list`

Enumerate stored plans (blob name only). Same store flags as above.

```bash
briar plan list
briar plan list --store postgres --company bitspark
```

### `briar plan clear <name>`

Remove a stored plan. Confirms by default; pass `--yes` to skip.

```bash
briar plan clear preview --yes
```

### What's in a `PlanCard`

Each card the synthesiser emits carries:

| Field | Source |
|---|---|
| `key` | tracker (Jira issue key, GH `owner/repo#N`, draft slug) |
| `title` / `url` | tracker |
| `summary` | LLM ➜ heuristic (first paragraph of the body) |
| `in_scope` / `out_of_scope` / `risks` | LLM ➜ heuristic (parses `## In Scope` / `## Out of Scope` / `## Risks` blocks) |
| `depends_on` | tracker explicit links + body lines + LLM judgement. Read as a *hint* by the selector; never used to gate picking |
| `branch_name` | derived (`briar/<key-slug>`) |
| `branch_parent` | `--default-branch` at build time. The LLM selector may emit a per-pick override |
| `status` | starts `pending`; transitions `pending → in_progress → done` (or `blocked` on failure) during `plan run` |
| `last_attempt_summary` | populated by `plan run` when a card fails — short string the selector reads next time |
| `sources` | best-effort URLs the card was assembled from |

The LLM enrichment pass is optional at build time and degrades to
the heuristic synthesiser when no provider is configured. The LLM
*selector* and *writer* used by `plan next` and `plan run` are NOT
optional — those subcommands require `--llm`.

### Live knowledge: `knowledge:<company>.<plan>`

`plan build` writes a seed body capturing the board context + every
card's title / summary / scope / risks. Every successful `plan run`
card invokes `KnowledgeWriter`, which asks the LLM to merge the new
learning into the same blob. The implement agent's existing
`KnowledgeSplicer` automatically picks up `knowledge:<company>*`
prefixed blobs, so this plan-scoped shard flows into every implement
call without extra wiring. The selector reads it on every pick.

Three writers / one namespace, scopes disjoint:

| Blob | Owner | Lifecycle |
|---|---|---|
| `knowledge:<company>` | `briar extract` | Periodic cold rebuild from live world state |
| `knowledge:<company>.<plan>` | `briar plan build` (seed) + `KnowledgeWriter` (per-card update) | Lives with the plan |

---

## `briar scaffold` — JSON config bundles for downstream tools

Emits a JSON bundle that a downstream orchestrator can consume. Two
templates today.

### `briar scaffold implementation`

Issue → plan → human approval → implement / comment.

| Flag | Required | Purpose |
|---|---|---|
| `--prefix <name>` | ✓ | prepended to every resource key |
| `--source {github,bitbucket,jira,aws,sentry}` | | repeatable; selects which sources contribute |
| `--archetype <name>` | | default `engineer`; one of `engineer`, `pr-fixer`, `pr-ci-fixer`, `pr-conflict-resolver`, `triager` |
| `--shape <name>` | | default `plan-approve-act`; one of `plan-approve-act`, `one-shot`, `triage` |
| `--trigger-kind <name>` | | default `github_webhook`; one of `github_webhook`, `bitbucket_webhook`, `schedule_cron`, `manual` |
| `--owner` / `--repo` | when `--source github` | GitHub identity |
| `--bitbucket-workspace` / `--bitbucket-repo` | when `--source bitbucket` | Bitbucket identity |
| `--jira-project` / `--jira-jql` | when `--source jira` | project key (repeatable) + optional JQL filter |
| `--aws-role-arn` / `--aws-external-id` / `--aws-region` / `--aws-services` | when `--source aws` | STS AssumeRole binding + which services to gather |
| `--sentry-org` / `--sentry-project` | when `--source sentry` | org slug + project slug (repeatable; at least one) |
| `--sentry-environment` / `--sentry-level` / `--sentry-query` | with `--source sentry` | optional filters: env list, severity list (`fatal`/`error`/`warning`/`info`/`debug`), Sentry search syntax |
| `--auth-mode {oauth,pat}` | | default `oauth`. Sentry ignores this and always requires a PAT (`--sentry-secret-id`); OAuth not yet supported. |
| `--github-secret-id` / `--bitbucket-secret-id` / `--jira-secret-id` / `--sentry-secret-id` | with `--auth-mode pat` (Sentry: always) | secret UUID holding the source's token |
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

# Sentry source (PAT-only), 15-min poll, triage flow.
# Sentry contributes 4 action tools: sentry.{comment_on_issue,resolve_issue,
# assign_issue,ignore_issue}. The triager archetype keeps only the
# comment tool; engineer (default) keeps all four.
briar scaffold implementation \
    --prefix acme-onerror \
    --source sentry \
    --sentry-org acme --sentry-project backend --sentry-project worker \
    --sentry-environment prod --sentry-level error --sentry-level fatal \
    --sentry-secret-id <uuid> \
    --shape triage --archetype triager \
    --trigger-kind schedule_cron --schedule "*/15 * * * *"

# Multi-source (GitHub + Jira + AWS), one-shot agent
briar scaffold implementation \
    --prefix acme-hourly \
    --source github --source jira --source aws \
    --owner iklobato --repo lightapi \
    --shape one-shot --out acme-hourly.json
```

> **Source families.** Each source declares one of two families. `tracker`
> sources (`github`, `bitbucket`, `jira`, `sentry`) read items and contribute
> mutation tools (comment / resolve / assign / open-PR / commit / …). `cloud`
> sources (`aws`) are read-only context — the agent inspects resource state
> but doesn't write through tools. The archetype's `tool_filter` is a
> substring match against `implementation_ref`, so naming verbs consistently
> across sources (e.g. `comment_on_issue`) makes archetype filters compose
> uniformly.

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

## `briar journal` — decision journal

Every instrumented command records a *session* — a tree of decisions
(which source, which archetype, which trigger, which tools survived the
archetype filter, …) tagged with rationale and the alternatives that
were on the table. Sessions persist to a configurable *store* (system
of record) and fan out to one-or-more *sinks* (publish destinations).

Today the only instrumented command is `briar scaffold`, and the only
store + sink are file-backed. Adding NotionSink / SlackSink to surface
sessions to a team channel is one new module each (Open/Closed —
`JournalSink` ABC + one tuple entry in `briar.journal.sinks`).

### Subcommands

```bash
# Enumerate recent sessions (newest first)
briar journal list [--command scaffold.] [--limit 50]

# Pretty-print one session as markdown
briar journal show <session-id>

# Export one session to a file or stdout
briar journal export <session-id> [--format {markdown,json}] [--out PATH]
```

`--store` and `--root` flags accept the same conventions as the rest
of the CLI (`--store file`, `--root ./journal`). Defaults match the
env-var configuration so an invocation with no flags reads from the
same place the recording side wrote to.

### What gets recorded

Each session captures: session id, command label (`scaffold.implementation`),
target (`acme/widgets`), start + end timestamps, and an ordered list
of `DecisionEvent`s. Each event has:

- `choice` — stable dotted slug (`scaffold.sources`, `scaffold.archetype`, …)
- `value` — what was selected (`["github", "jira"]`, `"engineer"`, …)
- `rationale` — one-sentence why
- `alternatives` — what else was on the menu
- `artifacts` — optional key→value bag (file paths, urls, ids)

### Where it goes

```
./journal/                          ← BRIAR_JOURNAL_ROOT
├── sessions/<YYYY-MM-DD>/<id>.json  ← system of record (FileJournalStore)
└── published/<id>.md                ← human-readable summary (FileSink)
```

The store is queryable (`briar journal list / show / export`); the
sink is a "leave it on disk for the next reader" artifact, ideal for
pasting into a PR description.

### Design

Two abstractions, deliberately separate (Single Responsibility):

| Concern | Pattern | Where |
|---|---|---|
| **Store** — system of record, queryable | Strategy + Registry | `briar/journal/store/` — `JournalStore` ABC, `FileJournalStore`, registered via `build_registry` |
| **Sinks** — publish fan-out, format owned per-destination | Adapter + Registry | `briar/journal/sinks/` — `JournalSink` ABC, `FileSink`, registered via `build_registry` |
| **Lifecycle** — open / record / close | Context manager + Null Object | `briar/journal/_journal.py` — `Journal` façade, `_NoOpJournal` default, `session(...)` context manager |
| **Recording** — what callers do | Façade function | `briar.journal.record(choice, value=..., rationale=...)` — one-liner per decision; goes through the active journal |

Instrumenting a new command = wrap it in `with session(...)` at the
boundary and call `record(...)` at each decision point. The composer's
existing branching is the natural surface — see
`src/briar/iac/scaffold/_composer.py` for the reference pattern.

---

## How the pieces fit together

Three command families, three concerns. Each diagram shows what a
command reads, what it invokes, and what comes out — so you can
predict the blast radius of a change.

### `briar runbook serve <dir>` — the long-running scheduler

```
            ┌──────────────────────────────────────────┐
            │ briar runbook serve runbooks/ --tick 5  │
            └─────────────────────┬────────────────────┘
                                  │
        reads at startup          │           reads at every fire
   ┌──────────────────────┐       │       ┌────────────────────────┐
   │ runbooks/*.yaml     │       │       │ /etc/briar/secrets.env │
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

A change in `runbooks/*.yaml` is picked up on the **next** schedule
fire because the executor re-loads the YAML on every iteration. Code
changes need a scheduler restart (the `briar` Python process caches
imported modules).

### `briar runbook extract <file.yaml>` — one-shot

```
   briar runbook extract runbooks/acme.yaml [--task tickets]
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
       │   --runbook runbooks/acme.yaml                   │
       └────────────────────────┬────────────────────────────┘
                                │
        ┌───────────────────────┼────────────────────────┐
        ▼                       ▼                        ▼
   ┌──────────┐         ┌────────────┐         ┌─────────────────┐
   │ secrets  │         │ runbooks/ │         │ KnowledgeStore  │
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

Both `prfix` and `implement` *also* fire `FetchMeetingContext` when
`--meeting-key` or `--meeting-query` resolves to something — for
`implement` the default query is the ticket key; for `prfix` it's
`owner/repo#pr`. Reads from the `MeetingProvider` registry
(`extract/_meetings/`, today: `fireflies`). Spliced into the agent's
system prompt alongside the ticket/PR context so decisions captured
in standups land in the code path that touches them.

### `briar plan build` / `briar plan run` — LLM-driven implementation

```
   ┌─────────────────────────────────────────────────────┐
   │ briar plan build <board> --name X --company acme   │
   │   [--llm anthropic] [--with-knowledge]             │
   └────────────────────────┬────────────────────────────┘
                            │
        ┌───────────────────┼───────────────────────────┐
        ▼                   ▼                           ▼
  ┌────────────┐    ┌────────────────┐         ┌────────────────┐
  │ secrets    │    │ BoardReader    │         │ KnowledgeStore │
  │ .env       │    │   .matches/    │         │  (existing     │
  │ • GITHUB   │    │   .parse/      │         │   knowledge:X  │
  │ • JIRA_*   │    │   .fetch       │         │   blobs spliced│
  │ • CLAUDE   │    │ ├── JiraBoard  │         │   in when      │
  └────┬───────┘    │ └── GhProjectV2│         │  --with-knowledge)│
       │            └────────┬───────┘         └────────┬───────┘
       ▼                     ▼                          ▼
  ┌──────────────────────────────────────────────────────────┐
  │ CardSynthesiser (Composite: LLM → Heuristic)             │
  │   LLM pass:    summary, scope, out-of-scope, risks       │
  │   Heuristic:   parses ## In Scope / Depends on lines     │
  │   branch_name = briar/<slug>;  branch_parent = default   │
  └────────────────────────────┬─────────────────────────────┘
                               │
                               ▼
       save_plan(store, plan)   ──▶ plan:<name> blob
       put_if_changed(seed)     ──▶ knowledge:<company>.<plan> blob
                               │
                               ▼
   ┌─────────────────────────────────────────────────────┐
   │ briar plan run <name> --llm anthropic              │
   │   (loops until COMPLETE / BLOCKED / failure)       │
   └────────────────────────┬────────────────────────────┘
                            │
                            ▼
  ┌──────────────────────────────────────────────────────────┐
  │ PlanContext.from_stores                                  │
  │   journal: completed / failed / in_progress              │
  │   knowledge: plan-scoped + company-scoped blobs          │
  └────────────────────────────┬─────────────────────────────┘
                               ▼
  ┌──────────────────────────────────────────────────────────┐
  │ Selector(llm).pick → SelectorDecision                    │
  │   kind ∈ {PICK, REPLAN, COMPLETE, BLOCKED}               │
  └─────┬───────────────┬───────────────┬─────────────┬──────┘
        ▼               ▼               ▼             ▼
     PICK            REPLAN          COMPLETE      BLOCKED
        │               │               │             │
        │               ▼               ▼             ▼
        │     ┌─────────────────┐    exit         exit
        │     │ replan(...)     │
        │     │   = build_plan  │
        │     │   + status      │
        │     │     preserve    │
        │     └────────┬────────┘
        ▼              │
  ┌───────────────────┘
  │  run_implement(card)  ──▶  rc == 0 ──▶  KnowledgeWriter.write
  │                            (merges into knowledge:<company>.<plan>)
  └──▶  rc != 0 ──▶  card.last_attempt_summary set; card.status=blocked
```

The implementer agent (`briar agent implement`) is the natural
downstream consumer: its `KnowledgeSplicer` already picks up every
blob whose name starts with `knowledge:<company>`, so the plan-scoped
shard flows into every implement call automatically. The selector
sees the freshest body on every pick.

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
| `runbooks/*.yaml` | no (next fire) | scheduler re-reads on every tick |
| `/etc/briar/secrets.env` | yes | scheduler holds env in process memory |
| `src/briar/` (editable install) | yes | imported modules are cached |
| Postgres `briar_knowledge` table | no | scheduler reads fresh on each fire |
| Jira session-token cookie | no — but log it | scheduler reads from env at startup; restart picks up rotation |

---

## Exit codes

`briar` returns conventional exit codes so it slots cleanly into CI
pipelines and shell glue. The canonical list lives in
`src/briar/commands/_enums.py:ExitCode`:

| Code | Symbol | Meaning |
|---|---|---|
| `0` | `OK` | Success — the requested operation completed without an error path. |
| `1` | `GENERAL_ERROR` | Soft failure that doesn't fit a more specific code (e.g. `briar plan clear` aborted at the confirm prompt, or `briar plan run` finished with one or more blocked cards). |
| `2` | `USAGE_ERROR` | Unknown subcommand / op (e.g. `briar agent typo`). Also the conventional code argparse returns on bad flags, so `briar agent prfix --pr abc` will exit `2` automatically without code in `briar` itself. |
| `3` | `STORE_OPEN_FAILED` | The `KnowledgeStore` couldn't be opened (typically Postgres DSN missing/invalid, or filesystem permissions on the file backend). |
| `4` | `CLONE_FAILED` | The agent's `git clone` step failed (network, missing token, wrong branch name, etc.). |
| `5` | `GIT_CONFIG_FAILED` | The worktree clone succeeded but setting `user.name` / `user.email` / `commit.gpgsign=false` failed inside it. |
| `6` | `AGENT_ERROR` | The agent run itself failed — LLM call raised, iteration ceiling hit, tool dispatch errored. `AgentRunResult.error` carries the detail in the log. |

Codes 1–6 are stable; `7–9` are reserved for future pre-LLM failure
categories; `10+` is reserved for future LLM/agent runtime failures.

The enum is wire-compatible — `return ExitCode.CLONE_FAILED` is
identical to `return 4` at the OS level, so existing shell scripts
that check `$?` against integer literals keep working.

---

## Testing

The suite lives under `tests/` and runs under stdlib `unittest` (for the
older modules) and `pytest` (for the recent additions). Both pass under
`pytest` — `pytest discover` finds all 824+ tests.

```bash
uv sync --extra test       # installs pytest + plugins + hypothesis + moto
uv run pytest              # ~10s; 824 passed, 1 xfailed
uv run pytest -n auto      # parallel via pytest-xdist
uv run pytest -m property  # hypothesis-only lane (slower, deeper search)
```

**What's covered** (see `tests/unit/` and `tests/integration/`):

- Every cross-cutting leaf module — `pagination`, `error_policy`,
  `log_context`, `decorators`, `env_vars`, `errors`, `formatting` —
  with hypothesis property tests asserting totality and roundtrips.
- Every CLI subcommand happy + failure path via the `cli` fixture
  (which sandboxes env vars, patches `configure_logging` so caplog
  survives, and routes through `briar.cli.main`).
- Every external-IO adapter (Slack/Telegram/Jira writers, PagerDuty
  /email/Slack/Telegram sinks, the envfile credential store, the file
  knowledge store) with `urllib.request.urlopen` / `smtplib.SMTP`
  mocked at the seam.
- A parametrized **registry-shape contract** that asserts the same
  invariants (no-duplicate-name, key-matches-ClassVar, factory returns
  instance) across all 10 plugin registries — `EXTRACTORS`, `STORES`,
  `ACQUIRERS`, `WRITERS`, `SINKS`, `BOARD_READERS`, `FORMATTERS`,
  `JOURNAL_SINKS`, `ARCHETYPES`, `BOOTSTRAPS`.
- `tests/unit/test_log_context.py` and `tests/unit/test_pagination.py`
  hold the `pytest.mark.property` hypothesis tests.

**Mutation testing** lives at [`tools/mutation_test.py`](tools/mutation_test.py).
It applies 7 representative mutations to the leaf modules (operator
flips, type narrowings, broad-except changes), runs the focused test
suite, and reports killed vs. survived. Current score: **7/7 killed**.

```bash
uv run python tools/mutation_test.py
#   [KILLED  ] error_policy:wait>0 → wait>=0 (would call sleep(0))
#   [KILLED  ] error_policy:max_attempts<1 → <=1 (rejects 1 as well)
#   [KILLED  ] pagination:type(page) is list → is tuple
#   [KILLED  ] decorators:except Exception → except ValueError
#   [KILLED  ] errors:HTML detection 9 chars → 8 chars
#   [KILLED  ] env_vars:str.upper → str.lower in for_company
#   [KILLED  ] log_context:always-empty filter (return True early)
# Mutation score: 7/7 killed (100%)
```

**CI lanes** ([`.github/workflows/tests.yml`](.github/workflows/tests.yml)):

| Lane | Triggers | Purpose |
|---|---|---|
| `unit` | every push + PR | `pytest -n auto` on Python 3.10 / 3.11 / 3.12 |
| `property` | every push + PR | `pytest -m property` (longer hypothesis budget) |
| `mutation` | `main` + manual dispatch only | `tools/mutation_test.py` — does not gate PRs |

A few documented quirks the suite asserts so they're visible as
warnings, not silent drift:

- `swallow_errors(default=[])` returns the same list object every time
  — mutable defaults alias across calls. A future change to copy them
  must also flip the corresponding test assertion.
- Empty-company `CredEnv.AWS_KEY_ID.for_company("")` produces
  `AWS__ACCESS_KEY_ID` (double underscore). Reject-or-correct would
  need to flip the documented assertion.
- The global `--format` flag collides with `briar journal export
  --format` because argparse always overwrites the global with the
  subparser's default — pinned as `xfail(strict=True)` until the
  global is renamed.

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

## Advanced examples — feature combinations

The per-command sections above show one feature at a time. These six
examples each combine ≥3 features and exercise the registries an
operator actually wires together day-to-day. Every name (extractor,
writer kind, archetype, tracker, board reader, LLM, …) is checked
against the live registries — running `briar` with these flags will
not produce an "unknown X" error.

### 1. Production-shaped runbook YAML — all YAML abstractions in one file

Multi-task scheduling, postgres store with shared DSN partitioned by
the `company` column, full `messages:` routing (4 of the 6 writer
kinds), per-company `git_identity:` for agent commits. Drop this under
`runbooks/<company>.yaml` and the scheduler picks it up on next sweep;
the `extract`/`sweep` paths work the same way without `serve`.

```yaml
version: 1

companies:
  acme:
    # Postgres-backed knowledge — explicit dsn_env keeps acme + other
    # companies pointed at the same managed cluster. Rows are partitioned
    # by the `company` column inside briar_knowledge, so collisions are
    # impossible. Resolution: dsn_env → BRIAR_ACME_DATABASE_URL → BRIAR_DATABASE_URL.
    knowledge:
      store: postgres
      name: knowledge:acme
      config:
        dsn_env: BRIAR_KB_DATABASE_URL

    # Per-company commit identity. Read by `briar agent prfix/implement`
    # when --runbook points at this YAML. CLI flags > YAML > hardcoded default.
    git_identity:
      name: Briar Bot (acme)
      email: bot+acme@usebriar.com

    # Named outbound channels. The agent's `send_message` tool resolves
    # handle → writer at run time. Adding a writer kind is one registry
    # entry; no schema edit here.
    messages:
      ticket_comment:
        kind: jira-comment
      ticket_transition:
        kind: jira-transition
        config:
          status: "In Review"
      pr_reply:
        kind: github-pr-comment
      ops_chat:
        kind: slack-channel
      escalation:
        kind: telegram-chat

    schedules:
      # Heavy mining, daily. 5 extractors — pr archaeology, reviewer
      # profile, code hotspots, ticket archaeology, AWS infra.
      - task: archaeology
        every: "day at 03:17"
        extract:
          - name: pr-archaeology
            args:
              provider: github
              pr_repo: [acme-co/acme-app, acme-co/acme-api]
              pr_max: 50
              pr_authors_block: ["github-actions[bot]", "dependabot[bot]"]
          - name: reviewer-profile
            args:
              provider: github
              reviewer_repo: [acme-co/acme-app]
              reviewer_pr_sample: 50
              reviewer_top_n: 5
          - name: code-hotspots
            args:
              provider: github
              hotspots_repo: [acme-co/acme-app]
              hotspots_since_days: 30
              hotspots_max_commits: 100
              hotspots_top_n: 10
          - name: ticket-archaeology
            args:
              tracker: jira
              ticket_archaeology_project: [ACME, PLAT]
              ticket_max: 100
          - name: aws-infra
            args:
              cloud: aws
              aws_extract_profile: acme
              aws_extract_region: us-east-1
              aws_extract_service: [ecs, lambda, logs, rds, sqs]

      # Codebase + deploy state, twice a day.
      - task: implementation
        every: "12 hours"
        extract:
          - name: codebase-conventions
            args:
              provider: github
              conventions_repo: [acme-co/acme-app, acme-co/acme-api]
          - name: github-deployments
            args:
              provider: github
              deploy_repo: [acme-co/acme-app]

      # Open work queue, hourly. PR + ticket sweeps.
      - task: live
        every: "hour"
        extract:
          - name: active-work
            args:
              provider: github
              active_repo: [acme-co/acme-app, acme-co/acme-api]
              active_authors_block: ["github-actions[bot]", "dependabot[bot]"]
          - name: active-tickets
            args:
              tracker: jira
              ticket_project: [ACME, PLAT]

      # Meeting digest from Fireflies, twice a day. Splices automatically
      # into the agent context when `--meeting-query` matches.
      - task: meetings
        every: "12 hours"
        extract:
          - name: meeting-digest
            args:
              meeting: fireflies
              meeting_since_days: 14
              meeting_max: 50
              meeting_attendee_allow: [alice@acme.com, bob@acme.com]
```

Manual invocation (terminal-driven, no `serve`):

```bash
briar runbook extract runbooks/acme.yaml --task live     # one task across this runbook
briar runbook sweep   runbooks/                           # every YAML in dir, one pass
briar runbook extract runbooks/acme.yaml                  # all tasks, this YAML
```

### 2. End-to-end LLM-driven implementation pipeline

`extract` seeds company knowledge → `plan build` enriches a tracker board
with that knowledge → `plan run` loops the LLM selector + implementer
+ knowledge writer until the plan completes or blocks. The `--meeting-query`
default (= ticket key) auto-surfaces standup transcripts that mention
each card.

```bash
# 1. Seed the company knowledge blob (one-time per cadence cycle).
#    Writes to knowledge:acme.
briar extract --company acme \
    --include pr-archaeology --include reviewer-profile \
    --include code-hotspots --include codebase-conventions \
    --include active-work --include active-tickets \
    --pr-repo acme-co/acme-app --pr-max 50 \
    --reviewer-repo acme-co/acme-app \
    --hotspots-repo acme-co/acme-app --hotspots-since-days 30 \
    --conventions-repo acme-co/acme-app \
    --active-repo acme-co/acme-app \
    --ticket-project ACME \
    --storage postgres

# 2. Build the plan from the live Jira board, with company knowledge
#    spliced into each card's synthesis context.
#    Writes plan:acme-q3 and seeds knowledge:acme.acme-q3.
briar plan build \
    https://acme.atlassian.net/jira/software/projects/ACME/boards/12 \
    --name acme-q3 --company acme \
    --llm anthropic --with-knowledge \
    --store postgres --max-cards 30

# 3. Smoke a single card first — dry-run prints the prompt without
#    spending tokens or touching the repo.
briar plan run acme-q3 \
    --company acme --owner acme-co --repo acme-app \
    --tracker jira --tracker-project ACME \
    --runbook runbooks/acme.yaml \
    --llm anthropic --model claude-sonnet-4-6 \
    --limit 1 --dry-run

# 4. Let the loop go wide. Failed cards are marked `blocked` and the
#    loop continues; each completed card triggers KnowledgeWriter to
#    merge new learnings into knowledge:acme.acme-q3.
briar plan run acme-q3 \
    --company acme --owner acme-co --repo acme-app \
    --tracker jira --tracker-project ACME \
    --runbook runbooks/acme.yaml \
    --llm anthropic --model claude-sonnet-4-6 \
    --continue-on-failure --max-replans 3

# 5. Status snapshot + decision audit.
briar plan status acme-q3 --format json | jq '.cards[] | select(.status=="blocked")'
briar journal list --command plan.run. --limit 5
briar journal show <session-id>
```

### 3. Credential bootstrap chain — Infisical + envfile + per-company scopes

Registry order is precedence. `envfile` runs FIRST at startup so
locally-persisted creds beat remote-vault values on conflict — operators
who logged in via `briar auth login --store envfile` aren't stranded
when Infisical 401s.

```bash
# One-time per laptop: bootstrap the password manager itself.
# Always lands in envfile because the bootstrap is chicken-and-egg.
briar auth login infisical

# Vendor creds → choose where they land. Three flavours in one host:
export BRIAR_DEFAULT_STORE=infisical
briar auth login github-pat            --company acme        # → Infisical (default)
briar auth login aws-sso               --company acme        # → Infisical
briar auth login jira-session          --company acme        # → Infisical (cookie walkthrough)
briar auth login bitbucket-app-password --company widgets    # → Infisical
briar auth login linear-api-key        --company widgets     # → Infisical
briar auth login aws-static            --company legacy \
                                       --store envfile       # → envfile (local override)

# Audit coverage WITHOUT printing values. Walks every runbook YAML's
# schedules:/messages: blocks; queries each provider/writer's
# required_env_vars(company); reports `ok` / `X MISSING:` per row.
briar secrets doctor --examples runbooks/                    # env-var view
briar secrets doctor --examples runbooks/ --store aws-secretsmanager

# Per-target liveness check (exits non-zero on any miss).
briar auth status aws-sso       --company acme
briar auth status jira-session  --company acme --store infisical

# Dry-run the bootstrap so you can see what would be hydrated.
briar secrets bootstrap --dry-run

# Refresh short-lived bundles (STS, etc.) without re-prompting.
briar auth refresh aws-sso --company acme

# Now run extract — auto_bootstrap() fires at CLI startup and the
# required env vars are present.
briar runbook extract runbooks/acme.yaml --task live
```

Resolution order at runtime (lowest precedence last):

```
1. os.environ at process start              ── operator-supplied wins
2. envfile bootstrap (laptop default)        ── earliest registry entry
3. infisical bootstrap (when configured)     ── later entry, only fills gaps
4. on-demand CredentialStore reads           ── for explicit --store flows
```

### 4. Cross-provider agent run — Bitbucket repo, Linear tickets, meeting splice

The agent is provider-agnostic — `--provider` and `--tracker` are
orthogonal. `--meeting-query` overrides the auto-derived default
(ticket key for `implement`, `owner/repo#pr` for `prfix`) when the
standup discussed the topic by feature name.

```bash
# Bitbucket repo + Linear tickets + Fireflies meeting search.
# Auto-query would be "ENG-7"; we override because the meeting
# discussed the feature by name.
briar agent implement \
    --company widgets --owner widgets-co --repo api \
    --provider bitbucket --tracker linear \
    --ticket-project ENG --ticket-key ENG-7 \
    --runbook runbooks/widgets.yaml \
    --meeting fireflies \
    --meeting-query "oauth refresh token rollout" \
    --meeting-top-k 3 --meeting-max-bytes 60000 \
    --model claude-sonnet-4-6 --max-iter 40

# Bitbucket PR-fix run — pin ONE specific meeting transcript instead
# of search (use when a reviewer cited "as discussed Thursday").
briar agent prfix \
    --company widgets --owner widgets-co --repo api \
    --pr 142 --branch fix/oauth-refresh \
    --provider bitbucket \
    --runbook runbooks/widgets.yaml \
    --meeting-key 01HABCDEF... \
    --git-user-name "widgets-bot" --git-user-email "bot@widgets.example"
```

Git identity resolution (per field): CLI flag > YAML
`companies.<name>.git_identity.{name,email}` > hardcoded default. So
`--git-user-name` above overrides the YAML's `git_identity.name` only;
`email` still comes from the YAML.

### 5. Multi-source scaffold — Sentry triage with GitHub + AWS context

Four sources, two families. `tracker` sources (`github`, `bitbucket`,
`jira`, `sentry`) read items and contribute mutation tools. `cloud`
sources (`aws`) are read-only context. The `triager` archetype filters
the bound tool list down to non-mutating tools (e.g. `comment_on_issue`
survives; `resolve_issue` / `assign_issue` are dropped).

```bash
# 15-min triage poll. Sentry contributes 4 action tools but only
# comment_on_issue survives the triager filter. GitHub + AWS contribute
# read-only context (PR queue, infra state) for the triager's reasoning.
briar scaffold implementation \
    --prefix acme-onerror \
    --source sentry --source github --source aws \
    --sentry-org acme --sentry-project backend --sentry-project worker \
    --sentry-environment prod \
    --sentry-level error --sentry-level fatal \
    --sentry-query "is:unresolved" \
    --auth-mode pat --sentry-secret-id <uuid> \
    --owner acme-co --repo acme-app --github-secret-id <uuid> \
    --aws-role-arn arn:aws:iam::123456789012:role/briar-readonly \
    --aws-external-id <id> --aws-region us-east-1 \
    --aws-services ecs --aws-services rds --aws-services logs \
    --archetype triager --shape triage \
    --trigger-kind schedule_cron --schedule "*/15 * * * *" \
    --company acme \
    --model claude-sonnet-4-6 \
    --out scaffolds/acme-onerror.json

# Same source mix, but as a plan→approve→act flow on Jira webhooks
# instead of cron. Engineer archetype keeps all four mutation tools.
briar scaffold implementation \
    --prefix acme-impl \
    --source jira --source github --source aws \
    --jira-project ACME --jira-jql "project = ACME AND status = 'To Do'" \
    --jira-secret-id <uuid> --auth-mode pat \
    --owner acme-co --repo acme-app --github-secret-id <uuid> \
    --aws-role-arn arn:aws:iam::123456789012:role/briar-readonly \
    --aws-external-id <id> --aws-region us-east-1 \
    --aws-services ecs --aws-services rds \
    --archetype engineer --shape plan-approve-act \
    --trigger-kind manual \
    --company acme --out scaffolds/acme-impl.json

# Inspect what the scaffold decided (and why).
briar journal list --command scaffold. --limit 5
briar journal show <session-id>
briar journal export <session-id> --format markdown --out decisions/acme-onerror.md
```

### 6. Knowledge-store routing — same runbook, file in dev / postgres in prod

The runbook's `knowledge.store: postgres` plus `config.dsn_env`
locks the production binding. For local development, the operator
overrides at the CLI level without editing the YAML — the agent
flow reads the same `knowledge:<company>` blob from whichever store
the CLI was told to use.

```bash
# Dev — write the same blob to a local file. `--storage file` overrides
# the YAML's store; `--root` controls where the file lands.
briar extract --company acme \
    --include pr-archaeology --include active-work \
    --pr-repo acme-co/acme-app \
    --active-repo acme-co/acme-app \
    --storage file --root ./knowledge --blob-name knowledge:acme

# Read it back.
briar context get knowledge:acme
briar context list --prefix knowledge:
briar context categories

# Prod — postgres. Three env vars resolve in order; first non-empty wins.
#   1. dsn_env from runbook YAML                  (explicit; highest precedence)
#   2. BRIAR_ACME_DATABASE_URL                    (per-company convention)
#   3. BRIAR_DATABASE_URL                         (global fallback)
export BRIAR_KB_DATABASE_URL='postgresql://briar_kb:***@pg.example:25060/briar?sslmode=require'
briar runbook extract runbooks/acme.yaml --task live

# Same DSN, same partition column, share the cluster across companies
# without leakage: every blob is keyed by (company, name) at the table.
briar context --store postgres list --prefix knowledge:

# Read the plan-scoped shard the agent stream writes to.
briar context --store postgres get knowledge:acme.acme-q3
```

Pair this with `briar dashboard --once > /tmp/snapshot.html` to render
a status snapshot from either store without standing up a long-running
HTTP listener.

---

## Examples + further reading

- `runbooks/` (gitignored — keep your real runbooks here so they
  don't leak into the public repo). Recommended pattern: one YAML
  per company, all with `knowledge.config.dsn_env: BRIAR_KB_DATABASE_URL`
  so they share a managed-Postgres knowledge store with row-level
  partitioning by the `company` column. Real-company planning docs
  (e.g. project roadmaps) belong here too.
- [`examples/all_features.yaml`](examples/all_features.yaml) — every
  abstraction × provider × writer combination across 4 companies.
  Schema reference for `knowledge.config`, `messages:`, `git_identity:`,
  and the Jira auth-strategy selector.
- [`examples/multi_company.yaml`](examples/multi_company.yaml) +
  [`.env.example`](examples/multi_company.env.example) — 3-company
  tutorial without the `messages:` block.
- [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) — per-provider
  credential acquisition guide (incl. Jira API token AND
  browser-session-cookie paths)
- [`DEPLOY_EC2.md`](DEPLOY_EC2.md) — systemd deployment recipe
- [`ARCHITECTURE.md`](ARCHITECTURE.md) +
  [`ARCHITECTURE_DEEP.md`](ARCHITECTURE_DEEP.md) — abstraction
  inventory + SOLID audit
