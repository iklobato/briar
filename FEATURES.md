# Briar — feature & architecture reference for AI agents

> **Audience.** Future AI agents working in `tool.usebriar.com`. The README
> is operator-facing prose; this file is a dense, decision-oriented
> reference. When something here disagrees with the code, the **code
> wins** — re-verify with the snippet in §13 and update this file.
>
> **Last verified against:** `briar-cli 1.1.21` (registry & flag snapshot
> taken from a clean `pip install -e .[all]`).
>
> **Boundaries.** Names listed in §3 are **runtime-validated** — every
> `name:` in a runbook YAML, every `--<flag> <value>` choice, every
> `messages.kind:`, every `--store`, every `--archetype`. Typoing produces
> `unknown X; known: ...`. Treat the lists as authoritative until §13's
> snapshot disagrees.

---

## 1. Command map

```
briar
├── version                       — print client version, no flags
├── extract                       — one-shot extraction (manual CLI flags)
├── runbook                       — schedule-driven extraction
│   ├── extract <file.yaml>           one YAML, one pass
│   ├── sweep   <dir>                  every *.yaml in dir, one pass
│   └── serve   <dir>                  long-running scheduler (cron replacement)
├── agent                         — autonomous LLM flows
│   ├── prfix                          address open review comments + CI failures on a PR
│   └── implement                      implement one tracker ticket end-to-end
├── plan                          — LLM-driven implementation plans
│   ├── build <board>                  fetch board, synthesize cards, persist plan
│   ├── show <name>                    print stored plan markdown
│   ├── status <name>                  per-card status breakdown
│   ├── next <name> --llm <p>          LLM selector → SelectorDecision
│   ├── advance <name> --card K        manually set card status
│   ├── run <name> --llm <p>           loop selector→implement→knowledge-writer
│   ├── list                            enumerate stored plans
│   └── clear <name>                    delete a stored plan
├── scaffold                      — JSON config bundles for downstream tools
│   ├── implementation                  plan/approve/act-shape agent
│   └── pr-fixes                        PR-review-comment sweep
├── context                       — local markdown CRUD
│   ├── put <name>
│   ├── get <name>
│   ├── list [--prefix <p>]
│   ├── delete <name>
│   └── categories
├── dashboard                     — read-only HTML status page
├── auth                          — interactive credential acquisition
│   ├── login <target> [--store <k>]
│   ├── logout <target>
│   ├── refresh <target>
│   ├── list [--store <k>] [--company <c>]
│   └── status <target>
├── secrets                       — credential coverage + remote-vault hydrate
│   ├── doctor                          per-(company,extractor,writer) coverage matrix
│   └── bootstrap                       fetch from remote vault → os.environ
├── journal                       — decision-journal inspection
│   ├── list
│   ├── show <session-id>
│   └── export <session-id>
└── telemetry                     — Sentry telemetry control
    ├── status
    ├── preview
    ├── off
    ├── errors-only
    ├── full
    └── reset
```

---

## 2. Where things live (filesystem)

```
src/briar/
├── cli.py                            — top-level argparse entrypoint
├── _registry.py                      — generic Strategy+Registry builder
├── env_vars.py                       — CredEnv enum: per-company env var keys
├── errors.py                         — CliError, ConfigError, CredentialExpired
├── decorators.py                     — @swallow_errors, retry helpers
├── log_context.py                    — contextvars logger filter
├── commands/                         — one file per `briar <verb>` subcommand
│   ├── _enums.py                         ExitCode (see §11)
│   ├── extract.py, runbook.py, agent.py, plan.py, scaffold.py,
│   ├── context.py, dashboard.py, auth.py, secrets.py, journal.py,
│   └── telemetry.py, version.py
├── extract/                          — knowledge extractors + provider abstractions
│   ├── __init__.py                       EXTRACTORS + TASK_SCOPED_EXTRACTORS registries
│   ├── base.py                           KnowledgeExtractor + 4 *BackedExtractor bases
│   ├── composer.py                       KnowledgeComposer (markdown + json + inventory renderers)
│   ├── _provider.py / _providers/        RepositoryProvider ABC + github/bitbucket impls
│   ├── _tracker.py  / _trackers/         TrackerProvider ABC + 4 impls
│   ├── _cloud.py    / _clouds/           CloudProvider ABC + 3 impls
│   ├── _meeting.py  / _meetings/         MeetingProvider ABC + fireflies impl
│   ├── aws_services/                     AWS_SERVICE_GATHERERS (ecs/rds/lambda/sqs/logs/tagging-inventory)
│   ├── language_detectors/               codebase-conventions sub-strategies
│   ├── pr_archaeology.py, active_work.py, github_deployments.py,
│   ├── codebase_conventions.py, reviewer_profile.py, code_hotspots.py,
│   ├── active_tickets.py, ticket_archaeology.py, aws_infra.py,
│   ├── meeting_digest.py,                — the original 10 scheduled extractors
│   ├── defect_hotspots.py, pr_hygiene.py, review_nits.py, revert_signals.py,
│   ├── commit_message_quality.py, stale_prs.py, ci_health.py, repo_governance.py,
│   ├── dependency_health.py, code_scanning.py, test_discipline.py,
│   ├── release_cadence.py, todo_density.py   — +13 code-quality extractors (23 total)
│   └── ticket_context.py, pr_review_context.py, meeting_context.py
│                                         — the 3 JIT (task-scoped) extractors
├── storage/                          — KnowledgeStore backends
│   ├── __init__.py                       KnowledgeStoreRegistry (make_store)
│   ├── base.py                           ABC + put_if_changed + StoreBinding
│   ├── file.py                           StoreFile (laptop dev)
│   ├── postgres.py                       StorePostgres (DO managed PG)
│   └── _models.py                        SQLAlchemy ORM (KnowledgeBlob + KnowledgeHistory)
├── messaging/                        — outbound message writers
│   └── (WRITERS registry: jira-comment, jira-transition, github-pr-comment,
│        bitbucket-pr-comment, slack-channel, telegram-chat)
├── notify/                           — alert sinks (BRIAR_NOTIFY_SINKS)
│   └── (SINKS registry: email, pagerduty, slack, telegram)
├── credentials/                      — CredentialStore + Bootstrap
│   ├── _store.py, envfile.py, vault.py, aws_secrets.py, ssm.py
│   └── _bootstraps/                       envfile (single backend today)
├── auth/_acquirers/                  — interactive credential flows
│                                       (9 acquirers — see §3)
├── plan/                             — board → cards → selector → run loop
│   ├── _boards/                          BOARD_READERS (jira, github-project)
│   ├── _synthesiser.py, _selector.py, _writer.py
│   └── _models.py, _ctx.py
├── agent/                            — agent runner (LLM tool-use loop)
│   ├── _llms/                            LLMS (anthropic, bedrock, gemini, openai)
│   ├── tools.py, runner.py
│   └── _repo_cloner.py
├── iac/
│   ├── runbook/                          RunbookFile schema + executor + scheduler
│   │   ├── models.py                       Pydantic schema (RunbookFile, CompanyEntry, …)
│   │   ├── executor.py                     RunbookExtractor._run_schedule
│   │   └── scheduler.py                    EveryParser + RunbookScheduler
│   └── scaffold/                          scaffold composer
│       ├── archetypes/                      ARCHETYPES
│       ├── shapes/                          WORKFLOW_SHAPES
│       ├── triggers/                        TRIGGER_TEMPLATES
│       └── sources/                         SOURCE_TEMPLATES
├── journal/                          — decision journal (Strategy + Registry × 2)
│   ├── _journal.py                        Journal façade, session() context manager
│   ├── store/                              JournalStore ABC + FileJournalStore
│   └── sinks/                              JournalSink ABC + FileSink
├── telemetry/                        — Sentry sink config
└── formatting/                       — FORMATTERS (table/json/yaml/csv/quiet)

runbooks/                             — real per-company YAMLs (gitignored)
examples/                             — public sample YAMLs
agents/                               — per-command operator docs (agents/runbook.md, etc.)
tools/mutation_test.py                — 7-mutant smoke test
bin/                                  — helper scripts
scripts/                              — none today
```

---

## 3. Plugin registries (the runtime-validated names)

Every name below is rejected if typoed. They live in their own files;
adding one is a one-line registry edit + one new module — no schema
edit anywhere else.

| Registry | Symbol | Names |
|---|---|---|
| Scheduled extractors | `briar.extract.EXTRACTORS` | `active-tickets`, `active-work`, `aws-infra`, `ci-health`, `code-hotspots`, `code-scanning`, `codebase-conventions`, `commit-message-quality`, `defect-hotspots`, `dependency-health`, `github-deployments`, `meeting-digest`, `pr-archaeology`, `pr-hygiene`, `release-cadence`, `repo-governance`, `revert-signals`, `review-nits`, `reviewer-profile`, `stale-prs`, `test-discipline`, `ticket-archaeology`, `todo-density` (23) |
| JIT extractors | `briar.extract.TASK_SCOPED_EXTRACTORS` | `meeting-context`, `pr-review-context`, `ticket-context` |
| Knowledge stores | `briar.storage.KnowledgeStoreRegistry.STORES` | `file`, `postgres` |
| Message writers (runbook `messages.kind:`) | `briar.messaging.WRITERS` | `bitbucket-pr-comment`, `github-pr-comment`, `jira-comment`, `jira-transition`, `slack-channel`, `telegram-chat` |
| Auth acquirers (`auth login <target>`) | `briar.auth._acquirers.ACQUIRERS` | `aws-sso`, `aws-static`, `bitbucket-app-password`, `fireflies`, `github-device`, `github-pat`, `jira-session`, `jira-token`, `linear-api-key` |
| Credential stores (`auth --store`) | `briar.credentials.STORES` | `aws-secretsmanager`, `envfile`, `ssm`, `vault` |
| Notify sinks (`$BRIAR_NOTIFY_SINKS`) | `briar.notify.SINKS` | `email`, `pagerduty`, `slack`, `telegram` |
| Board readers (`plan build <board>`) | `briar.plan._boards.BOARD_READERS` | `github-project`, `jira` |
| Journal sinks | `briar.journal.sinks.JOURNAL_SINKS` | `file` |
| LLM providers (`--llm`) | `briar.agent._llms.LLMS` | `anthropic`, `bedrock`, `gemini`, `openai` |
| Meeting providers (`--meeting`) | `briar.extract._meetings.MEETINGS` | `fireflies` |
| Repo providers (`--provider`) | `briar.extract._providers.PROVIDERS` | `bitbucket`, `github` |
| Tracker providers (`--tracker`) | `briar.extract._trackers.TRACKERS` | `bitbucket-issues`, `github-issues`, `jira`, `linear` |
| Cloud providers (`--cloud`) | `briar.extract._clouds.CLOUDS` | `aws`, `azure`, `gcp` |
| Agent archetypes (`scaffold --archetype`) | `briar.iac.scaffold.archetypes.ARCHETYPES` | `engineer`, `pr-ci-fixer`, `pr-conflict-resolver`, `pr-fixer`, `triager` |
| Workflow shapes (`scaffold --shape`) | `briar.iac.scaffold.shapes.WORKFLOW_SHAPES` | `one-shot`, `plan-approve-act`, `triage` |
| Trigger templates (`scaffold --trigger-kind`) | `briar.iac.scaffold.triggers.TRIGGER_TEMPLATES` | `bitbucket_webhook`, `github_webhook`, `manual`, `schedule_cron` |
| Source templates (`scaffold --source`) | `briar.iac.scaffold.sources.SOURCE_TEMPLATES` | `aws`, `bitbucket`, `github`, `jira`, `sentry` |
| Bootstraps (`secrets bootstrap --kind`) | `briar.credentials._bootstraps.BOOTSTRAPS` | `envfile` |
| AWS service gatherers (`--aws-extract-service`) | `briar.extract.aws_services.AWS_SERVICE_GATHERERS` | `ecs`, `lambda`, `logs`, `rds`, `sqs`, `tagging-inventory` |
| Output formatters (global `--format`) | `briar.formatting.FORMATTERS` | `table`, `json`, `yaml`, `csv`, `quiet` |

---

## 4. Data flow — extract pipeline

```
┌─────────────────────────────────────────────────────────────────────┐
│  briar runbook extract runbooks/acme.yaml [--task <name>]           │
│  briar runbook sweep   runbooks/                                    │
│  briar runbook serve   runbooks/        (cron replacement)          │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
         ┌─────────────────────────────────────────────┐
         │ RunbookLoader.load (yaml.safe_load → pydantic) │
         │   • forbids unknown keys (extra='forbid')   │
         │   • validates every name against live       │
         │     EXTRACTORS / STORES / WRITERS registries│
         └─────────────────────┬───────────────────────┘
                               │
                               ▼
                   for each (company, task):
                               │
                               ▼
         ┌─────────────────────────────────────────────┐
         │ RunbookExtractor._run_schedule              │
         │   1. _collect_sections                      │
         │      • for each ExtractEntry:               │
         │        - lookup extractor in EXTRACTORS     │
         │        - build Namespace from entry.args    │
         │          (inject `company` if not present)  │
         │        - extractor.is_available(ns)         │
         │        - extractor.extract(ns) →            │
         │          ExtractedSection                   │
         │   2. KnowledgeComposer.markdown(sections)   │
         │   3. make_store(binding.store, binding)     │
         │   4. store.put_if_changed(blob, md, "knowledge")│
         └─────────────────────┬───────────────────────┘
                               │
        ┌──────────────────────┼──────────────────────┐
        ▼                      ▼                      ▼
   PROVIDERS              KnowledgeStore        Notify sinks
   GithubProvider         StoreFile             (on failure only,
   BitbucketProvider      StorePostgres          per $BRIAR_NOTIFY_SINKS)
   JiraTracker            ↓                     Slack/Telegram/Email/PagerDuty
   GithubIssuesTracker    ./knowledge/...
   BbIssuesTracker        OR
   LinearTracker          briar_knowledge       Three failure points all
   AwsCloudProvider       briar_knowledge_history  route through _record_failure
   AzureCloudProvider                            (one shape, no drift)
   GcpCloudProvider
   FirefliesMeeting
```

### Extractor → provider routing

Each scheduled extractor inherits one of four `*BackedExtractor` bases
(`extract/base.py`). The base auto-registers a CLI flag + a
`_<vendor>(args)` helper:

| Base class | Flag auto-added | Helper | Extractors |
|---|---|---|---|
| `RepoBackedExtractor` | `--provider {github,bitbucket}` | `_provider(args)` | `pr-archaeology`, `active-work`, `github-deployments`, `codebase-conventions`, `reviewer-profile`, `code-hotspots`, `defect-hotspots`, `pr-hygiene`, `review-nits`, `revert-signals`, `commit-message-quality`, `stale-prs`, `ci-health`, `dependency-health`, `code-scanning`, `repo-governance`, `test-discipline`, `release-cadence`, `todo-density` |
| `TrackerBackedExtractor` | `--tracker {jira,github-issues,bitbucket-issues,linear}` | `_tracker(args)` | `active-tickets`, `ticket-archaeology` |
| `CloudBackedExtractor` | `--cloud {aws,gcp,azure}` | `_cloud(args)` | `aws-infra` |
| `MeetingBackedExtractor` | `--meeting {fireflies}` | `_meeting(args)` | `meeting-digest` |

Per-company credentials resolve through `CredEnv.<KEY>.for_company(company)`
(`env_vars.py`), e.g. `BITBUCKET_<COMPANY>_APP_PASSWORD`, `JIRA_<COMPANY>_TOKEN`,
`AWS_<COMPANY>_ACCESS_KEY_ID`. The runbook executor sets `args.company`
from the YAML key BEFORE calling the extractor.

### `put_if_changed` idempotency contract

```
new_hash = md5(content)
existing = store.fingerprint(blob_name)    # postgres: SELECT md5(content) FROM ...
if existing == new_hash:
    return PutIfChangedResult(wrote=False)  # SKIP — no UPSERT, no history row
else:
    UPSERT briar_knowledge ON CONFLICT(blob_name) DO UPDATE
    INSERT briar_knowledge_history (snapshot_at=NOW(), ...)
    return PutIfChangedResult(wrote=True)
```

Postgres backend (`storage/postgres.py`) overrides the default base
implementation to do md5 server-side AND do compare+write in one
transaction. Halves connection-slot pressure on managed PG and is
atomic against concurrent writers.

### Inventory companion (opt-in) — full detail without prompt bloat

Each `ExtractedSection` carries a terse `body` (rendered into the
prompt-baked markdown blob) **and** a structured `data` dict (the full
payload — e.g. every resource the `tagging-inventory` gatherer found).
The markdown drops `data`; the body stays small so agent prompts don't
bloat.

When `knowledge.config.inventory` is truthy, `_run_schedule` writes a
second **inventory companion** blob carrying that `data`:

```
KnowledgeComposer.inventory(company, sections)   # stable JSON: no timestamp, sorted keys
   → store.put_if_changed("inventory:<company>", json, category="inventory")
```

- Name derives from the knowledge blob: `knowledge:acme` → `inventory:acme`;
  `acme.md` → `acme.inventory.json`. Distinct `inventory` category keeps it
  **out of the agent knowledge splice** (list with `briar context list --prefix inventory:`).
- `inventory()` omits the volatile `generated_at` and sorts keys, so it's
  byte-stable — `put_if_changed` dedups it and `briar_knowledge_history`
  gains a row only on real drift, turning the companion into a
  cloud/repo-estate **change log**.
- Best-effort: a companion failure records its own row but never fails the
  already-written knowledge blob. Off by default — existing deployments are
  unchanged.

---

## 5. Data flow — agent pipeline (`agent prfix` / `agent implement`)

```
┌────────────────────────────────────────────────────────────────────┐
│  briar agent prfix --company acme --owner X --repo Y --pr 42      │
│                    --branch B --runbook runbooks/acme.yaml         │
│                    [--meeting-key K | --meeting-query "..."]      │
└──────────────────────────────┬─────────────────────────────────────┘
                               │
            ┌──────────────────┼──────────────────┐
            ▼                  ▼                  ▼
       secrets.env       runbook YAML       KnowledgeStore
       GITHUB_TOKEN      messages.kind      .get("knowledge:acme")
       JIRA_*            git_identity       (previously written by
       AWS_*                                 runbook extract)
       CLAUDE_API_KEY
                               │
                               ▼
                  ┌────────────────────────────┐
                  │ _resolve_git_identity      │
                  │  per-field precedence:     │
                  │  CLI flag > YAML > default │
                  └────────────┬───────────────┘
                               │
                               ▼
            ┌──────────────────────────────────────┐
            │ provider.clone_url + authed_clone_url│
            │ git clone → /tmp/<worktree>          │
            │ git config user.name + user.email    │
            └──────────────────┬───────────────────┘
                               │
                               ▼
            ┌──────────────────────────────────────┐
            │ JIT context fetch (TASK_SCOPED):     │
            │  prfix:    FetchPrReviewContext      │
            │            (PR + review thread + CI) │
            │  implement: FetchTicketContext       │
            │             (tracker ticket body)    │
            │  always:   FetchMeetingContext IF    │
            │            --meeting-key OR          │
            │            --meeting-query resolves  │
            └──────────────────┬───────────────────┘
                               │
                               ▼
            ┌──────────────────────────────────────┐
            │ AgentRunner (Anthropic API + tools)  │
            │   tools bound:                       │
            │     bash, read_file, write_file,    │
            │     edit_file, send_message,        │
            │     mcp__<server>__<tool> (opt-in)  │
            │                                      │
            │   send_message resolves handle →    │
            │   MessageWriter via runbook         │
            │   messages: block                    │
            │   MCP tools come from runbook       │
            │   mcp: block (McpClientManager)     │
            └──────────────────┬───────────────────┘
                               │
                               ▼
       commits + pushes via the same RepositoryProvider
       used by the scheduler (one auth chain, one verb set)
```

Meeting query defaults: `prfix` uses `<owner>/<repo>#<pr>`; `implement`
uses the ticket key (`ACME-42`). Override with `--meeting-query "..."`
or pin one transcript with `--meeting-key <id>`.

---

## 6. Data flow — plan pipeline (`plan build` → `plan run`)

```
┌──────────────────────────────────────────────────────────────────┐
│ briar plan build <board> --name X --company acme [--llm anthropic]│
│                  [--with-knowledge] [--store postgres]            │
└────────────────────────────┬─────────────────────────────────────┘
                             │
        ┌────────────────────┼─────────────────────────┐
        ▼                    ▼                         ▼
  secrets.env         BoardReader              KnowledgeStore
                      .matches(URL)            (splices existing
                      .fetch_cards()           knowledge:acme blobs
                      ├── JiraBoard            into each card's
                      └── GhProjectV2Board     synthesis context
                                                when --with-knowledge)
                             │
                             ▼
        ┌────────────────────────────────────────────────┐
        │ CardSynthesiser (Composite: LLM → Heuristic)   │
        │   per card:                                    │
        │     summary, in_scope, out_of_scope, risks,    │
        │     depends_on (hint only), branch_name        │
        └────────────────────┬───────────────────────────┘
                             │
                             ▼
              save_plan → plan:<name>
              seed knowledge:<company>.<plan>

┌──────────────────────────────────────────────────────────────────┐
│ briar plan run <name> --llm anthropic --company acme              │
│                --owner X --repo Y [--tracker jira]                │
│                [--continue-on-failure] [--limit N] [--dry-run]    │
└────────────────────────────┬─────────────────────────────────────┘
                             │
                             ▼
   PlanContext.from_stores  (journal + knowledge + plan)
                             │
                             ▼
   Selector(llm).pick → SelectorDecision
         │
         ├── PICK key=K branch_parent=B why="..."
         │       └── run_implement(card)
         │             ├── rc == 0 → KnowledgeWriter merges
         │             │     learnings → knowledge:acme.<plan>
         │             │     card.status = done
         │             └── rc != 0 → card.last_attempt_summary
         │                   card.status = blocked
         │                   (loop stops unless --continue-on-failure)
         ├── REPLAN → re-fetch board, preserve statuses (--max-replans)
         ├── COMPLETE → exit 0
         └── BLOCKED  → exit 1
```

Selector requires `--llm` (no deterministic fallback). The synthesiser
at `plan build` time degrades to heuristics when `--llm` is empty.

---

## 7. Data flow — credential lifecycle

```
PROCESS START
      │
      ▼
auto_bootstrap()  (briar/cli.py first thing after argparse)
      │
      ▼
BOOTSTRAPS in registry order:
      1. envfile     ← runs FIRST (laptop default; resolves $BRIAR_SECRETS_FILE
      │                 → /etc/briar/secrets.env → ~/.config/briar/secrets.env)
      │
      ▼
Operator-supplied env vars take precedence (already-set keys preserved)
      │
      ▼
CLI subcommand runs
      │
      ▼
On-demand reads (CredentialStore + CredEnv.<KEY>.for_company(company))
      │
      ├── EnvFileStore       — read from secrets.env
      ├── VaultStore         — HashiCorp Vault KV v2
      ├── AwsSecretsMgr      — /briar/<NAME> prefix
      └── SsmParameterStore  — /briar/ prefix, SecureString


INTERACTIVE ACQUISITION  (briar auth login <target>)
                  │
                  ▼
        AcquirerRegistry.make(target)
                  │
                  ▼
        acquirer.acquire(company, prompt) → Credentials
                  │
                  ▼
        _effective_store(target, --store)
        ├── EXTERNAL policy → use --store as-is (default = envfile)
        └── BOOTSTRAP_LOCAL → forced to envfile (chicken-and-egg)
                  │
                  ▼
        CredentialStore.write(name, value) for each entry
```

Resolution order at runtime (lowest precedence last):

1. `os.environ` at process start — operator-supplied wins
2. envfile bootstrap
3. On-demand `CredentialStore` reads for explicit `--store` flows

---

## 8. Knowledge store layout

### File backend (`storage/file.py`)

```
./knowledge/                          ← BRIAR_KB_FILE_ROOT (default)
├── knowledge/                        ← "knowledge:" category
│   ├── acme.md                       ← blob name "knowledge:acme"
│   ├── acme.archaeology.md           ← blob name "knowledge:acme.archaeology"
│   ├── acme.prfix.md
│   └── acme.acme-q3.md               ← plan-scoped: "knowledge:acme.acme-q3"
├── plan/
│   └── acme-q3.md                    ← "plan:acme-q3"
├── memory/
│   └── reviewer-iklobato.md          ← "memory:reviewer-iklobato"
└── lessons/
    └── python-typing.md              ← "lessons:python-typing"
```

Name resolution (`StoreFile._path_for`):
- `<cat>:<rest>`  → `<root>/<cat>/<rest>.md`
- `<bare>`        → `<root>/<bare>.md`
- `/`-bearing or `.md`-ending → path verbatim (legacy `knowledge_file:`)

### Postgres backend (`storage/_models.py`)

Two tables, one schema:

```sql
briar_knowledge                       -- current snapshot (UPSERT on put)
  blob_name   text PRIMARY KEY
  category    text NOT NULL
  company     text NOT NULL DEFAULT ''     -- derived from blob_name at write
  task        text NOT NULL DEFAULT ''     -- derived from blob_name at write
  content     text NOT NULL
  byte_count  int  NOT NULL
  created_at  timestamptz DEFAULT NOW()
  updated_at  timestamptz DEFAULT NOW()
  INDEX (category)
  INDEX (company)

briar_knowledge_history               -- append-only audit log (INSERT on every put)
  id           bigserial PRIMARY KEY
  blob_name    text NOT NULL
  category     text NOT NULL
  content      text NOT NULL                -- full snapshot
  byte_count   int  NOT NULL
  snapshot_at  timestamptz DEFAULT NOW()
  INDEX (blob_name, snapshot_at)
```

`company` + `task` are **denormalised** at write time by
`_company_task_from(blob_name)`:

| Blob name | company | task |
|---|---|---|
| `knowledge:acme` | `acme` | `` (empty) |
| `knowledge:acme.archaeology` | `acme` | `archaeology` |
| `knowledge:acme.acme-q3` | `acme` | `acme-q3` (plan-scoped) |
| `plan:acme-q3` | `plan` | `` |

Runtime access goes through scoped role `briar_kb` with only DML
permissions. Bootstrap (CREATE TABLE + CREATE ROLE + GRANT) is a
separate admin-DSN path: `StorePostgres.bootstrap_admin(admin_dsn, password)`.

### DSN resolution (`StorePostgres.from_binding`)

First non-empty wins. `CliError` lists every key tried.

1. `binding.config["dsn_env"]` — explicit YAML override:
   `knowledge: {store: postgres, config: {dsn_env: PROD_KB_PG}}`
2. `BRIAR_<COMPANY>_DATABASE_URL` — convention-based per-company
3. `BRIAR_DATABASE_URL` — global fallback

Pool: process-wide singleton per DSN. Defaults `pool_size=4`,
`max_overflow=2`, `pool_timeout=10`, `pool_recycle=1800`,
`pool_pre_ping=True`. Tunable via `BRIAR_PG_POOL_SIZE` /
`BRIAR_PG_POOL_OVERFLOW`.

---

## 9. Per-command flag reference

Compact, current-state. For prose + extended examples see `README.md`.

### `briar version`
No flags. Prints `briar-cli <version>` from package metadata.

### `briar extract`
One-shot manual extraction. Flags resolve through **CLI > env > `.briar.toml`
/ `[tool.briar]` > built-in default** (see §10).

**Core**

| Flag | Used by | Default |
|---|---|---|
| `--company <name>` | required (or `company` in config), drives blob title + name | — |
| `--include <extractor>` | repeatable; default = all available | (all) |
| `--store {file,postgres}` (alias `--storage`) | which backend | `file` |
| `--blob-name <name>` | override the derived name | `knowledge:<company>` |
| `--root <dir>` | file-store root | `./knowledge` |
| `--out-json <path>` | write parallel JSON | (skip) |
| `--merge-claude-md` | merge a knowledge index into CLAUDE.md; write full detail to `.briar/knowledge/<company>.md` for on-demand reading | (off) |
| `--claude-md-path <path>` | CLAUDE.md to merge into (with `--merge-claude-md`) | `./CLAUDE.md` |
| `--advanced-help` | print the full per-extractor override flags + exit | — |

**Provider selectors** (one shared flag each, already canonical):

| Flag | Used by | Default |
|---|---|---|
| `--provider {github,bitbucket}` | repo extractors | `github` |
| `--tracker {jira,github-issues,bitbucket-issues,linear}` | tracker extractors | `jira` |
| `--cloud {aws,gcp,azure}` | cloud extractors | `aws` |
| `--meeting {fireflies}` | meeting extractors | `fireflies` |

**Canonical extractor flags** — one knob per concept, applied to *every*
extractor selected with `--include` (a per-extractor override wins when both
are given):

| Flag | Concept | Default |
|---|---|---|
| `--repo <slug>` | repo/project list (repeatable); also the tracker `project` for `active-tickets` / `ticket-archaeology` | — |
| `--since-days <N>` | history lookback window | per-extractor (below) |
| `--max <N>` | max items / commits / alerts per repo | per-extractor (below) |
| `--top-n <N>` | results surfaced per repo | per-extractor (below) |
| `--sample <N>` | recent PRs sampled per repo | per-extractor (below) |
| `--authors-allow` / `--authors-block` | author allow/block (repeatable; allow ∩ ¬block) | — |
| `--assignees-allow` / `--assignees-block` | assignee allow/block (repeatable) | — |

**Per-extractor defaults** (the value a canonical flag overrides when unset):

| Extractor | since-days | max | top-n | sample |
|---|---|---|---|---|
| `pr-archaeology` | — | 100 | — | — |
| `reviewer-profile` | — | — | 5 | 20 |
| `code-hotspots` | 30 | 100 | 10 | — |
| `defect-hotspots` | 90 | 200 | 10 | — |
| `pr-hygiene` | — | 100 | — | 30 |
| `review-nits` | — | — | 15 | 30 |
| `revert-signals` | 90 | 200 | — | — |
| `commit-message-quality` | 90 | 200 | — | — |
| `stale-prs` | — | 100 | — | — |
| `ci-health` | — | 100 | — | — |
| `dependency-health` | — | 200 | — | — |
| `code-scanning` | — | 200 | 10 | — |
| `test-discipline` | — | — | 10 | — |
| `release-cadence` | — | 100 | — | — |
| `todo-density` | — | 200 | 10 | — |
| `ticket-archaeology` | — | 100 | — | — |
| `meeting-digest` | 7 | 25 | — | — |

**Genuinely extractor-specific flags** (no canonical analogue):

| Flag | Used by | Default |
|---|---|---|
| `--gov-branch <name>` | `repo-governance` | (default branch) |
| `--stale-days <N>` | `stale-prs` staleness threshold | 14 |
| `--prhygiene-large-loc <N>` | `pr-hygiene` "large PR" LOC cutoff | 400 |
| `--aws-extract-region <region>` | `aws-infra` | `us-east-1` |
| `--aws-extract-service <svc>` | `aws-infra` (repeatable; ecs/lambda/logs/rds/sqs/tagging-inventory) | (all) |
| `--aws-extract-profile <name>` | `aws-infra` | — |
| `--meeting-attendee-allow <email>` | `meeting-digest` (repeatable) | — |

**Legacy per-extractor flags** (`--pr-repo`, `--risk-since-days`,
`--reviewer-top-n`, the per-source `--*-authors-allow`, …) still parse but are
hidden from `-h`; the canonical flags above replace them. `briar extract
--advanced-help` lists the full set. Using one prints a one-line note pointing
at its canonical replacement. They remain the escape hatch for the rare case
where two extractors in one invocation need *different* values for the same
concept.

### `briar runbook extract <file.yaml>`
| Flag | Purpose |
|---|---|
| `--task <name>` | run only the schedule whose `task:` matches |

### `briar runbook sweep <directory>`
No subcommand-specific flags.

### `briar runbook serve <directory>`
| Flag | Purpose | Default |
|---|---|---|
| `--tick <seconds>` | scheduler tick | `1.0` |

### `briar agent prfix`
| Flag | Required | Default |
|---|---|---|
| `--company <name>` | ✓ | — |
| `--owner <name>` | ✓ | — |
| `--repo <name>` | ✓ | — |
| `--pr <N>` | ✓ | — |
| `--branch <name>` | ✓ | — |
| `--provider {github,bitbucket}` | | `github` |
| `--runbook <yaml>` | | — (binds send_message + mcp servers) |
| `--store {file,postgres}` | | `file` |
| `--knowledge <dir>` | | `./knowledge` |
| `--dry-run` | | off |
| `--model <name>` | | provider default |
| `--max-iter <N>` | | — |
| `--git-user-name` / `--git-user-email` | | CLI > YAML > default |
| `--keep-worktree` | | off |
| `--meeting {fireflies}` | | `fireflies` |
| `--meeting-key <id>` | | — |
| `--meeting-query <text>` | | `<owner>/<repo>#<pr>` |
| `--meeting-top-k <N>` | | 3 |
| `--meeting-max-bytes <N>` | | 50000 |

### `briar agent implement`
Same as `prfix` but uses ticket identity:

| Flag | Required | Default |
|---|---|---|
| `--ticket-project <key>` | ✓ | — |
| `--ticket-key <key>` | ✓ | — |
| `--tracker {jira,github-issues,bitbucket-issues,linear}` | | `jira` |
| `--meeting-query <text>` (default) | | the ticket key |

(All other prfix flags apply identically.)

### `briar plan build <board>`
| Flag | Required | Default |
|---|---|---|
| `board` (positional) | ✓ | — |
| `--name <slug>` | | derived from URL |
| `--default-branch <name>` | | `main` |
| `--max-cards <N>` | | 50 |
| `--llm {anthropic,openai,gemini,bedrock}` | | (heuristics-only) |
| `--model <name>` | | provider default |
| `--with-knowledge` | | off |
| `--print` | | off |
| `--dry-run` | | off (implies `--print`) |
| `--store {file,postgres}` | | `file` |
| `--company <name>` | | — |

### `briar plan show <name>` / `list` / `status <name>` / `clear <name>`
Common: `--store`, `--root`, `--company`. `clear` adds `--yes`.

### `briar plan next <name>`
| Flag | Required | Default |
|---|---|---|
| `--llm <provider>` | ✓ | — |

### `briar plan advance <name>`
| Flag | Required | Default |
|---|---|---|
| `--card <key>` | ✓ | — |
| `--status {pending,in_progress,done,blocked}` | | `done` |

### `briar plan run <name>`
| Flag | Required | Default |
|---|---|---|
| `name` (positional) | ✓ | — |
| `--company <key>` | ✓ | — |
| `--owner <slug>` | ✓ | — |
| `--repo <slug>` | ✓ | — |
| `--llm <provider>` | ✓ | — |
| `--tracker-project <key>` | | `<owner>/<repo>` |
| `--tracker <kind>` | | `github-issues` |
| `--provider <kind>` | | `github` |
| `--limit <N>` | | 0 (unlimited) |
| `--continue-on-failure` | | off |
| `--max-replans <N>` | | 3 |
| `--dry-run` | | off |
| `--model` / `--max-iter` / `--git-user-name` / `--git-user-email` / `--keep-worktree` / `--runbook` / `--meeting*` | | passes through to implement |
| `--journal-store {file}` / `--journal-root <dir>` | | `file` / `./journal` |

### `briar scaffold implementation` / `pr-fixes`
| Flag | Default / Notes |
|---|---|
| `--prefix <name>` | required |
| `--source {github,bitbucket,jira,aws,sentry}` | repeatable |
| `--archetype <name>` | `engineer` (default) / `pr-fixer` (pr-fixes default) / `pr-ci-fixer` / `pr-conflict-resolver` / `triager` |
| `--shape <name>` | `plan-approve-act` (implementation default) / `one-shot` (pr-fixes default) / `triage` |
| `--trigger-kind <name>` | `github_webhook` / `bitbucket_webhook` / `schedule_cron` / `manual` |
| `--auth-mode {oauth,pat}` | default `oauth`. Sentry always requires PAT. |
| `--company <name>` | splice the company's knowledge into the agent prompt |
| `--out <path>` | default stdout |
| `--owner` / `--repo` | when `--source github` |
| `--bitbucket-workspace` / `--bitbucket-repo` | when `--source bitbucket` |
| `--jira-project` / `--jira-jql` | when `--source jira` |
| `--aws-role-arn` / `--aws-external-id` / `--aws-region` / `--aws-services` | when `--source aws` |
| `--sentry-org` / `--sentry-project` / `--sentry-environment` / `--sentry-level` / `--sentry-query` | when `--source sentry` |
| `--github-secret-id` / `--bitbucket-secret-id` / `--jira-secret-id` / `--sentry-secret-id` | with `--auth-mode pat` (Sentry: always) |
| `--authors-allow` / `--authors-block` / `--assignees-allow` / `--assignees-block` | shared issue filters — apply to every `--source` (repeatable). Per-source `--jira-authors-allow` etc. still parse (hidden) and override |
| `--model` / `--llm-provider-key` | LLM defaults baked into the bundle |
| `--schedule "<cron>"` | with `--trigger-kind schedule_cron` |

### `briar context <subcommand>`
Parent-parser flags (must come BEFORE subcommand):

| Flag | Purpose |
|---|---|
| `--store {file,postgres}` | default `file` |
| `--root <dir>` | file-store root |

Subcommand flags:

| Subcommand | Flag |
|---|---|
| `put <name>` | `--content <text>` (`-` = stdin) / `--from-file <path>` / `--category <name>` |
| `get <name>` | — |
| `list` | `--prefix <s>` |
| `delete <name>` | `--yes` |
| `categories` | — |

**Gotcha:** `briar context list --store postgres` FAILS; use `briar context --store postgres list`. The `--store` lives on the parent. Compare with `briar plan`, where `--store` is on each subcommand — placement differs.

### `briar dashboard`
| Flag | Default |
|---|---|
| `--host <ip>` | `0.0.0.0` |
| `--port <n>` | `8080` |
| `--examples <dir>` | `./examples` |
| `--knowledge-store {file,postgres}` | postgres if `BRIAR_DATABASE_URL` else file |
| `--knowledge <dir>` | `./knowledge` |
| `--log-file <path>` | `/var/log/briar/scheduler.log` |
| `--disk-path <path>` | `/` |
| `--repo-path <dir>` | `.` |
| `--secrets-file <path>` | `/etc/briar/secrets.env` |
| `--du-path <dir>` | repeatable |
| `--once` | render once + exit |

### `briar auth <subcommand>`
| Subcommand | Required flags / args |
|---|---|
| `login <target>` | `target` (positional, required); `--company <name>` (for EXTERNAL targets); `--store <kind>` (default `$BRIAR_DEFAULT_STORE` or `envfile`) |
| `logout <target>` | `--company`, `--yes` |
| `refresh <target>` | `--company` |
| `list` | `--store <kind>`, `--company <name>` |
| `status <target>` | `--company`, `--store` (defaults to `$BRIAR_DEFAULT_STORE` or `envfile`) |

Acquirer destination policy:
- **EXTERNAL** (default) — vendor credentials. `--store` is honoured as-is.
- **BOOTSTRAP_LOCAL** — bootstrap targets (future `vault`). Forced to `envfile`. Warning printed if a different `--store` was passed.

### `briar secrets <subcommand>`
| Subcommand | Flags |
|---|---|
| `doctor` | `--examples <dir>` (default `./examples`); `--store {envfile,aws-secretsmanager,ssm,vault}` (default `envfile`) |
| `bootstrap` | `--kind {envfile}` (default = envfile); `--dry-run` |

### `briar journal <subcommand>`
| Subcommand | Flags |
|---|---|
| `list` | `--command <prefix>`, `--limit <N>` |
| `show <id>` | — |
| `export <id>` | `--format {markdown,json}`, `--out <path>` |

Common: `--store {file}`, `--root <dir>`. Defaults match `BRIAR_JOURNAL_STORE` / `BRIAR_JOURNAL_ROOT`.

### `briar telemetry <subcommand>`
| Subcommand | Effect |
|---|---|
| `status` | print current mode |
| `preview` | dry-run the next planned event |
| `off` | disable telemetry |
| `errors-only` | crash reports only |
| `full` | errors + usage |
| `reset` | clear locally cached config |

---

## 10. Global flags + environment variables

### Global CLI flags (all subcommands)

| Flag | Default | Purpose |
|---|---|---|
| `--format {table,json,yaml,csv,quiet}` | `table` | output format |
| `--verbose` / `-v` | INFO | DEBUG-level logging |

Global flags can be positioned before OR after the subcommand
(argparse tolerates both). `briar journal export --format` has a
known collision with the global (`xfail(strict=True)` in tests).

### Project config — `.briar.toml` / `[tool.briar]`

Stable per-project values (company, store, repo, agent model + git
identity) can live in a config file instead of being retyped every call.
Resolution precedence, highest first:

```
CLI flag  >  env var  >  project config  >  built-in default
```

briar searches upward from cwd for `.briar.toml` (keys at top level) or a
`pyproject.toml` carrying `[tool.briar]`. Config fills matching flags as
their new default — so an explicit CLI flag still wins, and config can
even satisfy an otherwise-required flag (`--company`, `agent --owner`/
`--repo`). When neither config nor flag supplies `--owner`/`--repo`, they
are inferred from the git `origin` remote.

```toml
# .briar.toml  (or [tool.briar] in pyproject.toml)
company = "acme"
store   = "postgres"          # alias: extract --storage
root    = "./knowledge"
tracker = "jira"

repos = ["acme-co/web", "acme-co/api"]   # canonical extract --repo list

[repo]                         # bare owner + name for agent / plan
owner = "acme-co"
repo  = "acme-app"

[agent]
model          = "claude-sonnet-4-6"
git_user_name  = "acme-bot"
git_user_email = "bot@acme.com"
```

Config keys map to dests via env override `BRIAR_COMPANY` (company) and
`BRIAR_DEFAULT_STORE` (store).

### Env vars — operational

| Env var | Effect |
|---|---|
| `BRIAR_VERBOSE=1` | same as `--verbose` |
| `BRIAR_LIB_DEBUG=1` | also surface third-party loggers (httpx, boto3) |
| `BRIAR_TELEMETRY=off` / `DO_NOT_TRACK=1` | disable telemetry |

### Env vars — knowledge store DSN

| Env var | Effect |
|---|---|
| `BRIAR_DATABASE_URL` | switch default knowledge store to `postgres`; final-fallback DSN |
| `BRIAR_<COMPANY>_DATABASE_URL` | per-company DSN (convention; auto-detected) |
| `BRIAR_KB_DATABASE_URL` (or any name in YAML `knowledge.config.dsn_env`) | explicit DSN |
| `BRIAR_PG_POOL_SIZE` / `BRIAR_PG_POOL_OVERFLOW` | pool tuning |

### Env vars — credentials & secrets

| Env var | Effect |
|---|---|
| `BRIAR_DEFAULT_STORE={envfile,vault,aws-secretsmanager,ssm}` | default `--store` for `auth login` |
| `BRIAR_SECRETS_FILE=/path/to/secrets.env` | overrides resolution: this → `/etc/briar/secrets.env` → `~/.config/briar/secrets.env` |
| `GITHUB_TOKEN` | workspace-wide GitHub PAT |
| `BITBUCKET_<COMPANY>_WORKSPACE` / `_USERNAME` / `_APP_PASSWORD` | per-tenant Bitbucket |
| `JIRA_<COMPANY>_URL` / `_EMAIL` / `_TOKEN` | token-auth Jira |
| `JIRA_<COMPANY>_SESSION_TOKEN` / `_TENANT_SESSION_TOKEN` / `_XSRF_TOKEN` / `_USER_AGENT` | session-auth Jira |
| `JIRA_<COMPANY>_AUTH_KIND={token,session}` | force a Jira auth strategy |
| `LINEAR_<COMPANY>_TOKEN` | Linear PAT |
| `AWS_<COMPANY>_ACCESS_KEY_ID` / `_SECRET_ACCESS_KEY` / `_REGION` / `_SESSION_TOKEN` | per-tenant AWS |
| `FIREFLIES_<COMPANY>_API_KEY` | Fireflies API key |

### Env vars — alerting

| Env var | Effect |
|---|---|
| `BRIAR_NOTIFY_SINKS=telegram,slack` | scheduler failure-alert sinks |
| `SLACK_<COMPANY>_WEBHOOK_URL` | per-tenant Slack |
| `TELEGRAM_BOT_TOKEN` | global Telegram bot |
| `TELEGRAM_<COMPANY>_CHAT_ID` | per-tenant target chat |

### Env vars — journal

| Env var | Effect |
|---|---|
| `BRIAR_JOURNAL=off` | disable journal entirely |
| `BRIAR_JOURNAL_STORE={file}` | system-of-record backend |
| `BRIAR_JOURNAL_SINKS=file` | comma-separated publish sinks |
| `BRIAR_JOURNAL_ROOT=./journal` | filesystem root |

### Cred env naming convention

Hyphens in company keys uppercased + replaced with `_`:

| Company key | Env var resolved |
|---|---|
| `acme` | `BITBUCKET_ACME_APP_PASSWORD` |
| `widget-co` | `BITBUCKET_WIDGET_CO_APP_PASSWORD` |
| `bitspark` | `AWS_BITSPARK_ACCESS_KEY_ID` |

Quirk (asserted by `tests/unit/test_env_vars.py`): empty-company
`CredEnv.AWS_KEY_ID.for_company("")` produces `AWS__ACCESS_KEY_ID`
(double underscore). Reject-or-correct would require flipping the
documented assertion.

---

## 11. Exit codes (`commands/_enums.py:ExitCode`)

| Code | Symbol | Meaning |
|---|---|---|
| 0 | `OK` | success |
| 1 | `GENERAL_ERROR` | soft failure not covered by specific codes (also: `plan run` finished with blocked cards; `plan clear` aborted at confirm) |
| 2 | `USAGE_ERROR` | argparse / unknown subcommand |
| 3 | `STORE_OPEN_FAILED` | `KnowledgeStore` couldn't open (DSN bad, perms, etc.) |
| 4 | `CLONE_FAILED` | agent's `git clone` failed |
| 5 | `GIT_CONFIG_FAILED` | clone OK but `user.name`/`user.email` set failed |
| 6 | `AGENT_ERROR` | agent run itself failed (LLM raised, iter ceiling, tool errored) |

Codes 1–6 are stable. 7–9 reserved for future pre-LLM categories; 10+
reserved for future LLM/agent runtime failures.

---

## 12. Pitfalls & invariants worth knowing

### Flag-placement gotchas

- **`briar context --store ... <subcmd>`** — `--store` lives on the
  `context` parent parser. `briar context list --store postgres` fails
  with `unrecognized arguments: --store postgres`.
- **`briar plan <subcmd> --store ...`** — `--store` lives on each
  subparser. Both orderings work.
- **Global `--format`** works either side of the subcommand.

### Idempotency invariants

- `put_if_changed` is the **only** path the runbook executor uses to
  write. Direct `put()` writes unconditionally; do not call from a
  scheduler-style loop unless you want history bloat.
- Postgres `put_if_changed` does compare-and-set in ONE transaction
  (server-side md5). Concurrent writers cannot interleave.
- `fingerprint()` returns `""` on missing blob. `get()` also returns
  `""` on missing; callers cannot distinguish missing from empty —
  convention is markdown content is never legitimately empty.
- Skip path leaves `updated_at` AND history rows untouched.

### What invalidates what

| Changed... | Restart needed? | Effect |
|---|---|---|
| `runbooks/*.yaml` | no (next fire) | scheduler re-reads on every iteration |
| `/etc/briar/secrets.env` | yes | env held in process memory |
| `src/briar/` (editable install) | yes | imported modules cached |
| Postgres `briar_knowledge` table | no | scheduler reads fresh on each fire |
| Jira session-token cookie | no — but log it | scheduler reads from env at startup; restart picks up rotation |

### Schedule task-name renames

`schedule.task != "extractors"` quietly suffixes the blob name:
`knowledge:acme` → `knowledge:acme.<task>` (`executor._task_blob_name`).
Renaming a task in the YAML strands the old-named blob in the store;
clean up with `briar context delete knowledge:acme.<old>`.

### Empty-section semantics

`ExtractedSection(title="")` is `EMPTY_SECTION` (`base.py:37`). The
executor (`_collect_sections`) drops them before they reach the
composer. The composer never has to filter — `is_empty` is the contract.

### Provider `is_available()` gate

Extractors call `provider.is_available()` (or
`tracker.is_available()` etc.) inside their OWN `is_available(args)`.
Missing creds short-circuit the extractor; no 401 ever bubbles up
from the SDK. `extractor-skip: is_available() returned False —
likely missing credentials` is the log signature.

### Three failure boundaries → one shape

`_run_schedule` has three try/except sites (`_collect_sections`,
`make_store`, `put_if_changed`). All three route through
`_record_failure` (`executor.py:233-249`) so the
log-exception + notify + row-shape stay identical and don't drift
across edits.

### `_notify_failure` is fire-and-forget

A broken Telegram bot does not crash the extractor. Sinks raise
INTO the `_notify_failure` try/except and the loop continues
(`executor.py:281-282`).

### Bootstrap precedence is *registry order*

`BOOTSTRAPS` tuple in `credentials/_bootstraps/__init__.py` determines
who runs first. Today only `envfile` is registered. As future
bootstraps are added, earlier wins because later bootstraps only fill
vars not yet present — operators who logged in locally aren't stranded
by a remote vault being unreachable.

### Knowledge store backends are NOT fully isomorphic

- `delete` on file removes the file + cleans empty parent dirs.
- `delete` on postgres clears the current-snapshot row, **preserves** history.
- `list` on either backend shows current snapshots only — history
  is not surfaced through the `KnowledgeStore` ABC.

### Pool sizing

Process-wide singletons per DSN. Defaults give 6 slots per process
(pool=4, overflow=2). Three processes (dashboard + scheduler +
agent runner) × 6 = 18 slots, under DO managed PG small-tier's
~22 non-superuser budget.

### Agent git identity precedence

Per FIELD, not per object: CLI `--git-user-name` > YAML
`git_identity.name` > hardcoded `iklobato` default. You can set
`name` in YAML and override only `email` from the CLI.

### TASK_SCOPED_EXTRACTORS bypass the runbook executor

`meeting-context`, `pr-review-context`, `ticket-context` are NOT walked
by the scheduler. They have a `fetch(args)` verb (not `extract`), are
fetched JIT by the agent runner from the `TASK_SCOPED_EXTRACTORS`
registry, and their output is spliced into ONE agent's system prompt —
never persisted.

---

## 13. How to re-verify this file (registry snapshot)

If you suspect drift, run this to regenerate the §3 table. Anything
that disagrees is a registry edit you missed.

```bash
python3 <<'EOF' | column -t -s '|'
import importlib
specs = [
    ("EXTRACTORS",                                    "briar.extract:EXTRACTORS"),
    ("TASK_SCOPED_EXTRACTORS",                        "briar.extract:TASK_SCOPED_EXTRACTORS"),
    ("STORES (knowledge)",                            "briar.storage:KnowledgeStoreRegistry.STORES"),
    ("WRITERS (runbook messages.kind)",               "briar.messaging:WRITERS"),
    ("ACQUIRERS (auth login)",                        "briar.auth._acquirers:ACQUIRERS"),
    ("CRED_STORES (auth --store)",                    "briar.credentials:STORES"),
    ("NOTIFY_SINKS (BRIAR_NOTIFY_SINKS)",             "briar.notify:SINKS"),
    ("BOARD_READERS (plan build)",                    "briar.plan._boards:BOARD_READERS"),
    ("JOURNAL_SINKS",                                 "briar.journal.sinks:JOURNAL_SINKS"),
    ("LLMS (--llm)",                                  "briar.agent._llms:LLMS"),
    ("MEETINGS (--meeting)",                          "briar.extract._meetings:MEETINGS"),
    ("PROVIDERS (--provider)",                        "briar.extract._providers:PROVIDERS"),
    ("TRACKERS (--tracker)",                          "briar.extract._trackers:TRACKERS"),
    ("CLOUDS (--cloud)",                              "briar.extract._clouds:CLOUDS"),
    ("ARCHETYPES (scaffold --archetype)",             "briar.iac.scaffold.archetypes:ARCHETYPES"),
    ("WORKFLOW_SHAPES (scaffold --shape)",            "briar.iac.scaffold.shapes:WORKFLOW_SHAPES"),
    ("TRIGGER_TEMPLATES (scaffold --trigger-kind)",   "briar.iac.scaffold.triggers:TRIGGER_TEMPLATES"),
    ("SOURCE_TEMPLATES (scaffold --source)",          "briar.iac.scaffold.sources:SOURCE_TEMPLATES"),
    ("BOOTSTRAPS (secrets bootstrap --kind)",         "briar.credentials._bootstraps:BOOTSTRAPS"),
    ("AWS_SERVICE_GATHERERS",                         "briar.extract.aws_services:AWS_SERVICE_GATHERERS"),
    ("FORMATTERS (--format)",                         "briar.formatting:FORMATTERS"),
]
for label, spec in specs:
    mod, _, attr = spec.partition(":")
    obj = importlib.import_module(mod)
    for piece in attr.split("."):
        obj = getattr(obj, piece)
    print(f"{label}|{sorted(obj.keys())}")
EOF
```

For per-command flag drift, run `briar <cmd> --help` and diff against §9.

---

## 14. Related docs

- `README.md` — operator-facing prose + step-by-step examples
- `ARCHITECTURE.md` / `ARCHITECTURE_DEEP.md` — SOLID audit + abstraction inventory
- `IMPLEMENTATION_PLAN.md` — per-provider credential acquisition guide
- `DEPLOY_EC2.md` — systemd deployment recipe (currently NOT the deployment model — see CLAUDE.md)
- `agents/runbook.md` — operator doc for `briar runbook` (mentions `serve`)
- `examples/all_features.yaml` — comprehensive multi-company runbook reference
- `examples/multi_company.yaml` — 3-company tutorial without `messages:`
- `runbooks/` (gitignored) — real per-company runbooks; one YAML per company convention
- Global CLAUDE.md (`/Users/iklo/.claude/CLAUDE.md`) — operator preferences, immutable constraints
- Project CLAUDE.md (`/Users/iklo/briar/CLAUDE.md`) — workspace layout, droplet identity, prod gotchas
