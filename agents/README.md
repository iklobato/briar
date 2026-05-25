# Briar — agent operator's manual

This directory is the AI-agent-facing reference for running every
`briar` feature. Each file under `agents/` describes one command
family with the exact shape an autonomous agent needs: when to run
it, what must be in place first, the verbatim shell call, how to
confirm success, and what to do on failure.

If you are an agent picking up work in this repo, **read this index
first**, then the one file matching the task you've been handed.

## Conventions used in every file

| Section | What it answers |
|---|---|
| **Purpose** | What the command actually does, in 1–2 sentences |
| **When to use** | Trigger conditions you can match against your task |
| **Prerequisites** | Env vars, prior commands, credentials, files that must exist |
| **Commands** | Exact shell invocations with `<PLACEHOLDERS>` you fill in |
| **Verifying success** | What to inspect after running |
| **Common failures** | Specific error → specific fix. Do NOT swallow these |

## Global flags every command honours

| Flag | Default | What it does |
|---|---|---|
| `--format {table,json,yaml,csv,quiet}` | `table` (lists), `json` (single record) | Output shape |
| `--verbose` / `-v` | off | DEBUG logging; also via `BRIAR_VERBOSE=1` |

When piping into another tool, pass `--format json` and parse with `jq`.
When you only care about exit code, pass `--format quiet`.

## Exit codes (canonical, from `briar.commands._enums.ExitCode`)

| Code | Name | Meaning |
|---|---|---|
| `0` | `OK` | Success |
| `1` | `GENERAL_ERROR` | Soft failure (e.g. `plan run` finished with blocked cards, confirm aborted) |
| `2` | `USAGE_ERROR` | Bad CLI usage; check the args you passed |
| `3` | `CREDENTIAL_ERROR` | Missing or invalid credentials — run `briar secrets doctor` |
| `4` | `EXTERNAL_ERROR` | Upstream service (GitHub, Anthropic, Jira) failed |

Always check exit code before parsing output.

## Storage backends — `--store` is a shared concept

Two `KnowledgeStore` backends ship today:

| `--store` | Where data lives | When to pick it |
|---|---|---|
| `file` (default) | `./knowledge/<prefix>/<name>.md` on local disk | Local dev, single-host operation |
| `postgres` | `BRIAR_DATABASE_URL` (env) | Multi-host, shared truth, durable |

Blobs are named by category-prefix convention:

| Prefix | Owner | Notes |
|---|---|---|
| `knowledge:<company>` | `briar extract` | Cold rebuild from live world state |
| `knowledge:<company>.<plan>` | `briar plan build` + `KnowledgeWriter` | Plan-scoped live source of truth. Spliced into every `agent implement` call automatically |
| `plan:<name>` | `briar plan build` | Stored plan blob |
| `memory:*`, `lessons:*` | various | Free-form agent state |

## Journal — every command writes a decision audit trail

Every command opens a `Session` and records `DecisionEvent`s. Stored
under `./journal/sessions/` (or postgres) by `JournalStore`, published
to `./journal/published/` by `JournalSink`. Read with `briar journal
list` / `show` / `export`.

Sessions are append-only. Don't try to edit them directly; if you
need to redact, drop the row at the backend level.

## Credentials

Two patterns:

1. **Env vars** loaded from `/etc/briar/secrets.env` (production) or
   `$XDG_CONFIG_HOME/briar/secrets.env` (dev) at CLI startup. See
   `briar secrets doctor` to see which (company, extractor) pairs
   are covered.
2. **Interactive login** for OAuth/SSO flows (GitHub PAT, AWS SSO,
   Jira session): `briar auth login <target>`.

If a command exits 3 (`CREDENTIAL_ERROR`), the first move is `briar
secrets doctor --examples examples/` to see what's missing.

## The file map

| File | Command family |
|---|---|
| [version.md](version.md) | `briar version` — sanity-check the install |
| [extract.md](extract.md) | `briar extract` — one-shot knowledge extraction |
| [runbook.md](runbook.md) | `briar runbook` — scheduled extraction (extract / sweep / serve) |
| [agent.md](agent.md) | `briar agent` — autonomous LLM flows (prfix / implement) |
| [plan.md](plan.md) | `briar plan` — LLM-driven implementation plans (build / show / status / next / advance / run / list / clear) |
| [scaffold.md](scaffold.md) | `briar scaffold` — emit JSON config bundles |
| [context.md](context.md) | `briar context` — read/write markdown blobs |
| [dashboard.md](dashboard.md) | `briar dashboard` — read-only HTML status page |
| [auth.md](auth.md) | `briar auth` — interactive credential acquisition |
| [creds.md](creds.md) | `briar secrets` — credential coverage (`doctor`, `bootstrap`) |
| [journal.md](journal.md) | `briar journal` — inspect decision sessions |

## Rules you must not violate

These mirror the global constraints in the surrounding repo:

1. **Never commit to `main`, `master`, or `dev`.** Branch first.
2. **Never `--force-push` to a shared branch.**
3. **Prefix any PR/issue/ticket comment you post on the user's behalf with `[AI] `.**
4. **Never skip pre-commit hooks** (`--no-verify`) unless the user explicitly authorised it.
5. **Verify, don't claim.** Don't say a command worked until you've run it and read its output.
6. **Smallest change that solves the problem.** No drive-by refactors.
7. **No invented context.** Don't reference flags, env vars, or commands you haven't confirmed exist — re-read these docs or run `--help`.

When in doubt, run the command with `--help` first. The `--help`
output is the canonical contract; this directory is an opinionated
operator's guide on top of it.
