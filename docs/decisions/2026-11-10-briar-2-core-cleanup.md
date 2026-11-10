# briar 2.0: keep only the core features

Grill session, 2026-11-10. Goal: cut briar 2.0 down to its core commands
and parameters before the pipeline engine (spec 003) is built. Builds on
`2026-11-10-briar-2-pipeline.md` (D1 to D36, D29a, D29b, D32a) and
`2026-11-10-briar-2-source-logins.md` (L1 to L21). Where a decision below
changes one of those, it says so.

Starting point (measured on the top of the spec 002 stack, PR #89): 13
commands, about 35 operations, 189 parameters on the Typer side (counted per
operation), and 88 options on `extract` (58 of them hidden).

Format: `ID. Decision: chosen answer. Why. (depends on)`

## Decisions

### How to cut

- C1. Where a parameter lives: a flag only for what changes on each run
  (the target, `--company`, `--dry-run`); anything stable goes to
  `.briar.toml` or the runbook; tuning knobs become fixed defaults. The
  largest cut that stays usable. (none)
- C2. When: everything is cut now, before spec 003, including the commands
  the engine will replace. `v2` has no agent or pipeline feature until spec
  003 lands; D27 ships one 2.0 release at the end, so no user sees the gap.
  Changes the timing of D35. (C1)
- C3. Work: a new cleanup spec now, before specs 004 and 005. Spec 004 keeps
  the inside of the extractors (pydantic config, one Registry), spec 005 the
  new logins. One subject per spec. (C2)

### Commands that leave now

- C4. `plan`: all 8 operations leave. The `board` pipeline of spec 003 builds
  and shows the plan. (C2, D35)
- C5. The code only `agent` and `plan` use leaves with them: `agent/runner.py`,
  `agent/_llms/*`, the plan selector and loop, `mcp/` (and the runbook `mcp:`
  block). What spec 003 reuses (the selector, D19; the bash allowlist and
  forbidden tokens, D25) comes back from git history. Extends D28. (C2, C4)
- C6. `context` leaves. The blobs are `.md` files under `./knowledge`. (C1)
- C7. `telemetry` leaves as a command. The tier is set by env var or
  `.briar.toml` only, still off by default. Changes D30's surface, not its
  behaviour. (C1)
- C8. `secrets` leaves. The L4 check before each run and `auth status`
  answer which keys are missing. (C1, L4)
- C9. `version` leaves; `briar --version` / `-V` stays. (C1)
- C10. `journal` leaves now (D28 already removed it; C2 sets the timing). (C2, D28)

### What stays, and its parameters

- C11. Knowledge store: file only. The Postgres store leaves (SQLAlchemy,
  psycopg). `--store` leaves every command; the file root is the `root`
  setting only. Changes D21: checkpoints use SQLite only. (C1)
- C12. `briar extract` flags: `--company`, `--include`, `--repo`. Provider,
  tracker, meeting, cloud, author filters, limits and time windows move to
  `[extract]` / `[extract.<name>]` in `.briar.toml`. Shrinks D32a's CLI
  list. (C1, D32a)
- C13. `briar extract` output modes leave: `--out-json`, `--merge-claude-md`,
  `--claude-md-path`, `--blob-name`. It writes only the blob
  `knowledge:<company>`. Issue #76 becomes obsolete on `v2`. (C12)
- C14. `briar runbook`: `extract <file> [--task]` and `serve <dir>` stay;
  `sweep` leaves; `--tick` becomes a fixed 1 second. (C1)
- C15. `briar auth`: `login` and `status` stay; `logout`, `refresh` (it
  worked for no target) and `list` leave. A login is removed by editing
  `secrets.env`. `--cred-store` leaves (`envfile` is the only store).
  Spec 005 builds the new logins on these two operations. (C1, L1 to L21)
- C16. Setup: `init` and `doctor` stay. `config show` becomes the first table
  of `doctor`; `doctor` drops the LLM-key and store checks. (C1, C11)
- C17. `--format`: `table` and `json` only; `yaml`, `csv` and `quiet` leave.
  `quiet` printed nothing for most commands. (C1)
- C18. Logging: only `-v` / `--verbose`. `BRIAR_VERBOSE` and
  `BRIAR_LOG_LEVEL` leave. (C1)

### Code that leaves or changes with the commands

- C19. The 4 task-scoped extractors (`ticket-context`, `pr-review-context`,
  meeting context, Slack context) leave now; spec 003 brings back what its
  pipelines need. Changes D5. (C2, C5)
- C20. `messaging` (the runbook `messages:` block) and `notify`
  (`BRIAR_NOTIFY_SINKS`) leave now. `runbook serve` sends no failure alert
  until Apprise (D29) is added. Changes D29's timing. (C2, C5)
- C21. The personas in `iac/scaffold/archetypes/` become `.md` prompt files
  now (a mechanical move of their text) and their Python code leaves. Moves
  D18 forward. (C5)

### 1.x users

- C22. A removed command gets Typer's normal "No such command" error, no
  special message; the CHANGELOG explains. The removed-credential-store
  guard from PR #83 stays: without it a run goes on without its keys and
  fails later with a vague error. (C2)

## Resulting surface

```
briar [--format table|json] [-v] [-V] [--install-completion] [--show-completion]
briar extract --company C [--include NAME ...] [--repo OWNER/REPO ...]
briar runbook extract FILE [--task NAME]
briar runbook serve DIR
briar auth login TARGET [--company C]
briar auth status TARGET [--company C]
briar init [--company C] [--owner O] [--repo R] [--path P] [--force]
briar doctor
```

Then spec 003 adds `briar run <pipeline> <target>`, `briar resume <run-id>`
and `briar runs` (D35). That is 5 commands and 4 operations (`runbook extract`, `runbook serve`,
`auth login`, `auth status`) now, against 13 commands and about 35
operations today.

## Open risks

- K1. `v2` has no agent or pipeline features until spec 003 lands (C2). If
  spec 003 slips, so does the whole 2.0 release (D27).
- K2. Code that spec 003 reuses (selector, bash allowlist, task-scoped
  extractors, MCP manager) is restored from git history; the cleanup spec
  must record the commit each one is deleted in.
- K3. Postgres users lose their store (C11) with no export tool; the blobs
  in Postgres stay where they are.
- K4. `runbook serve` has no failure alert between C20 and Apprise.
- K5. A server loses the fine log-level control of `BRIAR_LOG_LEVEL` (C18);
  `runbook serve` still logs INFO progress (`daemon_logging`).
- K6. `tests/unit/cli/flags_1x.json` shrinks: the cases of removed commands
  and flags go, and the moved `extract` flags need config tests instead.
- K7. The README and most of `agents/*.md` describe the removed commands and
  must be rewritten in the same PRs; the site docs gap (pipeline R7) grows.
- K8. Issue #76 stays a 1.x bug on `main` even though C13 removes the feature
  on `v2`; #77 (`code-scanning`) still applies to `v2`.

## Left for the spec (not decided here)

- Whether `auth status` with no target lists every login (what 1.x `list` did).
- The names of the telemetry env var and config key (C7).
- The exact `[extract]` / `[extract.<name>]` schema for the moved flags (C12).
- Which dependencies leave (likely `anthropic`, `GitPython`, `SQLAlchemy`,
  `psycopg`, the `mcp`, `openai` and `gemini` extras): to be checked by
  import, not assumed.
- The spec's number and the PR split.
- Whether the startup regression from spec 002 (about 60 ms) is gone once
  the heavy modules leave: to be measured, not assumed.
